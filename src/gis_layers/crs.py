"""Coordinate reference system helpers.

All sampling and distance work happens in SWEREF99 TM (EPSG:3006) — a
projected CRS in meters that is accurate across Sweden. WGS84 is used at
the API boundary because that's what users and GeoJSON expect.
"""
from __future__ import annotations

from functools import lru_cache

from pyproj import Transformer

WGS84 = "EPSG:4326"
SWEREF99_TM = "EPSG:3006"


@lru_cache(maxsize=1)
def to_projected() -> Transformer:
    return Transformer.from_crs(WGS84, SWEREF99_TM, always_xy=True)


@lru_cache(maxsize=1)
def to_wgs84() -> Transformer:
    return Transformer.from_crs(SWEREF99_TM, WGS84, always_xy=True)


def project(lat: float, lon: float) -> tuple[float, float]:
    """WGS84 (lat, lon) → SWEREF99 TM (x, y) meters."""
    x, y = to_projected().transform(lon, lat)
    return float(x), float(y)


def unproject(x: float, y: float) -> tuple[float, float]:
    """SWEREF99 TM (x, y) → WGS84 (lat, lon)."""
    lon, lat = to_wgs84().transform(x, y)
    return float(lat), float(lon)
