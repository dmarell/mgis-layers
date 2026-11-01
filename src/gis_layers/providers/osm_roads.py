"""OpenStreetMap roads provider.

Fetches `highway=*` ways plus snowmobile trails (`snowmobile=designated|yes`
ways and `route=snowmobile` relations) via Overpass, caches as GeoJSON, then
answers ``nearest_road(point, max_distance_m)`` via STRtree on SWEREF99 TM line
geometries. Each hit carries road class (`highway`, or ``"snowmobile"`` for a
trail without one) and the tunnel/bridge/snowmobile flags.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import Point, shape
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


@dataclass(frozen=True)
class RoadHit:
    distance_m: float
    highway: str
    name: str | None
    tunnel: bool = False
    bridge: bool = False
    snowmobile: bool = False


@dataclass(frozen=True)
class _Road:
    geometry: Any
    highway: str
    name: str | None
    tunnel: bool = False
    bridge: bool = False
    snowmobile: bool = False


class OsmRoadsProvider:
    def __init__(self, roads: list[_Road]) -> None:
        self._roads = roads
        self._tree = STRtree([r.geometry for r in roads]) if roads else None

    @property
    def road_count(self) -> int:
        return len(self._roads)

    def lines_in_box(self, minx: float, miny: float, maxx: float, maxy: float) -> list["_Road"]:
        """Roads whose SWEREF99 TM geometry's bounding box meets the box (for rasterizing)."""
        if self._tree is None:
            return []
        from shapely.geometry import box
        return [self._roads[int(i)] for i in self._tree.query(box(minx, miny, maxx, maxy))]

    def nearest_road(self, point: LatLon, max_distance_m: float = 50.0) -> RoadHit | None:
        hits = self.roads_near(point, max_distance_m)
        return hits[0] if hits else None

    def roads_near(self, point: LatLon, max_distance_m: float = 50.0) -> list[RoadHit]:
        """All roads within ``max_distance_m``, nearest first (e.g. a bridge above a nearer road)."""
        if self._tree is None:
            return []
        x, y = project(point.lat, point.lon)
        query_point = Point(x, y)
        # Two-stage: STRtree gives indexes within bbox; refine with true distance.
        # An expanded query box of size max_distance ensures we don't miss lines
        # whose bounding box doesn't intersect the point itself.
        from shapely.geometry import box
        search = box(x - max_distance_m, y - max_distance_m, x + max_distance_m, y + max_distance_m)
        hits: list[RoadHit] = []
        for index in self._tree.query(search):
            road = self._roads[int(index)]
            distance = float(road.geometry.distance(query_point))
            if distance > max_distance_m:
                continue
            hits.append(RoadHit(
                distance_m=distance,
                highway=road.highway,
                name=road.name,
                tunnel=road.tunnel,
                bridge=road.bridge,
                snowmobile=road.snowmobile,
            ))
        hits.sort(key=lambda h: h.distance_m)
        return hits

    # --- construction ------------------------------------------------------

    @classmethod
    def from_geojson(cls, path: str | Path) -> "OsmRoadsProvider":
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
    ) -> "OsmRoadsProvider":
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"roads_{_safe(aoi.name)}.geojson"
        if target.exists() and not force:
            return cls.from_geojson(target)
        bbox = bbox_clause(aoi.bbox)
        query = f"""
[out:json][timeout:{int(timeout)}];
(
  way["highway"]{bbox};
  way["snowmobile"~"^(designated|yes)$"]{bbox};
  relation["route"="snowmobile"]{bbox};
);
out geom;
"""
        response = overpass_query(query, url=overpass_url, timeout=timeout)
        feature_collection = osm_response_to_geojson(response)
        target.write_text(json.dumps(feature_collection), "utf-8")
        return cls._from_features(feature_collection["features"])

    @classmethod
    def _from_features(cls, features: list[dict[str, Any]]) -> "OsmRoadsProvider":
        transformer = to_projected()

        def _project_xy(x: float, y: float, _z: float | None = None):
            xx, yy = transformer.transform(x, y)
            return xx, yy

        roads: list[_Road] = []
        for feature in features:
            geometry = feature.get("geometry")
            props = feature.get("properties") or {}
            if not isinstance(geometry, dict) or geometry.get("type") not in ("LineString", "MultiLineString"):
                continue
            road = road_properties(props)
            if road is None:
                continue
            try:
                geom = shape(geometry)
            except Exception:
                continue
            if geom.is_empty:
                continue
            projected = shapely_transform(_project_xy, geom)
            roads.append(
                _Road(
                    geometry=projected,
                    highway=road["highway"],
                    name=road["name"],
                    tunnel=road["tunnel"],
                    bridge=road["bridge"],
                    snowmobile=road["snowmobile"],
                )
            )
        return cls(roads)


def road_properties(props: dict[str, Any]) -> dict[str, Any] | None:
    """Road class and flags from OSM tags, or None when the feature is not a road or trail."""
    snowmobile = props.get("route") == "snowmobile" or props.get("snowmobile") in ("designated", "yes")
    highway = props.get("highway")
    if not isinstance(highway, str):
        if not snowmobile:
            return None
        highway = "snowmobile"
    return {
        "highway": highway,
        "name": props.get("name") if isinstance(props.get("name"), str) else None,
        "tunnel": _flag(props.get("tunnel")),
        "bridge": _flag(props.get("bridge")),
        "snowmobile": snowmobile,
    }


def _flag(value: Any) -> bool:
    """OSM tunnel/bridge tags: any value except ``no`` means yes (``culvert``, ``viaduct`` …)."""
    return isinstance(value, str) and value not in ("", "no")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "aoi"
