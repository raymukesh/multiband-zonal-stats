from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsGeometry,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from .compat import (
    GEOMETRY_TYPE_POLYGON,
    PARAMETER_FLAG_ADVANCED,
    PARAMETER_NUMBER_INTEGER,
    SOURCE_TYPE_VECTOR_POLYGON,
    is_null,
)
from .engine import CancelledError, EngineOptions, Zone, run_zonal_statistics
from .utils import (
    CATEGORICAL_LAYOUTS,
    CATEGORICAL_STATISTICS,
    OUTPUT_FORMATS,
    STATISTICS,
    parse_band_selection,
    safe_output_path,
)


class _BaseZonalStatisticsAlgorithm(QgsProcessingAlgorithm):
    """Shared inputs, CRS handling and zone reading for both analysis modes.

    Subclasses add the mode-specific statistic and layout parameters and build
    the engine ``EngineOptions``; everything else — reading polygons, resolving
    each input's CRS, transforming zones and running the engine — is common.
    """

    RASTER = "RASTER"
    RASTER_CRS = "RASTER_CRS"
    POLYGONS = "POLYGONS"
    ZONES_CRS = "ZONES_CRS"
    ID_FIELD = "ID_FIELD"
    NAME_FIELD = "NAME_FIELD"
    BANDS = "BANDS"
    BAND_PREFIX = "BAND_PREFIX"
    BAND_SUFFIX = "BAND_SUFFIX"
    STATS = "STATS"
    OUTPUT_FORMAT = "OUTPUT_FORMAT"
    ALL_TOUCHED = "ALL_TOUCHED"
    TILE_SIZE = "TILE_SIZE"
    BAND_CHUNK = "BAND_CHUNK"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        """Translate text explicitly for QGIS 3 and QGIS 4 compatibility."""
        return QCoreApplication.translate("FastZonalStatisticsAlgorithm", text)

    def group(self):
        return self.tr("Raster analysis")

    def groupId(self):
        return "raster_analysis"

    # --- parameter wiring ------------------------------------------------
    def initAlgorithm(self, config=None):
        self._addSharedInputParameters()
        self._addAnalysisParameters()
        self._addSharedExecutionParameters()

    def _addSharedInputParameters(self):
        self.addParameter(QgsProcessingParameterRasterLayer(self.RASTER, self.tr("Multiband raster")))
        # Optional CRS overrides reinterpret a layer whose CRS is missing or
        # wrong. Left unset, each input keeps its own declared CRS. The raster's
        # (effective) CRS is always the analysis CRS; only the zones are ever
        # transformed, and the raster is never resampled.
        self.addParameter(
            QgsProcessingParameterCrs(
                self.RASTER_CRS,
                self.tr("Raster CRS override (optional)"),
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.POLYGONS,
                self.tr("Polygon zones"),
                [SOURCE_TYPE_VECTOR_POLYGON],
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.ZONES_CRS,
                self.tr("Zones CRS override (optional)"),
                optional=True,
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
        # Optional text wrapped around every band name in the output. The
        # raster's bands are never renamed; only the CSV labels change.
        prefix = QgsProcessingParameterString(
            self.BAND_PREFIX,
            self.tr("Band name prefix (optional)"),
            defaultValue="",
            optional=True,
        )
        prefix.setFlags(prefix.flags() | PARAMETER_FLAG_ADVANCED)
        self.addParameter(prefix)
        suffix = QgsProcessingParameterString(
            self.BAND_SUFFIX,
            self.tr("Band name suffix (optional)"),
            defaultValue="",
            optional=True,
        )
        suffix.setFlags(suffix.flags() | PARAMETER_FLAG_ADVANCED)
        self.addParameter(suffix)

    def _addSharedExecutionParameters(self):
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
            type=PARAMETER_NUMBER_INTEGER,
            defaultValue=1024,
            minValue=128,
            maxValue=8192,
        )
        tile.setFlags(tile.flags() | PARAMETER_FLAG_ADVANCED)
        self.addParameter(tile)
        chunk = QgsProcessingParameterNumber(
            self.BAND_CHUNK,
            self.tr("Bands held in memory per pass"),
            type=PARAMETER_NUMBER_INTEGER,
            defaultValue=8,
            minValue=1,
            maxValue=128,
        )
        chunk.setFlags(chunk.flags() | PARAMETER_FLAG_ADVANCED)
        self.addParameter(chunk)
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT,
                self.tr("Output CSV"),
                fileFilter=self.tr("CSV files (*.csv)"),
            )
        )

    def _addAnalysisParameters(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def _buildOptions(self, parameters, context, bands):  # pragma: no cover - overridden
        raise NotImplementedError

    def _executionKwargs(self, parameters, context):
        return dict(
            all_touched=self.parameterAsBool(parameters, self.ALL_TOUCHED, context),
            tile_size=self.parameterAsInt(parameters, self.TILE_SIZE, context),
            band_chunk_size=self.parameterAsInt(parameters, self.BAND_CHUNK, context),
            band_prefix=self.parameterAsString(parameters, self.BAND_PREFIX, context) or "",
            band_suffix=self.parameterAsString(parameters, self.BAND_SUFFIX, context) or "",
        )

    # --- processing ------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        raster_path, zones, crs_wkt, bands, output = self._prepareInputs(parameters, context, feedback)
        options = self._buildOptions(parameters, context, bands)

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
                crs_wkt=crs_wkt,
            )
        except CancelledError as error:
            raise QgsProcessingException(str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise QgsProcessingException(str(error)) from error
        feedback.pushInfo(
            self.tr("Wrote {rows} rows from {tiles} raster tiles.").format(
                rows=f"{result['rows']:,}", tiles=f"{result['tiles']:,}"
            )
        )
        return {self.OUTPUT: output}

    def _prepareInputs(self, parameters, context, feedback):
        """Validate inputs, resolve CRSs and read zones. Shared by both modes."""
        raster = self.parameterAsRasterLayer(parameters, self.RASTER, context)
        source = self.parameterAsSource(parameters, self.POLYGONS, context)
        if raster is None or source is None:
            raise QgsProcessingException(self.tr("Both raster and polygon inputs are required."))
        if QgsWkbTypes.geometryType(source.wkbType()) != GEOMETRY_TYPE_POLYGON:
            raise QgsProcessingException(self.tr("The zones layer must contain polygon geometries."))
        if raster.providerType() != "gdal":
            raise QgsProcessingException(
                self.tr(
                    "This algorithm reads the raster directly with GDAL, so it needs a "
                    "file-based raster layer. '{name}' uses the '{provider}' provider. "
                    "Export it to GeoTIFF first, then run the analysis on the export."
                ).format(name=raster.name(), provider=raster.providerType())
            )

        id_field = self.parameterAsString(parameters, self.ID_FIELD, context)
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context)
        try:
            bands = parse_band_selection(
                self.parameterAsString(parameters, self.BANDS, context), raster.bandCount()
            )
            output = safe_output_path(self.parameterAsFileOutput(parameters, self.OUTPUT, context))
        except ValueError as error:
            raise QgsProcessingException(str(error)) from error

        # Resolve the effective CRS of each input: an override if one was given,
        # otherwise the layer's own CRS. The raster's effective CRS is the
        # analysis CRS.
        raster_crs = self.parameterAsCrs(parameters, self.RASTER_CRS, context)
        if not raster_crs.isValid():
            raster_crs = raster.crs()
        zones_crs = self.parameterAsCrs(parameters, self.ZONES_CRS, context)
        if not zones_crs.isValid():
            zones_crs = source.sourceCrs()

        if not raster_crs.isValid():
            feedback.pushWarning(
                self.tr(
                    "The raster has no CRS and no override was set; zone coordinates "
                    "are used as-is. Set a Raster CRS override if the results look misplaced."
                )
            )
        feedback.pushInfo(self.tr("Analysis CRS: {crs}").format(crs=raster_crs.authid() or raster_crs.description() or self.tr("unknown")))

        transform = None
        if raster_crs.isValid() and zones_crs.isValid() and zones_crs != raster_crs:
            transform = QgsCoordinateTransform(zones_crs, raster_crs, context.transformContext())
            feedback.pushInfo(
                self.tr("Transforming zones {source} → {target}.").format(
                    source=zones_crs.authid() or zones_crs.description(),
                    target=raster_crs.authid() or raster_crs.description(),
                )
            )
        elif zones_crs.isValid() and not raster_crs.isValid():
            feedback.pushWarning(
                self.tr("The zones cannot be aligned without a raster CRS; they are used as-is.")
            )

        # Pass the effective raster CRS to the engine only when it differs from
        # what the file itself declares, so an override reaches the mask code.
        crs_wkt = None
        if raster_crs.isValid() and raster_crs != raster.crs():
            crs_wkt = raster_crs.toWkt()

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
            zone_id = feature[id_field] if id_field and id_field in field_names else None
            if is_null(zone_id):
                zone_id = feature.id()
            zone_name = feature[name_field] if name_field and name_field in field_names else None
            zones.append(
                Zone(
                    internal_id=len(zones) + 1,
                    feature_id=zone_id,
                    name="" if is_null(zone_name) else str(zone_name),
                    wkb=bytes(geometry.asWkb()),
                    bounds=(bounds.xMinimum(), bounds.yMinimum(), bounds.xMaximum(), bounds.yMaximum()),
                )
            )
            if index % 500 == 0:
                feedback.setProgress(5.0 * index / total)
        if skipped:
            feedback.pushWarning(self.tr("Skipped {count} empty geometries.").format(count=skipped))

        raster_path = raster.source().split("|", 1)[0]
        return raster_path, zones, crs_wkt, bands, output


class FastZonalStatisticsAlgorithm(_BaseZonalStatisticsAlgorithm):
    """Continuous zonal statistics: count, min/max, sum, mean, standard deviation."""

    def name(self):
        return "multiband_zonal_statistics"

    def displayName(self):
        return self.tr("Fast multiband zonal statistics (continuous)")

    def shortHelpString(self):
        return self.tr(
            "Numeric statistics (count, min, max, sum, mean, standard deviation) for "
            "every polygon and selected raster band. Use this for continuous rasters "
            "such as elevation, temperature or reflectance. The raster is read in "
            "bounded tiles for predictable memory use."
        )

    def createInstance(self):
        return FastZonalStatisticsAlgorithm()

    def _addAnalysisParameters(self):
        self.addParameter(
            QgsProcessingParameterEnum(
                self.STATS,
                self.tr("Statistics"),
                options=[name.title() for name in STATISTICS],
                defaultValue=list(range(len(STATISTICS))),
                allowMultiple=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.OUTPUT_FORMAT,
                self.tr("Output layout"),
                options=[
                    self.tr("Long (one row per polygon and band)"),
                    self.tr("Wide (one row per polygon, bands across columns)"),
                ],
                defaultValue=0,
            )
        )

    def _buildOptions(self, parameters, context, bands):
        selected = [STATISTICS[i] for i in self.parameterAsEnums(parameters, self.STATS, context)]
        if not selected:
            raise QgsProcessingException(self.tr("Select at least one statistic."))
        return EngineOptions(
            bands=bands,
            statistics=selected,
            mode="continuous",
            output_format=OUTPUT_FORMATS[self.parameterAsEnum(parameters, self.OUTPUT_FORMAT, context)],
            **self._executionKwargs(parameters, context),
        )


class CategoricalZonalStatisticsAlgorithm(_BaseZonalStatisticsAlgorithm):
    """Categorical zonal statistics: majority, minority, variety and class breakdown."""

    def name(self):
        return "categorical_zonal_statistics"

    def displayName(self):
        return self.tr("Fast multiband zonal statistics (categorical)")

    def shortHelpString(self):
        return self.tr(
            "Class statistics (majority, minority, variety) and per-class pixel "
            "breakdowns for every polygon and selected raster band. Use this for "
            "discrete rasters such as land cover or soil classes. Raster values are "
            "read as integer class codes; a raster with thousands of distinct values "
            "is treated as continuous and rejected."
        )

    def createInstance(self):
        return CategoricalZonalStatisticsAlgorithm()

    def _addAnalysisParameters(self):
        self.addParameter(
            QgsProcessingParameterEnum(
                self.STATS,
                self.tr("Statistics (used by the summary layout)"),
                options=[name.title() for name in CATEGORICAL_STATISTICS],
                defaultValue=list(range(len(CATEGORICAL_STATISTICS))),
                allowMultiple=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.OUTPUT_FORMAT,
                self.tr("Output layout"),
                options=[
                    self.tr("Summary (one row per polygon and band)"),
                    self.tr("Class breakdown (one row per polygon, band and class)"),
                    self.tr("Class counts (one row per polygon and band, one column per class)"),
                ],
                defaultValue=0,
            )
        )

    def _buildOptions(self, parameters, context, bands):
        layout = CATEGORICAL_LAYOUTS[self.parameterAsEnum(parameters, self.OUTPUT_FORMAT, context)]
        selected = [CATEGORICAL_STATISTICS[i] for i in self.parameterAsEnums(parameters, self.STATS, context)]
        # The class breakdown ignores the statistic selection, so only the
        # summary layout needs at least one statistic chosen.
        if layout == "summary" and not selected:
            raise QgsProcessingException(self.tr("Select at least one statistic."))
        return EngineOptions(
            bands=bands,
            statistics=selected,
            mode="categorical",
            output_format=layout,
            **self._executionKwargs(parameters, context),
        )
