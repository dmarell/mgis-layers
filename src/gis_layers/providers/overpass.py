"""Tiny Overpass API client — JSON in, GeoJSON FeatureCollection out."""
from __future__ import annotations

import time
from typing import Any

import requests

from gis_layers.types import BBox

DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_MIRRORS = (
    DEFAULT_OVERPASS_URL,
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.de/osm/tools/overpass/api/interpreter",
)
USER_AGENT = "gis_layers/0.1 (https://example.invalid)"
_RETRYABLE = {406, 429, 500, 502, 503, 504}


def overpass_query(
    query: str,
    *,
    url: str = DEFAULT_OVERPASS_URL,
    timeout: float = 180.0,
    retries: int = 3,
) -> dict[str, Any]:
    """Run an Overpass QL query, return the raw JSON response."""
    mirrors = (url,) + tuple(m for m in OVERPASS_MIRRORS if m != url)
    last_error: Exception | None = None

    for mirror in mirrors:
        for attempt in range(retries):
            headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
            try:
                response = requests.post(
                    mirror,
                    data={"data": query},
                    headers=headers,
                    timeout=(10.0, timeout),
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(min(2.0 ** attempt, 4.0))
                continue

            if response.status_code in _RETRYABLE:
                last_error = requests.HTTPError(
                    f"{response.status_code} from {mirror}: {response.text[:200]}",
                    response=response,
                )
                if attempt + 1 < retries:
                    time.sleep(min(2.0 ** attempt, 4.0))
                continue

            try:
                response.raise_for_status()
                return response.json()
            except (requests.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(min(2.0 ** attempt, 4.0))

    if last_error is not None:
        raise last_error
    raise RuntimeError("Overpass query failed without a response")


def bbox_clause(bbox: BBox) -> str:
    """Overpass bbox is (south, west, north, east) — note the lat/lon order."""
    return f"({bbox.min_lat},{bbox.min_lon},{bbox.max_lat},{bbox.max_lon})"


def osm_response_to_geojson(response: dict[str, Any]) -> dict[str, Any]:
    """Convert Overpass JSON ('out geom') to a GeoJSON FeatureCollection.

    Supports tagged nodes (Point), ways, and relations (multipolygon, and
    type=route as MultiLineString with the relation's tags). Untagged nodes are
    only used as way coordinates; other elements are skipped.
    """
    nodes: dict[int, tuple[float, float]] = {}
    elements = response.get("elements") or []
    for el in elements:
        if el.get("type") == "node":
            lat = el.get("lat")
            lon = el.get("lon")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                nodes[int(el["id"])] = (float(lon), float(lat))

    features: list[dict[str, Any]] = []
    for el in elements:
        kind = el.get("type")
        tags = el.get("tags") or {}
        if kind == "node":
            coords = nodes.get(int(el["id"])) if "id" in el else None
            if tags and coords:
                features.append(_feature(el, {"type": "Point", "coordinates": list(coords)}, tags))
        elif kind == "way":
            coords = _way_coords(el, nodes)
            if not coords:
                continue
            geometry = _polygon_or_line(coords, tags)
            features.append(_feature(el, geometry, tags))
        elif kind == "relation" and tags.get("type") == "multipolygon":
            geometry = _multipolygon_from_relation(el, nodes)
            if geometry is None:
                continue
            features.append(_feature(el, geometry, tags))
        elif kind == "relation" and tags.get("type") == "route":
            lines = [
                coords
                for member in el.get("members") or []
                if member.get("type") == "way"
                for coords in [_member_way_coords(member, nodes)]
                if len(coords) >= 2
            ]
            if lines:
                features.append(_feature(el, {"type": "MultiLineString", "coordinates": lines}, tags))
    return {"type": "FeatureCollection", "features": features}


def _way_coords(way: dict[str, Any], nodes: dict[int, tuple[float, float]]) -> list[tuple[float, float]]:
    geom = way.get("geometry")
    if isinstance(geom, list):
        return [
            (float(p["lon"]), float(p["lat"]))
            for p in geom
            if isinstance(p, dict) and "lat" in p and "lon" in p
        ]
    coords: list[tuple[float, float]] = []
    for ref in way.get("nodes") or []:
        node = nodes.get(int(ref))
        if node:
            coords.append(node)
    return coords


def _polygon_or_line(coords: list[tuple[float, float]], tags: dict[str, Any]) -> dict[str, Any]:
    is_closed = len(coords) >= 4 and coords[0] == coords[-1]
    looks_areal = any(
        key in tags
        for key in ("landuse", "natural", "leisure", "amenity", "building", "water")
    )
    if is_closed and looks_areal:
        return {"type": "Polygon", "coordinates": [coords]}
    return {"type": "LineString", "coordinates": coords}


def _multipolygon_from_relation(
    relation: dict[str, Any], nodes: dict[int, tuple[float, float]]
) -> dict[str, Any] | None:
    """Assemble relation member ways into polygon geometry.

    OSM multipolygon outers are usually chains of open way segments; stitch
    them with ``shapely.ops.polygonize`` rather than requiring each member
    way to be a closed ring on its own.
    """
    from shapely.geometry import MultiPolygon, Polygon, mapping

    outer_segments: list[list[tuple[float, float]]] = []
    inner_segments: list[list[tuple[float, float]]] = []
    for member in relation.get("members") or []:
        if member.get("type") != "way":
            continue
        coords = _member_way_coords(member, nodes)
        if len(coords) < 2:
            continue
        if member.get("role") == "inner":
            inner_segments.append(coords)
        else:
            outer_segments.append(coords)

    outer_polys = _polygonize_segments(outer_segments)
    if not outer_polys:
        return None
    inner_polys = _polygonize_segments(inner_segments)

    assembled: list[Polygon] = []
    for outer in outer_polys:
        poly = outer
        for inner in inner_polys:
            if poly.contains(inner.representative_point()):
                poly = poly.difference(inner)
        if poly.is_empty:
            continue
        if poly.geom_type == "Polygon":
            assembled.append(poly)
        elif poly.geom_type == "MultiPolygon":
            assembled.extend(poly.geoms)

    if not assembled:
        return None
    if len(assembled) == 1:
        return mapping(assembled[0])
    return mapping(MultiPolygon(assembled))


def _polygonize_segments(segments: list[list[tuple[float, float]]]):
    from shapely.geometry import LineString
    from shapely.ops import polygonize

    if not segments:
        return []
    lines = [LineString(segment) for segment in segments]
    return [poly for poly in polygonize(lines) if not poly.is_empty and poly.area > 0]


def _member_way_coords(
    member: dict[str, Any],
    nodes: dict[int, tuple[float, float]],
) -> list[tuple[float, float]]:
    geom = member.get("geometry")
    if isinstance(geom, list):
        return [
            (float(p["lon"]), float(p["lat"]))
            for p in geom
            if isinstance(p, dict) and "lat" in p and "lon" in p
        ]
    coords: list[tuple[float, float]] = []
    for ref in member.get("nodes") or []:
        node = nodes.get(int(ref))
        if node:
            coords.append(node)
    return coords


def _feature(element: dict[str, Any], geometry: dict[str, Any], tags: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": f"{element.get('type')}/{element.get('id')}",
        "geometry": geometry,
        "properties": dict(tags),
    }
