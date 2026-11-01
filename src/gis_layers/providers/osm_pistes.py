"""OpenStreetMap ski pistes provider.

Fetches `piste:type=*` ways via Overpass and caches them as GeoJSON. Pistes
mapped as areas (`area=yes` on a closed way) become Polygons, the rest stay
LineStrings along the piste's centre line.
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


class OsmPistesProvider:
    def __init__(self, features: list[dict[str, Any]]) -> None:
        self._features = [f for f in (_as_area(f) for f in features) if _is_piste(f)]

    @property
    def piste_count(self) -> int:
        return len(self._features)

    @property
    def features(self) -> list[dict[str, Any]]:
        return list(self._features)

    @classmethod
    def from_geojson(cls, path: str | Path) -> "OsmPistesProvider":
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
    ) -> "OsmPistesProvider":
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"pistes_{_safe(aoi.name)}.geojson"
        if target.exists() and not force:
            return cls.from_geojson(target)
        bbox = bbox_clause(aoi.bbox)
        query = f"""
[out:json][timeout:{int(timeout)}];
(
  way["piste:type"]{bbox};
);
out geom;
"""
        response = overpass_query(query, url=overpass_url, timeout=timeout)
        provider = cls(osm_response_to_geojson(response)["features"])
        target.write_text(json.dumps({"type": "FeatureCollection", "features": provider.features}), "utf-8")
        return provider


def _as_area(feature: dict[str, Any]) -> dict[str, Any]:
    geometry = feature.get("geometry") or {}
    props = feature.get("properties") or {}
    coords = geometry.get("coordinates") or []
    if (
        geometry.get("type") == "LineString"
        and props.get("area") == "yes"
        and len(coords) >= 4
        and list(coords[0]) == list(coords[-1])
    ):
        return {**feature, "geometry": {"type": "Polygon", "coordinates": [coords]}}
    return feature


def _is_piste(feature: dict[str, Any]) -> bool:
    kind = (feature.get("properties") or {}).get("piste:type")
    geometry = feature.get("geometry") or {}
    return isinstance(kind, str) and bool(kind) and geometry.get("type") in ("LineString", "Polygon")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "aoi"
