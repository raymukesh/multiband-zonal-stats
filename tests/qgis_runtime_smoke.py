"""End-to-end smoke test intended for execution with QGIS's bundled Python.

Run it with the QGIS Python launcher for your platform, for example::

    "C:\\Program Files\\QGIS 3.40.5\\bin\\python-qgis-ltr.bat" tests/qgis_runtime_smoke.py
    /Applications/QGIS.app/Contents/MacOS/bin/python3 tests/qgis_runtime_smoke.py

The QGIS prefix is taken from the QGIS_PREFIX_PATH environment variable that the
launchers already export, so no path is hard-coded here.
"""

import csv
import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import QgsApplication, QgsProcessingContext, QgsProcessingFeedback


def create_inputs(folder):
    raster_path = folder / "two_band.tif"
    raster = gdal.GetDriverByName("GTiff").Create(str(raster_path), 4, 4, 2, gdal.GDT_Float32)
    raster.SetGeoTransform((0, 1, 0, 4, 0, -1))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    raster.SetProjection(srs.ExportToWkt())
    values = np.arange(1, 17, dtype=np.float32).reshape(4, 4)
    raster.GetRasterBand(1).WriteArray(values)
    raster.GetRasterBand(1).SetDescription("values")
    raster.GetRasterBand(2).WriteArray(values * 10)
    raster.GetRasterBand(2).SetDescription("values x10")
    raster = None

    vector_path = folder / "zones.geojson"
    vector = ogr.GetDriverByName("GeoJSON").CreateDataSource(str(vector_path))
    layer = vector.CreateLayer("zones", srs=srs, geom_type=ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("zone_id", ogr.OFTString))
    for zone_id, wkt in (
        ("left", "POLYGON ((0 0, 2 0, 2 4, 0 4, 0 0))"),
        ("overlap", "POLYGON ((1 1, 4 1, 4 3, 1 3, 1 1))"),
    ):
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetField("zone_id", zone_id)
        feature.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        layer.CreateFeature(feature)
    vector = None
    return raster_path, vector_path


def create_categorical_raster(folder):
    """A single-band class raster: class counts 1->4, 2->4, 3->5, 4->3."""
    raster_path = folder / "classes.tif"
    raster = gdal.GetDriverByName("GTiff").Create(str(raster_path), 4, 4, 1, gdal.GDT_Int32)
    raster.SetGeoTransform((0, 1, 0, 4, 0, -1))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    raster.SetProjection(srs.ExportToWkt())
    values = np.array([[1, 1, 2, 2], [1, 1, 2, 2], [3, 3, 3, 4], [3, 3, 4, 4]], dtype=np.int32)
    raster.GetRasterBand(1).WriteArray(values)
    raster.GetRasterBand(1).SetDescription("landcover")
    raster = None
    return raster_path


def algorithm_named(plugin, short_name):
    for algorithm in plugin.provider.algorithms():
        if algorithm.name() == short_name:
            return algorithm
    raise AssertionError(f"algorithm '{short_name}' is not registered")


# The two zones overlap, so these values only come out right when overlapping
# zones are rasterized in separate passes and shared pixels counted for both.
# Band 1 pixel centres: left -> 1,2,5,6,9,10,13,14; overlap -> 6,7,8,10,11,12.
EXPECTED = {
    ("left", "1"): {"count": 8, "min": 1.0, "max": 14.0, "sum": 60.0, "mean": 7.5, "stddev": 4.5},
    ("overlap", "1"): {"count": 6, "min": 6.0, "max": 12.0, "sum": 54.0, "mean": 9.0, "stddev": 2.160246899469287},
    ("left", "2"): {"count": 8, "min": 10.0, "max": 140.0, "sum": 600.0, "mean": 75.0, "stddev": 45.0},
    ("overlap", "2"): {"count": 6, "min": 60.0, "max": 120.0, "sum": 540.0, "mean": 90.0, "stddev": 21.60246899469287},
}


def main():
    plugin_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(plugin_root.parent))
    package = __import__(plugin_root.name, fromlist=["plugin"])
    FastZonalStatsPlugin = package.plugin.FastZonalStatsPlugin

    prefix = os.environ.get("QGIS_PREFIX_PATH")
    if prefix:
        QgsApplication.setPrefixPath(prefix, True)
    application = QgsApplication([], False)
    application.initQgis()
    plugin = FastZonalStatsPlugin(None)
    plugin.initGui()

    # Importing the dialog resolves the Qt and QGIS GUI enums in compat.py, which
    # is where a QGIS 4 / Qt6 incompatibility would surface first.
    importlib.import_module(f"{plugin_root.name}.dialog")
    print("dialog module imported; Qt and QGIS GUI enums resolved")

    failures = []
    # Not TemporaryDirectory: QGIS keeps the vector source open on Windows, so
    # cleanup has to tolerate locked files.
    temporary = tempfile.mkdtemp()
    try:
        folder = Path(temporary)
        raster, zones = create_inputs(folder)
        output = folder / "result.csv"
        results, successful = algorithm_named(plugin, "multiband_zonal_statistics").run(
            {
                "RASTER": str(raster),
                "POLYGONS": str(zones),
                "ID_FIELD": "zone_id",
                "NAME_FIELD": "",
                "BANDS": "all",
                "STATS": [0, 1, 2, 3, 4, 5, 6],
                "OUTPUT_FORMAT": 0,
                "ALL_TOUCHED": False,
                "TILE_SIZE": 128,
                "BAND_CHUNK": 2,
                "OUTPUT": str(output),
            },
            QgsProcessingContext(),
            QgsProcessingFeedback(),
        )
        assert successful, results
        rows = list(csv.DictReader(output.open(encoding="utf-8")))
        assert len(rows) == 4, rows
        assert {row["band_name"] for row in rows} == {"values", "values x10"}
        for row in rows:
            key = (row["polygon_id"], row["band_index"])
            expected = EXPECTED.get(key)
            if expected is None:
                failures.append(f"unexpected row {key}")
                continue
            for name, want in expected.items():
                got = float(row[name])
                if abs(got - want) > 1e-6:
                    failures.append(f"{key} {name}: expected {want}, got {got}")
            if int(row["nodata"]) != 0:
                failures.append(f"{key} nodata: expected 0, got {row['nodata']}")

        # Categorical algorithm: majority/minority/variety over the whole raster.
        cat_raster = create_categorical_raster(folder)
        cat_output = folder / "categorical.csv"
        cat_results, cat_ok = algorithm_named(plugin, "categorical_zonal_statistics").run(
            {
                "RASTER": str(cat_raster),
                "POLYGONS": str(zones),
                "ID_FIELD": "zone_id",
                "NAME_FIELD": "",
                "BANDS": "all",
                "STATS": [0, 1, 2, 3, 4],
                "OUTPUT_FORMAT": 0,
                "ALL_TOUCHED": False,
                "TILE_SIZE": 128,
                "BAND_CHUNK": 1,
                "OUTPUT": str(cat_output),
            },
            QgsProcessingContext(),
            QgsProcessingFeedback(),
        )
        assert cat_ok, cat_results
        cat_rows = {r["polygon_id"]: r for r in csv.DictReader(cat_output.open(encoding="utf-8"))}
        # 'left' spans columns 0-1 (classes 1 and 3 only): 1->4, 3->4, tie -> 1.
        if int(cat_rows["left"]["variety"]) != 2:
            failures.append(f"categorical left variety: expected 2, got {cat_rows['left']['variety']}")
        if int(cat_rows["left"]["majority"]) != 1:
            failures.append(f"categorical left majority: expected 1, got {cat_rows['left']['majority']}")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    plugin.unload()
    application.exitQgis()
    if failures:
        print("QGIS runtime smoke test FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("QGIS runtime smoke test passed (4 rows, values verified)")


if __name__ == "__main__":
    main()
