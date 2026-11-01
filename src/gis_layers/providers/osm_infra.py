"""OpenStreetMap infrastructure obstacles provider.

Fetches the things a low-flying drone can hit or must not cross — power lines,
pylons, masts and towers, wind turbines, railways (with the `electrified` tag)
and aerialways — via Overpass, caches as GeoJSON, and answers
``features_near(point, radius_m)`` via STRtree on SWEREF99 TM geometries.

Each feature gets a ``kind``, a height above ground (the OSM `height` tag when
present, otherwise a nominal value per kind), and a ``corridor`` flag for lines
that are crossings rather than point obstacles (power lines, overhead contact
lines, aerialway cables). A railway in a tunnel is kept but is neither an
obstacle nor a corridor.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from shapely.geometry import Point, box, shape
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
class InfraKind:
    height_m: float
    radius_m: float
    corridor: bool


# Nominal heights above ground (m) and horizontal reach (m) when OSM has no tag.
DEFAULT_INFRA_KINDS: dict[str, InfraKind] = {
    "power_line": InfraKind(height_m=30.0, radius_m=10.0, corridor=True),
    "power_minor_line": InfraKind(height_m=12.0, radius_m=3.0, corridor=True),
    "power_tower": InfraKind(height_m=30.0, radius_m=5.0, corridor=False),
    "wind_turbine": InfraKind(height_m=150.0, radius_m=60.0, corridor=False),
    "mast": InfraKind(height_m=50.0, radius_m=25.0, corridor=False),
    "communications_tower": InfraKind(height_m=80.0, radius_m=10.0, corridor=False),
    "tower": InfraKind(height_m=25.0, radius_m=5.0, corridor=False),
    "chimney": InfraKind(height_m=40.0, radius_m=5.0, corridor=False),
    "railway_electrified": InfraKind(height_m=7.0, radius_m=4.0, corridor=True),
    "railway": InfraKind(height_m=0.0, radius_m=3.0, corridor=False),
    "aerialway": InfraKind(height_m=15.0, radius_m=5.0, corridor=True),
    "aerialway_surface": InfraKind(height_m=8.0, radius_m=3.0, corridor=True),
}

_RAILWAYS = ("rail", "light_rail", "narrow_gauge", "tram")
_OVERHEAD_ELECTRIFIED = ("contact_line", "yes")
_SURFACE_LIFTS = ("drag_lift", "t-bar", "j-bar", "platter", "rope_tow")
_AERIALWAY_EXCLUDED = ("station", "pylon", "magic_carpet", "goods")
_MAN_MADE = ("mast", "tower", "communications_tower", "chimney")


@dataclass(frozen=True)
class InfraHit:
    kind: str
    subtype: str | None
    distance_m: float
    height_m: float
    height_from_tag: bool
    corridor: bool
    tunnel: bool
    name: str | None
    osm_id: str | None


@dataclass(frozen=True)
class _Infra:
    geometry: Any
    kind: str
    subtype: str | None
    height_m: float
    height_from_tag: bool
    radius_m: float
    corridor: bool
    tunnel: bool
    name: str | None
    osm_id: str | None


def classify(props: Mapping[str, Any]) -> tuple[str, str | None] | None:
    """Map OSM tags to (kind, subtype), or None when not an infrastructure obstacle."""
    power = props.get("power")
    if power == "line":
        return "power_line", "line"
    if power == "minor_line":
        return "power_minor_line", "minor_line"
    if power == "tower":
        return "power_tower", "tower"
    if power == "generator" and props.get("generator:source") == "wind":
        return "wind_turbine", "wind"
    man_made = props.get("man_made")
    if man_made in _MAN_MADE:
        return str(man_made), props.get("tower:type") if man_made == "tower" else None
    railway = props.get("railway")
    if railway in _RAILWAYS:
        electrified = props.get("electrified")
        if electrified is None and railway == "tram":
            electrified = "contact_line"
        if electrified in _OVERHEAD_ELECTRIFIED:
            return "railway_electrified", str(railway)
        return "railway", str(railway)
    aerialway = props.get("aerialway")
    if isinstance(aerialway, str) and aerialway not in _AERIALWAY_EXCLUDED:
        if aerialway in _SURFACE_LIFTS:
            return "aerialway_surface", aerialway
        return "aerialway", aerialway
    return None


def parse_height_m(value: Any) -> float | None:
    """OSM `height`: ``"45"``, ``"45 m"``, ``"45.5m"`` or ``"150 ft"`` → metres."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*([0-9]+(?:[.,][0-9]+)?)\s*(m|ft|')?\s*", value)
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    return number * 0.3048 if match.group(2) in ("ft", "'") else number


def _tagged_height(kind: str, props: Mapping[str, Any]) -> float | None:
    if kind == "wind_turbine":
        hub = parse_height_m(props.get("height:hub"))
        rotor = parse_height_m(props.get("rotor:diameter"))
        if hub is not None and rotor is not None:
            return hub + rotor / 2.0
    return parse_height_m(props.get("height"))


def infra_properties(
    props: Mapping[str, Any], kinds: Mapping[str, InfraKind] | None = None
) -> dict[str, Any] | None:
    """Kind, height and flags from OSM tags (as ``features_near`` reports them), or None."""
    classified = classify(props)
    if classified is None:
        return None
    kind, subtype = classified
    spec = {**DEFAULT_INFRA_KINDS, **(kinds or {})}[kind]
    tunnel = isinstance(props.get("tunnel"), str) and props["tunnel"] != "no"
    tagged = _tagged_height(kind, props)
    return {
        "kind": kind,
        "subtype": subtype,
        "height_m": 0.0 if tunnel else (tagged if tagged is not None else spec.height_m),
        "height_from_tag": tagged is not None and not tunnel,
        "radius_m": spec.radius_m,
        "corridor": spec.corridor and not tunnel,
        "tunnel": tunnel,
        "name": props.get("name") if isinstance(props.get("name"), str) else None,
    }


class OsmInfraProvider:
    def __init__(self, items: list[_Infra]) -> None:
        self._items = items
        self._tree = STRtree([i.geometry for i in items]) if items else None

    @property
    def feature_count(self) -> int:
        return len(self._items)

    def kind_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self._items:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return counts

    def features_near(self, point: LatLon, radius_m: float = 50.0) -> list[InfraHit]:
        """Features whose geometry plus reach lies within ``radius_m``, nearest first."""
        if self._tree is None:
            return []
        x, y = project(point.lat, point.lon)
        query_point = Point(x, y)
        max_reach = max((i.radius_m for i in self._items), default=0.0)
        search = radius_m + max_reach
        hits: list[InfraHit] = []
        for index in self._tree.query(box(x - search, y - search, x + search, y + search)):
            item = self._items[int(index)]
            distance = float(item.geometry.distance(query_point))
            if distance > radius_m + item.radius_m:
                continue
            hits.append(
                InfraHit(
                    kind=item.kind,
                    subtype=item.subtype,
                    distance_m=distance,
                    height_m=item.height_m,
                    height_from_tag=item.height_from_tag,
                    corridor=item.corridor,
                    tunnel=item.tunnel,
                    name=item.name,
                    osm_id=item.osm_id,
                )
            )
        hits.sort(key=lambda h: h.distance_m)
        return hits

    # --- construction ------------------------------------------------------

    @classmethod
    def from_geojson(
        cls, path: str | Path, kinds: Mapping[str, InfraKind] | None = None
    ) -> "OsmInfraProvider":
        data = json.loads(Path(path).read_text("utf-8"))
        return cls._from_features(data.get("features") or [], kinds)

    @classmethod
    def fetch(
        cls,
        aoi: AOI,
        cache_dir: str | Path,
        *,
        overpass_url: str = DEFAULT_OVERPASS_URL,
        timeout: float = 180.0,
        force: bool = False,
    ) -> "OsmInfraProvider":
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"infra_{_safe(aoi.name)}.geojson"
        if target.exists() and not force:
            return cls.from_geojson(target)
        bbox = bbox_clause(aoi.bbox)
        man_made = "|".join(_MAN_MADE)
        railways = "|".join(_RAILWAYS)
        query = f"""
[out:json][timeout:{int(timeout)}];
(
  way["power"~"^(line|minor_line)$"]{bbox};
  node["power"="tower"]{bbox};
  node["power"="generator"]["generator:source"="wind"]{bbox};
  node["man_made"~"^({man_made})$"]{bbox};
  way["railway"~"^({railways})$"]{bbox};
  way["aerialway"]{bbox};
);
out geom;
"""
        response = overpass_query(query, url=overpass_url, timeout=timeout)
        feature_collection = osm_response_to_geojson(response)
        feature_collection["features"] = [
            f for f in feature_collection["features"] if classify(f.get("properties") or {})
        ]
        target.write_text(json.dumps(feature_collection), "utf-8")
        return cls._from_features(feature_collection["features"])

    @classmethod
    def _from_features(
        cls, features: list[dict[str, Any]], kinds: Mapping[str, InfraKind] | None = None
    ) -> "OsmInfraProvider":
        transformer = to_projected()

        def _project_xy(x: float, y: float, _z: float | None = None):
            return transformer.transform(x, y)

        items: list[_Infra] = []
        for feature in features:
            geometry = feature.get("geometry")
            props = feature.get("properties") or {}
            if not isinstance(geometry, dict):
                continue
            info = infra_properties(props, kinds)
            if info is None:
                continue
            try:
                geom = shape(geometry)
            except Exception:
                continue
            if geom.is_empty:
                continue
            osm_id = feature.get("id")
            items.append(
                _Infra(
                    geometry=shapely_transform(_project_xy, geom),
                    osm_id=str(osm_id) if osm_id is not None else None,
                    **info,
                )
            )
        return cls(items)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "aoi"
