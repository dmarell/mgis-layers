from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float

    def __iter__(self) -> Iterator[float]:
        yield self.lat
        yield self.lon

    @classmethod
    def parse(cls, text: str) -> "LatLon":
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 2:
            raise ValueError(f"expected 'lat,lon', got {text!r}")
        return cls(float(parts[0]), float(parts[1]))


@dataclass(frozen=True)
class BBox:
    """Axis-aligned bounding box in WGS84 (lon/lat) degrees."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def __post_init__(self) -> None:
        if self.min_lon > self.max_lon or self.min_lat > self.max_lat:
            raise ValueError(f"degenerate bbox: {self}")

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

    def expand_meters(self, buffer_m: float) -> "BBox":
        if buffer_m <= 0:
            return self
        mid_lat = 0.5 * (self.min_lat + self.max_lat)
        lat_delta = buffer_m / 111_320.0
        lon_scale = 111_320.0 * max(math.cos(math.radians(mid_lat)), 0.01)
        lon_delta = buffer_m / lon_scale
        return BBox(
            max(self.min_lon - lon_delta, -180.0),
            max(self.min_lat - lat_delta, -90.0),
            min(self.max_lon + lon_delta, 180.0),
            min(self.max_lat + lat_delta, 90.0),
        )

    def contains(self, point: LatLon) -> bool:
        return (
            self.min_lon <= point.lon <= self.max_lon
            and self.min_lat <= point.lat <= self.max_lat
        )

    @classmethod
    def from_points(cls, points: list[LatLon], buffer_m: float = 0.0) -> "BBox":
        if not points:
            raise ValueError("at least one point required")
        min_lon = min(p.lon for p in points)
        max_lon = max(p.lon for p in points)
        min_lat = min(p.lat for p in points)
        max_lat = max(p.lat for p in points)
        return cls(min_lon, min_lat, max_lon, max_lat).expand_meters(buffer_m)


@dataclass(frozen=True)
class AOI:
    """Named area of interest, WGS84."""

    name: str
    bbox: BBox

    @classmethod
    def from_bbox(
        cls,
        *,
        name: str,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
    ) -> "AOI":
        return cls(name=name, bbox=BBox(min_lon, min_lat, max_lon, max_lat))

    @classmethod
    def from_geojson(cls, path: str | Path) -> list["AOI"]:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        features = _features(data)
        aois: list[AOI] = []
        for index, feature in enumerate(features, start=1):
            props = feature.get("properties") or {}
            name = str(props.get("name") or props.get("id") or f"aoi_{index}")
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict):
                raise ValueError(f"{path}: feature {name!r} has no geometry")
            bbox = _bbox_of_geometry(geometry)
            aois.append(cls(name=name, bbox=bbox))
        if not aois:
            raise ValueError(f"{path}: no AOI features found")
        return aois


@dataclass(frozen=True)
class ProfileSample:
    """One sampled point along a terrain profile."""

    distance_m: float
    point: LatLon
    elevation_m: float | None
    landuse: str | None


@dataclass(frozen=True)
class Profile:
    """Sampled terrain profile from `start` to `end`."""

    start: LatLon
    end: LatLon
    step_m: float
    samples: list[ProfileSample] = field(default_factory=list)

    @property
    def length_m(self) -> float:
        return self.samples[-1].distance_m if self.samples else 0.0


def _features(data: dict[str, Any]) -> list[dict[str, Any]]:
    geo_type = data.get("type")
    if geo_type == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            raise ValueError("FeatureCollection has no features array")
        return [f for f in features if isinstance(f, dict)]
    if geo_type == "Feature":
        return [data]
    raise ValueError("expected GeoJSON FeatureCollection or Feature")


def _bbox_of_geometry(geometry: dict[str, Any]) -> BBox:
    points = list(_iter_positions(geometry))
    if not points:
        raise ValueError("geometry has no positions")
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return BBox(min(lons), min(lats), max(lons), max(lats))


def _iter_positions(geometry: dict[str, Any]):
    if geometry.get("type") == "GeometryCollection":
        for child in geometry.get("geometries") or []:
            if isinstance(child, dict):
                yield from _iter_positions(child)
        return
    yield from _iter_coords(geometry.get("coordinates"))


def _iter_coords(value: Any):
    if isinstance(value, (list, tuple)):
        if (
            len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            lon = float(value[0])
            lat = float(value[1])
            if math.isfinite(lon) and math.isfinite(lat):
                yield lon, lat
            return
        for child in value:
            yield from _iter_coords(child)
