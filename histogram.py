"""A dependency-free histogram preview for a raster band.

The chart is drawn with QPainter (no matplotlib/QtCharts), so it works on every
QGIS install. Values are summarised by the QGIS-independent
``engine.raster_histogram`` helper, which reads the band decimated for speed.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor, QPainter, QPalette, QPen
from qgis.PyQt.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import engine
from .compat import qt_enum


ANTIALIASING = qt_enum(QPainter, "RenderHint", "Antialiasing")
NO_PEN = qt_enum(Qt, "PenStyle", "NoPen")
WAIT_CURSOR = qt_enum(Qt, "CursorShape", "WaitCursor")
ROLE_WINDOW = qt_enum(QPalette, "ColorRole", "Window")
ROLE_TEXT = qt_enum(QPalette, "ColorRole", "WindowText")
ROLE_HIGHLIGHT = qt_enum(QPalette, "ColorRole", "Highlight")
ROLE_MID = qt_enum(QPalette, "ColorRole", "Mid")

CATEGORICAL_COLOR = QColor("#d97706")  # amber, echoing the Categorical tab


class _HistogramChart(QWidget):
    """Draws the bar chart for a raster_histogram() result."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hist = None
        self.setMinimumHeight(240)
        self.setSizePolicy(
            qt_enum(QSizePolicy, "Policy", "Expanding"),
            qt_enum(QSizePolicy, "Policy", "Expanding"),
        )

    def setData(self, hist):
        self._hist = hist
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(ANTIALIASING)
        palette = self.palette()
        background = palette.color(ROLE_WINDOW)
        text = palette.color(ROLE_TEXT)
        axis = palette.color(ROLE_MID)
        painter.fillRect(self.rect(), background)

        width, height = self.width(), self.height()
        left, right, top, bottom = 56, 14, 14, 42
        plot_left, plot_top = left, top
        plot_right, plot_bottom = width - right, height - bottom
        plot_w, plot_h = plot_right - plot_left, plot_bottom - plot_top

        hist = self._hist
        if not hist or hist.get("valid", 0) == 0 or plot_w < 30 or plot_h < 30:
            painter.setPen(QPen(text))
            message = self.tr("No data to plot")
            metrics = painter.fontMetrics()
            painter.drawText(
                (width - metrics.horizontalAdvance(message)) // 2, height // 2, message
            )
            painter.end()
            return

        if hist["categorical"]:
            counts = [count for _, count in hist["classes"]]
            labels = [str(code) for code, _ in hist["classes"]]
            bar_color = CATEGORICAL_COLOR
            edges = None
        else:
            counts = hist["counts"]
            edges = hist["edges"]
            labels = None
            bar_color = palette.color(ROLE_HIGHLIGHT)

        n = max(1, len(counts))
        max_count = max(counts) if counts else 1
        max_count = max_count or 1

        # Axes.
        painter.setPen(QPen(axis))
        painter.drawLine(plot_left, plot_top, plot_left, plot_bottom)
        painter.drawLine(plot_left, plot_bottom, plot_right, plot_bottom)

        # Y-axis labels (0 and the peak count).
        painter.setPen(QPen(text))
        metrics = painter.fontMetrics()
        peak = f"{max_count:,}"
        painter.drawText(plot_left - 6 - metrics.horizontalAdvance(peak), plot_top + metrics.ascent(), peak)
        painter.drawText(plot_left - 6 - metrics.horizontalAdvance("0"), plot_bottom, "0")

        # Bars.
        bar_width = plot_w / n
        gap = 1.5 if bar_width > 4 else 0.0
        painter.setPen(QPen(NO_PEN))
        painter.setBrush(QBrush(bar_color))
        for index, count in enumerate(counts):
            bar_height = (count / max_count) * plot_h
            x = plot_left + index * bar_width + gap / 2
            painter.drawRect(
                int(round(x)),
                int(round(plot_bottom - bar_height)),
                max(1, int(round(bar_width - gap))),
                int(round(bar_height)),
            )

        # X-axis labels.
        painter.setPen(QPen(text))
        baseline = plot_bottom + metrics.ascent() + 6
        if hist["categorical"]:
            step = max(1, int(n / max(1, plot_w // 34)))
            for index in range(0, n, step):
                label = labels[index]
                centre = plot_left + (index + 0.5) * bar_width
                painter.drawText(int(centre - metrics.horizontalAdvance(label) / 2), baseline, label)
        else:
            ticks = [(plot_left, f"{edges[0]:.4g}", 0.0),
                     (plot_left + plot_w / 2, f"{edges[len(edges) // 2]:.4g}", 0.5),
                     (plot_right, f"{edges[-1]:.4g}", 1.0)]
            for centre, label, align in ticks:
                painter.drawText(int(centre - metrics.horizontalAdvance(label) * align), baseline, label)
        painter.end()


class HistogramDialog(QDialog):
    """A popup showing the distribution of one band of a raster."""

    def __init__(self, raster_path, band_labels, categorical, parent=None):
        super().__init__(parent)
        self.raster_path = raster_path
        self.setWindowTitle(self.tr("Raster histogram"))
        self.setMinimumSize(560, 420)
        self.resize(700, 500)

        root = QVBoxLayout(self)

        controls = QFormLayout()
        self.bandCombo = QComboBox()
        for index, name in band_labels:
            self.bandCombo.addItem(name, index)
        controls.addRow(self.tr("Band"), self.bandCombo)

        self.continuousRadio = QRadioButton(self.tr("Continuous (binned values)"))
        self.categoricalRadio = QRadioButton(self.tr("Categorical (class counts)"))
        self.categoricalRadio.setChecked(bool(categorical))
        self.continuousRadio.setChecked(not categorical)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.continuousRadio)
        mode_group.addButton(self.categoricalRadio)
        mode_box = QHBoxLayout()
        mode_box.addWidget(self.continuousRadio)
        mode_box.addWidget(self.categoricalRadio)
        mode_box.addStretch(1)
        controls.addRow(self.tr("Mode"), mode_box)

        self.binsSpin = QSpinBox()
        self.binsSpin.setRange(5, 256)
        self.binsSpin.setValue(50)
        controls.addRow(self.tr("Bins"), self.binsSpin)
        root.addLayout(controls)

        self.chart = _HistogramChart()
        root.addWidget(self.chart, 1)

        self.stats = self._hint("")
        self.note = self._hint("")
        root.addWidget(self.stats)
        root.addWidget(self.note)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton(self.tr("Close"))
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

        self.bandCombo.currentIndexChanged.connect(self._recompute)
        self.continuousRadio.toggled.connect(self._recompute)
        self.binsSpin.valueChanged.connect(self._recompute)
        self._recompute()

    def _hint(self, text):
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _recompute(self):
        categorical = self.categoricalRadio.isChecked()
        self.binsSpin.setEnabled(not categorical)
        band = self.bandCombo.currentData()
        if band is None:
            return
        QApplication.setOverrideCursor(WAIT_CURSOR)
        try:
            hist = engine.raster_histogram(
                self.raster_path, int(band), categorical=categorical, bins=self.binsSpin.value()
            )
        except (ValueError, RuntimeError) as error:
            self.chart.setData(None)
            self.stats.setText(str(error))
            self.note.setText("")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.chart.setData(hist)
        self._describe(hist)

    def _describe(self, hist):
        if hist.get("valid", 0) == 0:
            self.stats.setText(self.tr("The band has no valid (non-nodata) pixels."))
            self.note.setText("")
            return
        if hist["categorical"]:
            self.stats.setText(
                self.tr("Classes: {variety}   ·   majority {majority}   ·   valid pixels: {valid}").format(
                    variety=hist["variety"], majority=hist["majority"], valid=f"{hist['valid']:,}"
                )
            )
        else:
            self.stats.setText(
                self.tr("min {min:.4g}   ·   max {max:.4g}   ·   mean {mean:.4g}   ·   sd {sd:.4g}   ·   valid pixels: {valid}").format(
                    min=hist["min"], max=hist["max"], mean=hist["mean"], sd=hist["stddev"], valid=f"{hist['valid']:,}"
                )
            )
        notes = []
        if hist.get("sampled", 0) < hist.get("total_pixels", 0):
            notes.append(self.tr("Sampled {s} of {t} pixels for speed.").format(
                s=f"{hist['sampled']:,}", t=f"{hist['total_pixels']:,}"))
        if hist.get("truncated"):
            notes.append(self.tr("Showing the {n} most common classes.").format(n=len(hist["classes"])))
        self.note.setText("   ".join(notes))
