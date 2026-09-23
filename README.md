# geomosaic

[![CI](https://github.com/LambdaGeo/geomosaic/actions/workflows/ci.yml/badge.svg)](https://github.com/LambdaGeo/geomosaic/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docs](https://img.shields.io/badge/docs-lambdageo.github.io%2Fgeomosaic-teal)](https://lambdageo.github.io/geomosaic/)

> **Status: Alpha.** The API is small and tested, but may still change before 1.0.

**geomosaic** assembles a set of raster tiles into a single, validated
logical grid, written as a GDAL Virtual Raster (`.vrt`). The VRT copies
no pixels: it is a small XML file that references the original tiles,
and any GDAL-based reader (`rasterio`, QGIS, `gdal_translate`) opens it
like one ordinary GeoTIFF, including windows that cross tile boundaries.

It needs only [rasterio](https://rasterio.readthedocs.io), not the GDAL
Python bindings (`osgeo`), so it installs with plain `pip`.

## When to use it

Large rasters rarely arrive as one file:

- **Google Earth Engine exports** of big areas are split into tiles,
  often with several products or years written to the same folder;
- **national datasets** (DEMs, land cover, imagery) are distributed as
  map sheets;
- **processing pipelines** write their output tile by tile.

Before any analysis or model can use such data, the tiles have to be
put back together, and it has to be clear that they actually fit:
same CRS, same resolution, same data type, and aligned on one pixel
grid, with no tile silently hiding another. geomosaic does exactly that
step and nothing else: it knows nothing about execution engines,
blocks or halos, so it fits in front of any raster workflow.

It is also the data-preparation step for spatial models in the
[DisSModel](https://github.com/DisSModel) ecosystem: the VRT is the
input grid that [`haloexec`](https://github.com/DisSModel/haloexec)
reads block by block for large-scale cellular automata, with no special
integration (for `haloexec`, the VRT is just another raster file).

## Installation

```bash
pip install "geomosaic @ git+https://github.com/LambdaGeo/geomosaic.git"
```

For development:

```bash
git clone https://github.com/LambdaGeo/geomosaic.git
cd geomosaic
pip install -e ".[dev]"
pytest
```

## Quick start

```python
from geomosaic import discover_tiles, build_mosaic_contract, write_vrt

tiles = discover_tiles("data/export/", pattern="landcover_2020*")
contract = build_mosaic_contract(tiles)   # validates the tile set, derives positions
vrt_path = write_vrt(contract, "data/landcover_2020.vrt")

import rasterio
with rasterio.open(vrt_path) as ds:       # opens like a single GeoTIFF
    print(ds.width, ds.height, ds.crs)
```

`examples/tiled_export.py` runs the whole flow on a synthetic tiled
export (two products in one folder, an empty grid cell, a partial tile
at the edge):

```bash
python examples/tiled_export.py
```

## How it works

The workflow has three functions, and the order is enforced by types:
`write_vrt` only accepts a `MosaicContract`, which only
`build_mosaic_contract` produces, so a VRT cannot be written for a tile
set that was not validated.

| Function | What it does |
| --- | --- |
| `discover_tiles(directory, pattern="*")` | Lists the `.tif`/`.tiff` files in a directory (case-insensitive), optionally filtered by a glob pattern, e.g. to pick one product out of a mixed folder. |
| `build_mosaic_contract(paths)` | Reads the metadata of every tile, runs the checks below, and derives each tile's pixel offset in the mosaic from the tile's **own geotransform**, never from its file name. |
| `write_vrt(contract, path, bands=1, relative_paths=False)` | Writes the VRT. `bands` selects one or several source bands; `relative_paths=True` makes the VRT portable when the folder is moved or shared. |

`inspect_tile(path)` returns the metadata of a single file.

### Checks made by `build_mosaic_contract`

In order, failing fast on the first violation with a message naming
the file(s) involved:

1. the list of tiles is not empty;
2. all tiles share the same **CRS**;
3. no tile is rotated, all are north-up, and all share the same
   **pixel size**;
4. all tiles share the same **data type**, **nodata** value and
   **band count**;
5. every tile lands on an **integer pixel offset** of the mosaic grid,
   which catches sub-pixel misalignment (reprojection error, wrong
   origin) that a check based on file names would never see;
6. **no two tiles overlap.** In a VRT, where tiles overlap the one
   listed last wins, so mixing two products or years that share a grid
   would silently drop data. Pass `allow_overlap=True` when overlaps
   are intended.

Each check has a test proving that it fires (`tests/test_core.py`).

## Materializing a GeoTIFF (optional)

A `.vrt` opens anywhere a GeoTIFF does, so most workflows can use it
directly. When a real file is needed (for example, to share the mosaic
with someone who only accepts GeoTIFF), convert the VRT with GDAL:

```bash
gdal_translate -of COG -co COMPRESS=LZW -co BIGTIFF=YES \
  data/landcover_2020.vrt data/landcover_2020.tif
```

This is deliberately outside geomosaic, which only builds the mosaic.

## Documentation

Full documentation — concepts, every validation check, recipes and the API
reference — is at **<https://lambdageo.github.io/geomosaic/>** (source in
[`docs/`](docs/)).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Guidance for AI coding assistants is in [CLAUDE.md](CLAUDE.md).

## License

MIT. See [LICENSE](LICENSE).
