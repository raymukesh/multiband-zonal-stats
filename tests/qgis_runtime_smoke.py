"""End-to-end smoke test intended for execution with QGIS's bundled Python."""

import csv
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


def main():
    plugin_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(plugin_root))
    from fast_multiband_zonal_stats.plugin import FastZonalStatsPlugin

    QgsApplication.setPrefixPath(
        "/Applications/QGIS-final-4_2_0.app/Contents/Resources/qgis", True
    )
    application = QgsApplication([], False)
    application.initQgis()
    plugin = FastZonalStatsPlugin(None)
    plugin.initGui()
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        raster, zones = create_inputs(folder)
        output = folder / "result.csv"
        results, successful = plugin.provider.algorithms()[0].run(
            {
                "RASTER": str(raster),
                "POLYGONS": str(zones),
                "ID_FIELD": "zone_id",
                "NAME_FIELD": "",
                "BANDS": "all",
                "STATS": [0, 1, 2, 3, 4, 5, 6],
                "AREA_UNIT": 0,
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
        assert {row["polygon_id"] for row in rows} == {"left", "overlap"}
        assert {row["band_name"] for row in rows} == {"values", "values x10"}
        print(f"QGIS runtime smoke test passed ({len(rows)} rows)")
    plugin.unload()
    application.exitQgis()


if __name__ == "__main__":
    main()
