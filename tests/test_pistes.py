from gis_layers.providers.osm_pistes import OsmPistesProvider


def _ring(lon: float, lat: float, d: float = 1e-3) -> list:
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]


def test_area_pistes_become_polygons_and_lines_stay_lines():
    features = [
        {"type": "Feature", "id": "way/1", "geometry": {"type": "LineString", "coordinates": _ring(17.74, 59.34)},
         "properties": {"piste:type": "downhill", "area": "yes"}},
        {"type": "Feature", "id": "way/2", "geometry": {"type": "LineString", "coordinates": _ring(17.75, 59.34)},
         "properties": {"piste:type": "nordic"}},
        {"type": "Feature", "id": "way/3", "geometry": {"type": "LineString", "coordinates": [[17.74, 59.34], [17.75, 59.35]]},
         "properties": {"highway": "track"}},
    ]
    provider = OsmPistesProvider(features)
    assert [f["id"] for f in provider.features] == ["way/1", "way/2"]
    assert provider.features[0]["geometry"]["type"] == "Polygon"
    assert provider.features[1]["geometry"]["type"] == "LineString"
