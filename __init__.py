"""QGIS entry point for Fast Multiband Zonal Statistics."""


def classFactory(iface):
    from .plugin import FastZonalStatsPlugin

    return FastZonalStatsPlugin(iface)

