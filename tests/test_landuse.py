from gis_layers.crs import unproject
from gis_layers.providers.osm_landuse import OsmLanduseProvider
from gis_layers.types import LatLon


def test_landuse_classifies_west_and_east(synthetic_landuse, aoi_center):
    provider = OsmLanduseProvider.from_geojson(synthetic_landuse)
    assert provider.polygon_count == 2

    # Sample at offsets that we know fall inside each polygon (in projected meters)
    from gis_layers.crs import project
    cx, cy = project(*aoi_center)
    west_lat, west_lon = unproject(cx - 50.0, cy)
    east_lat, east_lon = unproject(cx + 50.0, cy)

    assert provider.landuse_at(LatLon(west_lat, west_lon)) == "landuse=forest"
    assert provider.landuse_at(LatLon(east_lat, east_lon)) == "landuse=residential"


def test_landuse_outside_returns_none(synthetic_landuse):
    provider = OsmLanduseProvider.from_geojson(synthetic_landuse)
    assert provider.landuse_at(LatLon(0.0, 0.0)) is None
