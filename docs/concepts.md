# Concepts

## Three steps, enforced by types

```python
tiles = discover_tiles(directory)        # list[Path]
contract = build_mosaic_contract(tiles)  # MosaicContract (validated)
write_vrt(contract, "mosaic.vrt")        # Path
```

`write_vrt` accepts only a `MosaicContract`, and only
`build_mosaic_contract` produces one. A VRT therefore cannot be written
for a tile set that was not validated: the order is a type dependency,
not a convention the caller has to remember.

## Tile positions come from the geotransform

Each tile's position in the mosaic is derived from the tile's **own
geotransform**, never from its file name. File names are unreliable
(every provider has its own convention, and some have none), while the
geotransform says exactly where the pixels are.

For a north-up raster, the geotransform is
`(pixel_width, 0, x_origin, 0, -pixel_height, y_origin)`, where
`(x_origin, y_origin)` is the upper-left corner. geomosaic:

1. takes the mosaic origin as the upper-left corner of the whole set —
   the minimum `x_origin` and the maximum `y_origin` over all tiles;
2. computes each tile's offset in pixels:

    ```
    col = (x_origin_tile - x_origin_mosaic) / pixel_width
    row = (y_origin_mosaic - y_origin_tile) / pixel_height
    ```

3. requires both to be **integers** (within `offset_tolerance_px`,
   default 0.001 px). A fractional offset means the tile does not sit on
   the same pixel grid as the others — a sub-pixel misalignment that a
   check based on file names would never see;
4. sizes the mosaic to the furthest tile edge in each direction.

Gaps are allowed: a grid cell with no tile is simply not covered by any
source, and reads as nodata.

## What the VRT contains

A VRT is a short XML description. For two 256 × 256 tiles side by
side, `write_vrt(contract, "landcover_2020.vrt", relative_paths=True)`
writes:

```xml
<VRTDataset rasterXSize="512" rasterYSize="256">
  <SRS>EPSG:31983</SRS>
  <GeoTransform>600000.0, 30.0, 0.0, 9720000.0, 0.0, -30.0</GeoTransform>
  <VRTRasterBand dataType="Byte" band="1">
    <NoDataValue>0</NoDataValue>
    <SimpleSource>
      <SourceFilename relativeToVRT="1">tiles/landcover_2020-0.tif</SourceFilename>
      <SourceBand>1</SourceBand>
      <SrcRect xOff="0" yOff="0" xSize="256" ySize="256"/>
      <DstRect xOff="0" yOff="0" xSize="256" ySize="256"/>
    </SimpleSource>
    <SimpleSource>
      <SourceFilename relativeToVRT="1">tiles/landcover_2020-1.tif</SourceFilename>
      <SourceBand>1</SourceBand>
      <SrcRect xOff="0" yOff="0" xSize="256" ySize="256"/>
      <DstRect xOff="256" yOff="0" xSize="256" ySize="256"/>
    </SimpleSource>
  </VRTRasterBand>
</VRTDataset>
```

- `rasterXSize` / `rasterYSize` and `GeoTransform` describe the mosaic
  grid derived above.
- Each `SimpleSource` places one tile: `DstRect` is its offset in the
  mosaic, `SourceBand` the band read from the tile.
- One `VRTRasterBand` is written per entry of `bands` (default: band 1).
- The data type and nodata come from the tiles, which the contract has
  already checked to be identical.

The XML is written in pure Python, so the GDAL Python bindings
(`osgeo.gdal.BuildVRT`) are not needed. Reading the VRT still goes
through GDAL, which ships inside the rasterio wheels.

## Absolute or relative paths

By default the VRT references each tile by **absolute** path: it works
from anywhere on the same machine, but breaks if the tiles move.

With `relative_paths=True`, tiles are referenced relative to the VRT's
location. Keep the VRT next to (or above) the tiles, and the pair can be
moved, zipped or shared as a unit.

## What geomosaic does not do

- **It does not copy or resample pixels.** Tiles must already share a
  CRS, resolution and pixel grid; reprojection belongs upstream (e.g.
  `gdalwarp`, or the export itself).
- **It does not materialize a GeoTIFF.** A VRT opens anywhere a
  GeoTIFF does; see [Recipes](recipes.md#materializing-a-geotiff) when a
  real file is needed.
- **It has no notion of blocks, halos or models.** Consumers such as
  `haloexec` read the VRT like any raster.
