# Multiband Zonal Stats

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

- Two analysis modes, each its own Processing algorithm and its own dialog tab:
  - **Continuous** — count, nodata count, min, max, sum, mean and standard
    deviation, in a long or wide UTF-8 CSV
  - **Categorical** — majority, minority and variety (distinct class count) per
    zone and band, or a full per-class pixel count and fraction breakdown
- Band lists and ranges such as `all`, `1-8`, or `1,3,7`
- Preview any band's distribution with a built-in histogram (continuous bins or
  categorical class counts) before running
- Every output row starts with a unique `fid` column as its stable identity
- Optional fixed **decimal places** for numeric cells (default: full precision)
- Output columns keep the raster's own band names, with an optional common prefix
  and suffix (for example `2020_ndvi`)
- Pixel-centre or all-touched inclusion
- Automatic zone reprojection into the raster CRS, with optional per-input CRS
  overrides for layers whose CRS is missing or wrong
- Configurable tile and band-chunk sizes
- Background execution, progress and cancellation
- Processing Toolbox, Model Builder and graphical dialog integration
- Atomic CSV publication—failed or cancelled jobs do not leave partial results

## Continuous vs categorical

Pick the mode that matches your raster — the dialog exposes them as two tabs, and
the Processing Toolbox as two algorithms.

**Continuous** rasters hold measured quantities (elevation, temperature,
reflectance). Averaging them is meaningful, so this mode reports count, min, max,
sum, mean and standard deviation. Output is *long* (one row per polygon and band)
or *wide* (one row per polygon, each band's columns prefixed with that band's own
name — for example `ndvi_mean`, `ndvi_sum`).

**Categorical** rasters hold discrete class codes (land cover, soil type). Their
values are labels, so averaging them is meaningless; this mode summarises the
class composition instead, in one of three layouts:

- **Summary** — one row per polygon and band, with the majority (most common
  class), minority (least common class) and variety (number of distinct classes).
  Ties on majority/minority resolve to the lowest class code.
- **Class breakdown** — one row per polygon, band and class, with that class's
  pixel count and its fraction of the zone's valid pixels.
- **Class counts (wide)** — one row per polygon and band, with a `class_<code>`
  column of pixel counts for every class found. Columns are the union of classes
  across all zones, so every row shares the same columns (absent classes are 0).

Values are read as integer class codes (floating-point rasters are rounded to the
nearest integer). A raster with more than 65,536 distinct values is treated as
continuous and rejected, since that almost always means the wrong mode was chosen.

## Band names

Output labels come from the raster's own band descriptions (falling back to
`band_001`, `band_002`, … when a band is unnamed). The wide layouts turn those
into spreadsheet-safe column headers — lowercased, with spaces and punctuation
replaced by underscores, and any collisions numbered.

An optional **band name prefix** and **suffix** wrap every band label — set the
prefix to `2020_` to get `2020_ndvi`, for instance. They change only the CSV
labels; the raster's bands are never renamed.

## Coordinate systems

The raster's CRS is always the analysis CRS, and the raster is never resampled —
resampling would fabricate pixel values and corrupt the statistics. Zones are
transformed into the raster CRS automatically when they differ.

When a layer's CRS is missing or wrong, set the **Raster CRS** or **Zones CRS**
override in the dialog (or the `RASTER_CRS` / `ZONES_CRS` Processing parameters).
An override *assigns* a CRS — it reinterprets the coordinates rather than
reprojecting the data — so a raster CRS override aligns the zone masks on a file
that GDAL reads as having no CRS.

The raster is read directly with GDAL, so the raster layer must be file-based.
Reported standard deviation is the population standard deviation (divided by
`n`), matching QGIS's own zonal statistics rather than the sample standard
deviation that pandas and NumPy's `ddof=1` produce.

## QGIS version support

Runs on QGIS 3.34 through QGIS 4.x from a single code path. `compat.py` resolves
the Qt5/Qt6 and QGIS 3/4 differences — scoped enums, `QAction` moving from
QtWidgets to QtGui, and the class-scoped enums that QGIS 4 relocated into the
central `Qgis` namespace — preferring the modern spelling and falling back to
the legacy one.

## Install for development

1. Rename or copy this directory to `multiband_zonal_stats` inside the QGIS
   user profile's `python/plugins` directory.
2. Restart QGIS or reload plugins.
3. Enable **Multiband Zonal Stats** in Plugin Manager.
4. Open it from the Raster menu, toolbar, or Processing Toolbox.

No separate Python packages are required beyond the GDAL and NumPy versions
distributed with QGIS.

## Smoke test in QGIS

1. Load a multiband raster and a polygon layer.
2. Open it from the **Raster → Multiband Zonal Stats** menu, the toolbar, or the Processing Toolbox.
3. Select inputs, leave bands as `all`, choose an output CSV and run.
4. Compare several polygon/band results with QGIS's standard zonal statistics.
5. Repeat with overlapping multipolygons, nodata and the all-touched option.

## Development tests

The engine tests need GDAL and NumPy, so run them with QGIS's bundled Python:

```bash
# Windows
"C:\Program Files\QGIS 3.40.5\bin\python-qgis-ltr.bat" -m unittest discover -s tests -t tests -v
# macOS / Linux
python3 -m unittest discover -s tests -t tests -v
```

`tests/test_utils.py` and `tests/test_engine_helpers.py` are pure Python and run
under any interpreter. `tests/test_engine_integration.py` exercises real GDAL
rasterization against known-correct values and skips if GDAL is unavailable.

A full in-QGIS run, including plugin registration and the dialog's Qt enum
lookups, is available as:

```bash
"C:\Program Files\QGIS 3.40.5\bin\python-qgis-ltr.bat" tests/qgis_runtime_smoke.py
```

Build an installable ZIP with:

```bash
python scripts/build_package.py
```
