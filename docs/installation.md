# Installation

## Requirements

- **QGIS 3.34 or newer** (tested on QGIS 3.34 LTR through QGIS 4.x)
- A **file-based raster** readable by GDAL (GeoTIFF, IMG, VRT, …)
- No extra Python packages — the plugin uses the **GDAL** and **NumPy** that ship with QGIS

## Install from ZIP (recommended)

1. Download the latest `multiband_zonal_stats-<version>.zip` from the
   [releases page](https://github.com/raymukesh/multiband-zonal-stats/releases).
2. In QGIS, open **Plugins → Manage and Install Plugins…**
3. Choose **Install from ZIP**, select the downloaded file, and click **Install Plugin**.
4. Make sure **Multiband Zonal Stats** is enabled in the **Installed** list.

!!! tip "Where it appears"
    After installing, the tool is available from the **Raster → Multiband Zonal Stats**
    menu, the toolbar, and the **Processing Toolbox** (as two algorithms — continuous
    and categorical).

## Install manually (for development)

1. Copy or clone the repository into a folder named `multiband_zonal_stats` inside
   your QGIS profile's plugin directory:

    === "Windows"

        ```
        %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\multiband_zonal_stats
        ```

    === "macOS"

        ```
        ~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/multiband_zonal_stats
        ```

    === "Linux"

        ```
        ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/multiband_zonal_stats
        ```

2. Restart QGIS (or use the **Plugin Reloader**).
3. Enable **Multiband Zonal Stats** in **Plugins → Manage and Install Plugins…**

## Building the ZIP yourself

From a clone of the repository:

```bash
python scripts/build_package.py
```

This writes `dist/multiband_zonal_stats-<version>.zip`, with the file list and
version derived from the working tree and `metadata.txt`.

## Next steps

[Quick Start :material-arrow-right:](getting-started.md){ .md-button .md-button--primary }
