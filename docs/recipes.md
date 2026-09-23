# Recipes

## A Google Earth Engine export with several products

Large GEE exports arrive as tiles named
`<description>-<row offset>-<col offset>.tif`, and it is common to
export several products or years into the same folder. Build one
mosaic per product, selected by name:

```python
from geomosaic import discover_tiles, build_mosaic_contract, write_vrt

for year in (1985, 2000, 2020):
    tiles = discover_tiles("export/", pattern=f"landcover_{year}*")
    contract = build_mosaic_contract(tiles)
    write_vrt(contract, f"export/landcover_{year}.vrt", relative_paths=True)
```

The offsets in the file names are ignored: positions come from each
tile's geotransform. Passing the whole folder at once would be rejected
by the [overlap check](validation.md#7-no-overlapping-tiles), since
every product repeats the same positions.

Exports clipped to an area of interest often have **empty grid cells**
(no tile) and **partial tiles** at the edges. Both are fine: empty cells
read as nodata, and partial tiles are placed by their own size.

## Assembling multi-band MapBiomas time series (GEE export)

Large-scale land cover initiatives like [MapBiomas](https://brasil.mapbiomas.org/)
export long time series as partitioned GeoTIFF grids. A single export directory
commonly holds several decadal series (e.g. 1985–1994, 1995–2004, 2005–2014, 2015–2024),
where each tile contains **10 bands** (one band per year), alongside auxiliary
transition products (e.g. 13 bands in `int16`).

Passing the whole directory at once is safely rejected because `geomosaic`
detects either differing `dtype` (between transition metrics and annual maps)
or overlapping footprints across the repeated grid:

```python
from pathlib import Path
from geomosaic import discover_tiles, build_mosaic_contract, write_vrt

data_dir = Path("data/mapbiomas_export/")

# Define the products sharing the export folder
products = [
    ("mangue_1985_1994", "*mangue_1985_1994*"),
    ("mangue_1995_2004", "*mangue_1995_2004*"),
    ("mangue_2005_2014", "*mangue_2005_2014*"),
    ("mangue_2015_2024", "*mangue_2015_2024*"),
    ("resumo_transicoes", "*resumo_transicoes*"),
]

for name, pattern in products:
    tiles = discover_tiles(data_dir, pattern=pattern)
    contract = build_mosaic_contract(tiles)

    # Preserve all decade bands (10 years) in the VRT
    vrt_path = write_vrt(
        contract,
        data_dir / f"{name}.vrt",
        bands=list(range(1, contract.band_count + 1)),
        relative_paths=True,
    )
    print(f"Created {vrt_path.name}: {contract.mosaic_width}x{contract.mosaic_height}, {contract.band_count} bands")
```

To read a single year across tile boundaries (for instance, 1990 is band 6 in the 1985–1994 series):

```python
import rasterio
from rasterio.windows import Window

with rasterio.open(data_dir / "mangue_1985_1994.vrt") as ds:
    # Read year 1990 (band 6) across tile borders seamlessly
    year_1990 = ds.read(6, window=Window(4000, 4000, 500, 500))
```

A complete end-to-end executable script supporting both demo simulation and real
datasets is provided in [`examples/mapbiomas_mangue.py`](https://github.com/LambdaGeo/geomosaic/blob/main/examples/mapbiomas_mangue.py).

## Multi-band mosaics

By default the VRT carries band 1 of each tile. Choose the source
bands, in the order you want them in the VRT:

```python
write_vrt(contract, "mosaic_rgb.vrt", bands=[3, 2, 1])  # e.g. reorder to RGB
write_vrt(contract, "band4.vrt", bands=4)                # a single band
```

All tiles have the same band count (checked by the contract), and
`write_vrt` rejects band numbers out of range.

## A portable VRT

```python
write_vrt(contract, "data/mosaic.vrt", relative_paths=True)
```

With relative paths, the folder holding the VRT and the tiles can be
moved, zipped or shared as a unit. Keep the VRT in the same directory as
the tiles or above them.

## Checking a tile set without writing anything

`build_mosaic_contract` is also a validator: run it to learn whether a
set of tiles fits, and where each one lands.

```python
contract = build_mosaic_contract(tiles)
print(contract.mosaic_height, contract.mosaic_width, contract.crs)
for tile, (row, col) in zip(contract.tiles, contract.tile_offsets):
    print(tile.path.name, row, col)
```

`inspect_tile(path)` returns the same metadata for a single file.

## Feeding a model with haloexec

[`haloexec`](https://github.com/DisSModel/haloexec) runs cellular
automata block by block over grids larger than RAM. It reads a VRT
exactly as it reads a GeoTIFF:

```python
from geomosaic import discover_tiles, build_mosaic_contract, write_vrt
from haloexec import MemmapRasterWorkspace, load_geotiff_into_workspace

contract = build_mosaic_contract(discover_tiles("export/", pattern="landcover_2020*"))
vrt = write_vrt(contract, "export/landcover_2020.vrt")

ws = MemmapRasterWorkspace.create(
    root="workspace/", shape=(contract.mosaic_height, contract.mosaic_width),
    arrays={"landcover": "uint8"}, block_h=512, block_w=512, halo=1,
)
load_geotiff_into_workspace(ws, vrt, [("landcover", "uint8", contract.nodata)])
```

Blocks that cross tile boundaries are read correctly — GDAL stitches the
tiles when the VRT is read. `haloexec`'s own test suite checks this
integration (`tests/test_geomosaic_integration.py`).

## Materializing a GeoTIFF

A `.vrt` opens anywhere a GeoTIFF does, so most workflows can use it
directly. When a real file is needed (for example, to share the mosaic
with someone who only accepts GeoTIFF), convert it with GDAL:

```bash
gdal_translate -of COG -co COMPRESS=LZW -co BIGTIFF=YES \
  data/landcover_2020.vrt data/landcover_2020.tif
```

This step is deliberately outside geomosaic, which only builds the
mosaic.
