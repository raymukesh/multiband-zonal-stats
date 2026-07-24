# Features

## Two analysis modes

The plugin ships as **two Processing algorithms** and **two dialog tabs**, so the
analysis kind is always an explicit choice.

| | Continuous | Categorical |
|---|---|---|
| **For** | measured values (elevation, NDVI) | class codes (land cover, soil) |
| **Statistics** | count, nodata, min, max, sum, mean, stddev | majority, minority, variety, count, nodata |
| **Layouts** | Long, Wide | Summary, Class breakdown, Class counts |

See [Continuous & Categorical](modes.md) for the full details and examples.

## Multiband in one pass

Every selected band is aggregated in a single tiled sweep of the raster. Band
selections accept `all`, ranges like `1-8`, and lists like `1,3,7`.

## Output that's easy to work with

<div class="grid cards" markdown>

-   :material-key:{ .lg .middle } **Unique `fid`**

    ---

    Every row starts with a 1-based `fid` — a stable primary key for joins and
    database imports, since `polygon_id` repeats across bands and classes.

-   :material-decimal:{ .lg .middle } **Decimal places**

    ---

    Keep full precision, or fix numeric cells to `0–15` decimals for clean,
    spreadsheet-friendly numbers.

-   :material-tag-text:{ .lg .middle } **Band names**

    ---

    Columns keep the raster's own band names, with an optional common **prefix**
    and **suffix** (e.g. `2020_ndvi`).

-   :material-atom:{ .lg .middle } **Atomic CSV**

    ---

    Output is written to a temporary file and renamed on success — a cancelled or
    failed run never leaves a partial CSV.

</div>

## Exclude values (extra nodata)

Type a comma-separated list of pixel values into **Exclude values** to drop them
from the calculation, just like nodata — for example `0` for out-of-boundary
pixels in a categorical raster, or `0, 255`. They apply to every band, on top of
the raster's declared nodata.

## Histogram preview

The **Histogram…** button previews a band's distribution in a popup — binned
values (continuous) or one bar per class (categorical). It describes the whole
raster (read decimated for speed) and is a quick way to decide which values to
exclude. Drawn with Qt directly, so it needs no extra Python packages.

## Coordinate systems

The raster's CRS is the analysis CRS and the raster is **never resampled**. Zones
are transformed into the raster CRS automatically. Optional **Raster CRS** and
**Zones CRS** overrides *assign* a CRS to a layer whose declared CRS is missing or
wrong — they reinterpret coordinates, they do not reproject data.

## Robust and fast

- **Overlapping polygons** — shared pixels are counted for every overlapping zone
  (overlaps are rasterized in separate passes).
- **Multipart features** keep one zone ID; holes are respected.
- **Per-band nodata** is honored, and the population standard deviation uses a
  numerically stable parallel-variance merge.
- **Bounded memory** via configurable **tile size** and **band chunk** settings,
  plus **pixel-centre** or **all-touched** inclusion.

## Everywhere in QGIS

Available from the Raster menu, the toolbar, the graphical dialog, the Processing
Toolbox, and Model Builder — the dialog and Processing runs share the same engine.
