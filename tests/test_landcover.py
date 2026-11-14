"""Tests for the NMD land cover provider, against a synthetic national raster in a zip."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from gis_layers.cli import main
from gis_layers.crs import project
from gis_layers.providers import nmd_landcover as nmd
from gis_layers.types import AOI, BBox, LatLon

# Around Skå-Edeby airfield, on NMD's 10 m grid.
LEFT, TOP = 655_000.0, 6_585_000.0
SIZE = 400  # 4 × 4 km


def _national(tmp_path: Path) -> Path:
    """A zip like NMD's: west half forest (112), east half heath (4222), a lake (61) in the north east."""
    data = np.full((SIZE, SIZE), 112, dtype=np.uint16)
    data[:, SIZE // 2:] = 4222
    data[:50, 340:380] = 61
    tif = tmp_path / "national.tif"
    with rasterio.open(
        tif, "w", driver="GTiff", width=SIZE, height=SIZE, count=1, dtype="uint16", crs="EPSG:3006",
        transform=from_origin(LEFT, TOP, 10.0, 10.0), tiled=True, blockxsize=128, blockysize=128,
        compress="packbits",
    ) as dst:
        dst.write(data, 1)
    z = tmp_path / nmd.ZIP_NAME
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_DEFLATED) as f:
        f.write(tif, nmd.MEMBER)
    return z


def _aoi(left: float, bottom: float, right: float, top: float) -> AOI:
    from gis_layers.crs import unproject

    (s, w), (n, e) = unproject(left, bottom), unproject(right, top)
    return AOI(name="test", bbox=BBox(min_lon=w, min_lat=s, max_lon=e, max_lat=n))


def test_fetch_crops_the_aoi_on_the_source_grid_and_lists_the_codes(tmp_path: Path):
    z = _national(tmp_path)
    aoi = _aoi(LEFT + 1_005, TOP - 3_005, LEFT + 2_995, TOP - 995)
    p = nmd.NmdLandcoverProvider.fetch(aoi, tmp_path / "landcover", source=nmd.source_path(z))
    m = json.loads((tmp_path / "landcover" / "manifest.json").read_text())
    left, bottom, right, top = m["bounds"]
    assert all(v % 10 == 0 for v in m["bounds"])
    xs, ys = zip(*[project(lat, lon) for lat in (aoi.bbox.min_lat, aoi.bbox.max_lat)
                   for lon in (aoi.bbox.min_lon, aoi.bbox.max_lon)])
    assert left <= min(xs) and right >= max(xs) and bottom <= min(ys) and top >= max(ys)
    assert max(min(xs) - left, right - max(xs), min(ys) - bottom, top - max(ys)) < nmd.RESOLUTION_M
    assert m["dataset"] == nmd.DATASET and m["licence"] == "CC0" and m["covered"] == 1.0
    assert set(m["codes"]) == {"112", "4222"} and m["legend"]["4222"] == "heath dominated, mesic"
    x = np.array([LEFT + 1_500, LEFT + 2_500, LEFT + 10_000])
    assert p.code_at_xy_np(x, np.full(3, TOP - 2_000)).tolist() == [112, 4222, nmd.NODATA]


def test_fetch_outside_the_source_is_nodata_and_the_cache_is_reused(tmp_path: Path):
    z = _national(tmp_path)
    aoi = _aoi(LEFT + 3_500, TOP - 500, LEFT + 4_500, TOP + 500)
    p = nmd.NmdLandcoverProvider.fetch(aoi, tmp_path / "lc", source=nmd.source_path(z))
    m = json.loads((tmp_path / "lc" / "manifest.json").read_text())
    assert 0.0 < m["covered"] < 0.5 and "61" in m["codes"]
    assert p.code_at_xy_np(np.array([LEFT + 3_600]), np.array([TOP - 100])).tolist() == [61]
    z.unlink()
    again = nmd.NmdLandcoverProvider.fetch(aoi, tmp_path / "lc", source=nmd.source_path(z))
    assert np.array_equal(again.codes, p.codes)


def test_fetch_downloads_the_zip_once_to_the_download_dir(tmp_path: Path):
    z = _national(tmp_path)
    calls = []

    def get(url: str, dest: Path) -> Path:
        calls.append((url, dest))
        return z

    aoi = _aoi(LEFT + 1_005, TOP - 3_005, LEFT + 2_995, TOP - 995)
    nmd.NmdLandcoverProvider.fetch(aoi, tmp_path / "lc", download_dir=tmp_path / "dl", get=get)
    assert calls == [(nmd.URL, tmp_path / "dl" / nmd.ZIP_NAME)]


def test_download_resumes_a_partial_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import requests

    body = b"0123456789"
    seen = []

    class Resp:
        def __init__(self, headers):
            start = int(headers["Range"].split("=")[1].rstrip("-")) if "Range" in headers else 0
            self.status_code = 206 if start else 200
            self.data = body[start:]
            seen.append(start)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield self.data

    monkeypatch.setattr(requests, "get", lambda url, headers, stream, timeout: Resp(headers))
    dest = tmp_path / "nmd.zip"
    dest.with_suffix(".zip.part").write_bytes(body[:4])
    assert nmd.download("http://x", dest) == dest
    assert dest.read_bytes() == body and seen == [4]
    nmd.download("http://x", dest)
    assert seen == [4]


def test_cli_fetches_landcover_into_the_aoi_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    dl = tmp_path / "dl"
    (dl / "nmd").mkdir(parents=True)
    _national(tmp_path).rename(dl / "nmd" / nmd.ZIP_NAME)
    monkeypatch.setenv(nmd.DOWNLOAD_DIR_ENV, str(dl))
    aoi = _aoi(LEFT + 1_005, TOP - 3_005, LEFT + 2_995, TOP - 995)
    path = tmp_path / "aoi.geojson"
    b = aoi.bbox
    ring = [[b.min_lon, b.min_lat], [b.max_lon, b.min_lat], [b.max_lon, b.max_lat], [b.min_lon, b.max_lat],
            [b.min_lon, b.min_lat]]
    path.write_text(json.dumps({"type": "Feature", "properties": {"name": "test"},
                                "geometry": {"type": "Polygon", "coordinates": [ring]}}))
    assert main(["fetch", "--aoi", str(path), "--out", str(tmp_path / "data"), "--layers", "landcover"]) == 0
    assert (tmp_path / "data" / "landcover" / "nmd.tif").is_file()
    assert "landcover" in json.loads((tmp_path / "data" / "aoi.json").read_text())["layers"]
    assert "landcover (NMD2023, 10 m): 2 classes" in capsys.readouterr().out
    assert nmd.NmdLandcoverProvider.from_cache(tmp_path / "data" / "landcover").code_at(
        LatLon(*_center(aoi))) in (112, 4222)


def _center(aoi: AOI) -> tuple[float, float]:
    b = aoi.bbox
    return (b.min_lat + b.max_lat) / 2, (b.min_lon + b.max_lon) / 2
