from __future__ import annotations

from pathlib import Path

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProject,
)
from qgis.gui import (
    QgsCollapsibleGroupBox,
    QgsFieldComboBox,
    QgsFileWidget,
    QgsMapLayerComboBox,
    QgsProjectionSelectionWidget,
)
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QFont, QIcon, QPalette
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .compat import file_widget_storage_mode, layer_filter, qt_enum
from .utils import CATEGORICAL_STATISTICS, STATISTICS, parse_band_selection, safe_output_path


FRAME_NO_FRAME = qt_enum(QFrame, "Shape", "NoFrame")
NO_SELECTION = qt_enum(QAbstractItemView, "SelectionMode", "NoSelection")
USER_ROLE = qt_enum(Qt, "ItemDataRole", "UserRole")
ITEM_IS_USER_CHECKABLE = qt_enum(Qt, "ItemFlag", "ItemIsUserCheckable")
CHECKED = qt_enum(Qt, "CheckState", "Checked")
UNCHECKED = qt_enum(Qt, "CheckState", "Unchecked")
ALIGN_TOP = qt_enum(Qt, "AlignmentFlag", "AlignTop")
COLOR_GROUP_DISABLED = qt_enum(QPalette, "ColorGroup", "Disabled")
ROLE_WINDOW_TEXT = qt_enum(QPalette, "ColorRole", "WindowText")
ROLE_WINDOW = qt_enum(QPalette, "ColorRole", "Window")
RASTER_LAYER_FILTER = layer_filter("RasterLayer")
POLYGON_LAYER_FILTER = layer_filter("PolygonLayer")
SAVE_FILE_MODE = file_widget_storage_mode("SaveFile")


class FastZonalStatsDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.task = None
        self.context = None
        self.feedback = None
        self.output_path = None
        self.setWindowTitle(self.tr("Multiband Zonal Stats"))
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.svg"))))
        self.setMinimumSize(640, 540)
        self.resize(820, 720)
        self.setModal(False)
        self._buildUi()
        self._connectSignals()
        self.refreshLayers()

    def _buildUi(self):
        """Build the dialog from unstyled Qt widgets.

        No stylesheet is applied anywhere: every colour and font comes from the
        active QGIS theme, so the dialog stays readable under light and dark
        themes alike and matches the rest of the application.
        """
        root = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(FRAME_NO_FRAME)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)

        # --- Inputs ---
        inputs = QGroupBox(self.tr("Inputs"))
        inputs_form = QFormLayout(inputs)
        self.rasterCombo = QgsMapLayerComboBox()
        self.rasterCombo.setFilters(RASTER_LAYER_FILTER)
        self.rasterCombo.setAllowEmptyLayer(True)
        self.polygonCombo = QgsMapLayerComboBox()
        self.polygonCombo.setFilters(POLYGON_LAYER_FILTER)
        self.polygonCombo.setAllowEmptyLayer(True)
        self.idField = QgsFieldComboBox()
        self.idField.setAllowEmptyFieldName(True)
        self.nameField = QgsFieldComboBox()
        self.nameField.setAllowEmptyFieldName(True)
        self.rasterCrs = QgsProjectionSelectionWidget()
        self.rasterCrs.setToolTip(
            self.tr("Set only if the raster's CRS is missing or wrong. This reinterprets "
                    "the raster, it does not resample it.")
        )
        self.zonesCrs = QgsProjectionSelectionWidget()
        self.zonesCrs.setToolTip(
            self.tr("Set only if the zones' CRS is missing or wrong. Zones are transformed "
                    "into the analysis CRS before processing.")
        )
        inputs_form.addRow(self.tr("Multiband raster"), self.rasterCombo)
        inputs_form.addRow(self.tr("Raster CRS"), self.rasterCrs)
        inputs_form.addRow(self.tr("Polygon zones"), self.polygonCombo)
        inputs_form.addRow(self.tr("Zones CRS"), self.zonesCrs)
        inputs_form.addRow(self.tr("ID field"), self.idField)
        inputs_form.addRow(self.tr("Name field"), self.nameField)
        inputs_form.addRow("", self._hint(self.tr("If ID is empty, the source feature ID is used.")))
        self.analysisCrsLabel = self._hint("")
        inputs_form.addRow("", self.analysisCrsLabel)
        body_layout.addWidget(inputs)

        # --- Analysis ---
        analysis = QGroupBox(self.tr("Analysis"))
        analysis_form = QFormLayout(analysis)
        analysis_form.setLabelAlignment(ALIGN_TOP)
        self.bandEdit = QLineEdit("all")
        self.bandEdit.setPlaceholderText(self.tr("all, 1-12, or 1,3,7"))
        self.bandSummary = self._hint(self.tr("All raster bands"))
        band_box = QVBoxLayout()
        band_box.setContentsMargins(0, 0, 0, 0)
        band_box.addWidget(self.bandEdit)
        band_box.addWidget(self.bandSummary)
        analysis_form.addRow(self.tr("Raster bands"), band_box)

        # Optional prefix/suffix wrapped around band names in the output; the
        # raster's bands themselves are never renamed.
        self.bandPrefix = QLineEdit()
        self.bandPrefix.setPlaceholderText(self.tr("prefix, e.g. 2020_"))
        self.bandSuffix = QLineEdit()
        self.bandSuffix.setPlaceholderText(self.tr("suffix, e.g. _b"))
        affix_box = QHBoxLayout()
        affix_box.setContentsMargins(0, 0, 0, 0)
        affix_box.addWidget(self.bandPrefix)
        affix_box.addWidget(self.bandSuffix)
        analysis_form.addRow(self.tr("Band name prefix / suffix"), affix_box)

        # A tab per raster kind so the user commits to one before choosing
        # statistics: continuous rasters carry measured values, categorical
        # rasters carry discrete class codes. The active tab is the analysis mode.
        self.modeHint = self._hint(
            self.tr(
                "Pick the tab that matches your raster. Continuous = measured values "
                "(elevation, temperature). Categorical = class codes (land cover, soils)."
            )
        )
        analysis_form.addRow("", self.modeHint)
        self.modeTabs = QTabWidget()

        # --- Continuous tab ---
        continuous_tab = QWidget()
        continuous_form = QFormLayout(continuous_tab)
        continuous_form.setLabelAlignment(ALIGN_TOP)
        self.statsList = self._makeStatsList(
            STATISTICS,
            {
                "count": self.tr("Valid pixel count"),
                "nodata": self.tr("Nodata pixel count"),
                "min": self.tr("Minimum"),
                "max": self.tr("Maximum"),
                "sum": self.tr("Sum"),
                "mean": self.tr("Mean"),
                "stddev": self.tr("Standard deviation"),
            },
        )
        continuous_form.addRow(self.tr("Statistics"), self.statsList)
        self.formatCombo = QComboBox()
        self.formatCombo.addItems(
            [
                self.tr("Long — one row per polygon and band"),
                self.tr("Wide — one row per polygon, bands across columns"),
            ]
        )
        continuous_form.addRow(self.tr("Layout"), self.formatCombo)
        self.formatHint = self._hint("")
        continuous_form.addRow("", self.formatHint)
        self.modeTabs.addTab(continuous_tab, self.tr("Continuous raster"))

        # --- Categorical tab ---
        categorical_tab = QWidget()
        categorical_form = QFormLayout(categorical_tab)
        categorical_form.setLabelAlignment(ALIGN_TOP)
        self.catStatsList = self._makeStatsList(
            CATEGORICAL_STATISTICS,
            {
                "majority": self.tr("Majority (most common class)"),
                "minority": self.tr("Minority (least common class)"),
                "variety": self.tr("Variety (distinct class count)"),
                "count": self.tr("Valid pixel count"),
                "nodata": self.tr("Nodata pixel count"),
            },
        )
        categorical_form.addRow(self.tr("Statistics"), self.catStatsList)
        self.catFormatCombo = QComboBox()
        self.catFormatCombo.addItems(
            [
                self.tr("Summary — one row per polygon and band"),
                self.tr("Class breakdown — one row per polygon, band and class"),
                self.tr("Class counts — one row per polygon and band, one column per class"),
            ]
        )
        categorical_form.addRow(self.tr("Layout"), self.catFormatCombo)
        self.catFormatHint = self._hint("")
        categorical_form.addRow("", self.catFormatHint)
        self.modeTabs.addTab(categorical_tab, self.tr("Categorical raster"))

        analysis_form.addRow(self.modeTabs)
        body_layout.addWidget(analysis)

        # --- Output ---
        output_group = QGroupBox(self.tr("Output"))
        output_form = QFormLayout(output_group)
        self.decimals = QSpinBox()
        # -1 is shown as "Full precision"; 0-15 fix the number of decimals.
        self.decimals.setRange(-1, 15)
        self.decimals.setValue(-1)
        self.decimals.setSpecialValueText(self.tr("Full precision"))
        output_form.addRow(self.tr("Decimal places"), self.decimals)
        self.outputWidget = QgsFileWidget()
        self.outputWidget.setStorageMode(SAVE_FILE_MODE)
        self.outputWidget.setFilter(self.tr("CSV files (*.csv)"))
        self.outputWidget.setDialogTitle(self.tr("Save zonal statistics CSV"))
        output_form.addRow(self.tr("CSV file"), self.outputWidget)
        body_layout.addWidget(output_group)

        # --- Advanced (collapsed by default, QGIS's own collapsible group box) ---
        advanced = QgsCollapsibleGroupBox(self.tr("Advanced performance settings"))
        advanced.setCollapsed(True)
        advanced_form = QFormLayout(advanced)
        self.tileSize = QSpinBox()
        self.tileSize.setRange(128, 8192)
        self.tileSize.setSingleStep(128)
        self.tileSize.setValue(1024)
        self.tileSize.setSuffix(self.tr(" px"))
        self.bandChunk = QSpinBox()
        self.bandChunk.setRange(1, 128)
        self.bandChunk.setValue(8)
        self.bandChunk.setSuffix(self.tr(" bands"))
        self.allTouched = QCheckBox(self.tr("Include every pixel touched by a polygon (slower)"))
        advanced_form.addRow(self.tr("Tile size"), self.tileSize)
        advanced_form.addRow(self.tr("Band chunk"), self.bandChunk)
        advanced_form.addRow("", self.allTouched)
        body_layout.addWidget(advanced)

        body_layout.addStretch(1)
        scroll.setWidget(body)

        # Two columns: the scrolling form on the left, a fixed About panel on the
        # right, matching the layout most QGIS plugin dialogs use.
        columns = QHBoxLayout()
        columns.addWidget(scroll, 1)
        columns.addWidget(self._buildAboutPanel())
        root.addLayout(columns, 1)

        self.statusLabel = QLabel(self.tr("Ready"))
        self.progressBar = QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        root.addWidget(self.statusLabel)
        root.addWidget(self.progressBar)

        footer_layout = QHBoxLayout()
        self.openButton = QPushButton(self.tr("Open result"))
        self.openButton.setVisible(False)
        self.cancelButton = QPushButton(self.tr("Cancel"))
        self.cancelButton.setVisible(False)
        self.closeButton = QPushButton(self.tr("Close"))
        self.runButton = QPushButton(self.tr("Run analysis"))
        self.runButton.setDefault(True)
        footer_layout.addWidget(self.openButton)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.cancelButton)
        footer_layout.addWidget(self.closeButton)
        footer_layout.addWidget(self.runButton)
        root.addLayout(footer_layout)

    def _hint(self, text):
        """A secondary-text label tinted with the theme's own disabled colour."""
        label = QLabel(text)
        label.setWordWrap(True)
        palette = label.palette()
        palette.setColor(ROLE_WINDOW_TEXT, palette.color(COLOR_GROUP_DISABLED, ROLE_WINDOW_TEXT))
        label.setPalette(palette)
        return label

    def _separator(self):
        line = QFrame()
        line.setFrameShape(qt_enum(QFrame, "Shape", "HLine"))
        line.setFrameShadow(qt_enum(QFrame, "Shadow", "Sunken"))
        return line

    def _readMetadata(self):
        """Read the plugin's own metadata.txt so the About panel stays in sync."""
        import configparser

        parser = configparser.ConfigParser()
        parser.optionxform = str  # metadata keys are not lowercased
        try:
            parser.read(Path(__file__).with_name("metadata.txt"), encoding="utf-8")
            return dict(parser["general"])
        except Exception:
            return {}

    def _buildAboutPanel(self):
        """A fixed-width sidebar describing the plugin, read from metadata.txt."""
        meta = self._readMetadata()
        panel = QGroupBox(self.tr("About"))
        panel.setMaximumWidth(260)
        layout = QVBoxLayout(panel)

        icon_path = Path(__file__).with_name("icon.svg")
        if icon_path.exists():
            icon_label = QLabel()
            icon_label.setPixmap(QIcon(str(icon_path)).pixmap(48, 48))
            layout.addWidget(icon_label)

        title = QLabel(meta.get("name", "Multiband Zonal Stats"))
        title.setWordWrap(True)
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        title.setFont(title_font)
        layout.addWidget(title)

        facts = []
        if meta.get("version"):
            facts.append(self.tr("Version {v}").format(v=meta["version"]))
        if meta.get("qgisMinimumVersion"):
            facts.append(self.tr("QGIS {v}+").format(v=meta["qgisMinimumVersion"]))
        if facts:
            layout.addWidget(self._hint("  •  ".join(facts)))

        about_text = meta.get("about") or meta.get("description")
        if about_text:
            summary = QLabel(about_text)
            summary.setWordWrap(True)
            layout.addWidget(summary)

        layout.addWidget(self._separator())

        steps = QLabel(
            self.tr(
                "1. Pick the raster and polygon layers.\n"
                "2. Choose the Continuous or Categorical tab to match your raster.\n"
                "3. Select statistics and a layout.\n"
                "4. Save the output CSV and run.\n\n"
                "Every output row carries a unique fid."
            )
        )
        steps.setWordWrap(True)
        layout.addWidget(steps)

        if meta.get("author"):
            layout.addWidget(self._separator())
            layout.addWidget(self._hint(self.tr("By {who}").format(who=meta["author"])))
            if meta.get("email"):
                layout.addWidget(self._hint(meta["email"]))

        layout.addStretch(1)
        return panel

    def _makeStatsList(self, names, friendly):
        """A checkable list whose items carry their statistic index in USER_ROLE."""
        widget = QListWidget()
        widget.setSelectionMode(NO_SELECTION)
        widget.setMaximumHeight(150)
        for index, name in enumerate(names):
            item = QListWidgetItem(friendly[name])
            item.setData(USER_ROLE, index)
            item.setFlags(item.flags() | ITEM_IS_USER_CHECKABLE)
            item.setCheckState(CHECKED)
            widget.addItem(item)
        return widget

    def _checkedIndices(self, widget):
        return [
            int(widget.item(i).data(USER_ROLE))
            for i in range(widget.count())
            if widget.item(i).checkState() == CHECKED
        ]

    def _isCategorical(self):
        return self.modeTabs.currentIndex() == 1

    def _errorColour(self):
        """Pick a red that stays legible against the current theme's background."""
        window = self.palette().color(ROLE_WINDOW)
        return "#ff6b6b" if window.lightness() < 128 else "#c0392b"

    def _connectSignals(self):
        self.polygonCombo.layerChanged.connect(self._polygonLayerChanged)
        self.rasterCombo.layerChanged.connect(self._rasterLayerChanged)
        self.rasterCrs.crsChanged.connect(self._updateAnalysisCrs)
        self.formatCombo.currentIndexChanged.connect(self._updateFormatHint)
        self.catFormatCombo.currentIndexChanged.connect(self._updateCatFormatHint)
        self.bandEdit.textChanged.connect(self._updateBandSummary)
        self.runButton.clicked.connect(self.runAnalysis)
        self.cancelButton.clicked.connect(self.cancelAnalysis)
        self.closeButton.clicked.connect(self.close)
        self.openButton.clicked.connect(self.openResult)

    def refreshLayers(self):
        self.rasterCombo.setProject(QgsProject.instance())
        self.polygonCombo.setProject(QgsProject.instance())
        self._rasterLayerChanged(self.rasterCombo.currentLayer())
        self._polygonLayerChanged(self.polygonCombo.currentLayer())
        self._updateBandSummary()
        self._updateFormatHint()
        self._updateCatFormatHint()

    def _updateFormatHint(self):
        if self.formatCombo.currentIndex() == 1:
            text = self.tr(
                "Columns per band are prefixed b<band>_ (for example b1_mean, b1_sum). "
                "UTF-8, written atomically."
            )
        else:
            text = self.tr("One row per polygon and band. UTF-8, written atomically.")
        self.formatHint.setText(text)

    def _updateCatFormatHint(self):
        index = self.catFormatCombo.currentIndex()
        if index == 1:
            text = self.tr(
                "One row per polygon, band and class, with the pixel count and fraction "
                "of each class. The statistics above are ignored for this layout."
            )
        elif index == 2:
            text = self.tr(
                "One row per polygon and band, with a class_<code> column of pixel counts "
                "for every class found. The statistics above are ignored for this layout."
            )
        else:
            text = self.tr("One row per polygon and band with the chosen class statistics.")
        self.catFormatHint.setText(text)

    def _rasterLayerChanged(self, layer):
        # Default the CRS override to the layer's own CRS; the user only changes
        # it when the declared CRS is missing or wrong.
        if layer is not None:
            self.rasterCrs.setCrs(layer.crs())
        self._updateBandSummary()
        self._updateAnalysisCrs()

    def _polygonLayerChanged(self, layer):
        self.idField.setLayer(layer)
        self.nameField.setLayer(layer)
        if layer is not None:
            self.zonesCrs.setCrs(layer.crs())

    def _updateAnalysisCrs(self):
        crs = self.rasterCrs.crs()
        if crs.isValid():
            self.analysisCrsLabel.setText(
                self.tr("Analysis CRS: {crs} — the raster grid is not resampled.").format(
                    crs=crs.authid() or crs.description()
                )
            )
            self.analysisCrsLabel.setStyleSheet("")
        else:
            self.analysisCrsLabel.setText(self.tr("Analysis CRS: none set — choose a raster or set its CRS."))
            self.analysisCrsLabel.setStyleSheet(f"color: {self._errorColour()};")

    def _updateBandSummary(self):
        layer = self.rasterCombo.currentLayer()
        if not layer:
            self.bandSummary.setText(self.tr("Choose a raster"))
            return
        try:
            selected = parse_band_selection(self.bandEdit.text(), layer.bandCount())
            if len(selected) == layer.bandCount():
                text = self.tr("All {total} bands selected").format(total=layer.bandCount())
            else:
                text = self.tr("{count} of {total} bands selected").format(
                    count=len(selected), total=layer.bandCount()
                )
            self.bandSummary.setText(text)
            # Clearing the stylesheet restores the palette-based hint colour.
            self.bandSummary.setStyleSheet("")
        except ValueError as error:
            self.bandSummary.setText(str(error))
            self.bandSummary.setStyleSheet(f"color: {self._errorColour()};")

    def _selectedStats(self):
        return self._checkedIndices(self.statsList)

    def _selectedCatStats(self):
        return self._checkedIndices(self.catStatsList)

    def _validate(self):
        raster = self.rasterCombo.currentLayer()
        polygons = self.polygonCombo.currentLayer()
        if not raster:
            raise ValueError(self.tr("Choose a multiband raster."))
        if not polygons:
            raise ValueError(self.tr("Choose a polygon layer."))
        parse_band_selection(self.bandEdit.text(), raster.bandCount())
        if self._isCategorical():
            # The class breakdown layout does not use the statistic selection.
            if self.catFormatCombo.currentIndex() == 0 and not self._selectedCatStats():
                raise ValueError(self.tr("Select at least one statistic."))
        elif not self._selectedStats():
            raise ValueError(self.tr("Select at least one statistic."))
        return raster, polygons, safe_output_path(self.outputWidget.filePath())

    def runAnalysis(self):
        try:
            raster, polygons, output = self._validate()
        except ValueError as error:
            QMessageBox.warning(self, self.tr("Check the inputs"), str(error))
            return
        if self._isCategorical():
            algorithm_id = "fastzonalstats:categorical_zonal_statistics"
            stats = self._selectedCatStats()
            output_format = self.catFormatCombo.currentIndex()
        else:
            algorithm_id = "fastzonalstats:multiband_zonal_statistics"
            stats = self._selectedStats()
            output_format = self.formatCombo.currentIndex()
        algorithm = QgsApplication.processingRegistry().algorithmById(algorithm_id)
        if algorithm is None:
            QMessageBox.critical(self, self.tr("Plugin error"), self.tr("The Processing algorithm is not registered."))
            return
        parameters = {
            "RASTER": raster,
            "RASTER_CRS": self.rasterCrs.crs(),
            "POLYGONS": polygons,
            "ZONES_CRS": self.zonesCrs.crs(),
            "ID_FIELD": self.idField.currentField(),
            "NAME_FIELD": self.nameField.currentField(),
            "BANDS": self.bandEdit.text(),
            "BAND_PREFIX": self.bandPrefix.text(),
            "BAND_SUFFIX": self.bandSuffix.text(),
            "STATS": stats,
            "OUTPUT_FORMAT": output_format,
            "ALL_TOUCHED": self.allTouched.isChecked(),
            "TILE_SIZE": self.tileSize.value(),
            "BAND_CHUNK": self.bandChunk.value(),
            "DECIMAL_PLACES": self.decimals.value(),
            "OUTPUT": output,
        }
        self.output_path = output
        self.context = QgsProcessingContext()
        self.context.setProject(QgsProject.instance())
        self.feedback = QgsProcessingFeedback()
        self.task = QgsProcessingAlgRunnerTask(algorithm, parameters, self.context, self.feedback)
        self.task.executed.connect(self._analysisFinished)
        self.task.progressChanged.connect(self._progressChanged)
        self._setRunning(True)
        self.statusLabel.setText(self.tr("Preparing zones and raster tiles…"))
        QgsApplication.taskManager().addTask(self.task)

    def cancelAnalysis(self):
        if self.task:
            self.statusLabel.setText(self.tr("Cancelling safely…"))
            self.task.cancel()

    def _progressChanged(self, value):
        self.progressBar.setValue(round(value))
        if value > 5:
            self.statusLabel.setText(
                self.tr("Processing raster tiles… {percent}%").format(percent=f"{value:.0f}")
            )

    def _analysisFinished(self, successful, results):
        self._setRunning(False)
        self.task = None
        if successful:
            self.progressBar.setValue(100)
            self.statusLabel.setText(self.tr("Complete — the CSV is ready."))
            self.openButton.setVisible(True)
            self.iface.messageBar().pushSuccess(
                self.tr("Multiband Zonal Stats"), self.tr("Zonal statistics CSV created successfully.")
            )
        else:
            self.progressBar.setValue(0)
            self.statusLabel.setText(self.tr("The analysis did not complete. Check the Processing log."))
            self.iface.messageBar().pushWarning(
                self.tr("Multiband Zonal Stats"), self.tr("Analysis stopped or failed; no partial CSV was published.")
            )

    def _setRunning(self, running):
        self.runButton.setEnabled(not running)
        self.closeButton.setEnabled(not running)
        self.cancelButton.setVisible(running)
        self.openButton.setVisible(False if running else self.openButton.isVisible())

    def openResult(self):
        if self.output_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_path))

    def reject(self):
        if self.task:
            QMessageBox.information(self, self.tr("Analysis running"), self.tr("Cancel the analysis before closing this window."))
            return
        super().reject()
