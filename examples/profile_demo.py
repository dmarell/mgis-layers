"""Demo: fetch land use and roads for an AOI and sample a terrain profile.

Reads an elevation cache written by `gis-layers fetch` (open DEM or Lantmäteriet
tiles). Land use and roads are fetched from Overpass into `<data-dir>/osm`.

Usage, from the repository root:
    gis-layers fetch --aoi examples/aoi_ska_edeby.geojson --out data/ska_edeby
    python examples/profile_demo.py data/ska_edeby
"""
from __future__ import annotations

import sys
from pathlib import Path

from gis_layers import AOI, LatLon, OsmLanduseProvider, OsmRoadsProvider, terrain_profile
from gis_layers.providers.elevation_loader import load_elevation

_EXAMPLES = Path(__file__).resolve().parent

# Along runway 03/21 at Skå-Edeby airfield.
RUNWAY_SW = LatLon(59.34029, 17.73718)
RUNWAY_NE = LatLon(59.34653, 17.74423)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: profile_demo.py <data-dir>", file=sys.stderr)
        return 2
    data_dir = Path(sys.argv[1])

    aoi = AOI.from_geojson(_EXAMPLES / "aoi_ska_edeby.geojson")[0]
    elevation = load_elevation(data_dir / "elevation")
    landuse = OsmLanduseProvider.fetch(aoi, cache_dir=data_dir / "osm")
    roads = OsmRoadsProvider.fetch(aoi, cache_dir=data_dir / "osm")

    profile = terrain_profile(RUNWAY_SW, RUNWAY_NE, step_m=10.0, elevation=elevation, landuse=landuse)
    for sample in profile.samples[::10]:
        print(
            f"{sample.distance_m:6.0f} m  "
            f"{sample.point.lat:.5f},{sample.point.lon:.5f}  "
            f"elev={sample.elevation_m}  landuse={sample.landuse}"
        )

    midpoint = profile.samples[len(profile.samples) // 2].point
    nearest = roads.nearest_road(midpoint, max_distance_m=200.0)
    if nearest:
        print(f"nearest road at midpoint: {nearest.distance_m:.1f} m {nearest.highway} {nearest.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
