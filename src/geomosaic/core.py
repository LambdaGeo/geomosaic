"""
Tile discovery, mosaic-contract validation and VRT construction.

geomosaic assembles a set of raster tiles (e.g. the pieces of a large
Google Earth Engine export, a national DEM split into sheets, or any
other tiled product) into a single logical grid, written as a GDAL
Virtual Raster (``.vrt``). The VRT copies no pixels: it is a small XML
file that references the original tiles, and any GDAL-based reader
(``rasterio.open``, QGIS, ``gdal_translate``) opens it like one
ordinary GeoTIFF, including windows that cross tile boundaries.

The position of each tile in the mosaic is derived from the tile's own
geotransform, never from its file name, and the tile set is validated
before anything is written (see :func:`build_mosaic_contract`).

geomosaic has no notion of execution engines, blocks or halos. It is a
data-preparation step that sits *before* any tool that consumes the
mosaic, such as the DisSModel ecosystem (``haloexec`` reads the VRT
block by block) or any other raster workflow.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import rasterio


@dataclass(frozen=True)
class TileInfo:
    """Metadata of one raster tile (no pixel data is read)."""

    path: Path
    height: int
    width: int
    crs: str | None
    transform: tuple  # affine 6-tuple (a, b, c, d, e, f), rasterio order
    band_count: int
    dtype: str
    nodata: float | None


@dataclass(frozen=True)
class MosaicContract:
    """A validated tile set: the derived mosaic grid plus the pixel
    offset of each tile inside it (computed from each file's
    geotransform, not from its name)."""

    mosaic_height: int
    mosaic_width: int
    mosaic_transform: tuple
    crs: str | None
    tiles: list[TileInfo]
    tile_offsets: list[tuple[int, int]]  # (row_offset, col_offset), same order as `tiles`

    @property
    def band_count(self) -> int:
        return self.tiles[0].band_count

    @property
    def dtype(self) -> str:
        return self.tiles[0].dtype

    @property
    def nodata(self) -> float | None:
        return self.tiles[0].nodata


def inspect_tile(path: str | Path) -> TileInfo:
    """Read the metadata of a raster file (no pixel data)."""
    path = Path(path)
    with rasterio.open(str(path)) as ds:
        dtypes = set(ds.dtypes)
        if len(dtypes) > 1:
            raise ValueError(f"Bands with different dtypes are not supported: {path} has {sorted(dtypes)}")
        return TileInfo(
            path=path,
            height=ds.height,
            width=ds.width,
            crs=str(ds.crs) if ds.crs else None,
            transform=tuple(ds.transform)[:6],
            band_count=ds.count,
            dtype=ds.dtypes[0],
            nodata=ds.nodata,
        )


def _same_nodata(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    if math.isnan(a) and math.isnan(b):
        return True
    return a == b


def _find_overlap(tiles: list[TileInfo], offsets: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Return the indices of the first pair of tiles whose footprints
    overlap on the mosaic grid, or None. Sweep over tiles sorted by
    column offset, so only horizontally overlapping candidates are
    compared."""
    order = sorted(range(len(tiles)), key=lambda i: offsets[i][1])
    active: list[int] = []
    for i in order:
        row_i, col_i = offsets[i]
        active = [j for j in active if offsets[j][1] + tiles[j].width > col_i]
        for j in active:
            row_j = offsets[j][0]
            if row_i < row_j + tiles[j].height and row_j < row_i + tiles[i].height:
                return (j, i) if j < i else (i, j)
        active.append(i)
    return None


def build_mosaic_contract(
    paths: Sequence[str | Path],
    resolution_tolerance: float = 1e-6,
    offset_tolerance_px: float = 1e-3,
    allow_overlap: bool = False,
) -> MosaicContract:
    """
    Validate that a list of raster files forms a consistent mosaic and
    derive the pixel position of each file in the mosaic grid.

    Checks, in order (fails fast on the first violation):

    - the list is not empty;
    - all tiles share the same CRS;
    - all tiles share the same pixel size (within ``resolution_tolerance``);
    - no tile is rotated, and all are north-up (negative pixel height);
    - all tiles share the same dtype, nodata value and band count;
    - each tile lands on an *integer* pixel offset of the mosaic grid
      (within ``offset_tolerance_px``), which catches sub-pixel
      misalignment (reprojection error, wrong origin) without relying
      on any file-naming convention;
    - no two tiles overlap, unless ``allow_overlap=True``. Overlapping
      tiles usually mean that several products or dates sharing the
      same grid were mixed in one list; in a VRT, the tile listed last
      would silently hide the others.

    Parameters
    ----------
    paths : sequence of str or Path
        Raster files to combine (see :func:`discover_tiles`).
    resolution_tolerance : float
        Maximum difference in pixel size between tiles, in CRS units.
    offset_tolerance_px : float
        Maximum distance, in pixels, from a tile's offset to the nearest
        integer pixel position.
    allow_overlap : bool
        Accept tiles whose footprints overlap. Where they do, the VRT
        returns the value of the tile that comes last in ``paths``.

    Returns
    -------
    MosaicContract

    Raises
    ------
    ValueError
        With a message naming the offending file(s) and the failed check.
    """
    if not paths:
        raise ValueError("The list of tiles is empty.")

    tiles = [inspect_tile(p) for p in paths]
    ref = tiles[0]

    for t in tiles:
        if t.crs != ref.crs:
            raise ValueError(f"Inconsistent CRS: {t.path} has {t.crs}, expected {ref.crs} (from {ref.path})")

    px_w_ref = ref.transform[0]
    px_h_ref = ref.transform[4]
    for t in tiles:
        if abs(t.transform[1]) > 1e-9 or abs(t.transform[3]) > 1e-9:
            raise ValueError(f"Rotated tiles are not supported: {t.path}")
        if t.transform[4] >= 0:
            raise ValueError(
                f"Only north-up rasters (negative pixel height) are supported: {t.path} has "
                f"pixel height {t.transform[4]}"
            )
        if abs(t.transform[0] - px_w_ref) > resolution_tolerance or \
           abs(t.transform[4] - px_h_ref) > resolution_tolerance:
            raise ValueError(
                f"Inconsistent resolution: {t.path} has pixel size "
                f"({t.transform[0]}, {t.transform[4]}), expected ({px_w_ref}, {px_h_ref})"
            )

    for t in tiles:
        if t.dtype != ref.dtype:
            raise ValueError(f"Inconsistent dtype: {t.path} is {t.dtype}, expected {ref.dtype} (from {ref.path})")
        if not _same_nodata(t.nodata, ref.nodata):
            raise ValueError(
                f"Inconsistent nodata: {t.path} has {t.nodata}, expected {ref.nodata} (from {ref.path})"
            )
        if t.band_count != ref.band_count:
            raise ValueError(
                f"Inconsistent band count: {t.path} has {t.band_count}, expected {ref.band_count} (from {ref.path})"
            )

    # Mosaic origin: upper-left corner (minimum x, maximum y)
    origin_x = min(t.transform[2] for t in tiles)
    origin_y = max(t.transform[5] for t in tiles)

    offsets = []
    for t in tiles:
        col_f = (t.transform[2] - origin_x) / px_w_ref
        row_f = (origin_y - t.transform[5]) / abs(px_h_ref)
        col = round(col_f)
        row = round(row_f)
        if abs(col_f - col) > offset_tolerance_px or abs(row_f - row) > offset_tolerance_px:
            raise ValueError(
                f"Tile not aligned to the pixel grid: {t.path} would land on a fractional "
                f"offset (row={row_f:.4f}, col={col_f:.4f}); check its origin/reprojection."
            )
        offsets.append((row, col))

    if not allow_overlap:
        pair = _find_overlap(tiles, offsets)
        if pair is not None:
            a, b = (tiles[i] for i in pair)
            same = offsets[pair[0]] == offsets[pair[1]]
            raise ValueError(
                f"Overlapping tiles: {a.path} and {b.path} "
                f"{'land on the same offset ' + str(offsets[pair[0]]) if same else 'have overlapping footprints'}. "
                "If the list mixes several products or dates on the same grid, select one "
                "(e.g. discover_tiles(directory, pattern=...)); pass allow_overlap=True "
                "to accept overlaps (the tile listed last wins)."
            )

    mosaic_height = max(row + t.height for (row, _col), t in zip(offsets, tiles, strict=True))
    mosaic_width = max(col + t.width for (_row, col), t in zip(offsets, tiles, strict=True))

    mosaic_transform = (px_w_ref, 0.0, origin_x, 0.0, px_h_ref, origin_y)

    return MosaicContract(
        mosaic_height=mosaic_height,
        mosaic_width=mosaic_width,
        mosaic_transform=mosaic_transform,
        crs=ref.crs,
        tiles=tiles,
        tile_offsets=offsets,
    )


# numpy/rasterio dtype -> GDAL data type name. Int8 needs GDAL >= 3.7 and
# (U)Int64 needs GDAL >= 3.5; the rasterio wheels ship a newer GDAL.
_GDAL_DTYPE_MAP = {
    "uint8": "Byte", "int8": "Int8",
    "uint16": "UInt16", "int16": "Int16",
    "uint32": "UInt32", "int32": "Int32",
    "uint64": "UInt64", "int64": "Int64",
    "float32": "Float32", "float64": "Float64",
    "complex64": "CFloat32", "complex128": "CFloat64",
}


def _format_nodata(value: float) -> str:
    if math.isnan(value):
        return "nan"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def write_vrt(
    contract: MosaicContract,
    output_path: str | Path,
    bands: int | Sequence[int] = 1,
    relative_paths: bool = False,
) -> Path:
    """
    Write a GDAL Virtual Raster (``.vrt``) that places each tile of the
    contract at its derived position. Pure Python: it does not need the
    GDAL Python bindings (``osgeo.gdal.BuildVRT``).

    Parameters
    ----------
    contract : MosaicContract
        Output of :func:`build_mosaic_contract`. Requiring it by type
        means a VRT can only be written for a validated tile set.
    output_path : str or Path
        Destination ``.vrt`` file. Parent directories are created.
    bands : int or sequence of int
        Source band(s) to include, 1-based. The VRT gets one band per
        entry, in the given order. Default: band 1.
    relative_paths : bool
        Reference tiles relative to the VRT location instead of by
        absolute path, so the VRT keeps working when the directory
        holding it and the tiles is moved or shared.

    Returns
    -------
    Path
        ``output_path``.
    """
    output_path = Path(output_path)
    band_list = [bands] if isinstance(bands, int) else list(bands)
    if not band_list:
        raise ValueError("`bands` is empty.")
    for b in band_list:
        if not 1 <= b <= contract.band_count:
            raise ValueError(f"Band {b} out of range: tiles have {contract.band_count} band(s) (1-based).")

    gdal_dtype = _GDAL_DTYPE_MAP.get(contract.dtype)
    if gdal_dtype is None:
        raise ValueError(f"Unsupported dtype for VRT: {contract.dtype}")

    vrt_dir = output_path.parent.resolve()

    def source_ref(tile: TileInfo) -> tuple[str, int]:
        if relative_paths:
            return os.path.relpath(tile.path.resolve(), vrt_dir), 1
        return str(tile.path.resolve()), 0

    nodata_tag = (
        f"    <NoDataValue>{_format_nodata(contract.nodata)}</NoDataValue>\n"
        if contract.nodata is not None else ""
    )

    band_blocks = []
    for vrt_band, src_band in enumerate(band_list, start=1):
        sources = []
        for tile, (row, col) in zip(contract.tiles, contract.tile_offsets, strict=True):
            filename, relative = source_ref(tile)
            sources.append(
                f"    <SimpleSource>\n"
                f'      <SourceFilename relativeToVRT="{relative}">{escape(filename)}</SourceFilename>\n'
                f"      <SourceBand>{src_band}</SourceBand>\n"
                f'      <SrcRect xOff="0" yOff="0" xSize="{tile.width}" ySize="{tile.height}"/>\n'
                f'      <DstRect xOff="{col}" yOff="{row}" xSize="{tile.width}" ySize="{tile.height}"/>\n'
                f"    </SimpleSource>\n"
            )
        band_blocks.append(
            f'  <VRTRasterBand dataType="{gdal_dtype}" band="{vrt_band}">\n'
            f"{nodata_tag}{''.join(sources)}"
            f"  </VRTRasterBand>\n"
        )

    px_w, _, ox, _, px_h, oy = contract.mosaic_transform
    vrt_xml = (
        f'<VRTDataset rasterXSize="{contract.mosaic_width}" rasterYSize="{contract.mosaic_height}">\n'
        f"  <SRS>{escape(contract.crs or '')}</SRS>\n"
        f"  <GeoTransform>{ox!r}, {px_w!r}, 0.0, {oy!r}, 0.0, {px_h!r}</GeoTransform>\n"
        f"{''.join(band_blocks)}"
        f"</VRTDataset>\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(vrt_xml, encoding="utf-8")
    return output_path


def discover_tiles(
    directory: str | Path,
    pattern: str = "*",
    extensions: tuple[str, ...] = (".tif", ".tiff"),
) -> list[Path]:
    """
    List raster files in a directory, sorted by path.

    No naming convention is assumed: the position of each tile is
    derived geometrically by :func:`build_mosaic_contract`, not from
    its name.

    Parameters
    ----------
    directory : str or Path
        Directory to search (not recursive).
    pattern : str
        Glob pattern the file name must match, e.g. ``"*landcover_2020*"``
        to select one product when a directory holds several products
        or dates on the same grid.
    extensions : tuple of str
        Accepted file extensions, compared case-insensitively.

    Raises
    ------
    FileNotFoundError
        If no matching raster is found.
    """
    directory = Path(directory)
    exts = {e.lower() for e in extensions}
    files = sorted(p for p in directory.glob(pattern) if p.is_file() and p.suffix.lower() in exts)
    if not files:
        raise FileNotFoundError(
            f"No raster ({', '.join(extensions)}) matching {pattern!r} found in: {directory}"
        )
    return files
