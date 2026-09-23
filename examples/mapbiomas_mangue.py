"""
End-to-end example: assemble multi-band temporal series from MapBiomas GEE exports.

Real-world Google Earth Engine exports frequently contain:
- Multiple temporal products (e.g. decadal annual series of land cover) and
  summary products stored in the same export directory.
- Multi-band tiles (e.g. 10 bands per tile representing 10 consecutive years).
- Different data types (e.g. uint8 for annual classifications vs int16 for transitions).
- Spatial footprints partitioned into tiles with GEE offset suffixes
  (e.g. ``<product>-<row_offset>-<col_offset>.tif``).

This example demonstrates how ``geomosaic``:
1. Rejects mixed directories when products share the grid (inconsistent dtype or overlaps).
2. Builds validated mosaics for each temporal series using ``pattern=...``.
3. Generates multi-band VRTs preserving all years (or selecting specific years).
4. Seamlessly reads spatial windows across tile boundaries.

Usage:
  # Demo mode with synthetic MapBiomas-like export (self-contained, used by CI):
  python examples/mapbiomas_mangue.py

  # Real data mode (pointing to a local MapBiomas export directory):
  python examples/mapbiomas_mangue.py /path/to/mapbiomas_historico
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

from geomosaic import build_mosaic_contract, discover_tiles, write_vrt

SYNTH_TILE_SIZE = 128
SYNTH_PX_SIZE = 30.0  # 30m resolution (Landsat-like)
SYNTH_ORIGIN = (5_582_190.0, 10_043_700.0)  # EPSG:5880 (Polyconic)


def _create_synthetic_dataset(directory: Path) -> list[tuple[str, str, int]]:
    """Create a synthetic MapBiomas-like export folder with 3 products."""
    rng = np.random.default_rng(2024)
    layout = [(0, 0), (0, 1), (1, 0), (1, 1)]

    products = [
        ("mangue_1985_1994", 10, "uint8", 255),
        ("mangue_1995_2004", 10, "uint8", 255),
        ("resumo_transicoes", 13, "int16", -9999),
    ]

    for prod_name, bands, dtype, nodata in products:
        for r, c in layout:
            height = SYNTH_TILE_SIZE if r == 0 else SYNTH_TILE_SIZE - 28  # partial bottom tile
            width = SYNTH_TILE_SIZE
            if dtype == "uint8":
                data = rng.integers(1, 10, size=(bands, height, width), dtype=dtype)
            else:
                data = rng.integers(0, 100, size=(bands, height, width), dtype=dtype)

            y_off = r * SYNTH_TILE_SIZE * SYNTH_PX_SIZE
            x_off = c * SYNTH_TILE_SIZE * SYNTH_PX_SIZE
            transform = from_origin(
                SYNTH_ORIGIN[0] + x_off,
                SYNTH_ORIGIN[1] - y_off,
                SYNTH_PX_SIZE,
                SYNTH_PX_SIZE,
            )
            # GEE style naming: <prefix>-<row_offset:010d>-<col_offset:010d>.tif
            filename = (
                f"br_{prod_name}-"
                f"{r * SYNTH_TILE_SIZE:010d}-{c * SYNTH_TILE_SIZE:010d}.tif"
            )
            with rasterio.open(
                directory / filename,
                "w",
                driver="GTiff",
                height=height,
                width=width,
                count=bands,
                dtype=dtype,
                crs="EPSG:5880",
                transform=transform,
                nodata=nodata,
            ) as dst:
                dst.write(data)

    return [(name, f"*{name}*", bands) for name, bands, _, _ in products]


def run_pipeline(data_dir: Path, output_dir: Path, product_specs: list[tuple[str, str, int]] | None = None) -> None:
    """Run validation, contract derivation and VRT generation for MapBiomas products."""
    print(f"Reading rasters from: {data_dir}")

    # Step 1: Check if tiles form a single product or a mixed export
    all_tiles = discover_tiles(data_dir)
    print(f"Total tiles in directory: {len(all_tiles)}")
    try:
        contract_all = build_mosaic_contract(all_tiles)
        print(f"All {len(all_tiles)} tiles form a single consistent mosaic: "
              f"{contract_all.mosaic_width} x {contract_all.mosaic_height} pixels ({contract_all.band_count} bands).\n")
    except ValueError as err:
        print(f"Safety check passed — mixed product directory safely rejected:\n  {err}\n")

    # Step 2: Auto-detect or use defined products
    if product_specs is None:
        # Detect common MapBiomas decade series & summary products in directory
        known_patterns = [
            ("mangue_1985_1994", "*mangue_1985_1994*"),
            ("mangue_1995_2004", "*mangue_1995_2004*"),
            ("mangue_2005_2014", "*mangue_2005_2014*"),
            ("mangue_2015_2024", "*mangue_2015_2024*"),
            ("resumo_transicoes", "*resumo_transicoes*"),
        ]
        product_specs = []
        for name, pattern in known_patterns:
            matching = list(data_dir.glob(f"{pattern}.tif")) + list(data_dir.glob(f"{pattern}.tiff"))
            if matching:
                product_specs.append((name, pattern, -1))

    if not product_specs:
        print("No matching MapBiomas product patterns found in directory.")
        return

    # Step 3: Build a validated mosaic and VRT for each product
    for name, pattern, _ in product_specs:
        tiles = discover_tiles(data_dir, pattern=pattern)
        contract = build_mosaic_contract(tiles)

        print(f"Product: {name}")
        print(f"  Tiles found:    {len(tiles)}")
        print(f"  Mosaic grid:    {contract.mosaic_width} x {contract.mosaic_height} pixels")
        print(f"  CRS:            {contract.crs}")
        print(f"  Bands:          {contract.band_count}")
        print(f"  Dtype / Nodata: {contract.dtype} / {contract.nodata}")

        # Write multi-band VRT preserving all source bands
        vrt_path = output_dir / f"{name}.vrt"
        all_bands = list(range(1, contract.band_count + 1))
        write_vrt(contract, vrt_path, bands=all_bands, relative_paths=True)
        print(f"  Generated VRT:  {vrt_path.resolve()}")

        # Step 4: Validate reading across tile boundaries
        with rasterio.open(vrt_path) as ds:
            # Read a small window near the first tile boundary
            test_tile = contract.tiles[0]
            read_x = max(0, test_tile.width - 5)
            read_y = max(0, test_tile.height - 5)
            sample_w = min(10, ds.width - read_x)
            sample_h = min(10, ds.height - read_y)

            win = Window(read_x, read_y, sample_w, sample_h)
            data = ds.read(window=win)
            print(f"  Read window crossing boundary ({win}): shape {data.shape}, bands {ds.count}\n")

    print(f"Done! All VRT mosaics saved to:\n  {output_dir.resolve()}\n")
    print("Tip: You can open the .vrt file directly in QGIS, gdalinfo, or with rasterio.open().")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble MapBiomas GEE tiled exports into validated multi-band VRTs."
    )
    parser.add_argument(
        "data_dir",
        nargs="?",
        default=None,
        help="Path to directory containing MapBiomas GeoTIFF tiles (defaults to bundled sample in examples/data/).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=None,
        help="Directory to save generated .vrt files (defaults to the data directory).",
    )
    args = parser.parse_args()

    default_sample_dir = Path(__file__).parent / "data" / "mapbiomas_mangue"

    if args.data_dir and Path(args.data_dir).is_dir():
        real_path = Path(args.data_dir).resolve()
        output_dir = Path(args.output_dir).resolve() if args.output_dir else real_path
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"--- Running MapBiomas pipeline with dataset at {real_path} ---")
        print(f"Output directory for VRTs: {output_dir}\n")
        run_pipeline(real_path, output_dir)
    elif default_sample_dir.is_dir() and any(default_sample_dir.glob("*.tif")):
        output_dir = Path(args.output_dir).resolve() if args.output_dir else default_sample_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"--- Running with bundled real MapBiomas sample tiles ({default_sample_dir.name}) ---")
        print(f"Output directory for VRTs: {output_dir}\n")
        run_pipeline(default_sample_dir, output_dir)
    else:
        print("--- Running self-contained synthetic demo ---")
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            export_dir = tmp_path / "export"
            export_dir.mkdir()
            products = _create_synthetic_dataset(export_dir)
            vrt_dir = Path(args.output_dir).resolve() if args.output_dir else tmp_path / "vrt_output"
            vrt_dir.mkdir(parents=True, exist_ok=True)
            run_pipeline(export_dir, vrt_dir, products)


if __name__ == "__main__":
    main()
