"""Synthetic fixtures so tests don't touch the network or auth."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from gis_layers.crs import project


def _bbox_around(lat: float, lon: float, half_size_m: float) -> tuple[float, float, float, float]:
    cx, cy = project(lat, lon)
    return (cx - half_size_m, cy - half_size_m, cx + half_size_m, cy + half_size_m)


@pytest.fixture
def synthetic_dem_dir(tmp_path: Path) -> Path:
    """A 200x200m DEM tile, 1m resolution, sloped from west to east.

    Centered roughly on Ska-Edeby airfield; height = 100 + (x - left) / 10 (so 100..120).
    """
    center_lat, center_lon = 59.3427, 17.7392
    left, bottom, right, top = _bbox_around(center_lat, center_lon, 100.0)
    width = int(round(right - left))
    height = int(round(top - bottom))

    xs = np.arange(width, dtype=np.float32)
    elevation = 100.0 + xs / 10.0
    array = np.broadcast_to(elevation, (height, width)).astype(np.float32)

    transform = from_origin(left, top, 1.0, 1.0)
    out = tmp_path / "elevation"
    out.mkdir()
    tile_path = out / "synthetic.tif"
    with rasterio.open(
        tile_path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="float32",
        crs="EPSG:3006",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(array, 1)
    return out


@pytest.fixture
def synthetic_landuse(tmp_path: Path) -> Path:
    """Two abutting polygons: forest in the west half, residential in the east."""
    center_lat, center_lon = 59.3427, 17.7392
    left, bottom, right, top = _bbox_around(center_lat, center_lon, 100.0)
    mid_x = (left + right) / 2.0

    from gis_layers.crs import unproject

    def ring(corners: list[tuple[float, float]]) -> list[list[float]]:
        coords = []
        for x, y in corners:
            lat, lon = unproject(x, y)
            coords.append([lon, lat])
        coords.append(coords[0])
        return coords

    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [ring([(left, bottom), (mid_x, bottom), (mid_x, top), (left, top)])],
                },
                "properties": {"landuse": "forest"},
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [ring([(mid_x, bottom), (right, bottom), (right, top), (mid_x, top)])],
                },
                "properties": {"landuse": "residential"},
            },
        ],
    }
    path = tmp_path / "landuse.geojson"
    path.write_text(json.dumps(feature_collection), "utf-8")
    return path


@pytest.fixture
def synthetic_roads(tmp_path: Path) -> Path:
    """One straight road running south-to-north through the AOI center."""
    center_lat, center_lon = 59.3427, 17.7392
    left, bottom, right, top = _bbox_around(center_lat, center_lon, 100.0)
    mid_x = (left + right) / 2.0

    from gis_layers.crs import unproject

    south_lat, south_lon = unproject(mid_x, bottom)
    north_lat, north_lon = unproject(mid_x, top)

    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[south_lon, south_lat], [north_lon, north_lat]],
                },
                "properties": {"highway": "residential", "name": "Test Road"},
            }
        ],
    }
    path = tmp_path / "roads.geojson"
    path.write_text(json.dumps(feature_collection), "utf-8")
    return path


@pytest.fixture
def aoi_center() -> tuple[float, float]:
    return 59.3427, 17.7392
