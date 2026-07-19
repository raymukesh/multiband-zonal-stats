# Fast Multiband Zonal Statistics

A QGIS Processing plugin for producing tidy zonal-statistics CSV files from
multiband rasters and polygon or multipolygon layers.

## Why it is fast

The engine processes the raster in bounded windows. For each window it creates
an in-memory polygon-ID mask, reads a chunk of selected bands, and aggregates all
zones with NumPy operations. Raster reads therefore scale with touched raster
tiles instead of multiplying full reads by every polygon and band.

Overlapping polygons are placed in separate mask passes so shared pixels are
counted for every polygon. Multipart features retain one zone ID.

## Current features

- Long-format UTF-8 CSV: one row per polygon and raster band
- Count, nodata count, min, max, sum, mean and standard deviation
- Band lists and ranges such as `all`, `1-8`, or `1,3,7`
- Pixel, square metre, square kilometre, hectare and acre output
- Pixel-centre or all-touched inclusion
- Configurable tile and band-chunk sizes
- Background execution, progress and cancellation
- Processing Toolbox, Model Builder and graphical dialog integration
- Atomic CSV publication—failed or cancelled jobs do not leave partial results

Metric pixel areas require a projected raster CRS in this initial release. For
geographic rasters, use pixel units or reproject to an appropriate equal-area CRS.

## Install for development

1. Rename or copy this directory to `fast_multiband_zonal_stats` inside the QGIS
   user profile's `python/plugins` directory.
2. Restart QGIS or reload plugins.
3. Enable **Fast Multiband Zonal Statistics** in Plugin Manager.
4. Open it from the Raster menu, toolbar, or Processing Toolbox.

No separate Python packages are required beyond the GDAL and NumPy versions
distributed with QGIS.

## Smoke test in QGIS

1. Load a multiband raster and a polygon layer.
2. Open **Raster → Fast Zonal Statistics → Fast Multiband Zonal Statistics**.
3. Select inputs, leave bands as `all`, choose an output CSV and run.
4. Compare several polygon/band results with QGIS's standard zonal statistics.
5. Repeat with overlapping multipolygons, nodata and the all-touched option.

## Development tests

From the parent directory:

```bash
python -m unittest discover -s "QGIS Plugin - Zonal Stats/tests" -v
```

Build an installable ZIP with:

```bash
python scripts/build_package.py
```
