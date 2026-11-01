"""Tests for the Skogsstyrelsen tree height provider."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from gis_layers.crs import project, unproject
from gis_layers.providers import tree_height as th
from gis_layers.types import AOI, BBox, LatLon


def test_tile_keys_cover_bounds():
    keys = th.tile_keys(655_900, 6_581_900, 659_800, 6_587_000)
    assert keys[0] == (6_580_000, 655_000) and keys[-1] == (6_585_000, 657_500)
    assert len(keys) == 3 * 2
    assert th.block_name(6_582_500, 657_500) == "65_6"


def test_choose_tiles_takes_newest_scan_and_reports_missing():
    listing = {
        "Tradhojd_LaserdataSkog/2020/65_6": ["THL_20E018_65800_6550_2020.mrf", "THL_20E018_65800_6550_2020.lrc"],
        "Tradhojd_LaserdataSkog/2022/65_6": ["THL_22D054_65800_6550_2022.mrf", "THL_22D054_65825_6550_2022.mrf",
                                             "THL_22D054_66000_6550_2022.mrf"],
    }
    tiles, missing = th.choose_tiles(listing, [(6_580_000, 655_000), (6_582_500, 655_000), (6_585_000, 655_000)])
    assert [(t.north_m, t.year) for t in tiles] == [(6_580_000, 2022), (6_582_500, 2022)]
    assert tiles[0].path == "Tradhojd_LaserdataSkog/2022/65_6/THL_22D054_65800_6550_2022"
    assert missing == [(6_585_000, 655_000)]


def _tile(path: Path, left: float, bottom: float, value: int, size: int = 50) -> Path:
    data = np.full((size, size), value, dtype=np.int16)
    data[0, 0] = -1
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=1, dtype="int16", crs="EPSG:3006",
        transform=from_origin(left, bottom + size, 1.0, 1.0), nodata=-1,
    ) as dst:
        dst.write(data, 1)
    return path


def test_mosaic_places_tiles_and_keeps_nodata(tmp_path: Path):
    a = _tile(tmp_path / "a.tif", 1000, 2000, 120)
    b = _tile(tmp_path / "b.tif", 1050, 2000, 80)
    info = th.mosaic([a, b], (1010.5, 2010.2, 1080.0, 2050.0), tmp_path / "chm.tif")
    assert info["bounds"] == [1010, 2010, 1080, 2050]
    with rasterio.open(tmp_path / "chm.tif") as src:
        d = src.read(1)
        assert d.shape == (40, 70) and src.nodata == -1
    assert d[0, 0] == 120 and d[0, 39] == 120 and d[0, 40] == -1 and d[0, 41] == 80 and d[-1, -1] == 80
    provider = th.TreeHeightProvider.from_cache(tmp_path)
    assert provider.height_at_xy_np(np.array([1012.5, 1060.5, 900.0]), np.array([2020.5, 2020.5, 2020.5])) == \
        pytest.approx([12.0, 8.0, np.nan], nan_ok=True)


class _FakeFtp:
    def __init__(self, files: dict[str, bytes]):
        self.files = files

    def nlst(self, folder: str) -> list[str]:
        prefix = folder.rstrip("/") + "/"
        return sorted({prefix + p[len(prefix):].split("/")[0] for p in self.files if p.startswith(prefix)})

    def retrbinary(self, cmd: str, callback) -> None:
        callback(self.files[cmd.split(" ", 1)[1]])

    def quit(self) -> None:
        pass


def test_fetch_downloads_newest_tiles_and_writes_manifest(tmp_path: Path):
    x0, y0 = project(59.342735, 17.739182)
    tl = (int(x0 // 2500) * 2500, int(y0 // 2500) * 2500)
    src = _tile(tmp_path / "src.tif", tl[0], tl[1], 155, size=2500)
    blob = src.read_bytes()
    name = f"THL_22D054_{tl[1] // 100}_{tl[0] // 100}_2022"
    old = f"THL_20E018_{tl[1] // 100}_{tl[0] // 100}_2020"
    block = th.block_name(tl[1], tl[0])
    files = {
        f"Tradhojd_LaserdataSkog/2022/{block}/{name}.mrf": blob,
        f"Tradhojd_LaserdataSkog/2022/{block}/{name}.idx": b"",
        f"Tradhojd_LaserdataSkog/2022/{block}/{name}.lrc": b"x",
        f"Tradhojd_LaserdataSkog/2020/{block}/{old}.mrf": b"old",
        "Tradhojd_LaserdataSkog/2025/Omdrev2/61_3/THL_x_61000_3000_2025.mrf": b"",
    }
    lat, lon = unproject(tl[0] + 1250, tl[1] + 1250)
    aoi = AOI(name="t", bbox=BBox(lon - 0.002, lat - 0.002, lon + 0.002, lat + 0.002))
    provider = th.TreeHeightProvider.fetch(aoi, tmp_path / "treeheight", connect=lambda timeout: _FakeFtp(files))
    assert provider.height_at(LatLon(lat, lon)) == pytest.approx(15.5)
    m = json.loads((tmp_path / "treeheight" / "manifest.json").read_text())
    assert m["tiles"] == [{"name": name, "year": 2022}] and m["missing_tiles"] == [] and m["covered"] == 1.0
    assert (tmp_path / "treeheight" / "tiles" / f"{name}.idx").exists()
    again = th.TreeHeightProvider.fetch(aoi, tmp_path / "treeheight", connect=lambda timeout: pytest.fail("no fetch"))
    assert again.height_at(LatLon(lat, lon)) == pytest.approx(15.5)
