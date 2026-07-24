# Output & Options

## The `fid` column

Every output row — in every layout — begins with a unique **1-based `fid`**,
assigned in write order. It's the stable identity of each row: `polygon_id`
repeats across bands (and across classes in the breakdown), so `fid` is the real
primary key for joins, database imports, or referencing a specific row.

## Decimal places

Control the decimals of numeric cells:

- **Full precision** (the default) keeps every digit.
- Set **0–15** to fix the decimals — only floating-point cells (statistics,
  fraction) are formatted; integers stay clean.

!!! tip "Numbers look like text in Excel?"
    That's usually a locale mismatch — the CSV uses a `.` decimal but your Excel
    expects a `,`. Import via **Data → From Text/CSV** and set the decimal
    separator, or reduce the output to a few decimal places.

## Band names

Output columns keep the raster's own band descriptions (falling back to
`band_001`, `band_002`, … when unnamed). Add a common **prefix** and **suffix** to
label a run of files — the raster's bands are never renamed, only the CSV labels.

| Band name | Prefix | Suffix | Wide column (sanitised) |
|---|---|---|---|
| NDVI | `2020_` | — | `2020_ndvi_mean` |
| Surface Temp | — | — | `surface_temp_mean` |
| band 1 | — | `_dry` | `band_1_dry_mean` |

Wide-format headers are lowercased, with spaces and punctuation turned into
underscores and any collisions numbered.

## Exclude values (extra nodata)

Type a comma-separated list into **Exclude values** to drop those pixel values
from the calculation, just like nodata:

- Categorical rasters often use `0` for **out-of-boundary** pixels — exclude it so
  it doesn't dominate the class counts.
- Multiple sentinels work too, e.g. `0, 255`.

They apply to **every band**, on top of the raster's declared nodata. Use the
[histogram](#histogram-preview) first to spot which value to exclude.

## Histogram preview

The **Histogram…** button opens a quick preview of a band's distribution:

- Pick any **band**, and toggle **Continuous** (binned values, with an adjustable
  bin count) or **Categorical** (one bar per class). It opens on your active tab's
  mode.
- It describes the **whole raster** (it ignores the polygons) and reads the band
  **decimated** for speed, so even large rasters preview promptly.
- Summary stats sit beneath the chart: min / max / mean / sd for continuous, or the
  class count and majority for categorical.

## Advanced settings

| Setting | What it does |
|---|---|
| **Tile size** | Raster window read at a time. Larger = fewer reads, more memory. |
| **Band chunk** | Bands held in memory per pass. Lower it for many-band rasters on tight memory. |
| **All touched** | Include every pixel a polygon touches, not just pixel centres (slower). |

## Atomic output

The CSV is written to a temporary file and renamed on success, so a cancelled or
failed run never publishes a partial result. Output is UTF-8.
