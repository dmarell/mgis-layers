from gis_layers.crs import unproject
from gis_layers.providers import elevation
from gis_layers.providers._lantmateriet_auth import Auth
from gis_layers.providers.elevation import ElevationProvider
from gis_layers.types import BBox, LatLon


def test_stac_search_asks_only_for_the_dtm(monkeypatch):
    bodies = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"features": [{"id": "688_41", "collection": "dtm-cog"}], "links": []}

    def fake_post(url, json, headers, timeout):
        bodies.append(json)
        return Response()

    monkeypatch.setattr(elevation.requests, "post", fake_post)
    items = elevation._stac_search(
        "https://stac.example/v1", BBox(13.2, 62.1, 13.4, 62.2), auth=Auth("Basic x"), timeout=5.0
    )
    assert [item["id"] for item in items] == ["688_41"]
    assert bodies[0]["collections"] == ["dtm-cog"]


def test_height_at_center_matches_synthetic_slope(synthetic_dem_dir):
    provider = ElevationProvider.from_cache(synthetic_dem_dir)
    assert provider.tile_count == 1

    # In _bbox_around, x ranges over 200m centered on origin: left = cx - 100.
    # Slope is 100 + (x - left) / 10, so at x = cx (column 100) we get 110m.
    # Sample exactly at the center.
    from gis_layers.crs import project
    left = next(iter(provider._tiles)).bounds[0]
    cx = left + 100.0
    cy = next(iter(provider._tiles)).bounds[1] + 100.0
    lat, lon = unproject(cx, cy)
    height = provider.height_at(LatLon(lat, lon))
    assert height is not None
    assert abs(height - 110.0) < 0.5


def test_stale_dem_beside_tiles_is_ignored(synthetic_dem_dir):
    tiles = synthetic_dem_dir / "tiles"
    tiles.mkdir()
    (synthetic_dem_dir / "synthetic.tif").rename(tiles / "m688_41.tif")
    (synthetic_dem_dir / "dem.tif").write_bytes(b"")
    provider = ElevationProvider.from_cache(synthetic_dem_dir)
    assert [t.path.name for t in provider._tiles] == ["m688_41.tif"]


def test_height_outside_returns_none(synthetic_dem_dir):
    provider = ElevationProvider.from_cache(synthetic_dem_dir)
    assert provider.height_at(LatLon(0.0, 0.0)) is None
