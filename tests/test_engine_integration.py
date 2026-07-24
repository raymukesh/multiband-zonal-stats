"""Engine tests that exercise real GDAL rasterization and NumPy aggregation.

These need GDAL and NumPy but not QGIS, so they run under the QGIS Python
launcher without starting a QgsApplication.
"""

import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path

# Works whether this file is run directly or discovered as tests.<module>.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from plugin_modules import load  # noqa: E402

utils, engine = load()

try:
    import numpy as np
    from osgeo import gdal, ogr, osr
except ImportError:  # pragma: no cover - reported by the test runner
    np = gdal = ogr = osr = None


def build_raster(path, values_per_band, nodata=None, dtype=None, epsg=3857):
    """Write a north-up raster whose top-left corner is (0, height) with 1-unit pixels.

    ``epsg=None`` leaves the raster without a projection, which is how a file with
    a missing CRS reaches the engine.
    """
    first = values_per_band[0]
    height, width = first.shape
    dtype = dtype or gdal.GDT_Float32
    dataset = gdal.GetDriverByName("GTiff").Create(
        str(path), width, height, len(values_per_band), dtype
    )
    dataset.SetGeoTransform((0, 1, 0, height, 0, -1))
    if epsg is not None:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(epsg)
        dataset.SetProjection(srs.ExportToWkt())
    for index, values in enumerate(values_per_band, start=1):
        band = dataset.GetRasterBand(index)
        band.WriteArray(values)
        if nodata is not None:
            band.SetNoDataValue(nodata)
    dataset = None
    return str(path)


def projected_wkt(epsg=3857):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    return srs.ExportToWkt()


def zone(internal_id, wkt, name=""):
    geometry = ogr.CreateGeometryFromWkt(wkt)
    xmin, xmax, ymin, ymax = geometry.GetEnvelope()
    return engine.Zone(
        internal_id=internal_id,
        feature_id=internal_id,
        name=name,
        wkb=bytes(geometry.ExportToWkb()),
        bounds=(xmin, ymin, xmax, ymax),
    )


def run(raster_path, zones, statistics, crs_wkt=None, **option_overrides):
    options = engine.EngineOptions(
        bands=option_overrides.pop("bands", [1]),
        statistics=statistics,
        **option_overrides,
    )
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "out.csv"
        engine.run_zonal_statistics(raster_path, zones, str(output), options, crs_wkt=crs_wkt)
        with output.open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))


@unittest.skipIf(np is None, "GDAL and NumPy are required")
class EngineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.folder = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)

    def two_band_raster(self):
        values = np.arange(1, 17, dtype=np.float32).reshape(4, 4)
        return build_raster(self.folder / "two_band.tif", [values, values * 10])

    def overlapping_zones(self):
        return [
            zone(1, "POLYGON ((0 0, 2 0, 2 4, 0 4, 0 0))", "left"),
            zone(2, "POLYGON ((1 1, 4 1, 4 3, 1 3, 1 1))", "overlap"),
        ]

    def test_known_values_for_overlapping_zones(self):
        rows = run(
            self.two_band_raster(),
            self.overlapping_zones(),
            ["count", "nodata", "min", "max", "sum", "mean", "stddev"],
            bands=[1, 2],
        )
        by_key = {(r["polygon_name"], r["band_index"]): r for r in rows}
        self.assertEqual(len(rows), 4)

        left = by_key[("left", "1")]
        self.assertEqual(int(left["count"]), 8)
        self.assertEqual(int(left["nodata"]), 0)
        self.assertAlmostEqual(float(left["sum"]), 60.0)
        self.assertAlmostEqual(float(left["mean"]), 7.5)
        self.assertAlmostEqual(float(left["min"]), 1.0)
        self.assertAlmostEqual(float(left["max"]), 14.0)
        self.assertAlmostEqual(float(left["stddev"]), 4.5)

        # Shared pixels must be counted for the overlapping zone too.
        overlap = by_key[("overlap", "1")]
        self.assertEqual(int(overlap["count"]), 6)
        self.assertAlmostEqual(float(overlap["sum"]), 54.0)
        self.assertAlmostEqual(float(overlap["mean"]), 9.0)
        self.assertAlmostEqual(float(overlap["stddev"]), math.sqrt(28.0 / 6.0))

        # Band 2 is band 1 scaled by ten.
        self.assertAlmostEqual(float(by_key[("left", "2")]["sum"]), 600.0)
        self.assertAlmostEqual(float(by_key[("overlap", "2")]["stddev"]), 10 * math.sqrt(28.0 / 6.0))

    def test_band_chunking_does_not_change_results(self):
        values = [np.arange(1, 17, dtype=np.float32).reshape(4, 4) * k for k in range(1, 6)]
        raster = build_raster(self.folder / "five_band.tif", values)
        zones = self.overlapping_zones()
        statistics = ["count", "min", "max", "sum", "mean", "stddev"]
        baseline = run(raster, zones, statistics, bands=[1, 2, 3, 4, 5], band_chunk_size=1)
        for chunk in (2, 5, 64):
            other = run(raster, zones, statistics, bands=[1, 2, 3, 4, 5], band_chunk_size=chunk)
            self.assertEqual(baseline, other, f"band_chunk_size={chunk} changed the output")

    def test_tiling_does_not_change_results(self):
        values = np.arange(1, 65 * 65 + 1, dtype=np.float32).reshape(65, 65)
        raster = build_raster(self.folder / "big.tif", [values])
        zones = [
            zone(1, "POLYGON ((0 0, 40 0, 40 40, 0 40, 0 0))"),
            zone(2, "POLYGON ((20 20, 65 20, 65 65, 20 65, 20 20))"),
        ]
        statistics = ["count", "min", "max", "sum", "mean", "stddev"]
        baseline = run(raster, zones, statistics, tile_size=4096)
        for tile in (128, 256):
            self.assertEqual(baseline, run(raster, zones, statistics, tile_size=tile))

    def test_mask_cache_fallback_matches_cached_path(self):
        """A zero budget forces per-chunk rasterization; results must be identical."""
        values = [np.arange(1, 17, dtype=np.float32).reshape(4, 4) * k for k in (1, 2, 3)]
        raster = build_raster(self.folder / "cache.tif", values)
        zones = self.overlapping_zones()
        statistics = ["count", "min", "max", "sum", "mean", "stddev"]
        cached = run(raster, zones, statistics, bands=[1, 2, 3], band_chunk_size=1)
        original = engine.MASK_CACHE_BUDGET_BYTES
        engine.MASK_CACHE_BUDGET_BYTES = 0
        try:
            uncached = run(raster, zones, statistics, bands=[1, 2, 3], band_chunk_size=1)
        finally:
            engine.MASK_CACHE_BUDGET_BYTES = original
        self.assertEqual(cached, uncached)

    def test_nodata_is_excluded_and_counted(self):
        values = np.arange(1, 17, dtype=np.float32).reshape(4, 4)
        values[0, 0] = -9999.0
        values[0, 1] = -9999.0
        raster = build_raster(self.folder / "nodata.tif", [values], nodata=-9999.0)
        rows = run(
            raster,
            [zone(1, "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))")],
            ["count", "nodata", "sum", "mean"],
        )
        # 16 pixels, two of them nodata; remaining values are 3..16.
        self.assertEqual(int(rows[0]["count"]), 14)
        self.assertEqual(int(rows[0]["nodata"]), 2)
        self.assertAlmostEqual(float(rows[0]["sum"]), sum(range(3, 17)))

    def test_stddev_survives_a_large_offset(self):
        """Naive E[x^2]-E[x]^2 loses all precision at this magnitude."""
        offset = 1.0e8
        values = (offset + np.arange(16, dtype=np.float64)).reshape(4, 4)
        raster = build_raster(self.folder / "offset.tif", [values], dtype=gdal.GDT_Float64)
        rows = run(
            raster,
            [zone(1, "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))")],
            ["count", "mean", "stddev"],
        )
        expected = float(np.std(np.arange(16, dtype=np.float64)))
        self.assertEqual(int(rows[0]["count"]), 16)
        self.assertAlmostEqual(float(rows[0]["stddev"]), expected, places=6)

    def test_requesting_a_subset_of_statistics(self):
        rows = run(self.two_band_raster(), self.overlapping_zones(), ["mean"])
        self.assertEqual(list(rows[0])[-1:], ["mean"])
        self.assertAlmostEqual(float(rows[0]["mean"]), 7.5)

    def test_empty_zone_reports_blank_continuous_statistics(self):
        raster = self.two_band_raster()
        # Entirely outside the raster extent.
        rows = run(raster, [zone(1, "POLYGON ((90 90, 95 90, 95 95, 90 95, 90 90))")], ["count", "mean", "min"])
        self.assertEqual(int(rows[0]["count"]), 0)
        self.assertEqual(rows[0]["mean"], "")
        self.assertEqual(rows[0]["min"], "")

    def test_all_touched_includes_edge_pixels(self):
        raster = self.two_band_raster()
        # A sliver that contains no pixel centre at all.
        sliver = [zone(1, "POLYGON ((0.9 0.4, 1.1 0.4, 1.1 0.6, 0.9 0.6, 0.9 0.4))")]
        centres = run(raster, sliver, ["count"])
        touched = run(raster, sliver, ["count"], all_touched=True)
        self.assertEqual(int(centres[0]["count"]), 0)
        self.assertGreater(int(touched[0]["count"]), 0)

    def test_crs_override_does_not_change_pixel_statistics(self):
        """Assigning a CRS reinterprets coordinates only; values are untouched."""
        values = np.arange(1, 17, dtype=np.float32).reshape(4, 4)
        raster = build_raster(self.folder / "override.tif", [values], epsg=None)
        zones = [zone(1, "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))")]
        stats = ["count", "min", "max", "sum", "mean"]
        without = run(raster, zones, stats)
        with_override = run(raster, zones, stats, crs_wkt=projected_wkt(3857))
        for name in stats:
            self.assertEqual(without[0][name], with_override[0][name])

    def test_wide_format_layout_and_headers(self):
        raster = self.two_band_raster()
        zones = self.overlapping_zones()
        stats = ["count", "min", "max", "sum", "mean", "stddev"]
        rows = run(raster, zones, stats, bands=[1, 2], output_format="wide")
        # One row per polygon, not per polygon-band.
        self.assertEqual(len(rows), 2)
        header = list(rows[0])
        self.assertEqual(header[:3], ["fid", "polygon_id", "polygon_name"])
        # Each band contributes its statistics, prefixed with the band's own name
        # (here the band_001/band_002 fallback, since the raster has no names).
        self.assertIn("band_001_mean", header)
        self.assertIn("band_002_stddev", header)
        by_name = {r["polygon_name"]: r for r in rows}
        self.assertAlmostEqual(float(by_name["left"]["band_001_mean"]), 7.5)
        self.assertAlmostEqual(float(by_name["left"]["band_002_sum"]), 600.0)
        self.assertAlmostEqual(float(by_name["overlap"]["band_001_count"]), 6)

    def test_wide_and_long_carry_identical_numbers(self):
        values = [np.arange(1, 17, dtype=np.float32).reshape(4, 4) * k for k in (1, 2, 3)]
        raster = build_raster(self.folder / "compare.tif", values)
        zones = self.overlapping_zones()
        stats = ["count", "nodata", "min", "max", "sum", "mean", "stddev"]
        long_rows = run(raster, zones, stats, bands=[1, 2, 3], output_format="long")
        wide_rows = run(raster, zones, stats, bands=[1, 2, 3], output_format="wide")
        wide_by_id = {r["polygon_id"]: r for r in wide_rows}
        for lr in long_rows:
            wr = wide_by_id[lr["polygon_id"]]
            band = lr["band_index"]
            token = f"band_{int(band):03d}"
            for name in stats:
                self.assertEqual(lr[name], wr[f"{token}_{name}"], f"{lr['polygon_id']} {token} {name}")

    def test_band_names_and_affixes_reach_the_output(self):
        values = np.arange(1, 17, dtype=np.float32).reshape(4, 4)
        path = self.folder / "named.tif"
        dataset = gdal.GetDriverByName("GTiff").Create(str(path), 4, 4, 1, gdal.GDT_Float32)
        dataset.SetGeoTransform((0, 1, 0, 4, 0, -1))
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3857)
        dataset.SetProjection(srs.ExportToWkt())
        dataset.GetRasterBand(1).WriteArray(values)
        dataset.GetRasterBand(1).SetDescription("Surface Temp")
        dataset = None
        zones = [zone(1, "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))", "z")]
        # Long: band_name carries the raster's real name wrapped in the affixes.
        long_rows = run(str(path), zones, ["mean"], band_prefix="2020_", band_suffix="")
        self.assertEqual(long_rows[0]["band_name"], "2020_Surface Temp")
        # Wide: the same label sanitised into a spreadsheet-safe column header.
        wide_rows = run(str(path), zones, ["mean"], output_format="wide", band_prefix="2020_")
        self.assertIn("2020_surface_temp_mean", list(wide_rows[0]))

    def test_fid_is_a_unique_sequential_first_column(self):
        rows = run(self.two_band_raster(), self.overlapping_zones(), ["count"], bands=[1, 2])
        self.assertEqual(list(rows[0])[0], "fid")
        fids = [int(r["fid"]) for r in rows]
        self.assertEqual(fids, list(range(1, len(rows) + 1)))
        self.assertEqual(len(set(fids)), len(rows))

    def test_decimal_places_rounds_only_float_cells(self):
        rows = run(
            self.two_band_raster(), self.overlapping_zones(),
            ["count", "mean", "stddev"], bands=[1], decimal_places=2,
        )
        overlap = next(r for r in rows if r["polygon_name"] == "overlap")
        self.assertEqual(overlap["mean"], "9.00")    # 9.0 fixed to 2 decimals
        self.assertEqual(overlap["stddev"], "2.16")  # sqrt(28/6) = 2.16024...
        self.assertEqual(overlap["count"], "6")      # integer cell is untouched

    def test_negative_decimal_places_is_rejected(self):
        options = engine.EngineOptions(bands=[1], statistics=["mean"], decimal_places=-1)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "decimal_places"):
                engine.run_zonal_statistics(
                    self.two_band_raster(), self.overlapping_zones(), str(Path(folder) / "x.csv"), options
                )

    def test_extra_nodata_excludes_pixels(self):
        raster = self.two_band_raster()  # band 1 holds values 1..16
        full = [zone(1, "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))", "z")]
        rows = run(raster, full, ["count", "min", "sum"], bands=[1], extra_nodata=(1.0,))
        self.assertEqual(int(rows[0]["count"]), 15)  # the single value-1 pixel dropped
        self.assertAlmostEqual(float(rows[0]["min"]), 2.0)
        self.assertAlmostEqual(float(rows[0]["sum"]), sum(range(2, 17)))

    def test_raster_histogram_continuous(self):
        raster = self.two_band_raster()  # band 1 holds values 1..16
        hist = engine.raster_histogram(raster, 1, categorical=False, bins=4)
        self.assertFalse(hist["categorical"])
        self.assertEqual(hist["valid"], 16)
        self.assertEqual(sum(hist["counts"]), 16)
        self.assertEqual(len(hist["edges"]), 5)  # bins + 1
        self.assertAlmostEqual(hist["min"], 1.0)
        self.assertAlmostEqual(hist["max"], 16.0)
        self.assertAlmostEqual(hist["mean"], 8.5)

    def test_raster_histogram_rejects_bad_band(self):
        with self.assertRaisesRegex(ValueError, "range"):
            engine.raster_histogram(self.two_band_raster(), 9, categorical=False)

    def test_unknown_output_format_is_rejected(self):
        raster = self.two_band_raster()
        options = engine.EngineOptions(bands=[1], statistics=["count"], output_format="tall")
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "output format"):
                engine.run_zonal_statistics(
                    raster, self.overlapping_zones(), str(Path(folder) / "x.csv"), options
                )

    def test_no_partial_csv_is_left_after_cancellation(self):
        raster = self.two_band_raster()
        zones = self.overlapping_zones()
        options = engine.EngineOptions(bands=[1], statistics=["count"])
        output = self.folder / "cancelled.csv"
        with self.assertRaises(engine.CancelledError):
            engine.run_zonal_statistics(
                raster, zones, str(output), options, is_cancelled=lambda: True
            )
        self.assertFalse(output.exists())
        leftovers = [p.name for p in self.folder.iterdir() if p.suffix == ".csv"]
        self.assertEqual(leftovers, [], "a temporary CSV was left behind")


if __name__ == "__main__":
    unittest.main()
