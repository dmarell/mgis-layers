"""Overpass JSON → GeoJSON conversion tests."""
from __future__ import annotations

from shapely.geometry import Point, shape

from gis_layers.providers.overpass import osm_response_to_geojson


def _relation_with_open_segments(
    *,
    rel_id: int,
    segments: list[list[tuple[float, float]]],
    inner_from: int | None = None,
    tags: dict[str, str] | None = None,
) -> dict:
    members = []
    for index, segment in enumerate(segments):
        role = "inner" if inner_from is not None and index >= inner_from else "outer"
        members.append(
            {
                "type": "way",
                "ref": 1000 + index,
                "role": role,
                "geometry": [{"lat": lat, "lon": lon} for lon, lat in segment],
            }
        )
    return {
        "elements": [
            {
                "type": "relation",
                "id": rel_id,
                "tags": {"type": "multipolygon", **(tags or {})},
                "members": members,
            }
        ]
    }


def test_multipolygon_from_open_way_segments():
    """OSM forest relations use open outer ways that must be stitched."""
    response = _relation_with_open_segments(
        rel_id=5300663,
        tags={"landuse": "forest"},
        segments=[
            [(0.0, 0.0), (1.0, 0.0)],
            [(1.0, 0.0), (1.0, 1.0)],
            [(1.0, 1.0), (0.0, 1.0)],
            [(0.0, 1.0), (0.0, 0.0)],
        ],
    )
    fc = osm_response_to_geojson(response)
    assert len(fc["features"]) == 1
    geom = shape(fc["features"][0]["geometry"])
    assert geom.contains(Point(0.5, 0.5))
    assert fc["features"][0]["properties"]["landuse"] == "forest"


def test_multipolygon_with_hole_from_open_segments():
    response = _relation_with_open_segments(
        rel_id=42,
        tags={"natural": "wood"},
        segments=[
            [(0.0, 0.0), (4.0, 0.0)],
            [(4.0, 0.0), (4.0, 4.0)],
            [(4.0, 4.0), (0.0, 4.0)],
            [(0.0, 4.0), (0.0, 0.0)],
            [(1.0, 1.0), (3.0, 1.0)],
            [(3.0, 1.0), (3.0, 3.0)],
            [(3.0, 3.0), (1.0, 3.0)],
            [(1.0, 3.0), (1.0, 1.0)],
        ],
        inner_from=4,
    )
    fc = osm_response_to_geojson(response)
    assert len(fc["features"]) == 1
    geom = shape(fc["features"][0]["geometry"])
    assert geom.contains(Point(0.5, 0.5))
    assert not geom.contains(Point(2.0, 2.0))


def test_tagged_nodes_become_points_untagged_do_not():
    response = {
        "elements": [
            {"type": "node", "id": 1, "lat": 62.1, "lon": 13.2, "tags": {"man_made": "mast", "height": "45"}},
            {"type": "node", "id": 2, "lat": 62.2, "lon": 13.3},
        ]
    }
    fc = osm_response_to_geojson(response)
    assert len(fc["features"]) == 1
    feature = fc["features"][0]
    assert feature["geometry"] == {"type": "Point", "coordinates": [13.2, 62.1]}
    assert feature["properties"]["man_made"] == "mast"


def test_route_relation_becomes_multilinestring_with_relation_tags():
    response = {
        "elements": [
            {
                "type": "relation",
                "id": 7,
                "tags": {"type": "route", "route": "snowmobile", "name": "Skoterled"},
                "members": [
                    {"type": "way", "ref": 1, "geometry": [{"lat": 0.0, "lon": 0.0}, {"lat": 0.0, "lon": 1.0}]},
                    {"type": "way", "ref": 2, "geometry": [{"lat": 0.0, "lon": 1.0}, {"lat": 1.0, "lon": 1.0}]},
                    {"type": "node", "ref": 3},
                ],
            }
        ]
    }
    fc = osm_response_to_geojson(response)
    assert len(fc["features"]) == 1
    geom = shape(fc["features"][0]["geometry"])
    assert geom.geom_type == "MultiLineString"
    assert len(geom.geoms) == 2
    assert fc["features"][0]["properties"]["route"] == "snowmobile"
