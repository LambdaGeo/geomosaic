"""geomosaic: assemble raster tiles into one validated virtual mosaic (VRT)."""

from .core import (
    MosaicContract,
    TileInfo,
    build_mosaic_contract,
    discover_tiles,
    inspect_tile,
    write_vrt,
)

__all__ = [
    "MosaicContract",
    "TileInfo",
    "build_mosaic_contract",
    "discover_tiles",
    "inspect_tile",
    "write_vrt",
]

__version__ = "0.1.0"
