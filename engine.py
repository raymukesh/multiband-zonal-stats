"""Tile-based GDAL/NumPy zonal statistics engine.

This module deliberately has no QGIS imports. Each zone is supplied as OGR WKB
already transformed into the raster CRS, keeping it suitable for background use.
"""

from __future__ import annotations

import csv
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .utils import AREA_UNITS, safe_band_name


@dataclass(frozen=True)
class Zone:
    internal_id: int
    feature_id: object
    name: str
    wkb: bytes
    bounds: tuple[float, float, float, float]


@dataclass(frozen=True)
class EngineOptions:
    bands: Sequence[int]
    statistics: Sequence[str]
    area_unit: str = "pixels"
    all_touched: bool = False
    tile_size: int = 1024
    band_chunk_size: int = 8


class CancelledError(RuntimeError):
    pass


def _intersects(a, b) -> bool:
    # Treat touching bounds as intersecting. This is conservative for the
    # default pixel-centre rule and required for correct ALL_TOUCHED masks.
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def non_overlapping_passes(zones: Sequence[Zone]) -> list[list[Zone]]:
    """Greedily group zones whose bounding boxes do not overlap.

    Bounding boxes may create extra passes, but never cause overlapping zones to
    be incorrectly combined into a single-label raster.
    """
    passes: list[list[Zone]] = []
    for zone in sorted(zones, key=lambda z: (z.bounds[0], z.bounds[1], z.internal_id)):
        for group in passes:
            if all(not _intersects(zone.bounds, other.bounds) for other in group):
                group.append(zone)
                break
        else:
            passes.append([zone])
    return passes


def _window_geotransform(gt, xoff: int, yoff: int):
    return (
        gt[0] + xoff * gt[1] + yoff * gt[2],
        gt[1],
        gt[2],
        gt[3] + xoff * gt[4] + yoff * gt[5],
        gt[4],
        gt[5],
    )


def _pixel_area_m2(srs, gt) -> float | None:
    if not srs or not srs.IsProjected():
        return None
    native_area = abs(gt[1] * gt[5] - gt[2] * gt[4])
    factor = float(srs.GetLinearUnits() or 1.0)
    return native_area * factor * factor


def _area_factor(unit: str) -> float:
    factors = {"m2": 1.0, "km2": 1e-6, "ha": 1e-4, "acres": 0.00024710538146717}
    return factors[unit]


def _tile_bounds(gt, xoff, yoff, width, height):
    points = []
    for px, py in ((xoff, yoff), (xoff + width, yoff), (xoff, yoff + height), (xoff + width, yoff + height)):
        points.append((gt[0] + px * gt[1] + py * gt[2], gt[3] + px * gt[4] + py * gt[5]))
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _candidate_tiles(zones, gt, raster_width, raster_height, tile_size):
    """Map zone bounding boxes to raster tile coordinates in O(zones)."""
    determinant = gt[1] * gt[5] - gt[2] * gt[4]
    if determinant == 0:
        raise ValueError("The raster has a non-invertible geotransform.")

    def world_to_pixel(x, y):
        dx, dy = x - gt[0], y - gt[3]
        return (
            (gt[5] * dx - gt[2] * dy) / determinant,
            (-gt[4] * dx + gt[1] * dy) / determinant,
        )

    columns = math.ceil(raster_width / tile_size)
    rows = math.ceil(raster_height / tile_size)
    candidates = {}
    for zone in zones:
        xmin, ymin, xmax, ymax = zone.bounds
        pixels = [
            world_to_pixel(x, y)
            for x, y in ((xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax))
        ]
        px, py = zip(*pixels)
        col_start = max(0, math.floor(min(px) / tile_size))
        col_end = min(columns - 1, math.floor(max(px) / tile_size))
        row_start = max(0, math.floor(min(py) / tile_size))
        row_end = min(rows - 1, math.floor(max(py) / tile_size))
        if col_end < col_start or row_end < row_start:
            continue
        for row in range(row_start, row_end + 1):
            for column in range(col_start, col_end + 1):
                candidates.setdefault((column, row), []).append(zone)
    return candidates


def _rasterize_labels(gdal, ogr, osr, zones, projection, gt, width, height, all_touched):
    mask_ds = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Int32)
    mask_ds.SetGeoTransform(gt)
    mask_ds.SetProjection(projection)
    vector_driver = ogr.GetDriverByName("MEM") or ogr.GetDriverByName("Memory")
    vector_ds = vector_driver.CreateDataSource("")
    spatial_ref = None
    if projection:
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromWkt(projection)
    layer = vector_ds.CreateLayer("zones", srs=spatial_ref, geom_type=ogr.wkbUnknown)
    layer.CreateField(ogr.FieldDefn("zone_id", ogr.OFTInteger))
    definition = layer.GetLayerDefn()
    for zone in zones:
        feature = ogr.Feature(definition)
        feature.SetField("zone_id", zone.internal_id)
        feature.SetGeometry(ogr.CreateGeometryFromWkb(zone.wkb))
        layer.CreateFeature(feature)
        feature = None
    options = ["ATTRIBUTE=zone_id"]
    if all_touched:
        options.append("ALL_TOUCHED=TRUE")
    error = gdal.RasterizeLayer(mask_ds, [1], layer, options=options)
    if error:
        raise RuntimeError("GDAL could not rasterize a polygon mask.")
    labels = mask_ds.GetRasterBand(1).ReadAsArray()
    return labels


def run_zonal_statistics(
    raster_path: str,
    zones: Sequence[Zone],
    output_path: str,
    options: EngineOptions,
    progress: Callable[[float, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Calculate statistics and atomically write a long-format CSV."""
    from osgeo import gdal, ogr, osr
    import numpy as np

    if options.area_unit not in AREA_UNITS:
        raise ValueError(f"Unsupported area unit: {options.area_unit}")
    dataset = gdal.Open(raster_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise ValueError(f"GDAL could not open raster: {raster_path}")
    if not zones:
        raise ValueError("The polygon layer contains no usable features.")

    gt = dataset.GetGeoTransform()
    projection = dataset.GetProjection()
    srs = osr.SpatialReference()
    if projection:
        srs.ImportFromWkt(projection)
    pixel_area_m2 = _pixel_area_m2(srs, gt)
    if options.area_unit != "pixels" and pixel_area_m2 is None:
        raise ValueError(
            "Metric area output currently requires a projected raster CRS. "
            "Reproject the raster to a suitable equal-area CRS or choose pixels."
        )

    band_names = {
        index: safe_band_name(dataset.GetRasterBand(index).GetDescription(), index)
        for index in options.bands
    }
    zone_by_id = {zone.internal_id: zone for zone in zones}
    zone_count = max(zone_by_id)
    tile = max(128, int(options.tile_size))
    windows = []
    candidates_by_tile = _candidate_tiles(
        zones, gt, dataset.RasterXSize, dataset.RasterYSize, tile
    )
    for (column, row), candidates in sorted(candidates_by_tile.items(), key=lambda item: (item[0][1], item[0][0])):
        xoff, yoff = column * tile, row * tile
        width = min(tile, dataset.RasterXSize - xoff)
        height = min(tile, dataset.RasterYSize - yoff)
        bounds = _tile_bounds(gt, xoff, yoff, width, height)
        candidates = [zone for zone in candidates if _intersects(zone.bounds, bounds)]
        if candidates:
            windows.append((xoff, yoff, width, height, candidates))

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", suffix=".csv", prefix=f".{output.stem}-", dir=output.parent, delete=False
    )
    temp_name = temporary.name
    rows_written = 0
    try:
        writer = csv.writer(temporary)
        header = ["polygon_id", "polygon_name", "band_index", "band_name", "area", "area_unit"]
        header.extend(options.statistics)
        writer.writerow(header)
        chunks = [options.bands[i : i + max(1, options.band_chunk_size)] for i in range(0, len(options.bands), max(1, options.band_chunk_size))]
        total_steps = max(1, len(chunks) * len(windows))
        completed = 0
        for band_chunk in chunks:
            shape = (len(band_chunk), zone_count + 1)
            count = np.zeros(shape, dtype=np.int64)
            covered = np.zeros(shape, dtype=np.int64)
            sums = np.zeros(shape, dtype=np.float64)
            sums_sq = np.zeros(shape, dtype=np.float64)
            minima = np.full(shape, np.inf, dtype=np.float64)
            maxima = np.full(shape, -np.inf, dtype=np.float64)

            for xoff, yoff, width, height, candidates in windows:
                if is_cancelled and is_cancelled():
                    raise CancelledError("Processing cancelled.")
                data = dataset.ReadAsArray(xoff, yoff, width, height, band_list=list(band_chunk))
                if data.ndim == 2:
                    data = data[np.newaxis, :, :]
                for group in non_overlapping_passes(candidates):
                    labels = _rasterize_labels(gdal, ogr, osr, group, projection, _window_geotransform(gt, xoff, yoff), width, height, options.all_touched)
                    inside = labels > 0
                    if not inside.any():
                        continue
                    label_values = labels[inside].astype(np.int64, copy=False)
                    base_covered = np.bincount(label_values, minlength=zone_count + 1)
                    for local_index, band_index in enumerate(band_chunk):
                        values = data[local_index][inside]
                        band = dataset.GetRasterBand(int(band_index))
                        nodata = band.GetNoDataValue()
                        valid = np.isfinite(values)
                        if nodata is not None:
                            valid &= values != nodata
                        ids = label_values[valid]
                        vals = values[valid].astype(np.float64, copy=False)
                        covered[local_index] += base_covered
                        count[local_index] += np.bincount(ids, minlength=zone_count + 1)
                        sums[local_index] += np.bincount(ids, weights=vals, minlength=zone_count + 1)
                        sums_sq[local_index] += np.bincount(ids, weights=vals * vals, minlength=zone_count + 1)
                        np.minimum.at(minima[local_index], ids, vals)
                        np.maximum.at(maxima[local_index], ids, vals)
                completed += 1
                if progress:
                    progress(100.0 * completed / total_steps, f"Reading raster tiles ({completed}/{total_steps})")

            for local_index, band_index in enumerate(band_chunk):
                for internal_id in sorted(zone_by_id):
                    zone = zone_by_id[internal_id]
                    n = int(count[local_index, internal_id])
                    total = int(covered[local_index, internal_id])
                    area = float(n) if options.area_unit == "pixels" else float(n) * pixel_area_m2 * _area_factor(options.area_unit)
                    values = {
                        "count": n,
                        "nodata": total - n,
                        "min": "" if n == 0 else float(minima[local_index, internal_id]),
                        "max": "" if n == 0 else float(maxima[local_index, internal_id]),
                        "sum": "" if n == 0 else float(sums[local_index, internal_id]),
                        "mean": "" if n == 0 else float(sums[local_index, internal_id] / n),
                        "stddev": "" if n == 0 else float(math.sqrt(max(0.0, sums_sq[local_index, internal_id] / n - (sums[local_index, internal_id] / n) ** 2))),
                    }
                    writer.writerow([zone.feature_id, zone.name, band_index, band_names[band_index], area, options.area_unit] + [values[name] for name in options.statistics])
                    rows_written += 1
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.replace(temp_name, output)
    except Exception:
        temporary.close()
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    finally:
        dataset = None
    return {"output": str(output), "rows": rows_written, "tiles": len(windows)}
