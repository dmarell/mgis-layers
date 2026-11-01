"""OpenStreetMap buildings provider.

Fetches `building=*` ways and multipolygon relations via Overpass and caches
them as GeoJSON. Each footprint carries its building type and, when tagged,
height, levels and roof shape (``building_properties``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gis_layers.types import AOI

from .overpass import (
    DEFAULT_OVERPASS_URL,
    bbox_clause,
    osm_response_to_geojson,
    overpass_query,
)


class OsmBuildingsProvider:
    def __init__(self, features: list[dict[str, Any]]) -> None:
        self._features = [f for f in features if _is_footprint(f)]

    @property
    def building_count(self) -> int:
        return len(self._features)

    @property
    def features(self) -> list[dict[str, Any]]:
        return list(self._features)

    @classmethod
    def from_geojson(cls, path: str | Path) -> "OsmBuildingsProvider":
        data = json.loads(Path(path).read_text("utf-8"))
        return cls(data.get("features") or [])

    @classmethod
    def fetch(
        cls,
        aoi: AOI,
        cache_dir: str | Path,
        *,
        overpass_url: str = DEFAULT_OVERPASS_URL,
        timeout: float = 180.0,
        force: bool = False,
    ) -> "OsmBuildingsProvider":
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"buildings_{_safe(aoi.name)}.geojson"
        if target.exists() and not force:
            return cls.from_geojson(target)
        bbox = bbox_clause(aoi.bbox)
        query = f"""
[out:json][timeout:{int(timeout)}];
(
  way["building"]{bbox};
  relation["building"]["type"="multipolygon"]{bbox};
);
out geom;
"""
        response = overpass_query(query, url=overpass_url, timeout=timeout)
        feature_collection = osm_response_to_geojson(response)
        target.write_text(json.dumps(feature_collection), "utf-8")
        return cls(feature_collection["features"])


def building_properties(props: dict[str, Any]) -> dict[str, Any] | None:
    """Building type and the tagged height (m), levels and roof shape, or None for a non-building."""
    kind = props.get("building")
    if not isinstance(kind, str) or kind in ("", "no"):
        return None
    return {
        "kind": kind,
        "height_m": _number(props.get("height")),
        "levels": _number(props.get("building:levels")),
        "roof_shape": props.get("roof:shape") if isinstance(props.get("roof:shape"), str) else None,
        "name": props.get("name") if isinstance(props.get("name"), str) else None,
    }


def _number(value: Any) -> float | None:
    """OSM numbers like ``6``, ``6.5`` or ``6 m``; anything else is None."""
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip().removesuffix("m").strip().replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number > 0 else None


def _is_footprint(feature: dict[str, Any]) -> bool:
    geometry = feature.get("geometry") or {}
    return geometry.get("type") in ("Polygon", "MultiPolygon") and building_properties(
        feature.get("properties") or {}
    ) is not None


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "aoi"
