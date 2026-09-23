# Validation checks

`build_mosaic_contract` reads the metadata of every tile (no pixel
data) and runs the checks below **in order**, failing fast on the first
violation with a `ValueError` that names the file(s) involved. Each
check has a test proving that it fires (`tests/test_core.py`).

| # | Check | Typical cause when it fails |
|---|-------|-----------------------------|
| 1 | The list is not empty | Wrong directory or pattern |
| 2 | Same CRS | A tile exported or reprojected separately |
| 3 | No rotation, north-up | Rotated or "south-up" source rasters |
| 4 | Same pixel size | Tiles from different resolutions |
| 5 | Same dtype, nodata, band count | Mixed products, or a tile re-saved with other options |
| 6 | Integer pixel offsets | Sub-pixel misalignment (reprojection, wrong origin) |
| 7 | No overlaps | Several products or dates on the same grid in one list |

## 1. Non-empty list

```text
ValueError: The list of tiles is empty.
```

`discover_tiles` already raises `FileNotFoundError` when nothing
matches, so this mostly catches lists built by hand.

## 2. Same CRS

```text
ValueError: Inconsistent CRS: b.tif has EPSG:4326, expected EPSG:31984 (from a.tif)
```

CRSs are compared as the strings rasterio reports. Reproject the odd
tile(s) to the common CRS before building the mosaic.

## 3. No rotation, north-up only

```text
ValueError: Rotated tiles are not supported: t.tif
ValueError: Only north-up rasters (negative pixel height) are supported: s.tif has pixel height 100.0
```

The offset arithmetic assumes the usual north-up layout (row 0 at the
top). A raster with a positive pixel height ("south-up") would
otherwise land at the wrong offset **silently**; it is rejected
instead. Fix with `gdalwarp` before mosaicking.

## 4. Same pixel size

```text
ValueError: Inconsistent resolution: b.tif has pixel size (30.0, -30.0), expected (100.0, -100.0)
```

Compared within `resolution_tolerance` (default `1e-6`, in CRS units).

## 5. Same dtype, nodata and band count

```text
ValueError: Inconsistent dtype: b.tif is float32, expected int16 (from a.tif)
ValueError: Inconsistent nodata: b.tif has 255.0, expected 0.0 (from a.tif)
ValueError: Inconsistent band count: b.tif has 2, expected 1 (from a.tif)
```

A VRT band has **one** data type and **one** nodata value. Without
these checks the VRT would take them from the first tile, and other
tiles would be read through it: a `float32` value of 2.7 in an `int16`
mosaic is read back as `3`, and a tile whose nodata differs has its
empty cells read as valid data. `NaN` nodata values are treated as
equal to each other.

## 6. Integer pixel offsets

```text
ValueError: Tile not aligned to the pixel grid: b.tif would land on a fractional offset (row=0.0000, col=10.3750); check its origin/reprojection.
```

Every tile must sit on the same pixel grid: its offset from the mosaic
origin, in pixels, must be a whole number (within
`offset_tolerance_px`, default `0.001`). A fractional offset usually
means the tile was reprojected or resampled on its own. See
[Concepts](concepts.md#tile-positions-come-from-the-geotransform).

## 7. No overlapping tiles

```text
ValueError: Overlapping tiles: landcover_2010-0.tif and landcover_2020-0.tif land on the same offset (0, 0). If the list mixes several products or dates on the same grid, select one (e.g. discover_tiles(directory, pattern=...)); pass allow_overlap=True to accept overlaps (the tile listed last wins).
```

In a VRT, where two sources overlap the one listed **last** wins, and
the other is hidden without any error. The most common way to get there
is a directory holding several products or years that share one grid —
a typical Google Earth Engine export — passed as a single list. The fix
is to select one product:

```python
tiles = discover_tiles("export/", pattern="landcover_2020*")
```

Tiles that only **touch** (share an edge) are not overlaps. When
overlaps are intended — e.g. tiles exported with a buffer, where either
copy of the shared cells is fine — pass `allow_overlap=True`; the order
of `paths` then decides which tile wins.

## Checks made by `write_vrt`

`write_vrt` also refuses inputs that would produce a VRT that only fails
later, when read:

```text
ValueError: Band 3 out of range: tiles have 1 band(s) (1-based).
ValueError: `bands` is empty.
ValueError: Unsupported dtype for VRT: <dtype>
```

Supported data types: `uint8`, `int8`, `uint16`, `int16`, `uint32`,
`int32`, `uint64`, `int64`, `float32`, `float64`, `complex64`,
`complex128`.
