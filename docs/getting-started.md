# Quick Start

Produce a zonal-statistics CSV in four steps.

## 1. Open the tool

From **Raster → Multiband Zonal Stats**, the toolbar, or the Processing Toolbox.

## 2. Choose your layers

- **Multiband raster** — a file-based (GDAL) raster.
- **Polygon zones** — a polygon or multipolygon layer.
- Optionally set an **ID field** and **Name field** to label each zone in the output.

!!! info "Coordinate systems"
    The raster's CRS is the analysis CRS, and the raster is never resampled. Zones
    are transformed into the raster CRS automatically. If a layer's CRS is missing
    or wrong, set a **Raster CRS** or **Zones CRS** override — this *assigns* a CRS,
    it does not reproject the data.

## 3. Pick the mode and statistics

Choose the tab that matches your raster:

=== "Continuous raster"

    Measured values (elevation, temperature, NDVI). Select from **count, nodata,
    min, max, sum, mean, standard deviation**, and a **Long** or **Wide** layout.

=== "Categorical raster"

    Discrete class codes (land cover, soil type). Select from **majority, minority,
    variety**, and a **Summary**, **Class breakdown**, or **Class counts** layout.

Set the **Raster bands** (`all`, `1-8`, or `1,3,7`), and optionally an
**Exclude values** list (e.g. `0`) to drop pixels from the calculation.

!!! tip "Preview first"
    Click **Histogram…** to see the distribution of a band before you run — a quick
    way to spot values worth excluding (for example a spike at `0`).

## 4. Save and run

Pick an **output CSV**, optionally set the **decimal places**, then click
**Run analysis**. The job runs in the background with progress and a safe Cancel,
and the CSV is written atomically — a cancelled or failed run leaves no partial file.

When it finishes, click **Open result** to view the CSV.

## What you get

Every output row begins with a unique `fid`. For example, continuous **Long** output:

```text
fid,polygon_id,polygon_name,band_index,band_name,count,mean,stddev
1,101,North field,1,ndvi,4210,0.62,0.11
2,101,North field,2,ndvi_may,4210,0.58,0.09
```

See [Output & Options](output.md) for every layout, and
[Continuous & Categorical](modes.md) for choosing a mode.
