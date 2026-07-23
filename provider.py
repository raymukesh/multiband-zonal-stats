from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import CategoricalZonalStatisticsAlgorithm, FastZonalStatisticsAlgorithm


class FastZonalStatsProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(FastZonalStatisticsAlgorithm())
        self.addAlgorithm(CategoricalZonalStatisticsAlgorithm())

    def id(self):
        return "fastzonalstats"

    def name(self):
        return self.tr("Fast Zonal Statistics")

    def longName(self):
        return self.name()

    def icon(self):
        return QIcon(self.iconPath())

    def iconPath(self):
        from pathlib import Path

        return str(Path(__file__).with_name("icon.svg"))

