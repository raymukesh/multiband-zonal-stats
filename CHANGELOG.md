# Changelog

## 0.5.0 — 2026-07-22

- Mark the plugin as stable (`experimental=False`) and complete the metadata for
  the QGIS plugin repository: `repository`, `tracker` and `homepage` URLs
- Remove the obsolete `supportsQt6` metadata flag (no longer recognised by QGIS);
  QGIS 4 readiness is declared via `qgisMaximumVersion=4.99`, and the code already
  uses the `qgis.PyQt` shim with no Qt5-only APIs
- Add an **Exclude values** input (dialog and both algorithms) that drops
  specific pixel values from the calculation as extra nodata — e.g. `0` for
  out-of-boundary in categorical rasters — on top of the raster's declared nodata
- Add a **Histogram…** button that previews a raster band's distribution in a
  popup — binned values (continuous) or class counts (categorical), drawn with
  QPainter (no extra dependencies) and read decimated for speed
- Add a **Help** button (bottom-left of the dialog) that opens a bundled, richly
  formatted `help.html` in the browser — modes, statistics, layouts with example
  CSVs, band naming, and troubleshooting
- Rework the dialog into a two-column layout with a right-side **About** panel
  (icon, name, version and a short how-to, read live from `metadata.txt`),
  separated from the form by a draggable splitter
- Prepend a unique 1-based `fid` column to every output row, in all layouts, as
  a stable per-row identity (e.g. for joins or database imports)
- Add an optional **Decimal places** control (dialog and both algorithms) that
  fixes the decimals of numeric cells; `-1` / default keeps full precision
- Rename the plugin's display name to **Multiband Zonal Stats** across menus, the
  dialog, and the Processing group and algorithm titles, and rename the packaged
  plugin folder/zip to `multiband_zonal_stats`. The provider id `fastzonalstats`
  and the algorithm ids are unchanged, so existing Processing models keep working
- Set the plugin author to Mukesh Ray (dr.raymukesh@gmail.com) in the metadata
- Add categorical (discrete-class) analysis as a second Processing algorithm,
  *Multiband zonal stats (categorical)*, and a matching **Categorical
  raster** tab in the dialog. It reports the majority, minority and variety
  (distinct class count) per zone and band in a summary layout, a full per-class
  pixel count and fraction in a class-breakdown layout, or a **class-counts
  (wide)** layout with one `class_<code>` pixel-count column per class
- Wide continuous output now labels each band's columns with the raster's own
  band name (for example `ndvi_mean`) instead of the positional `b1_`, `b2_`
- Add optional **band name prefix** and **suffix** inputs (both algorithms and
  the dialog) that wrap every band label in the output — for example `2020_ndvi`.
  The raster's bands are never renamed; only the CSV labels change
- The dialog now presents **Continuous raster** and **Categorical raster** tabs
  so the analysis kind is an explicit choice; the continuous algorithm keeps its
  `multiband_zonal_statistics` id, so existing models still run
- Categorical rasters with more than 65,536 distinct values are rejected with a
  clear message, since that pattern indicates a continuous raster chosen by
  mistake; floating-point values are rounded to the nearest integer class
- Both modes share one tiled read/mask path, so the raster is read and each
  polygon mask rasterized exactly once regardless of analysis kind

## 0.4.0 — 2026-07-22

- Add a wide output layout as an alternative to the long format: one row per
  polygon, with each band's statistics spread across columns prefixed
  `b<band index>_` (for example `b1_mean`, `b2_sum`). Selectable in the dialog
  and as the `OUTPUT_FORMAT` Processing parameter; long remains the default
- Both layouts share one value-computation path, so their numbers are identical
- Remove the zone area and area-unit output, the `AREA_UNIT` parameter and the
  dialog's area-unit selector. Metric area no longer forces a projected raster
  CRS, so geographic rasters run without an override

## 0.3.0 — 2026-07-21

- Add optional per-input CRS overrides for the raster and the zones, in both the
  dialog and the Processing algorithm. An override reinterprets (assigns) a
  layer's coordinate system for files whose CRS is missing or wrong; it never
  resamples the raster
- Show the analysis CRS in the dialog and log it during Processing runs, making
  it clear that the raster's CRS is the analysis CRS and the grid is untouched
- A projected raster CRS override now enables metric area output on a file that
  GDAL would otherwise read as unprojected

## 0.2.1 — 2026-07-19

- Remove the custom dialog stylesheet, which hardcoded white panels without
  setting a text colour and left every label unreadable on dark QGIS themes
- Rebuild the dialog from unstyled Qt widgets so it follows the active QGIS
  theme: form layouts, plain group boxes, and QGIS's own collapsible group box
  for the advanced settings
- Derive secondary text from the theme's disabled colour and pick the validation
  error red from the current background brightness

## 0.2.0 — 2026-07-19

- Support QGIS 4 and Qt6 alongside QGIS 3.34+ through a single compatibility
  layer; `supportsQt6` and an explicit `qgisMaximumVersion` are now declared
- Rasterize each window once per overlap pass instead of once per band chunk
  (up to ~1.5x faster when the band chunk size is small)
- Skip accumulating minimum, maximum, standard deviation and nodata counts when
  those statistics were not requested
- Replace the naive `E[x^2] - E[x]^2` variance with Chan's parallel merge; the
  old form lost most of its precision on large-magnitude rasters and could
  report a standard deviation that was wrong by several percent
- Report a clear error for non-GDAL raster layers instead of failing inside GDAL
- Treat NULL ID and name fields consistently under both Qt5 and Qt6 bindings
- Make translatable strings use placeholders so they can actually be translated
- Derive the packaged file list and version from the working tree and
  `metadata.txt` so releases cannot silently omit a module
- Add GDAL-backed engine tests covering nodata, tiling, band chunking, overlap,
  area units, cancellation and variance precision

## 0.1.1 — 2026-07-18

- Added an explicit Processing algorithm translation helper for QGIS 4
- Fixed provider startup on QGIS 4.2

## 0.1.0 — 2026-07-18

- Initial QGIS Processing provider and graphical workflow
- Tile-based multiband GDAL/NumPy processing engine
- Correct handling of multipart and overlapping polygon zones
- Long-format CSV with seven continuous statistics
- Pixel and projected-area units
- Background execution, progress, cancellation, and atomic output
