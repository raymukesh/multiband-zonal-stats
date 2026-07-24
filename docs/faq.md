# FAQ

## How is this different from QGIS's built-in Zonal Statistics?

QGIS's native *Zonal statistics* processes a **single raster band** at a time and
writes into the vector layer. Multiband Zonal Stats is built for **multiband**
rasters: it aggregates every selected band in one tiled pass and writes a tidy CSV,
with dedicated **categorical** support (majority/minority/variety and per-class
breakdowns) that the native tool does not provide.

## The tool says it needs a "file-based raster". Why?

The engine reads the raster directly with GDAL for speed, so it needs a
file-backed source (GeoTIFF, IMG, VRT, …). For WMS/tiled/provider-only layers,
export to GeoTIFF first and run the analysis on the export.

## My sums look like text in Excel (with a leading apostrophe).

That's a locale mismatch, not a bug: the CSV uses a `.` decimal separator but your
Excel expects a `,`. Import via **Data → From Text/CSV** and set the decimal
separator, or reduce the output to a few **decimal places** for cleaner numbers.

## I got "more than 65,536 distinct values".

You ran **Categorical** mode on a raster that looks continuous. Switch to the
Continuous tab, or reclassify the raster into discrete classes first.

## How do I exclude out-of-boundary or fill pixels?

Put the value(s) into **Exclude values** (e.g. `0`, or `0, 255`). They're dropped
from the calculation like nodata, across all bands. The **Histogram** preview helps
you spot which value dominates.

## Can it handle overlapping polygons and multipart features?

Yes. Shared pixels are counted for **every** overlapping zone (overlaps are
rasterized in separate passes), and all parts of a multipart feature share one
zone ID, with holes respected.

## Does it work with geographic (lat/long) rasters?

Yes — statistics and class counts work regardless of CRS. (There is no metric-area
output; pixel counts are always available.)

## Is my `polygon_id` unique per row?

Not necessarily — it repeats across bands, and across classes in the breakdown
layout. Use the `fid` column (the first column) as the unique per-row key.

## Which QGIS versions are supported?

QGIS **3.34 or newer**, from a single code path that runs on QGIS 3.34 through 4.x
(Qt5 and Qt6).

## Where do I report a bug or request a feature?

On the [GitHub issue tracker](https://github.com/raymukesh/multiband-zonal-stats/issues).
