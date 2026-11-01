"""Tests for OpenTopoData-backed elevation cache."""
from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from gis_layers.crs import project, unproject
from gis_layers.providers.elevation import ElevationProvider
from gis_layers.providers.elevation_loader import load_elevation
from gis_layers.providers.open_elevation import OpenElevationProvider
from gis_layers.types import AOI, LatLon


@pytest.fixture
def open_dem_dir(tmp_path: Path) -> Path:
    center_lat, center_lon = 59.34, 17.74
    left, bottom = project(center_lat - 0.01, center_lon - 0.01)
    right, top = project(center_lat + 0.01, center_lon + 0.01)
    width = height = 10
    transform = from_origin(left, top, (right - left) / width, (top - bottom) / height)
    array = np.full((height, width), 850.0, dtype=np.float32)

    out = tmp_path / "elevation"
    out.mkdir()
    with rasterio.open(
        out / "dem.tif",
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="float32",
        crs="EPSG:3006",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(array, 1)
    (out / "manifest.json").write_text(
        json.dumps({"source": "opentopodata", "dataset": "eudem25m"}),
        encoding="utf-8",
    )
    return out


def test_open_elevation_height_at(open_dem_dir: Path):
    provider = OpenElevationProvider.from_cache(open_dem_dir)
    with rasterio.open(open_dem_dir / "dem.tif") as src:
        cx = (src.bounds.left + src.bounds.right) / 2
        cy = (src.bounds.bottom + src.bounds.top) / 2
    lat, lon = unproject(cx, cy)
    assert provider.height_at(LatLon(lat, lon)) == pytest.approx(850.0, abs=0.5)


def test_load_elevation_picks_open_cache(open_dem_dir: Path):
    layer = load_elevation(open_dem_dir)
    assert isinstance(layer, OpenElevationProvider)


def test_load_elevation_picks_lantmateriet_tiles(synthetic_dem_dir: Path):
    layer = load_elevation(synthetic_dem_dir)
    assert isinstance(layer, ElevationProvider)


def test_open_elevation_fetch_writes_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    aoi = AOI.from_bbox(name="test", min_lon=17.73, min_lat=59.33, max_lon=17.75, max_lat=59.35)
    cache_dir = tmp_path / "elevation"

    def fake_get(url, params=None, headers=None, timeout=None):
        locations = (params or {}).get("locations", "")
        count = len(locations.split("|"))
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": "OK",
            "results": [{"elevation": 900.0 + i} for i in range(count)],
        }
        return response

    monkeypatch.setattr("gis_layers.providers.open_elevation.requests.get", fake_get)
    provider = OpenElevationProvider.fetch(aoi, cache_dir, resolution_m=100.0)
    assert (cache_dir / "dem.tif").exists()
    assert (cache_dir / "manifest.json").exists()
    lat, lon = 59.34, 17.74
    assert aoi.bbox.contains(LatLon(lat, lon))
    height = provider.height_at(LatLon(lat, lon))
    assert height is not None
    assert height >= 900.0


def test_sample_bbox_coordinates_inside_aoi():
    from gis_layers.providers.open_elevation import _sample_bbox_to_grid

    aoi = AOI.from_bbox(name="airfield", min_lon=17.717, min_lat=59.331, max_lon=17.765, max_lat=59.355)
    # Patch query to capture first point without network.
    captured: list[tuple[float, float]] = []

    def fake_query(points, **kwargs):
        captured.extend(points[:3])
        return [800.0] * len(points)

    import gis_layers.providers.open_elevation as mod

    original = mod._query_opentopodata
    mod._query_opentopodata = fake_query
    try:
        _sample_bbox_to_grid(
            aoi.bbox,
            resolution_m=30.0,
            api_url=mod.DEFAULT_API_URL,
            dataset=mod.DEFAULT_DATASET,
            timeout=10.0,
        )
    finally:
        mod._query_opentopodata = original

    assert captured
    for lat, lon in captured:
        assert 59.2 <= lat <= 59.4, lat
        assert 17.4 <= lon <= 17.9, lon


def test_dem_write_preserves_north_south_orientation(tmp_path: Path):
    """GeoTIFF row 0 is north; sampling grid row 0 is south — flip on write."""
    from gis_layers.providers.open_elevation import OpenElevationProvider

    south_lat, north_lat = 59.331, 59.355
    west_lon, east_lon = 17.717, 17.765
    sw_x, sw_y = project(south_lat, west_lon)
    ne_x, ne_y = project(north_lat, east_lon)
    min_x, max_x = min(sw_x, ne_x), max(sw_x, ne_x)
    min_y, max_y = min(sw_y, ne_y), max(sw_y, ne_y)
    resolution_m = 30.0
    cols = max(2, int(math.ceil((max_x - min_x) / resolution_m)))
    rows = max(2, int(math.ceil((max_y - min_y) / resolution_m)))
    transform = from_origin(min_x, max_y, resolution_m, resolution_m)

    grid = np.zeros((rows, cols), dtype=np.float32)
    for row in range(rows):
        grid[row, :] = 100.0 + (row / max(rows - 1, 1)) * 800.0

    cache_dir = tmp_path / "elevation"
    cache_dir.mkdir()
    dem_path = cache_dir / "dem.tif"
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        width=cols,
        height=rows,
        count=1,
        dtype="float32",
        crs="EPSG:3006",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(np.flipud(grid), 1)
    (cache_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "source": "opentopodata"}),
        encoding="utf-8",
    )

    provider = OpenElevationProvider.from_cache(cache_dir)
    south_h = provider.height_at(LatLon(south_lat + 0.002, (west_lon + east_lon) / 2))
    north_h = provider.height_at(LatLon(north_lat - 0.002, (west_lon + east_lon) / 2))
    assert south_h is not None and north_h is not None
    assert south_h < north_h


def test_legacy_dem_cache_is_auto_corrected(tmp_path: Path):
    from gis_layers.providers.open_elevation import OpenElevationProvider

    south_lat, north_lat = 59.331, 59.355
    west_lon, east_lon = 17.717, 17.765
    sw_x, sw_y = project(south_lat, west_lon)
    ne_x, ne_y = project(north_lat, east_lon)
    min_x, max_x = min(sw_x, ne_x), max(sw_x, ne_x)
    min_y, max_y = min(sw_y, ne_y), max(sw_y, ne_y)
    resolution_m = 30.0
    cols = max(2, int(math.ceil((max_x - min_x) / resolution_m)))
    rows = max(2, int(math.ceil((max_y - min_y) / resolution_m)))
    transform = from_origin(min_x, max_y, resolution_m, resolution_m)
    grid = np.zeros((rows, cols), dtype=np.float32)
    for row in range(rows):
        grid[row, :] = 100.0 + (row / max(rows - 1, 1)) * 800.0

    cache_dir = tmp_path / "elevation"
    cache_dir.mkdir()
    with rasterio.open(
        cache_dir / "dem.tif",
        "w",
        driver="GTiff",
        width=cols,
        height=rows,
        count=1,
        dtype="float32",
        crs="EPSG:3006",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(grid.astype(np.float32), 1)  # legacy wrong orientation on disk
    (cache_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "source": "opentopodata"}),
        encoding="utf-8",
    )

    provider = OpenElevationProvider.from_cache(cache_dir)
    south_h = provider.height_at(LatLon(south_lat + 0.002, (west_lon + east_lon) / 2))
    north_h = provider.height_at(LatLon(north_lat - 0.002, (west_lon + east_lon) / 2))
    assert south_h is not None and north_h is not None
    assert south_h < north_h


def test_fetch_refetches_when_aoi_grows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    small = AOI.from_bbox(name="small", min_lon=17.71, min_lat=59.33, max_lon=17.77, max_lat=59.35)
    large = AOI.from_bbox(name="large", min_lon=17.63, min_lat=59.32, max_lon=17.78, max_lat=59.37)
    cache_dir = tmp_path / "elevation"
    calls: list[int] = []

    def fake_query(points, **kwargs):
        calls.append(len(points))
        return [800.0] * len(points)

    import gis_layers.providers.open_elevation as mod

    monkeypatch.setattr(mod, "_query_opentopodata", fake_query)
    OpenElevationProvider.fetch(small, cache_dir, resolution_m=200.0)
    assert len(calls) == 1
    OpenElevationProvider.fetch(large, cache_dir, resolution_m=200.0)
    assert len(calls) == 2


def test_fetch_reuses_cache_for_same_aoi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The grid covers all four (skewed) AOI corners, so a second fetch hits the cache."""
    aoi = AOI.from_bbox(name="airfield_wide", min_lon=17.637719, min_lat=59.325075, max_lon=17.77821, max_lat=59.367197)
    calls: list[int] = []

    def fake_query(points, **kwargs):
        calls.append(len(points))
        return [800.0] * len(points)

    import gis_layers.providers.open_elevation as mod

    monkeypatch.setattr(mod, "_query_opentopodata", fake_query)
    provider = OpenElevationProvider.fetch(aoi, tmp_path / "elevation", resolution_m=200.0)
    OpenElevationProvider.fetch(aoi, tmp_path / "elevation", resolution_m=200.0)
    assert len(calls) == 1
    b = aoi.bbox
    for lat, lon in ((b.min_lat, b.max_lon), (b.max_lat, b.min_lon)):
        assert provider.height_at(LatLon(lat, lon)) == 800.0
