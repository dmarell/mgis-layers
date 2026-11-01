"""OpenStreetMap landuse provider.

Fetches `landuse=*` and `natural=*` polygons over an AOI via Overpass,
caches as GeoJSON, then answers ``landuse_at(point)`` via point-in-polygon
against an STRtree built in SWEREF99 TM (so the index is in meters).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import shape
from shapely.ops import transform as shapely_transform
from shapely.strtree import STRtree

from gis_layers.crs import project, to_projected
from gis_layers.types import AOI, LatLon

from .overpass import (
    DEFAULT_OVERPASS_URL,
    bbox_clause,
    osm_response_to_geojson,
    overpass_query,
)

# Tags considered "land cover" for classification, in priority order
# (more specific tags take precedence when polygons overlap).
LANDUSE_TAGS = ("landuse", "natural", "leisure", "amenity", "water")


@dataclass(frozen=True)
class _Polygon:
    geometry: Any  # shapely geometry in SWEREF99 TM
    label: str
    priority: int  # smaller = higher priority


class OsmLanduseProvider:
    def __init__(self, polygons: list[_Polygon]) -> None:
        self._polygons = polygons
        self._tree = STRtree([p.geometry for p in polygons]) if polygons else None

    @property
    def polygon_count(self) -> int:
        return len(self._polygons)

    def landuse_at(self, point: LatLon) -> str | None:
        if self._tree is None:
            return None
        x, y = project(point.lat, point.lon)
        from shapely.geometry import Point
        query_point = Point(x, y)
        candidates = self._tree.query(query_point)
        best: _Polygon | None = None
        for index in candidates:
            polygon = self._polygons[int(index)]
            if not polygon.geometry.contains(query_point):
                continue
            if best is None or polygon.priority < best.priority:
                best = polygon
        return best.label if best else None

    # --- construction ------------------------------------------------------

    @classmethod
    def from_geojson(cls, path: str | Path) -> "OsmLanduseProvider":
        path = Path(path)
        data = json.loads(path.read_text("utf-8"))
        return cls._from_features(data.get("features") or [])

    @classmethod
    def fetch(
        cls,
        aoi: AOI,
        cache_dir: str | Path,
        *,
        overpass_url: str = DEFAULT_OVERPASS_URL,
        timeout: float = 180.0,
        force: bool = False,
    ) -> "OsmLanduseProvider":
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"landuse_{_safe(aoi.name)}.geojson"
        if target.exists() and not force:
            return cls.from_geojson(target)
        bbox = bbox_clause(aoi.bbox)
        query = f"""
[out:json][timeout:{int(timeout)}];
(
  way["landuse"]{bbox};
  relation["landuse"]["type"="multipolygon"]{bbox};
  way["natural"]{bbox};
  relation["natural"]["type"="multipolygon"]{bbox};
  way["leisure"]{bbox};
  relation["leisure"]["type"="multipolygon"]{bbox};
  way["water"]{bbox};
  relation["water"]["type"="multipolygon"]{bbox};
  way["waterway"~"^(river|stream|canal|ditch|drain|brook)$"]{bbox};
);
out geom;
"""
        response = overpass_query(query, url=overpass_url, timeout=timeout)
        feature_collection = osm_response_to_geojson(response)
        target.write_text(json.dumps(feature_collection), "utf-8")
        return cls._from_features(feature_collection["features"])

    # --- helpers ----------------------------------------------------------

    @classmethod
    def _from_features(cls, features: list[dict[str, Any]]) -> "OsmLanduseProvider":
        transformer = to_projected()

        def _project_xy(x: float, y: float, _z: float | None = None):
            # shapely passes (x, y) which is (lon, lat) for our WGS84 inputs
            xx, yy = transformer.transform(x, y)
            return xx, yy

        polygons: list[_Polygon] = []
        for feature in features:
            geometry = feature.get("geometry")
            props = feature.get("properties") or {}
            if not isinstance(geometry, dict):
                continue
            geo_type = geometry.get("type")
            if geo_type not in {"Polygon", "MultiPolygon"}:
                continue
            try:
                geom = shape(geometry)
            except Exception:
                continue
            if geom.is_empty or not geom.is_valid:
                geom = geom.buffer(0)
                if geom.is_empty:
                    continue
            projected = shapely_transform(_project_xy, geom)
            label, priority = _classify(props)
            polygons.append(_Polygon(projected, label=label, priority=priority))
        return cls(polygons)


def landuse_label(tags: dict[str, Any]) -> str:
    """``"<key>=<value>"`` for the first landuse-like tag, as ``landuse_at`` returns it."""
    return _classify(tags)[0]


def _classify(tags: dict[str, Any]) -> tuple[str, int]:
    for priority, key in enumerate(LANDUSE_TAGS):
        value = tags.get(key)
        if isinstance(value, str) and value:
            return f"{key}={value}", priority
    return "unknown", len(LANDUSE_TAGS)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "aoi"
