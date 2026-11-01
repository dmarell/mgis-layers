from gis_layers.crs import project, unproject
from gis_layers.profile import terrain_profile
from gis_layers.providers.elevation import ElevationProvider
from gis_layers.providers.osm_landuse import OsmLanduseProvider
from gis_layers.types import LatLon


def test_profile_west_to_east_walks_slope_and_landuse(
    synthetic_dem_dir, synthetic_landuse, aoi_center
):
    elevation = ElevationProvider.from_cache(synthetic_dem_dir)
    landuse = OsmLanduseProvider.from_geojson(synthetic_landuse)

    cx, cy = project(*aoi_center)
    start_lat, start_lon = unproject(cx - 80.0, cy)
    end_lat, end_lon = unproject(cx + 80.0, cy)

    profile = terrain_profile(
        LatLon(start_lat, start_lon),
        LatLon(end_lat, end_lon),
        step_m=10.0,
        elevation=elevation,
        landuse=landuse,
    )

    assert len(profile.samples) >= 17
    assert abs(profile.length_m - 160.0) < 1.0

    first_elev = profile.samples[0].elevation_m
    last_elev = profile.samples[-1].elevation_m
    assert first_elev is not None and last_elev is not None
    # Slope 1m per 10m of x → 16m rise across 160m
    assert abs(last_elev - first_elev - 16.0) < 0.5

    landuse_labels = {s.landuse for s in profile.samples if s.landuse}
    assert "landuse=forest" in landuse_labels
    assert "landuse=residential" in landuse_labels


def test_profile_step_zero_rejected():
    import pytest
    with pytest.raises(ValueError):
        terrain_profile(LatLon(59.0, 18.0), LatLon(59.1, 18.1), step_m=0.0)
