from pathlib import Path

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import FastZonalStatsProvider


class FastZonalStatsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None
        self.dialog = None

    def initGui(self):
        self.provider = FastZonalStatsProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)
        # qgis_process loads enabled Processing plugins without a desktop iface.
        if self.iface is None:
            return
        icon = QIcon(str(Path(__file__).with_name("icon.svg")))
        self.action = QAction(icon, self.tr("Fast Multiband Zonal Statistics…"), self.iface.mainWindow())
        self.action.setObjectName("fastMultibandZonalStatisticsAction")
        self.action.triggered.connect(self.openDialog)
        self.iface.addPluginToRasterMenu(self.tr("Fast Zonal Statistics"), self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.dialog:
            self.dialog.close()
            self.dialog = None
        if self.action and self.iface is not None:
            self.iface.removePluginRasterMenu(self.tr("Fast Zonal Statistics"), self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def openDialog(self):
        if self.dialog is None:
            from .dialog import FastZonalStatsDialog

            self.dialog = FastZonalStatsDialog(self.iface, self.iface.mainWindow())
        self.dialog.refreshLayers()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def tr(self, text):
        return text
