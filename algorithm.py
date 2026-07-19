from __future__ import annotations

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterString,
    QgsGeometry,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from .engine import CancelledError, EngineOptions, Zone, run_zonal_statistics
from .utils import AREA_UNITS, STATISTICS, parse_band_selection, safe_output_path


class FastZonalStatisticsAlgorithm(QgsProcessingAlgorithm):
    RASTER = "RASTER"
    POLYGONS = "POLYGONS"
    ID_FIELD = "ID_FIELD"
    NAME_FIELD = "NAME_FIELD"
    BANDS = "BANDS"
    STATS = "STATS"
    AREA_UNIT = "AREA_UNIT"
    ALL_TOUCHED = "ALL_TOUCHED"
    TILE_SIZE = "TILE_SIZE"
    BAND_CHUNK = "BAND_CHUNK"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        """Translate text explicitly for QGIS 3 and QGIS 4 compatibility."""
        return QCoreApplication.translate("FastZonalStatisticsAlgorithm", text)

    def name(self):
        return "multiband_zonal_statistics"

    def displayName(self):
        return self.tr("Fast multiband zonal statistics")

    def group(self):
        return self.tr("Raster analysis")

    def groupId(self):
        return "raster_analysis"

    def shortHelpString(self):
        return self.tr(
            "Calculates tidy, long-format statistics for every polygon and selected "
            "raster band. The raster is read in bounded tiles for predictable memory use."
        )

    def createInstance(self):
        return FastZonalStatisticsAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.RASTER, self.tr("Multiband raster")))
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.POLYGONS,
                self.tr("Polygon zones"),
                [QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.ID_FIELD,
                self.tr("Polygon ID field"),
                parentLayerParameterName=self.POLYGONS,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.NAME_FIELD,
                self.tr("Polygon name field"),
                parentLayerParameterName=self.POLYGONS,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.BANDS,
                self.tr("Bands (all, 1,3,5, or 1-8)"),
                defaultValue="all",
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.STATS,
                self.tr("Statistics"),
                options=[name.title() for name in STATISTICS],
                defaultValue=[0, 1, 2, 3, 4, 5, 6],
                allowMultiple=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.AREA_UNIT,
                self.tr("Area unit"),
                options=["Pixels", "Square metres", "Square kilometres", "Hectares", "Acres"],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ALL_TOUCHED,
                self.tr("Include all pixels touched by polygons"),
                defaultValue=False,
            )
        )
        tile = QgsProcessingParameterNumber(
            self.TILE_SIZE,
            self.tr("Tile size (pixels)"),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=1024,
            minValue=128,
            maxValue=8192,
        )
        tile.setFlags(tile.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(tile)
        chunk = QgsProcessingParameterNumber(
            self.BAND_CHUNK,
            self.tr("Bands held in memory per pass"),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=8,
            minValue=1,
            maxValue=128,
        )
        chunk.setFlags(chunk.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(chunk)
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT,
                self.tr("Output CSV"),
                fileFilter=self.tr("CSV files (*.csv)"),
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        raster = self.parameterAsRasterLayer(parameters, self.RASTER, context)
        source = self.parameterAsSource(parameters, self.POLYGONS, context)
        if raster is None or source is None:
            raise QgsProcessingException(self.tr("Both raster and polygon inputs are required."))
        if QgsWkbTypes.geometryType(source.wkbType()) != QgsWkbTypes.PolygonGeometry:
            raise QgsProcessingException(self.tr("The zones layer must contain polygon geometries."))

        id_field = self.parameterAsString(parameters, self.ID_FIELD, context)
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context)
        selected_stats = [STATISTICS[i] for i in self.parameterAsEnums(parameters, self.STATS, context)]
        if not selected_stats:
            raise QgsProcessingException(self.tr("Select at least one statistic."))
        try:
            bands = parse_band_selection(
                self.parameterAsString(parameters, self.BANDS, context), raster.bandCount()
            )
            output = safe_output_path(self.parameterAsFileOutput(parameters, self.OUTPUT, context))
        except ValueError as error:
            raise QgsProcessingException(str(error)) from error

        transform = None
        if source.sourceCrs() != raster.crs():
            transform = QgsCoordinateTransform(source.sourceCrs(), raster.crs(), context.transformContext())
            feedback.pushInfo(self.tr("Transforming zone geometries to the raster CRS."))

        field_names = source.fields().names()
        request = QgsFeatureRequest()
        zones = []
        skipped = 0
        total = max(1, source.featureCount())
        for index, feature in enumerate(source.getFeatures(request), start=1):
            if feedback.isCanceled():
                raise QgsProcessingException(self.tr("Processing cancelled."))
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                skipped += 1
                continue
            geometry = QgsGeometry(geometry)
            if transform:
                geometry.transform(transform)
            bounds = geometry.boundingBox()
            zone_id = feature[id_field] if id_field and id_field in field_names else feature.id()
            zone_name = feature[name_field] if name_field and name_field in field_names else ""
            zones.append(
                Zone(
                    internal_id=len(zones) + 1,
                    feature_id=zone_id,
                    name="" if zone_name is None else str(zone_name),
                    wkb=bytes(geometry.asWkb()),
                    bounds=(bounds.xMinimum(), bounds.yMinimum(), bounds.xMaximum(), bounds.yMaximum()),
                )
            )
            if index % 500 == 0:
                feedback.setProgress(5.0 * index / total)
        if skipped:
            feedback.pushWarning(self.tr(f"Skipped {skipped} empty geometries."))

        raster_path = raster.source().split("|", 1)[0]
        options = EngineOptions(
            bands=bands,
            statistics=selected_stats,
            area_unit=AREA_UNITS[self.parameterAsEnum(parameters, self.AREA_UNIT, context)],
            all_touched=self.parameterAsBool(parameters, self.ALL_TOUCHED, context),
            tile_size=self.parameterAsInt(parameters, self.TILE_SIZE, context),
            band_chunk_size=self.parameterAsInt(parameters, self.BAND_CHUNK, context),
        )

        def report(percent, message):
            feedback.setProgress(5.0 + percent * 0.95)

        try:
            result = run_zonal_statistics(
                raster_path,
                zones,
                output,
                options,
                progress=report,
                is_cancelled=feedback.isCanceled,
            )
        except CancelledError as error:
            raise QgsProcessingException(str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise QgsProcessingException(str(error)) from error
        feedback.pushInfo(self.tr(f"Wrote {result['rows']:,} rows from {result['tiles']:,} raster tiles."))
        return {self.OUTPUT: output}
