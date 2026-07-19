from __future__ import annotations

from pathlib import Path

from qgis.core import (
    QgsApplication,
    QgsMapLayerProxyModel,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProject,
)
from qgis.gui import QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
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
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .utils import STATISTICS, parse_band_selection, safe_output_path


def _qt_enum(owner, scope, name):
    """Return an enum value under both PyQt5 and PyQt6 scoping rules."""
    direct = getattr(owner, name, None)
    if direct is not None:
        return direct
    return getattr(getattr(owner, scope), name)


FRAME_NO_FRAME = _qt_enum(QFrame, "Shape", "NoFrame")
NO_SELECTION = _qt_enum(QAbstractItemView, "SelectionMode", "NoSelection")
EXPANDING = _qt_enum(QSizePolicy, "Policy", "Expanding")
MAXIMUM = _qt_enum(QSizePolicy, "Policy", "Maximum")
USER_ROLE = _qt_enum(Qt, "ItemDataRole", "UserRole")
ITEM_IS_USER_CHECKABLE = _qt_enum(Qt, "ItemFlag", "ItemIsUserCheckable")
CHECKED = _qt_enum(Qt, "CheckState", "Checked")
ALIGN_TOP = _qt_enum(Qt, "AlignmentFlag", "AlignTop")


class FastZonalStatsDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.task = None
        self.context = None
        self.feedback = None
        self.output_path = None
        self.setWindowTitle(self.tr("Fast Multiband Zonal Statistics"))
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.svg"))))
        self.setMinimumSize(690, 720)
        self.resize(760, 820)
        self.setModal(False)
        self._buildUi()
        self._connectSignals()
        self.refreshLayers()

    def _buildUi(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("hero")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(26, 22, 26, 22)
        icon = QLabel()
        icon.setPixmap(QIcon(str(Path(__file__).with_name("icon.svg"))).pixmap(54, 54))
        header_layout.addWidget(icon)
        title_box = QVBoxLayout()
        title = QLabel(self.tr("Fast Multiband Zonal Statistics"))
        title.setObjectName("heroTitle")
        subtitle = QLabel(self.tr("Process many polygons and raster bands into one tidy CSV"))
        subtitle.setObjectName("heroSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(FRAME_NO_FRAME)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 22, 24, 20)
        body_layout.setSpacing(16)

        inputs = self._group(self.tr("1  Inputs"))
        grid = QGridLayout(inputs)
        grid.setColumnStretch(1, 1)
        self.rasterCombo = QgsMapLayerComboBox()
        self.rasterCombo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.rasterCombo.setAllowEmptyLayer(True)
        self.polygonCombo = QgsMapLayerComboBox()
        self.polygonCombo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.polygonCombo.setAllowEmptyLayer(True)
        self.idField = QgsFieldComboBox()
        self.idField.setAllowEmptyFieldName(True)
        self.nameField = QgsFieldComboBox()
        self.nameField.setAllowEmptyFieldName(True)
        grid.addWidget(QLabel(self.tr("Multiband raster")), 0, 0)
        grid.addWidget(self.rasterCombo, 0, 1)
        grid.addWidget(QLabel(self.tr("Polygon zones")), 1, 0)
        grid.addWidget(self.polygonCombo, 1, 1)
        grid.addWidget(QLabel(self.tr("ID field")), 2, 0)
        grid.addWidget(self.idField, 2, 1)
        grid.addWidget(QLabel(self.tr("Name field")), 3, 0)
        grid.addWidget(self.nameField, 3, 1)
        hint = QLabel(self.tr("If ID is empty, the source feature ID is used."))
        hint.setObjectName("hint")
        grid.addWidget(hint, 4, 1)
        body_layout.addWidget(inputs)

        analysis = self._group(self.tr("2  Analysis"))
        analysis_grid = QGridLayout(analysis)
        analysis_grid.setColumnStretch(1, 1)
        self.bandEdit = QLineEdit("all")
        self.bandEdit.setPlaceholderText(self.tr("all, 1-12, or 1,3,7"))
        self.bandSummary = QLabel(self.tr("All raster bands"))
        self.bandSummary.setObjectName("hint")
        self.statsList = QListWidget()
        self.statsList.setSelectionMode(NO_SELECTION)
        self.statsList.setMaximumHeight(154)
        friendly = {
            "count": self.tr("Valid pixel count"),
            "nodata": self.tr("Nodata pixel count"),
            "min": self.tr("Minimum"),
            "max": self.tr("Maximum"),
            "sum": self.tr("Sum"),
            "mean": self.tr("Mean"),
            "stddev": self.tr("Standard deviation"),
        }
        for index, name in enumerate(STATISTICS):
            item = QListWidgetItem(friendly[name])
            item.setData(USER_ROLE, index)
            item.setFlags(item.flags() | ITEM_IS_USER_CHECKABLE)
            item.setCheckState(CHECKED)
            self.statsList.addItem(item)
        self.unitCombo = QComboBox()
        self.unitCombo.addItems(
            [self.tr("Pixels"), self.tr("Square metres"), self.tr("Square kilometres"), self.tr("Hectares"), self.tr("Acres")]
        )
        analysis_grid.addWidget(QLabel(self.tr("Raster bands")), 0, 0, ALIGN_TOP)
        band_box = QVBoxLayout()
        band_box.addWidget(self.bandEdit)
        band_box.addWidget(self.bandSummary)
        analysis_grid.addLayout(band_box, 0, 1)
        analysis_grid.addWidget(QLabel(self.tr("Statistics")), 1, 0, ALIGN_TOP)
        analysis_grid.addWidget(self.statsList, 1, 1)
        analysis_grid.addWidget(QLabel(self.tr("Area unit")), 2, 0)
        analysis_grid.addWidget(self.unitCombo, 2, 1)
        body_layout.addWidget(analysis)

        output_group = self._group(self.tr("3  Output"))
        output_layout = QVBoxLayout(output_group)
        self.outputWidget = QgsFileWidget()
        self.outputWidget.setStorageMode(QgsFileWidget.SaveFile)
        self.outputWidget.setFilter(self.tr("CSV files (*.csv)"))
        self.outputWidget.setDialogTitle(self.tr("Save zonal statistics CSV"))
        output_layout.addWidget(self.outputWidget)
        format_hint = QLabel(self.tr("One row per polygon and band • UTF-8 • atomic file writing"))
        format_hint.setObjectName("hint")
        output_layout.addWidget(format_hint)
        body_layout.addWidget(output_group)

        advanced = QGroupBox(self.tr("Advanced performance settings"))
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_layout = QGridLayout(advanced)
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
        advanced_layout.addWidget(QLabel(self.tr("Tile size")), 0, 0)
        advanced_layout.addWidget(self.tileSize, 0, 1)
        advanced_layout.addWidget(QLabel(self.tr("Band chunk")), 1, 0)
        advanced_layout.addWidget(self.bandChunk, 1, 1)
        advanced_layout.addWidget(self.allTouched, 2, 0, 1, 2)
        body_layout.addWidget(advanced)

        self.statusLabel = QLabel(self.tr("Ready"))
        self.statusLabel.setObjectName("status")
        self.progressBar = QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        body_layout.addWidget(self.statusLabel)
        body_layout.addWidget(self.progressBar)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        footer = QFrame()
        footer.setObjectName("footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 14, 24, 14)
        self.openButton = QPushButton(self.tr("Open result"))
        self.openButton.setVisible(False)
        self.cancelButton = QPushButton(self.tr("Cancel"))
        self.cancelButton.setVisible(False)
        self.closeButton = QPushButton(self.tr("Close"))
        self.runButton = QPushButton(self.tr("Run analysis"))
        self.runButton.setObjectName("primary")
        self.runButton.setDefault(True)
        footer_layout.addWidget(self.openButton)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.cancelButton)
        footer_layout.addWidget(self.closeButton)
        footer_layout.addWidget(self.runButton)
        root.addWidget(footer)
        self.setStyleSheet(self._style())

    def _group(self, title):
        box = QGroupBox(title)
        box.setSizePolicy(EXPANDING, MAXIMUM)
        return box

    def _connectSignals(self):
        self.polygonCombo.layerChanged.connect(self._polygonLayerChanged)
        self.rasterCombo.layerChanged.connect(self._updateBandSummary)
        self.bandEdit.textChanged.connect(self._updateBandSummary)
        self.runButton.clicked.connect(self.runAnalysis)
        self.cancelButton.clicked.connect(self.cancelAnalysis)
        self.closeButton.clicked.connect(self.close)
        self.openButton.clicked.connect(self.openResult)

    def refreshLayers(self):
        self.rasterCombo.setProject(QgsProject.instance())
        self.polygonCombo.setProject(QgsProject.instance())
        self._polygonLayerChanged(self.polygonCombo.currentLayer())
        self._updateBandSummary()

    def _polygonLayerChanged(self, layer):
        self.idField.setLayer(layer)
        self.nameField.setLayer(layer)

    def _updateBandSummary(self):
        layer = self.rasterCombo.currentLayer()
        if not layer:
            self.bandSummary.setText(self.tr("Choose a raster"))
            return
        try:
            selected = parse_band_selection(self.bandEdit.text(), layer.bandCount())
            if len(selected) == layer.bandCount():
                text = self.tr(f"All {layer.bandCount()} bands selected")
            else:
                text = self.tr(f"{len(selected)} of {layer.bandCount()} bands selected")
            self.bandSummary.setText(text)
            self.bandSummary.setProperty("error", False)
        except ValueError as error:
            self.bandSummary.setText(str(error))
            self.bandSummary.setProperty("error", True)
        self.bandSummary.style().unpolish(self.bandSummary)
        self.bandSummary.style().polish(self.bandSummary)

    def _selectedStats(self):
        return [
            int(self.statsList.item(i).data(USER_ROLE))
            for i in range(self.statsList.count())
            if self.statsList.item(i).checkState() == CHECKED
        ]

    def _validate(self):
        raster = self.rasterCombo.currentLayer()
        polygons = self.polygonCombo.currentLayer()
        if not raster:
            raise ValueError(self.tr("Choose a multiband raster."))
        if not polygons:
            raise ValueError(self.tr("Choose a polygon layer."))
        parse_band_selection(self.bandEdit.text(), raster.bandCount())
        if not self._selectedStats():
            raise ValueError(self.tr("Select at least one statistic."))
        return raster, polygons, safe_output_path(self.outputWidget.filePath())

    def runAnalysis(self):
        try:
            raster, polygons, output = self._validate()
        except ValueError as error:
            QMessageBox.warning(self, self.tr("Check the inputs"), str(error))
            return
        algorithm = QgsApplication.processingRegistry().algorithmById(
            "fastzonalstats:multiband_zonal_statistics"
        )
        if algorithm is None:
            QMessageBox.critical(self, self.tr("Plugin error"), self.tr("The Processing algorithm is not registered."))
            return
        parameters = {
            "RASTER": raster,
            "POLYGONS": polygons,
            "ID_FIELD": self.idField.currentField(),
            "NAME_FIELD": self.nameField.currentField(),
            "BANDS": self.bandEdit.text(),
            "STATS": self._selectedStats(),
            "AREA_UNIT": self.unitCombo.currentIndex(),
            "ALL_TOUCHED": self.allTouched.isChecked(),
            "TILE_SIZE": self.tileSize.value(),
            "BAND_CHUNK": self.bandChunk.value(),
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
            self.statusLabel.setText(self.tr(f"Processing raster tiles… {value:.0f}%"))

    def _analysisFinished(self, successful, results):
        self._setRunning(False)
        self.task = None
        if successful:
            self.progressBar.setValue(100)
            self.statusLabel.setText(self.tr("Complete — the CSV is ready."))
            self.openButton.setVisible(True)
            self.iface.messageBar().pushSuccess(
                self.tr("Fast Zonal Statistics"), self.tr("Zonal statistics CSV created successfully.")
            )
        else:
            self.progressBar.setValue(0)
            self.statusLabel.setText(self.tr("The analysis did not complete. Check the Processing log."))
            self.iface.messageBar().pushWarning(
                self.tr("Fast Zonal Statistics"), self.tr("Analysis stopped or failed; no partial CSV was published.")
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

    def _style(self):
        return """
            QDialog { background: #f5f7f8; }
            QFrame#hero { background: #0f766e; }
            QLabel#heroTitle { color: white; font-size: 21px; font-weight: 700; }
            QLabel#heroSubtitle { color: #ccfbf1; font-size: 12px; }
            QFrame#footer { background: white; border-top: 1px solid #dbe3e5; }
            QGroupBox {
                background: white; border: 1px solid #dbe3e5; border-radius: 8px;
                margin-top: 12px; padding: 14px 12px 10px 12px; font-weight: 600;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #164e63; }
            QLineEdit, QComboBox, QSpinBox, QListWidget {
                background: white; border: 1px solid #bdc9cc; border-radius: 5px; padding: 5px;
            }
            QLineEdit:focus, QComboBox:focus, QListWidget:focus { border: 1px solid #0f766e; }
            QLabel#hint { color: #60777c; font-size: 11px; }
            QLabel#hint[error="true"] { color: #b42318; }
            QLabel#status { color: #31555c; font-weight: 600; }
            QPushButton { min-height: 30px; padding: 2px 15px; border-radius: 5px; }
            QPushButton#primary { background: #0f766e; color: white; border: none; font-weight: 700; }
            QPushButton#primary:hover { background: #115e59; }
            QProgressBar { border: 1px solid #bdc9cc; border-radius: 5px; text-align: center; background: white; }
            QProgressBar::chunk { background: #14b8a6; border-radius: 4px; }
        """
