"""
End-to-end example: turn a tiled export into one analysis-ready mosaic.

Large rasters exported from Google Earth Engine (or split into map
sheets by a data provider) arrive as many GeoTIFF tiles, often with
several products sharing the same grid in one directory, and a partial
tile where the export was clipped to the area of interest. This script
builds such a directory with synthetic data, then:

1. selects one product with ``discover_tiles(..., pattern=...)``;
2. validates the tile set with ``build_mosaic_contract``;
3. writes a VRT and reads a window that crosses tile boundaries.

It also shows the check that stops a mixed directory (two products on
the same grid) from producing a mosaic that silently drops data.

Run:  python examples/tiled_export.py
"""

import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

from geomosaic import build_mosaic_contract, discover_tiles, write_vrt

TILE = 256          # tile size in pixels
PX = 30.0           # pixel size in metres (Landsat-like)
ORIGIN = (600_000.0, 9_720_000.0)  # upper-left corner, EPSG:31983 (SIRGAS 2000 / UTM 23S)


def make_export(directory: Path) -> None:
    """Write a 2x3 tiled export of two products (land cover for 2010 and
    2020). The bottom-right tile is partial and one grid cell is empty."""
    rng = np.random.default_rng(42)
    layout = {(0, 0): TILE, (0, 1): TILE, (0, 2): TILE, (1, 0): TILE, (1, 1): 100}  # (1, 2) empty
    for year in (2010, 2020):
        for (r, c), height in layout.items():
            data = rng.integers(1, 6, size=(height, TILE), dtype="uint8")
            transform = from_origin(ORIGIN[0] + c * TILE * PX, ORIGIN[1] - r * TILE * PX, PX, PX)
            # GEE-style names: <product>-<row offset>-<col offset>.tif
            name = f"landcover_{year}-{r * TILE:010d}-{c * TILE:010d}.tif"
            with rasterio.open(
                directory / name, "w", driver="GTiff", height=height, width=TILE, count=1,
                dtype="uint8", crs="EPSG:31983", transform=transform, nodata=0,
            ) as dst:
                dst.write(data, 1)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tiles_dir = Path(tmp) / "export"
        tiles_dir.mkdir()
        make_export(tiles_dir)

        # A directory with two products on the same grid is rejected
        # instead of producing a VRT that silently keeps only one of them.
        try:
            build_mosaic_contract(discover_tiles(tiles_dir))
        except ValueError as err:
            print(f"Mixed directory rejected:\n  {err}\n")

        tiles = discover_tiles(tiles_dir, pattern="landcover_2020*")
        contract = build_mosaic_contract(tiles)
        print(f"{len(tiles)} tiles -> mosaic of {contract.mosaic_height} x {contract.mosaic_width} pixels")
        for tile, offset in zip(contract.tiles, contract.tile_offsets, strict=True):
            print(f"  {tile.path.name:<40} offset (row, col) = {offset}")

        vrt = write_vrt(contract, Path(tmp) / "landcover_2020.vrt", relative_paths=True)
        with rasterio.open(vrt) as ds:
            window = ds.read(1, window=Window(TILE - 5, TILE - 5, 10, 10))  # crosses 4 tiles
            empty = ds.read(1, window=Window(2 * TILE, TILE, 10, 10))       # cell with no tile
            print(f"\nVRT {vrt.name}: {ds.width} x {ds.height}, {ds.crs}, nodata={ds.nodata}")
            print(f"  window across 4 tiles: {window.shape}, classes {sorted(int(v) for v in np.unique(window))}")
            print(f"  empty grid cell is nodata: {bool(np.all(empty == ds.nodata))}")


if __name__ == "__main__":
    main()
