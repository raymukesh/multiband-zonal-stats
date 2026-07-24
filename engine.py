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

from .utils import (
    ANALYSIS_MODES,
    CATEGORICAL_LAYOUTS,
    OUTPUT_FORMATS,
    safe_band_name,
    unique_header_names,
)


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
    all_touched: bool = False
    tile_size: int = 1024
    band_chunk_size: int = 8
    # Optional text wrapped around every band name in the output. They never
    # rename the raster's bands, only the labels/columns the CSV carries.
    band_prefix: str = ""
    band_suffix: str = ""
    # Round floating-point cells to this many decimals in the CSV. None keeps the
    # full-precision repr (the default); a non-negative int fixes the decimals.
    decimal_places: int | None = None
    # "continuous" reduces each zone to numeric statistics; "categorical" builds
    # a per-zone class histogram (majority, minority, variety, class breakdown).
    mode: str = "continuous"
    # Continuous: "long" (one row per zone+band) or "wide". Categorical:
    # "summary" (one row per zone+band) or "breakdown" (one row per zone+band+class).
    output_format: str = "long"


class CancelledError(RuntimeError):
    pass


# Rasterized zone masks are cached per window so a window is rasterized once
# rather than once per band chunk. Heavily overlapping zones need one mask per
# overlap pass, so the cache is capped; beyond the cap the engine falls back to
# recomputing per chunk, which is slower but keeps memory bounded.
MASK_CACHE_BUDGET_BYTES = 256 * 1024 * 1024


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


def _merge_variance(np, mean_row, m2_row, existing_count, tile_count, tile_sum, ids, vals, slots):
    """Fold one tile's values into running per-zone mean and sum-of-squares.

    Uses Chan's parallel variance merge rather than ``E[x^2] - E[x]^2``. The
    naive form loses most of its significant digits when values are large
    relative to their spread (elevations, unscaled radiance), which can even
    yield a negative variance.

    ``existing_count`` must be the count *before* this tile is added.
    """
    active = tile_count > 0
    if not active.any():
        return
    tile_mean = np.zeros(slots, dtype=np.float64)
    tile_mean[active] = tile_sum[active] / tile_count[active]
    deviations = vals - tile_mean[ids]
    tile_m2 = np.bincount(ids, weights=deviations * deviations, minlength=slots)

    total = existing_count + tile_count
    merge = active & (total > 0)
    delta = tile_mean[merge] - mean_row[merge]
    # float64 conversion keeps the count product clear of int64 overflow.
    weight_a = existing_count[merge].astype(np.float64)
    weight_b = tile_count[merge].astype(np.float64)
    combined = total[merge].astype(np.float64)
    m2_row[merge] += tile_m2[merge] + delta * delta * (weight_a * weight_b / combined)
    mean_row[merge] += delta * (weight_b / combined)


def _valid_mask(np, values, nodata):
    """Return the finite, non-nodata mask for one band's window values."""
    if np.issubdtype(values.dtype, np.floating):
        valid = np.isfinite(values)
    else:
        # Integer rasters cannot hold NaN or infinity.
        valid = np.ones(values.shape, dtype=bool)
    if nodata is not None:
        valid &= values != nodata
    return valid


def raster_histogram(
    raster_path: str,
    band: int,
    *,
    categorical: bool = False,
    bins: int = 50,
    max_cells: int = 2_000_000,
    max_classes: int = 50,
) -> dict:
    """Summarise the distribution of one raster band for a quick preview.

    The band is read decimated to at most ``max_cells`` pixels (GDAL resamples on
    read) so even large rasters return promptly; nodata and non-finite values are
    dropped. This ignores any polygons — it describes the raster itself.

    Returns a dict with ``categorical`` plus, for continuous bands,
    ``counts``/``edges`` and min/max/mean/stddev; for categorical bands,
    ``classes`` (a list of ``(class, count)`` sorted by class code), ``variety``
    and ``majority``. ``truncated`` is True when more classes exist than shown.
    """
    from osgeo import gdal
    import numpy as np

    dataset = gdal.Open(raster_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise ValueError(f"GDAL could not open raster: {raster_path}")
    if band < 1 or band > dataset.RasterCount:
        raise ValueError(f"Band {band} is outside the available range 1–{dataset.RasterCount}.")

    raster_band = dataset.GetRasterBand(band)
    width, height = dataset.RasterXSize, dataset.RasterYSize
    total = width * height
    # Decimate to a bounded number of cells; GDAL resamples during the read.
    if total > max_cells and total > 0:
        scale = (max_cells / total) ** 0.5
        buf_x = max(1, int(width * scale))
        buf_y = max(1, int(height * scale))
    else:
        buf_x, buf_y = width, height
    # Read nodata before the dataset is released; the band handle dies with it.
    nodata = raster_band.GetNoDataValue()
    values = raster_band.ReadAsArray(buf_xsize=buf_x, buf_ysize=buf_y)
    dataset = None
    if values is None:
        raise ValueError("GDAL could not read the raster band.")

    values = values.ravel()
    values = values[_valid_mask(np, values, nodata)]

    result = {
        "band": band,
        "categorical": categorical,
        "sampled": buf_x * buf_y,
        "total_pixels": total,
        "valid": int(values.size),
    }
    if values.size == 0:
        return result

    if categorical:
        if np.issubdtype(values.dtype, np.floating):
            codes = np.rint(values).astype(np.int64)
        else:
            codes = values.astype(np.int64, copy=False)
        unique, counts = np.unique(codes, return_counts=True)
        result["variety"] = int(unique.size)
        result["majority"] = int(unique[int(np.argmax(counts))])
        if unique.size > max_classes:
            # Keep the most common classes, then restore class-code order.
            top = np.argsort(counts)[::-1][:max_classes]
            keep = np.sort(top)
            unique, counts = unique[keep], counts[keep]
            result["truncated"] = True
        else:
            result["truncated"] = False
        result["classes"] = list(zip(unique.tolist(), counts.tolist()))
    else:
        vmin, vmax = float(values.min()), float(values.max())
        if vmax <= vmin:
            # A flat band would give zero-width bins; widen it a touch.
            vmax = vmin + 1.0
        counts, edges = np.histogram(values, bins=max(1, int(bins)), range=(vmin, vmax))
        result["counts"] = counts.tolist()
        result["edges"] = edges.tolist()
        result["min"] = float(values.min())
        result["max"] = float(values.max())
        result["mean"] = float(values.mean())
        result["stddev"] = float(values.std())
    return result


# A categorical raster with a runaway number of distinct values is almost always
# a continuous raster chosen by mistake; refuse it rather than build an enormous
# histogram.
MAX_CATEGORICAL_CLASSES = 65536


def _iter_masked_pixels(
    np, gdal, ogr, osr, dataset, gt, projection, windows, chunks,
    band_position, nodata_by_band, slots, need_covered, all_touched,
    is_cancelled, progress,
):
    """Yield ``(row, ids, values, base_covered)`` for each (window, group, band).

    ``ids`` are the internal zone ids and ``values`` the raw band values for the
    valid (inside a zone, not nodata) pixels. Both the continuous and categorical
    paths consume this, so the raster is read and the masks rasterized exactly
    once regardless of mode. ``base_covered`` is the per-group count of pixels
    inside each zone before nodata masking, or ``None`` when nodata is not needed.
    The per-window mask cache rasterizes a window once per overlap group rather
    than once per band chunk.
    """
    total_steps = max(1, len(chunks) * len(windows))
    completed = 0
    for xoff, yoff, width, height, candidates in windows:
        if is_cancelled and is_cancelled():
            raise CancelledError("Processing cancelled.")
        window_gt = _window_geotransform(gt, xoff, yoff)
        groups = non_overlapping_passes(candidates)
        # Each cached entry retains the bool "inside" mask (1 byte per pixel) plus
        # the int64 label values for the pixels it selects (8 bytes in the worst
        # case where the group covers the whole window).
        cached_bytes_per_group = width * height * 9
        cache_masks = len(groups) * cached_bytes_per_group <= MASK_CACHE_BUDGET_BYTES
        cached: list = []
        for chunk_position, band_chunk in enumerate(chunks):
            if is_cancelled and is_cancelled():
                raise CancelledError("Processing cancelled.")
            data = dataset.ReadAsArray(xoff, yoff, width, height, band_list=list(band_chunk))
            if data.ndim == 2:
                data = data[np.newaxis, :, :]
            for group_index, group in enumerate(groups):
                if cache_masks and chunk_position > 0:
                    prepared = cached[group_index]
                else:
                    labels = _rasterize_labels(
                        gdal, ogr, osr, group, projection, window_gt, width, height, all_touched
                    )
                    inside = labels > 0
                    if inside.any():
                        label_values = labels[inside].astype(np.int64, copy=False)
                        prepared = (
                            inside,
                            label_values,
                            np.bincount(label_values, minlength=slots) if need_covered else None,
                        )
                    else:
                        prepared = None
                    if cache_masks:
                        cached.append(prepared)
                if prepared is None:
                    continue
                inside, label_values, base_covered = prepared
                for local_index, band_index in enumerate(band_chunk):
                    row = band_position[band_index]
                    values = data[local_index][inside]
                    valid = _valid_mask(np, values, nodata_by_band[band_index])
                    if valid.all():
                        yield row, label_values, values, base_covered
                    else:
                        yield row, label_values[valid], values[valid], base_covered
            completed += 1
            if progress:
                progress(100.0 * completed / total_steps, f"Reading raster tiles ({completed}/{total_steps})")


class _RowWriter:
    """Wraps a ``csv.writer`` to give every data row a unique 1-based ``fid``.

    ``header`` prepends the literal ``fid`` column name; ``row`` prepends the next
    sequential id. The id is written in row order and is the stable identity of
    each output row, so the same inputs always yield the same fids.

    When ``decimal_places`` is set, floating-point cells are formatted to that many
    decimals; integers and text pass through untouched. Applying it here keeps
    every layout consistent from one place.
    """

    def __init__(self, writer, decimal_places=None):
        self._writer = writer
        self._places = decimal_places
        self.count = 0

    def _format(self, cell):
        if self._places is not None and isinstance(cell, float):
            return f"{cell:.{self._places}f}"
        return cell

    def header(self, columns):
        self._writer.writerow(["fid", *columns])

    def row(self, cells):
        self.count += 1
        self._writer.writerow([self.count, *(self._format(cell) for cell in cells)])


def _write_continuous(
    np, gdal, ogr, osr, rows, dataset, gt, projection, windows, chunks,
    options, band_position, band_names, band_headers, nodata_by_band, zone_by_id, slots,
    is_cancelled, progress,
):
    """Accumulate numeric statistics per (zone, band) and write the CSV."""
    # Only pay for the statistics that were actually requested.
    requested = set(options.statistics)
    need_nodata = "nodata" in requested
    need_min = "min" in requested
    need_max = "max" in requested
    need_stddev = "stddev" in requested

    # Accumulators span every band so the window loop can sit outside the band
    # loop; they are indexed by position in options.bands.
    shape = (len(options.bands), slots)
    count = np.zeros(shape, dtype=np.int64)
    covered = np.zeros(shape, dtype=np.int64) if need_nodata else None
    sums = np.zeros(shape, dtype=np.float64)
    minima = np.full(shape, np.inf, dtype=np.float64) if need_min else None
    maxima = np.full(shape, -np.inf, dtype=np.float64) if need_max else None
    running_mean = np.zeros(shape, dtype=np.float64) if need_stddev else None
    m2 = np.zeros(shape, dtype=np.float64) if need_stddev else None

    for row, ids, raw_vals, base_covered in _iter_masked_pixels(
        np, gdal, ogr, osr, dataset, gt, projection, windows, chunks,
        band_position, nodata_by_band, slots, need_nodata, options.all_touched,
        is_cancelled, progress,
    ):
        if need_nodata:
            covered[row] += base_covered
        if ids.size == 0:
            continue
        vals = raw_vals.astype(np.float64, copy=False)
        tile_count = np.bincount(ids, minlength=slots)
        tile_sum = np.bincount(ids, weights=vals, minlength=slots)
        if need_stddev:
            _merge_variance(
                np, running_mean[row], m2[row], count[row],
                tile_count, tile_sum, ids, vals, slots,
            )
        count[row] += tile_count
        sums[row] += tile_sum
        if need_min:
            np.minimum.at(minima[row], ids, vals)
        if need_max:
            np.maximum.at(maxima[row], ids, vals)

    ordered_ids = sorted(zone_by_id)

    def stat_cells(row, internal_id, n):
        """Ordered statistic values for one (band, zone); "" where undefined.

        Counts are always meaningful; the continuous statistics are blank for
        zones with no valid pixels. Both output formats share this so their
        numbers can never drift apart.
        """
        cells = []
        for name in options.statistics:
            if name == "count":
                cells.append(n)
            elif name == "nodata":
                cells.append(int(covered[row, internal_id]) - n)
            elif n == 0:
                cells.append("")
            elif name == "min":
                cells.append(float(minima[row, internal_id]))
            elif name == "max":
                cells.append(float(maxima[row, internal_id]))
            elif name == "sum":
                cells.append(float(sums[row, internal_id]))
            elif name == "mean":
                cells.append(float(sums[row, internal_id] / n))
            elif name == "stddev":
                cells.append(float(math.sqrt(max(0.0, m2[row, internal_id] / n))))
        return cells

    if options.output_format == "wide":
        # One row per zone; each band contributes its statistics, prefixed with
        # the band's (sanitised, unique) name — e.g. ndvi_mean, ndvi_sum.
        header = ["polygon_id", "polygon_name"]
        for band_index in options.bands:
            header.extend(f"{band_headers[band_index]}_{name}" for name in options.statistics)
        rows.header(header)
        for internal_id in ordered_ids:
            zone = zone_by_id[internal_id]
            cells = [zone.feature_id, zone.name]
            for band_index in options.bands:
                row = band_position[band_index]
                n = int(count[row, internal_id])
                cells.extend(stat_cells(row, internal_id, n))
            rows.row(cells)
    else:
        # Long format: one row per (zone, band).
        header = ["polygon_id", "polygon_name", "band_index", "band_name"]
        header.extend(options.statistics)
        rows.header(header)
        for band_index in options.bands:
            row = band_position[band_index]
            for internal_id in ordered_ids:
                zone = zone_by_id[internal_id]
                n = int(count[row, internal_id])
                rows.row(
                    [zone.feature_id, zone.name, band_index, band_names[band_index]]
                    + stat_cells(row, internal_id, n)
                )
    return rows.count


def _pick_extreme(counts_map, most):
    """Class with the most (``most=True``) or fewest pixels; ties → lowest class."""
    best_class = None
    best_count = None
    for class_code in sorted(counts_map):
        tally = counts_map[class_code]
        if best_count is None or (tally > best_count if most else tally < best_count):
            best_class, best_count = class_code, tally
    return best_class


def _write_categorical(
    np, gdal, ogr, osr, rows, dataset, gt, projection, windows, chunks,
    options, band_position, band_names, band_headers, nodata_by_band, zone_by_id, slots,
    is_cancelled, progress,
):
    """Accumulate a per-(zone, band) class histogram and write the CSV.

    Class codes are the raster values cast to integers (floating rasters are
    rounded to the nearest integer). The histogram is a sparse dict, so an
    unbounded class range costs memory only for classes that actually occur.
    """
    from collections import defaultdict

    summary = options.output_format == "summary"
    breakdown = options.output_format == "breakdown"
    need_nodata = summary and "nodata" in set(options.statistics)

    # class_counts[(row, internal_id)][class_code] = pixel count.
    class_counts: dict = defaultdict(lambda: defaultdict(int))
    covered = np.zeros((len(options.bands), slots), dtype=np.int64) if need_nodata else None
    distinct_classes: set = set()

    for row, ids, raw_vals, base_covered in _iter_masked_pixels(
        np, gdal, ogr, osr, dataset, gt, projection, windows, chunks,
        band_position, nodata_by_band, slots, need_nodata, options.all_touched,
        is_cancelled, progress,
    ):
        if need_nodata:
            covered[row] += base_covered
        if ids.size == 0:
            continue
        if np.issubdtype(raw_vals.dtype, np.floating):
            classes = np.rint(raw_vals).astype(np.int64)
        else:
            classes = raw_vals.astype(np.int64, copy=False)
        pairs = np.stack((ids, classes), axis=1)
        unique_pairs, counts = np.unique(pairs, axis=0, return_counts=True)
        for (zone_id, class_code), tally in zip(unique_pairs.tolist(), counts.tolist()):
            class_counts[(row, zone_id)][class_code] += int(tally)
            distinct_classes.add(class_code)
        if len(distinct_classes) > MAX_CATEGORICAL_CLASSES:
            raise ValueError(
                f"The raster has more than {MAX_CATEGORICAL_CLASSES} distinct values, "
                "which looks continuous rather than categorical. Use the continuous "
                "analysis, or reclassify the raster into discrete classes first."
            )

    ordered_ids = sorted(zone_by_id)
    if breakdown:
        # One row per (zone, band, class): the full class composition.
        rows.header(
            ["polygon_id", "polygon_name", "band_index", "band_name", "class", "pixel_count", "fraction"]
        )
        for band_index in options.bands:
            row = band_position[band_index]
            for internal_id in ordered_ids:
                zone = zone_by_id[internal_id]
                counts_map = class_counts.get((row, internal_id))
                base = [zone.feature_id, zone.name, band_index, band_names[band_index]]
                if not counts_map:
                    # Keep every zone visible even when it covers no valid pixels.
                    rows.row(base + ["", 0, ""])
                    continue
                valid_total = sum(counts_map.values())
                for class_code in sorted(counts_map):
                    tally = counts_map[class_code]
                    rows.row(base + [class_code, tally, tally / valid_total])
    elif options.output_format == "counts":
        # One row per (zone, band); one pixel-count column per class, using the
        # union of classes seen anywhere so every row shares the same columns.
        all_classes = sorted(distinct_classes)
        rows.header(
            ["polygon_id", "polygon_name", "band_index", "band_name"]
            + [f"class_{class_code}" for class_code in all_classes]
        )
        for band_index in options.bands:
            row = band_position[band_index]
            for internal_id in ordered_ids:
                zone = zone_by_id[internal_id]
                counts_map = class_counts.get((row, internal_id), {})
                rows.row(
                    [zone.feature_id, zone.name, band_index, band_names[band_index]]
                    + [counts_map.get(class_code, 0) for class_code in all_classes]
                )
    else:
        # One row per (zone, band): summary statistics of the class histogram.
        header = ["polygon_id", "polygon_name", "band_index", "band_name"]
        header.extend(options.statistics)
        rows.header(header)
        for band_index in options.bands:
            row = band_position[band_index]
            for internal_id in ordered_ids:
                zone = zone_by_id[internal_id]
                counts_map = class_counts.get((row, internal_id), {})
                valid_total = sum(counts_map.values())
                cells = [zone.feature_id, zone.name, band_index, band_names[band_index]]
                for name in options.statistics:
                    if name == "count":
                        cells.append(valid_total)
                    elif name == "nodata":
                        cells.append(int(covered[row, internal_id]) - valid_total)
                    elif valid_total == 0:
                        cells.append("")
                    elif name == "variety":
                        cells.append(len(counts_map))
                    elif name == "majority":
                        cells.append(_pick_extreme(counts_map, most=True))
                    elif name == "minority":
                        cells.append(_pick_extreme(counts_map, most=False))
                rows.row(cells)
    return rows.count


def run_zonal_statistics(
    raster_path: str,
    zones: Sequence[Zone],
    output_path: str,
    options: EngineOptions,
    progress: Callable[[float, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    crs_wkt: str | None = None,
) -> dict:
    """Calculate statistics and atomically write a long-format CSV.

    ``crs_wkt`` reinterprets the raster's coordinate system without touching the
    pixel grid: the geotransform is left as-is, so this only assigns a CRS rather
    than resampling. Zone WKB must already be expressed in the same CRS. It is
    used when the raster's own CRS is missing or wrong so the zone masks align.
    """
    from osgeo import gdal, ogr, osr
    import numpy as np

    if options.mode not in ANALYSIS_MODES:
        raise ValueError(f"Unsupported analysis mode: {options.mode}")
    valid_formats = OUTPUT_FORMATS if options.mode == "continuous" else CATEGORICAL_LAYOUTS
    if options.output_format not in valid_formats:
        raise ValueError(f"Unsupported output format: {options.output_format}")
    if options.decimal_places is not None and options.decimal_places < 0:
        raise ValueError("decimal_places must be zero or greater, or None for full precision.")
    dataset = gdal.Open(raster_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise ValueError(f"GDAL could not open raster: {raster_path}")
    if not zones:
        raise ValueError("The polygon layer contains no usable features.")

    gt = dataset.GetGeoTransform()
    # An explicit override wins over whatever CRS the file declares.
    projection = crs_wkt if crs_wkt else dataset.GetProjection()

    # The raster's own band names, wrapped in the optional prefix/suffix. These
    # are the readable labels that appear in the ``band_name`` column. For wide
    # column headers they are additionally sanitised and de-duplicated so two
    # bands can never collide into one column.
    band_names = {
        index: f"{options.band_prefix}"
        f"{safe_band_name(dataset.GetRasterBand(index).GetDescription(), index)}"
        f"{options.band_suffix}"
        for index in options.bands
    }
    header_tokens = unique_header_names([band_names[index] for index in options.bands])
    band_headers = {index: header_tokens[position] for position, index in enumerate(options.bands)}
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
        chunk_size = max(1, options.band_chunk_size)
        chunks = [options.bands[i : i + chunk_size] for i in range(0, len(options.bands), chunk_size)]

        slots = zone_count + 1
        band_position = {band: index for index, band in enumerate(options.bands)}
        nodata_by_band = {
            band: dataset.GetRasterBand(int(band)).GetNoDataValue() for band in options.bands
        }
        writer_fn = _write_categorical if options.mode == "categorical" else _write_continuous
        rows_written = writer_fn(
            np, gdal, ogr, osr, _RowWriter(writer, options.decimal_places), dataset, gt, projection, windows, chunks,
            options, band_position, band_names, band_headers, nodata_by_band, zone_by_id, slots,
            is_cancelled, progress,
        )
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
