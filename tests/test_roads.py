from gis_layers.crs import project, unproject
from gis_layers.providers.osm_roads import OsmRoadsProvider
from gis_layers.types import LatLon


def test_nearest_road_distance_matches_offset(synthetic_roads, aoi_center):
    provider = OsmRoadsProvider.from_geojson(synthetic_roads)
    cx, cy = project(*aoi_center)
    offset_m = 25.0
    off_lat, off_lon = unproject(cx + offset_m, cy)
    hit = provider.nearest_road(LatLon(off_lat, off_lon), max_distance_m=100.0)
    assert hit is not None
    assert hit.highway == "residential"
    assert hit.name == "Test Road"
    assert abs(hit.distance_m - offset_m) < 0.1


def test_nearest_road_returns_none_when_too_far(synthetic_roads, aoi_center):
    provider = OsmRoadsProvider.from_geojson(synthetic_roads)
    cx, cy = project(*aoi_center)
    far_lat, far_lon = unproject(cx + 500.0, cy)
    assert provider.nearest_road(LatLon(far_lat, far_lon), max_distance_m=50.0) is None


def _line(cx: float, cy: float, dx: float, length_m: float = 200.0) -> list[list[float]]:
    coords = []
    for dy in (-length_m / 2, length_m / 2):
        lat, lon = unproject(cx + dx, cy + dy)
        coords.append([lon, lat])
    return coords


def test_tunnel_bridge_and_snowmobile_flags(aoi_center):
    cx, cy = project(*aoi_center)
    features = [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": _line(cx, cy, 0.0)},
         "properties": {"highway": "primary", "tunnel": "yes"}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": _line(cx, cy, 300.0)},
         "properties": {"highway": "secondary", "bridge": "viaduct"}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": _line(cx, cy, 600.0)},
         "properties": {"highway": "track", "snowmobile": "designated", "tunnel": "no"}},
        {"type": "Feature",
         "geometry": {"type": "MultiLineString", "coordinates": [_line(cx, cy, 900.0)]},
         "properties": {"type": "route", "route": "snowmobile", "name": "Led 1"}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": _line(cx, cy, 1200.0)},
         "properties": {"railway": "rail"}},
    ]
    provider = OsmRoadsProvider._from_features(features)
    assert provider.road_count == 4

    def hit(dx: float):
        lat, lon = unproject(cx + dx + 5.0, cy)
        return provider.nearest_road(LatLon(lat, lon), max_distance_m=20.0)

    tunnel = hit(0.0)
    assert tunnel.highway == "primary" and tunnel.tunnel and not tunnel.bridge
    bridge = hit(300.0)
    assert bridge.bridge and not bridge.tunnel
    track = hit(600.0)
    assert track.highway == "track" and track.snowmobile and not track.tunnel
    route = hit(900.0)
    assert route.highway == "snowmobile" and route.snowmobile and route.name == "Led 1"
    assert hit(1200.0) is None

    lat, lon = unproject(cx + 290.0, cy)
    near = provider.roads_near(LatLon(lat, lon), max_distance_m=400.0)
    assert [h.highway for h in near] == ["secondary", "primary", "track"]
    assert near[0].bridge
