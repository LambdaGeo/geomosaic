"""
End-to-end example: assemble multi-band temporal series from MapBiomas GEE exports.

Large-scale exports from Google Earth Engine (like MapBiomas) often provide:
- Spatial grids partitioned into tiles (<prefix>-<row_offset>-<col_offset>.tif).
- Multi-band tiles (e.g. 10 bands per tile representing 10 consecutive years).
- Multiple products in the same folder (e.g. several decadal series or transition summaries).

This script demonstrates how `geomosaic`:
1. Safely discovers and validates tiles using `discover_tiles` and `build_mosaic_contract`.
2. Automatically derives the 2D grid layout from geotransforms (ignoring file name suffixes).
3. Writes a portable, multi-band GDAL Virtual Raster (.vrt).
4. Reads spatial windows across tile boundaries.

Usage:
  python examples/mapbiomas_mangue.py                         # runs on bundled sample tiles
  python examples/mapbiomas_mangue.py /path/to/export_folder  # runs on a local export folder
"""

from __future__ import annotations

import argparse
from pathlib import Path

import rasterio
from rasterio.windows import Window

from geomosaic import build_mosaic_contract, discover_tiles, write_vrt

DEFAULT_DATA_DIR = Path(__file__).parent / "data" / "mapbiomas_mangue"

# Known MapBiomas products/series commonly exported together
PRODUCT_PATTERNS = [
    ("mangue_1985_1994", "*mangue_1985_1994*"),
    ("mangue_1995_2004", "*mangue_1995_2004*"),
    ("mangue_2005_2014", "*mangue_2005_2014*"),
    ("mangue_2015_2024", "*mangue_2015_2024*"),
    ("resumo_transicoes", "*resumo_transicoes*"),
]


def process_product(data_dir: Path, output_dir: Path, name: str, pattern: str) -> None:
    """Validate a tile set, write its multi-band VRT, and verify reading across tiles."""
    tiles = discover_tiles(data_dir, pattern=pattern)
    contract = build_mosaic_contract(tiles)

    print(f"\nProduct: {name}")
    print(f"  Tiles found:    {len(tiles)}")
    print(f"  Mosaic grid:    {contract.mosaic_width} x {contract.mosaic_height} pixels")
    print(f"  CRS:            {contract.crs}")
    print(f"  Bands:          {contract.band_count}")
    print(f"  Dtype / Nodata: {contract.dtype} / {contract.nodata}")

    # Write multi-band VRT (relative_paths=True keeps the folder portable)
    vrt_path = output_dir / f"{name}.vrt"
    all_bands = list(range(1, contract.band_count + 1))
    write_vrt(contract, vrt_path, bands=all_bands, relative_paths=True)
    print(f"  Generated VRT:  {vrt_path.resolve()}")

    # Verify that GDAL/rasterio reads seamlessly across tile boundaries
    with rasterio.open(vrt_path) as ds:
        ref_tile = contract.tiles[0]
        # Read a 10x10 window overlapping the first tile's corner/edge
        win = Window(max(0, ref_tile.width - 5), max(0, ref_tile.height - 5), 10, 10)
        data = ds.read(window=win)
        print(f"  Sample read crossing tile edge ({win}): shape {data.shape} ({ds.count} bands)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble MapBiomas GEE tiled exports into validated multi-band VRTs."
    )
    parser.add_argument(
        "data_dir",
        nargs="?",
        default=DEFAULT_DATA_DIR,
        type=Path,
        help=f"Directory with MapBiomas GeoTIFF tiles (default: {DEFAULT_DATA_DIR}).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=None,
        help="Directory to save generated .vrt files (defaults to data_dir).",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    output_dir = (args.output_dir or data_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Input directory:  {data_dir}")
    print(f"Output directory: {output_dir}")

    # Check which products exist in the directory
    active_products = [
        (name, pat) for name, pat in PRODUCT_PATTERNS
        if any(data_dir.glob(f"{pat}.tif")) or any(data_dir.glob(f"{pat}.tiff"))
    ]

    # If the directory doesn't match known MapBiomas names, process all files as one mosaic
    if not active_products:
        active_products = [("mosaic", "*")]

    # Demonstrate that geomosaic safely catches mixed products when ingesting unpartitioned
    all_tiles = discover_tiles(data_dir)
    print(f"Total tiles in folder: {len(all_tiles)}")
    try:
        build_mosaic_contract(all_tiles)
        print("  All tiles form a single compatible grid.")
    except ValueError as err:
        print(f"  Notice: Mixed folder detected and safely caught by geomosaic:\n    {err}")

    for name, pattern in active_products:
        process_product(data_dir, output_dir, name, pattern)

    print("\nDone! Open the generated VRT in QGIS or via rasterio.open().")


if __name__ == "__main__":
    main()
