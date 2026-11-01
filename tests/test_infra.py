"""Infrastructure obstacles: classification, heights, corridors, fetch via a fake Overpass."""
from __future__ import annotations

import json

import pytest

from gis_layers import cli
from gis_layers.crs import project, unproject
from gis_layers.providers import osm_infra
from gis_layers.providers.osm_infra import OsmInfraProvider, classify, parse_height_m
from gis_layers.types import LatLon

CENTER = (59.343409, 17.740703)


def _ll(dx: float, dy: float) -> tuple[float, float]:
    cx, cy = project(*CENTER)
    return unproject(cx + dx, cy + dy)


def _point(dx: float, dy: float = 0.0) -> LatLon:
    return LatLon(*_ll(dx, dy))


def _node(osm_id: int, dx: float, dy: float, tags: dict) -> dict:
    lat, lon = _ll(dx, dy)
    return {"type": "node", "id": osm_id, "lat": lat, "lon": lon, "tags": tags}


def _ns_way(osm_id: int, dx: float, tags: dict) -> dict:
    geometry = [dict(zip(("lat", "lon"), _ll(dx, dy))) for dy in (-500.0, 500.0)]
    return {"type": "way", "id": osm_id, "geometry": geometry, "tags": tags}


OVERPASS_RESPONSE = {
    "elements": [
        _ns_way(1, 0.0, {"power": "line", "voltage": "130000"}),
        _ns_way(2, 200.0, {"railway": "rail", "electrified": "contact_line"}),
        _ns_way(3, 400.0, {"railway": "rail", "electrified": "no"}),
        _ns_way(4, 600.0, {"railway": "rail", "electrified": "contact_line", "tunnel": "yes"}),
        _ns_way(5, 800.0, {"aerialway": "chair_lift", "name": "Stolliften"}),
        _ns_way(6, 1000.0, {"aerialway": "t-bar"}),
        _ns_way(7, 1200.0, {"aerialway": "magic_carpet"}),
        _ns_way(8, 1400.0, {"power": "minor_line"}),
        _node(10, 2000.0, 0.0, {"power": "generator", "generator:source": "wind",
                                "height:hub": "120", "rotor:diameter": "130"}),
        _node(11, 3000.0, 0.0, {"man_made": "mast", "height": "72 m"}),
        _node(12, 4000.0, 0.0, {"man_made": "chimney"}),
        _node(13, 5000.0, 0.0, {"amenity": "bench"}),
        {"type": "node", "id": 14, "lat": 62.0, "lon": 13.0},
    ]
}


@pytest.fixture
def provider() -> OsmInfraProvider:
    fc = osm_infra.osm_response_to_geojson(OVERPASS_RESPONSE)
    return OsmInfraProvider._from_features(fc["features"])


def test_parse_height():
    assert parse_height_m("45") == 45.0
    assert parse_height_m("45.5 m") == 45.5
    assert parse_height_m("12,5m") == 12.5
    assert parse_height_m("100 ft") == pytest.approx(30.48)
    assert parse_height_m("tall") is None
    assert parse_height_m(None) is None


def test_classify_skips_unrelated_and_excluded():
    assert classify({"amenity": "bench"}) is None
    assert classify({"aerialway": "station"}) is None
    assert classify({"aerialway": "magic_carpet"}) is None
    assert classify({"railway": "tram"}) == ("railway_electrified", "tram")
    assert classify({"railway": "rail", "electrified": "rail"}) == ("railway", "rail")


def test_kinds_and_counts(provider):
    assert provider.kind_counts() == {
        "power_line": 1,
        "railway_electrified": 2,
        "railway": 1,
        "aerialway": 1,
        "aerialway_surface": 1,
        "power_minor_line": 1,
        "wind_turbine": 1,
        "mast": 1,
        "chimney": 1,
    }


def test_power_line_is_corridor_with_nominal_height(provider):
    hits = provider.features_near(_point(15.0), radius_m=10.0)
    assert [h.kind for h in hits] == ["power_line"]
    hit = hits[0]
    assert hit.corridor and not hit.height_from_tag
    assert hit.height_m == osm_infra.DEFAULT_INFRA_KINDS["power_line"].height_m
    assert hit.distance_m == pytest.approx(15.0, abs=0.1)
    assert hit.osm_id == "way/1"


def test_railway_electrified_vs_not_vs_tunnel(provider):
    electrified = provider.features_near(_point(200.0), radius_m=1.0)[0]
    assert electrified.kind == "railway_electrified" and electrified.corridor
    plain = provider.features_near(_point(400.0), radius_m=1.0)[0]
    assert plain.kind == "railway" and not plain.corridor and plain.height_m == 0.0
    tunnel = provider.features_near(_point(600.0), radius_m=1.0)[0]
    assert tunnel.tunnel and not tunnel.corridor and tunnel.height_m == 0.0


def test_aerialways(provider):
    chair = provider.features_near(_point(800.0), radius_m=1.0)[0]
    assert chair.kind == "aerialway" and chair.subtype == "chair_lift" and chair.name == "Stolliften"
    tbar = provider.features_near(_point(1000.0), radius_m=1.0)[0]
    assert tbar.kind == "aerialway_surface" and tbar.corridor
    assert provider.features_near(_point(1200.0), radius_m=1.0) == []


def test_point_obstacles_use_tags_and_reach(provider):
    turbine = provider.features_near(_point(2050.0), radius_m=0.0)
    assert turbine and turbine[0].kind == "wind_turbine"
    assert turbine[0].height_m == pytest.approx(185.0) and turbine[0].height_from_tag
    mast = provider.features_near(_point(3000.0), radius_m=5.0)[0]
    assert mast.height_m == 72.0 and mast.height_from_tag
    chimney = provider.features_near(_point(4000.0), radius_m=5.0)[0]
    assert chimney.height_m == osm_infra.DEFAULT_INFRA_KINDS["chimney"].height_m
    assert provider.features_near(_point(5000.0), radius_m=5.0) == []


def test_fetch_caches_only_classified_features(tmp_path, monkeypatch):
    calls = []

    def fake_query(query, **_kw):
        calls.append(query)
        return OVERPASS_RESPONSE

    monkeypatch.setattr(osm_infra, "overpass_query", fake_query)
    from gis_layers.types import AOI, BBox

    aoi = AOI("test", BBox(13.2, 62.1, 13.3, 62.2))
    provider = OsmInfraProvider.fetch(aoi, cache_dir=tmp_path)
    assert provider.feature_count == 10
    cached = json.loads((tmp_path / "infra_test.geojson").read_text())
    assert len(cached["features"]) == 10
    assert 'aerialway' in calls[0] and 'generator:source' in calls[0]
    OsmInfraProvider.fetch(aoi, cache_dir=tmp_path)
    assert len(calls) == 1


def test_cli_fetch_writes_infra_and_aoi_index(tmp_path, monkeypatch):
    monkeypatch.setattr(osm_infra, "overpass_query", lambda q, **kw: OVERPASS_RESPONSE)
    aoi_file = tmp_path / "aoi.geojson"
    aoi_file.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "test"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [13.2, 62.1], [13.2, 62.2], [13.3, 62.2], [13.3, 62.1], [13.2, 62.1]]]},
        }],
    }))
    out = tmp_path / "data"
    assert cli.main(["fetch", "--aoi", str(aoi_file), "--out", str(out), "--layers", "infra"]) == 0
    assert (out / "osm" / "infra_test.geojson").exists()
    index = json.loads((out / "aoi.json").read_text())
    assert index["aois"] == [{"name": "test", "bbox": [13.2, 62.1, 13.3, 62.2]}]
    assert index["layers"] == ["infra"]
    assert "elevation_source" not in index

    cli.write_aoi_index(out, [], {"roads"})
    assert json.loads((out / "aoi.json").read_text())["layers"] == ["infra", "roads"]

    assert cli.main(["fetch", "--aoi", str(aoi_file), "--out", str(out), "--layers", "bogus"]) == 1
