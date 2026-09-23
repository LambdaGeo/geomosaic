"""
Tests for geomosaic: tile discovery, mosaic-contract validation and VRT
construction. Only rasterio is needed; every raster is synthetic and
written to pytest's tmp_path.
"""

import math
import shutil

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_origin
from rasterio.windows import Window

from geomosaic import build_mosaic_contract, discover_tiles, inspect_tile, write_vrt

ORIGIN_X, ORIGIN_Y, PX = 500_000.0, 9_700_000.0, 100.0


def _write_tile(path, data, origin_x, origin_y, px_size=PX, crs="EPSG:31984", nodata=None, transform=None):
    """Write a GeoTIFF; `data` is (rows, cols) or (bands, rows, cols)."""
    data = data if data.ndim == 3 else data[np.newaxis]
    with rasterio.open(
        str(path), "w", driver="GTiff", height=data.shape[1], width=data.shape[2],
        count=data.shape[0], dtype=str(data.dtype), crs=crs, nodata=nodata,
        transform=transform or from_origin(origin_x, origin_y, px_size, px_size),
    ) as dst:
        dst.write(data)
    return path


@pytest.fixture
def mosaic_2x2(tmp_path):
    """Four 10x10 tiles forming a 20x20 mosaic, plus the reference array."""
    rng = np.random.default_rng(3)
    ref = rng.integers(1, 100, size=(20, 20)).astype("int16")
    tiles = {
        "top_left": (ref[0:10, 0:10], ORIGIN_X, ORIGIN_Y),
        "top_right": (ref[0:10, 10:20], ORIGIN_X + 10 * PX, ORIGIN_Y),
        "bottom_left": (ref[10:20, 0:10], ORIGIN_X, ORIGIN_Y - 10 * PX),
        "bottom_right": (ref[10:20, 10:20], ORIGIN_X + 10 * PX, ORIGIN_Y - 10 * PX),
    }
    paths = [_write_tile(tmp_path / f"{name}.tif", d, ox, oy) for name, (d, ox, oy) in tiles.items()]
    return paths, ref, tmp_path


def _pair(tmp_path, a_kwargs=None, b_kwargs=None, a_data=None, b_data=None, b_dx=10 * PX):
    """Two side-by-side 10x10 tiles; kwargs override _write_tile args per tile."""
    a_data = np.ones((10, 10), "int16") if a_data is None else a_data
    b_data = np.full((10, 10), 2, "int16") if b_data is None else b_data
    a = _write_tile(tmp_path / "a.tif", a_data, ORIGIN_X, ORIGIN_Y, **(a_kwargs or {}))
    b = _write_tile(tmp_path / "b.tif", b_data, ORIGIN_X + b_dx, ORIGIN_Y, **(b_kwargs or {}))
    return [a, b]


# ── discover_tiles ───────────────────────────────────────────────────────────

def test_discover_tiles(mosaic_2x2):
    _paths, _ref, tmp_path = mosaic_2x2
    found = discover_tiles(tmp_path)
    assert len(found) == 4
    assert found == sorted(found)


def test_discover_tiles_pattern_selects_one_product(tmp_path):
    for product in ("landcover_2010", "landcover_2020"):
        for i in range(2):
            _write_tile(tmp_path / f"{product}-{i}.tif", np.ones((2, 2), "uint8"), ORIGIN_X + i * 200, ORIGIN_Y)
    found = discover_tiles(tmp_path, pattern="landcover_2020*")
    assert [p.name for p in found] == ["landcover_2020-0.tif", "landcover_2020-1.tif"]


def test_discover_tiles_extension_is_case_insensitive(tmp_path):
    _write_tile(tmp_path / "A.TIF", np.ones((2, 2), "uint8"), ORIGIN_X, ORIGIN_Y)
    (tmp_path / "notes.txt").write_text("not a raster")
    assert [p.name for p in discover_tiles(tmp_path)] == ["A.TIF"]


def test_discover_tiles_raises_when_nothing_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="No raster"):
        discover_tiles(tmp_path)


# ── build_mosaic_contract: positive cases ───────────────────────────────────

def test_contract_derives_positions_from_geotransform(mosaic_2x2):
    paths, _ref, _tmp_path = mosaic_2x2
    contract = build_mosaic_contract(paths)
    assert (contract.mosaic_height, contract.mosaic_width) == (20, 20)
    assert sorted(contract.tile_offsets) == [(0, 0), (0, 10), (10, 0), (10, 10)]
    assert contract.mosaic_transform == (PX, 0.0, ORIGIN_X, 0.0, -PX, ORIGIN_Y)


def test_sparse_grid_with_gap_and_partial_edge_tile(tmp_path):
    """Irregular export: an empty grid cell (no tile) and a partial tile
    at the bottom edge, as produced when an export is clipped to an area
    of interest."""
    full = np.full((10, 10), 7, "int16")
    partial = np.full((4, 10), 9, "int16")
    paths = [
        _write_tile(tmp_path / "t00.tif", full, ORIGIN_X, ORIGIN_Y, nodata=-1),
        _write_tile(tmp_path / "t01.tif", full, ORIGIN_X + 10 * PX, ORIGIN_Y, nodata=-1),
        # (1, 1) has no tile
        _write_tile(tmp_path / "t10.tif", partial, ORIGIN_X, ORIGIN_Y - 10 * PX, nodata=-1),
    ]
    contract = build_mosaic_contract(paths)
    assert (contract.mosaic_height, contract.mosaic_width) == (14, 20)

    with rasterio.open(write_vrt(contract, tmp_path / "m.vrt")) as ds:
        assert ds.nodata == -1
        assert np.all(ds.read(1, window=Window(10, 10, 10, 4)) == -1)  # gap -> nodata
        assert np.all(ds.read(1, window=Window(0, 10, 10, 4)) == 9)    # partial tile
        edge = ds.read(1, window=Window(5, 5, 10, 2))                  # crosses col 10
        assert np.all(edge == 7)


# ── build_mosaic_contract: every check must actually fire ───────────────────

def test_rejects_empty_list():
    with pytest.raises(ValueError, match="empty"):
        build_mosaic_contract([])


def test_rejects_inconsistent_crs(tmp_path):
    paths = _pair(tmp_path, b_kwargs={"crs": "EPSG:4326"})
    with pytest.raises(ValueError, match="Inconsistent CRS"):
        build_mosaic_contract(paths)


def test_rejects_inconsistent_resolution(tmp_path):
    paths = _pair(tmp_path, b_kwargs={"px_size": 30.0})
    with pytest.raises(ValueError, match="Inconsistent resolution"):
        build_mosaic_contract(paths)


def test_rejects_rotated_tile(tmp_path):
    rotated = Affine(PX, 5.0, ORIGIN_X, 5.0, -PX, ORIGIN_Y)
    paths = _pair(tmp_path, b_kwargs={"transform": rotated})
    with pytest.raises(ValueError, match="Rotated"):
        build_mosaic_contract(paths)


def test_rejects_south_up_raster(tmp_path):
    """Positive pixel height used to produce wrong offsets silently."""
    south_up = Affine(PX, 0.0, ORIGIN_X, 0.0, PX, ORIGIN_Y)
    path = _write_tile(tmp_path / "s.tif", np.ones((4, 4), "int16"), 0, 0, transform=south_up)
    with pytest.raises(ValueError, match="north-up"):
        build_mosaic_contract([path])


def test_rejects_inconsistent_dtype(tmp_path):
    """Mixed dtypes used to be accepted: the VRT took tile 0's dtype, so
    a float 2.7 in another tile was read back as the integer 3."""
    paths = _pair(tmp_path, b_data=np.full((10, 10), 2.7, "float32"))
    with pytest.raises(ValueError, match="Inconsistent dtype"):
        build_mosaic_contract(paths)


@pytest.mark.parametrize("nodata_a, nodata_b", [(0, 255), (0, None), (None, 0)])
def test_rejects_inconsistent_nodata(tmp_path, nodata_a, nodata_b):
    paths = _pair(tmp_path, a_kwargs={"nodata": nodata_a}, b_kwargs={"nodata": nodata_b})
    with pytest.raises(ValueError, match="Inconsistent nodata"):
        build_mosaic_contract(paths)


def test_nan_nodata_counts_as_consistent(tmp_path):
    data = np.ones((10, 10), "float32")
    paths = _pair(tmp_path, a_data=data, b_data=data,
                  a_kwargs={"nodata": math.nan}, b_kwargs={"nodata": math.nan})
    assert math.isnan(build_mosaic_contract(paths).nodata)


def test_rejects_inconsistent_band_count(tmp_path):
    paths = _pair(tmp_path, b_data=np.ones((2, 10, 10), "int16"))
    with pytest.raises(ValueError, match="Inconsistent band count"):
        build_mosaic_contract(paths)


def test_rejects_subpixel_misaligned_tile(tmp_path):
    """Second tile shifted by 37.5 m (less than one 100 m pixel)."""
    paths = _pair(tmp_path, b_dx=10 * PX + 37.5)
    with pytest.raises(ValueError, match="not aligned"):
        build_mosaic_contract(paths)


def test_rejects_tiles_on_the_same_offset(tmp_path):
    """Two products sharing one grid (a common layout for multi-product
    exports in one directory) used to be accepted, and the VRT silently
    kept only the tile listed last."""
    paths = _pair(tmp_path, b_dx=0.0)
    with pytest.raises(ValueError, match="Overlapping tiles.*same offset"):
        build_mosaic_contract(paths)


def test_rejects_partially_overlapping_tiles(tmp_path):
    paths = _pair(tmp_path, b_dx=5 * PX)
    with pytest.raises(ValueError, match="overlapping footprints"):
        build_mosaic_contract(paths)


def test_adjacent_tiles_do_not_count_as_overlap(mosaic_2x2):
    paths, _ref, _tmp_path = mosaic_2x2
    build_mosaic_contract(paths)  # edges touch, interiors do not


def test_allow_overlap_last_tile_wins(tmp_path):
    paths = _pair(tmp_path, b_dx=5 * PX)
    contract = build_mosaic_contract(paths, allow_overlap=True)
    assert contract.mosaic_width == 15
    with rasterio.open(write_vrt(contract, tmp_path / "m.vrt")) as ds:
        row = ds.read(1)[0]
    assert list(row) == [1] * 5 + [2] * 10


# ── write_vrt ───────────────────────────────────────────────────────────────

def test_vrt_reads_correctly_across_tile_boundary(mosaic_2x2):
    paths, ref, tmp_path = mosaic_2x2
    vrt_path = write_vrt(build_mosaic_contract(paths), tmp_path / "mosaic.vrt")
    with rasterio.open(str(vrt_path)) as ds:
        assert np.array_equal(ds.read(1), ref)
        assert np.array_equal(ds.read(1, window=Window(5, 0, 10, 20)), ref[:, 5:15])  # crosses col 10
        assert ds.crs.to_epsg() == 31984


@pytest.mark.parametrize("dtype, value", [
    ("int8", -5),                  # used to be written as Byte: -5 read back as 0
    ("int64", 2**53 + 1),          # used to fall back to Float64: lost precision
    ("uint64", 2**63 + 1),
    ("float64", 0.1),
    ("uint16", 65_000),
])
def test_vrt_preserves_dtype(tmp_path, dtype, value):
    path = _write_tile(tmp_path / "t.tif", np.full((4, 4), value, dtype), ORIGIN_X, ORIGIN_Y)
    with rasterio.open(write_vrt(build_mosaic_contract([path]), tmp_path / "t.vrt")) as ds:
        assert ds.dtypes[0] == dtype
        assert ds.read(1)[0, 0] == np.array(value, dtype)


def test_vrt_multiband(tmp_path):
    data = np.stack([np.full((10, 10), b, "uint8") for b in (10, 20, 30)])
    paths = _pair(tmp_path, a_data=data, b_data=data)
    contract = build_mosaic_contract(paths)

    with rasterio.open(write_vrt(contract, tmp_path / "all.vrt", bands=[1, 2, 3])) as ds:
        assert ds.count == 3
        assert [int(ds.read(i)[0, 15]) for i in (1, 2, 3)] == [10, 20, 30]

    with rasterio.open(write_vrt(contract, tmp_path / "b3.vrt", bands=3)) as ds:
        assert ds.count == 1
        assert np.all(ds.read(1) == 30)


@pytest.mark.parametrize("bands", [0, 2, [1, 2], []])
def test_vrt_rejects_invalid_bands(tmp_path, bands):
    """Out-of-range bands used to produce a VRT that only failed when read."""
    paths = _pair(tmp_path)
    with pytest.raises(ValueError, match="out of range|empty"):
        write_vrt(build_mosaic_contract(paths), tmp_path / "m.vrt", bands=bands)


def test_vrt_writes_nodata(tmp_path):
    paths = _pair(tmp_path, a_kwargs={"nodata": -9999}, b_kwargs={"nodata": -9999})
    with rasterio.open(write_vrt(build_mosaic_contract(paths), tmp_path / "m.vrt")) as ds:
        assert ds.nodata == -9999


def test_vrt_relative_paths_survive_moving_the_directory(mosaic_2x2):
    paths, ref, tmp_path = mosaic_2x2
    src = tmp_path
    write_vrt(build_mosaic_contract(paths), src / "mosaic.vrt", relative_paths=True)

    moved = src.parent / (src.name + "_moved")
    shutil.move(str(src), str(moved))
    with rasterio.open(str(moved / "mosaic.vrt")) as ds:
        assert np.array_equal(ds.read(1), ref)


def test_inspect_tile_reads_metadata_only(tmp_path):
    path = _write_tile(tmp_path / "t.tif", np.ones((3, 5), "uint16"), ORIGIN_X, ORIGIN_Y, nodata=0)
    info = inspect_tile(path)
    assert (info.height, info.width, info.band_count, info.dtype, info.nodata) == (3, 5, 1, "uint16", 0)
    assert info.transform == (PX, 0.0, ORIGIN_X, 0.0, -PX, ORIGIN_Y)
