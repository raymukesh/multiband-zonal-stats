# Continuous & Categorical

Pick the mode that matches your raster. The dialog exposes them as two tabs, and
the Processing Toolbox as two algorithms.

!!! warning "Why the split matters"
    The mean of land-cover codes `{1=water, 2=forest, 5=urban}` is a meaningless
    number. Categorical mode summarises the **class composition** instead — and a
    raster with more than 65,536 distinct values is rejected as "actually continuous".

## Continuous

For measured quantities where averaging is meaningful (elevation, temperature,
reflectance, NDVI).

**Statistics:** `count`, `nodata`, `min`, `max`, `sum`, `mean`, `stddev`
(population standard deviation, divided by *n*, matching QGIS's own tool).

### Long layout

One row per polygon **and** band:

```text
fid,polygon_id,polygon_name,band_index,band_name,count,mean,stddev
1,101,North field,1,ndvi,4210,0.62,0.11
2,101,North field,2,ndvi_may,4210,0.58,0.09
3,102,South field,1,ndvi,3980,0.71,0.08
```

### Wide layout

One row per polygon; each band's stats become columns prefixed with that band's
own name:

```text
fid,polygon_id,polygon_name,ndvi_mean,ndvi_stddev,ndvi_may_mean,ndvi_may_stddev
1,101,North field,0.62,0.11,0.58,0.09
2,102,South field,0.71,0.08,0.66,0.07
```

## Categorical

For discrete class codes (land cover, soil type, zoning). Values are read as
**integer class codes** (floating-point rasters are rounded to the nearest integer).

**Statistics:** `majority` (most common class), `minority` (least common class),
`variety` (distinct class count), plus `count` and `nodata`. Ties on
majority/minority resolve to the lowest class code.

### Summary layout

One row per polygon and band:

```text
fid,polygon_id,polygon_name,band_index,band_name,majority,minority,variety,count
1,101,North field,1,landcover,3,4,4,16
2,102,South field,1,landcover,2,1,3,16
```

### Class breakdown layout

One row per polygon, band **and** class, with the pixel count and fraction:

```text
fid,polygon_id,polygon_name,band_index,band_name,class,pixel_count,fraction
1,101,North field,1,landcover,1,4,0.250
2,101,North field,1,landcover,2,4,0.250
3,101,North field,1,landcover,3,5,0.312
4,101,North field,1,landcover,4,3,0.188
```

### Class counts (wide) layout

One row per polygon and band, with one `class_<code>` column of pixel counts:

```text
fid,polygon_id,polygon_name,band_index,band_name,class_1,class_2,class_3,class_4
1,101,North field,1,landcover,4,4,5,3
2,102,South field,1,landcover,7,4,1,4
```

!!! info "Shared columns"
    The class columns are the **union** of classes across all zones, so every row
    shares the same columns — a class absent from a zone is simply `0`.
