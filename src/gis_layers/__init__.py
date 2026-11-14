"""gis_layers — fetch elevation/landuse/roads/infrastructure for an area, sample profiles."""

from gis_layers.types import AOI, BBox, LatLon, Profile, ProfileSample
from gis_layers.profile import terrain_profile
from gis_layers.providers.elevation import ElevationProvider
from gis_layers.providers.elevation_loader import load_elevation
from gis_layers.providers.nmd_landcover import NmdLandcoverProvider
from gis_layers.providers.open_elevation import OpenElevationProvider
from gis_layers.providers.osm_infra import InfraHit, OsmInfraProvider
from gis_layers.providers.osm_landuse import OsmLanduseProvider
from gis_layers.providers.osm_roads import OsmRoadsProvider, RoadHit
from gis_layers.providers.tree_height import TreeHeightProvider

__all__ = [
    "AOI",
    "BBox",
    "LatLon",
    "Profile",
    "ProfileSample",
    "terrain_profile",
    "ElevationProvider",
    "OpenElevationProvider",
    "load_elevation",
    "NmdLandcoverProvider",
    "InfraHit",
    "OsmInfraProvider",
    "OsmLanduseProvider",
    "OsmRoadsProvider",
    "RoadHit",
    "TreeHeightProvider",
]
