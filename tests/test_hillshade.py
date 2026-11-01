"""Hillshade on the synthetic west→east slope (conftest.synthetic_dem_dir)."""
from __future__ import annotations

import math

import numpy as np

from gis_layers.crs import project, unproject
from gis_layers.hillshade import hillshade
from gis_layers.providers.elevation_loader import load_elevation
from gis_layers.types import BBox

FLAT = math.sin(math.radians(45.0))


def _bbox(center, half_m: float) -> BBox:
    cx, cy = project(*center)
    s_lat, w_lon = unproject(cx - half_m, cy - half_m)
    n_lat, e_lon = unproject(cx + half_m, cy + half_m)
    return BBox(w_lon, s_lat, e_lon, n_lat)


def _mid(h) -> float:
    rows, cols, _ = h.rgba.shape
    return h.rgba[rows // 2, cols // 2, 0] / 255.0


def test_slope_facing_sun_is_brighter_than_flat(synthetic_dem_dir, aoi_center):
    dem = load_elevation(synthetic_dem_dir)
    lit = hillshade(dem, _bbox(aoi_center, 60.0), resolution_m=5.0, azimuth_deg=270.0)
    dark = hillshade(dem, _bbox(aoi_center, 60.0), resolution_m=5.0, azimuth_deg=90.0)
    assert _mid(lit) > FLAT + 0.02
    assert _mid(dark) < FLAT - 0.02


def test_outside_dem_is_transparent_and_corners(synthetic_dem_dir, aoi_center):
    dem = load_elevation(synthetic_dem_dir)
    h = hillshade(dem, _bbox(aoi_center, 200.0), resolution_m=10.0)
    alpha = h.rgba[:, :, 3]
    assert alpha[0, 0] == 0 and alpha[alpha.shape[0] // 2, alpha.shape[1] // 2] == 255
    (tl, tr, br, bl) = h.corners
    assert tl[1] > bl[1] and tr[0] > tl[0] and br[0] > bl[0]
    assert h.png()[:8] == b"\x89PNG\r\n\x1a\n"


def test_max_px_caps_size(synthetic_dem_dir, aoi_center):
    dem = load_elevation(synthetic_dem_dir)
    h = hillshade(dem, _bbox(aoi_center, 100.0), resolution_m=1.0, max_px=50)
    assert max(h.rgba.shape[:2]) <= 51
    assert h.resolution_m >= 4.0
