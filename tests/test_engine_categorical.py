"""Categorical (discrete-class) engine tests.

Like the continuous integration tests, these need GDAL and NumPy but not QGIS,
so they run under the QGIS Python launcher without a QgsApplication.
"""

import csv
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


def build_raster(path, values, nodata=None, dtype=None, epsg=3857):
    """Write a single-band north-up raster with 1-unit pixels from ``values``."""
    height, width = values.shape
    dtype = dtype or gdal.GDT_Int32
    dataset = gdal.GetDriverByName("GTiff").Create(str(path), width, height, 1, dtype)
    dataset.SetGeoTransform((0, 1, 0, height, 0, -1))
    if epsg is not None:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(epsg)
        dataset.SetProjection(srs.ExportToWkt())
    band = dataset.GetRasterBand(1)
    band.WriteArray(values)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    dataset = None
    return str(path)


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


def run(raster_path, zones, statistics, **option_overrides):
    option_overrides.setdefault("mode", "categorical")
    options = engine.EngineOptions(
        bands=option_overrides.pop("bands", [1]),
        statistics=statistics,
        **option_overrides,
    )
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "out.csv"
        engine.run_zonal_statistics(raster_path, zones, str(output), options)
        with output.open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))


@unittest.skipIf(np is None, "GDAL and NumPy are required")
class CategoricalEngineTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.folder = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)

    def classes_raster(self, nodata=None):
        # Class counts over the 4x4 grid: 1->4, 2->4, 3->5, 4->3.
        values = np.array(
            [[1, 1, 2, 2], [1, 1, 2, 2], [3, 3, 3, 4], [3, 3, 4, 4]], dtype=np.int32
        )
        return build_raster(self.folder / "classes.tif", values, nodata=nodata)

    def full_zone(self):
        return [zone(1, "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))", "all")]

    def test_summary_majority_minority_variety(self):
        rows = run(
            self.classes_raster(),
            self.full_zone(),
            ["majority", "minority", "variety", "count"],
            output_format="summary",
        )
        row = rows[0]
        self.assertEqual(int(row["count"]), 16)
        self.assertEqual(int(row["variety"]), 4)
        self.assertEqual(int(row["majority"]), 3)  # class 3 has the most pixels
        self.assertEqual(int(row["minority"]), 4)  # class 4 has the fewest

    def test_majority_tie_breaks_on_lowest_class(self):
        # Classes 1 and 2 both cover 8 pixels; 1 must win as the lower code.
        values = np.array([[1, 1, 2, 2]] * 4, dtype=np.int32)
        raster = build_raster(self.folder / "tie.tif", values)
        rows = run(raster, self.full_zone(), ["majority", "minority"], output_format="summary")
        self.assertEqual(int(rows[0]["majority"]), 1)
        self.assertEqual(int(rows[0]["minority"]), 1)

    def test_class_breakdown_counts_and_fractions(self):
        rows = run(self.classes_raster(), self.full_zone(), [], output_format="breakdown")
        by_class = {int(r["class"]): r for r in rows}
        self.assertEqual(set(by_class), {1, 2, 3, 4})
        self.assertEqual(int(by_class[3]["pixel_count"]), 5)
        self.assertAlmostEqual(float(by_class[3]["fraction"]), 5 / 16)
        self.assertAlmostEqual(sum(float(r["fraction"]) for r in rows), 1.0)

    def test_class_counts_wide_layout(self):
        rows = run(self.classes_raster(), self.full_zone(), [], output_format="counts")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        header = list(row)
        self.assertEqual(header[:5], ["fid", "polygon_id", "polygon_name", "band_index", "band_name"])
        self.assertEqual(header[5:], ["class_1", "class_2", "class_3", "class_4"])
        self.assertEqual(int(row["class_1"]), 4)
        self.assertEqual(int(row["class_2"]), 4)
        self.assertEqual(int(row["class_3"]), 5)
        self.assertEqual(int(row["class_4"]), 3)

    def test_class_counts_columns_are_the_union_across_zones(self):
        # 'left' spans columns 0-1 (classes 1 and 3); 'right' spans 2-3 (2, 3, 4).
        zones = [
            zone(1, "POLYGON ((0 0, 2 0, 2 4, 0 4, 0 0))", "left"),
            zone(2, "POLYGON ((2 0, 4 0, 4 4, 2 4, 2 0))", "right"),
        ]
        rows = run(self.classes_raster(), zones, [], output_format="counts")
        by_name = {r["polygon_name"]: r for r in rows}
        # Every row shares the same class_1..class_4 columns; absent classes are 0.
        self.assertEqual(int(by_name["left"]["class_1"]), 4)
        self.assertEqual(int(by_name["left"]["class_2"]), 0)
        self.assertEqual(int(by_name["left"]["class_3"]), 4)
        self.assertEqual(int(by_name["right"]["class_2"]), 4)
        self.assertEqual(int(by_name["right"]["class_4"]), 3)

    def test_decimal_places_formats_the_fraction(self):
        zones = [zone(1, "POLYGON ((2 0, 4 0, 4 4, 2 4, 2 0))", "right")]  # classes 2,3,4
        rows = run(self.classes_raster(), zones, [], output_format="breakdown", decimal_places=3)
        by_class = {int(r["class"]): r for r in rows}
        self.assertEqual(by_class[2]["fraction"], "0.500")  # 4/8
        self.assertEqual(by_class[3]["fraction"], "0.125")  # 1/8
        self.assertEqual(by_class[2]["pixel_count"], "4")   # integer stays unformatted

    def test_fid_is_sequential_across_breakdown_rows(self):
        rows = run(self.classes_raster(), self.full_zone(), [], output_format="breakdown")
        self.assertEqual(list(rows[0])[0], "fid")
        fids = [int(r["fid"]) for r in rows]
        self.assertEqual(fids, list(range(1, len(rows) + 1)))

    def test_nodata_is_excluded_and_counted(self):
        raster = self.classes_raster(nodata=4)  # the three class-4 pixels are nodata
        rows = run(
            raster,
            self.full_zone(),
            ["count", "nodata", "variety", "majority"],
            output_format="summary",
        )
        row = rows[0]
        self.assertEqual(int(row["count"]), 13)
        self.assertEqual(int(row["nodata"]), 3)
        self.assertEqual(int(row["variety"]), 3)  # only classes 1, 2, 3 remain
        self.assertEqual(int(row["majority"]), 3)

    def test_empty_zone_reports_blank_summary(self):
        rows = run(
            self.classes_raster(),
            [zone(1, "POLYGON ((90 90, 95 90, 95 95, 90 95, 90 90))")],
            ["count", "majority", "variety"],
            output_format="summary",
        )
        row = rows[0]
        self.assertEqual(int(row["count"]), 0)
        self.assertEqual(row["majority"], "")
        self.assertEqual(row["variety"], "")

    def test_empty_zone_still_appears_in_breakdown(self):
        rows = run(
            self.classes_raster(),
            [zone(1, "POLYGON ((90 90, 95 90, 95 95, 90 95, 90 90))")],
            [],
            output_format="breakdown",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["class"], "")
        self.assertEqual(int(rows[0]["pixel_count"]), 0)

    def test_float_values_are_rounded_to_classes(self):
        values = np.array(
            [[1.0, 1.0, 2.0, 2.0], [1.0, 1.0, 2.0, 2.0], [3.0, 3.0, 3.0, 3.0], [3.0, 3.0, 3.0, 3.0]],
            dtype=np.float32,
        )
        raster = build_raster(self.folder / "floatclasses.tif", values, dtype=gdal.GDT_Float32)
        rows = run(raster, self.full_zone(), ["variety", "majority"], output_format="summary")
        self.assertEqual(int(rows[0]["variety"]), 3)
        self.assertEqual(int(rows[0]["majority"]), 3)

    def test_runaway_class_count_is_rejected(self):
        """A continuous raster in categorical mode is refused once the cap is hit."""
        values = (np.arange(16, dtype=np.float32) * 0.5).reshape(4, 4)
        raster = build_raster(self.folder / "continuous.tif", values, dtype=gdal.GDT_Float32)
        original = engine.MAX_CATEGORICAL_CLASSES
        engine.MAX_CATEGORICAL_CLASSES = 4
        try:
            with self.assertRaisesRegex(ValueError, "distinct"):
                run(raster, self.full_zone(), ["variety"], output_format="summary")
        finally:
            engine.MAX_CATEGORICAL_CLASSES = original

    def test_unknown_categorical_layout_is_rejected(self):
        options = engine.EngineOptions(
            bands=[1], statistics=["variety"], mode="categorical", output_format="wide"
        )
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "output format"):
                engine.run_zonal_statistics(
                    self.classes_raster(), self.full_zone(), str(Path(folder) / "x.csv"), options
                )


if __name__ == "__main__":
    unittest.main()
