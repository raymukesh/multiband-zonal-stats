"""QGIS entry point for Multiband Zonal Stats."""


def classFactory(iface):
    from .plugin import FastZonalStatsPlugin

    return FastZonalStatsPlugin(iface)

