"""Terrain profile sampling between two WGS84 points.

The line is sampled at fixed metric intervals in SWEREF99 TM (EPSG:3006),
so spacing is uniform on the ground regardless of latitude. Each sample
queries the providers passed in; missing providers leave that field None.
"""
from __future__ import annotations

import math

from gis_layers.crs import project, unproject
from gis_layers.providers.base import ElevationLayer, LanduseLayer
from gis_layers.types import LatLon, Profile, ProfileSample


def terrain_profile(
    start: LatLon,
    end: LatLon,
    *,
    step_m: float = 10.0,
    elevation: ElevationLayer | None = None,
    landuse: LanduseLayer | None = None,
) -> Profile:
    if step_m <= 0:
        raise ValueError("step_m must be positive")

    sx, sy = project(start.lat, start.lon)
    ex, ey = project(end.lat, end.lon)
    dx = ex - sx
    dy = ey - sy
    length = math.hypot(dx, dy)

    samples: list[ProfileSample] = []
    if length == 0.0:
        samples.append(_sample(0.0, sx, sy, elevation, landuse))
        return Profile(start=start, end=end, step_m=step_m, samples=samples)

    n_steps = max(1, int(math.ceil(length / step_m)))
    actual_step = length / n_steps
    for i in range(n_steps + 1):
        s = i * actual_step
        t = s / length
        x = sx + t * dx
        y = sy + t * dy
        samples.append(_sample(s, x, y, elevation, landuse))
    return Profile(start=start, end=end, step_m=actual_step, samples=samples)


def _sample(
    distance_m: float,
    x: float,
    y: float,
    elevation: ElevationLayer | None,
    landuse: LanduseLayer | None,
) -> ProfileSample:
    lat, lon = unproject(x, y)
    point = LatLon(lat, lon)
    elevation_m = elevation.height_at(point) if elevation else None
    landuse_label = landuse.landuse_at(point) if landuse else None
    return ProfileSample(
        distance_m=distance_m,
        point=point,
        elevation_m=elevation_m,
        landuse=landuse_label,
    )
