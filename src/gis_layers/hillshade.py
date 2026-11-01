"""Hillshade raster for an AOI, for map display.

The grid is axis-aligned in SWEREF99 TM and covers all four corners of the AOI's
lat/lon box. ``corners`` are the grid's corners in WGS84 (top-left, top-right,
bottom-right, bottom-left), which is what a MapLibre image source takes.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from gis_layers.crs import project, unproject
from gis_layers.types import BBox, LatLon


@dataclass(frozen=True)
class Hillshade:
    """``rgba`` is (rows, cols, 4) uint8, row 0 north; transparent where the DEM has no data."""

    rgba: np.ndarray
    corners: list[list[float]]
    resolution_m: float

    def png(self) -> bytes:
        import rasterio
        from rasterio.errors import NotGeoreferencedWarning
        import warnings

        rows, cols, _ = self.rgba.shape
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.MemoryFile() as mem:
                with mem.open(driver="PNG", width=cols, height=rows, count=4, dtype="uint8") as dst:
                    dst.write(np.moveaxis(self.rgba, 2, 0))
                return mem.read()


def hillshade(
    elevation: Any,
    bbox: BBox,
    *,
    resolution_m: float = 15.0,
    azimuth_deg: float = 315.0,
    altitude_deg: float = 45.0,
    max_px: int = 2000,
) -> Hillshade:
    """Shade ``elevation`` (anything with ``height_at_xy`` or ``height_at``) over ``bbox``."""
    corners_xy = [project(lat, lon) for lat in (bbox.min_lat, bbox.max_lat) for lon in (bbox.min_lon, bbox.max_lon)]
    left, right = min(x for x, _ in corners_xy), max(x for x, _ in corners_xy)
    bottom, top = min(y for _, y in corners_xy), max(y for _, y in corners_xy)
    res = max(resolution_m, (right - left) / max_px, (top - bottom) / max_px)
    cols = max(2, int(math.ceil((right - left) / res)))
    rows = max(2, int(math.ceil((top - bottom) / res)))
    right, bottom = left + cols * res, top - rows * res

    if hasattr(elevation, "height_at_xy"):
        sample = elevation.height_at_xy
    else:
        def sample(x: float, y: float):
            return elevation.height_at(LatLon(*unproject(x, y)))

    z = np.full((rows, cols), np.nan, dtype=np.float64)
    for r in range(rows):
        y = top - (r + 0.5) * res
        for c in range(cols):
            h = sample(left + (c + 0.5) * res, y)
            if h is not None:
                z[r, c] = h

    shade = _shade(z, res, azimuth_deg, altitude_deg)
    valid = np.isfinite(shade)
    gray = np.where(valid, np.clip(shade, 0.0, 1.0) * 255.0, 0.0).astype(np.uint8)
    rgba = np.dstack([gray, gray, gray, np.where(valid, 255, 0).astype(np.uint8)])

    def ll(x: float, y: float) -> list[float]:
        lat, lon = unproject(x, y)
        return [lon, lat]

    corners = [ll(left, top), ll(right, top), ll(right, bottom), ll(left, bottom)]
    return Hillshade(rgba=rgba, corners=corners, resolution_m=res)


def _shade(z: np.ndarray, res: float, azimuth_deg: float, altitude_deg: float) -> np.ndarray:
    """Lambert shading, 0..1; NaN where a neighbour is missing. Row 0 is north."""
    filled = np.where(np.isfinite(z), z, np.nanmean(z) if np.isfinite(z).any() else 0.0)
    dz_drow, dz_dcol = np.gradient(filled, res)
    dz_dx, dz_dy = dz_dcol, -dz_drow
    slope = np.arctan(np.hypot(dz_dx, dz_dy))
    aspect = np.arctan2(-dz_dx, -dz_dy)
    az = math.radians(azimuth_deg)
    alt = math.radians(altitude_deg)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    missing = ~np.isfinite(z)
    if missing.any():
        grown = missing.copy()
        grown[1:, :] |= missing[:-1, :]
        grown[:-1, :] |= missing[1:, :]
        grown[:, 1:] |= missing[:, :-1]
        grown[:, :-1] |= missing[:, 1:]
        shade = np.where(grown, np.nan, shade)
    return shade
