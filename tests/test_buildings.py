from gis_layers.providers.osm_buildings import OsmBuildingsProvider, building_properties


def _square(lon: float, lat: float, d: float = 1e-4) -> dict:
    ring = [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


def test_building_properties_parse_osm_numbers():
    p = building_properties({"building": "house", "height": "6,5 m", "building:levels": "2", "roof:shape": "gabled"})
    assert p == {"kind": "house", "height_m": 6.5, "levels": 2.0, "roof_shape": "gabled", "name": None}
    assert building_properties({"building": "yes", "height": "tall"})["height_m"] is None
    assert building_properties({"building": "no"}) is None
    assert building_properties({"amenity": "shelter"}) is None


def test_provider_keeps_only_footprints():
    features = [
        {"type": "Feature", "id": "way/1", "geometry": _square(13.2, 62.1), "properties": {"building": "yes"}},
        {"type": "Feature", "id": "node/2", "geometry": {"type": "Point", "coordinates": [13.2, 62.1]},
         "properties": {"building": "yes"}},
        {"type": "Feature", "id": "way/3", "geometry": _square(13.3, 62.1), "properties": {"landuse": "residential"}},
    ]
    provider = OsmBuildingsProvider(features)
    assert provider.building_count == 1 and provider.features[0]["id"] == "way/1"
