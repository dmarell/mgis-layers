"""Elevation provider — Lantmäteriet Markhöjdmodell DEM tiles.

Two construction paths:

- ``ElevationProvider.from_cache(dir)`` — read tiles already on disk.
- ``ElevationProvider.fetch(aoi, ...)`` — download missing tiles from STAC,
  then read them.

Heights are bilinearly interpolated. Inputs are WGS84; the projected CRS
(EPSG:3006) is read from each GeoTIFF, so this works for tiles in any of
the projection variants Lantmäteriet ships.
"""
from __future__ import annotations

import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import rasterio
import requests

from gis_layers.crs import project
from gis_layers.types import AOI, BBox, LatLon

from ._lantmateriet_auth import (
    DEFAULT_STAC_URL,
    USER_AGENT,
    Auth,
    auth_for_asset_download,
    auth_from_settings,
    load_settings,
)

# The same STAC also serves Laserdata Skog point clouds (dsm-skoglig-copc), gigabytes per tile.
DTM_COLLECTION = "dtm-cog"


@dataclass
class _Tile:
    path: Path
    bounds: tuple[float, float, float, float]  # (left, bottom, right, top) in projected CRS
    crs_epsg: int
    nodata: float | None
    _array: np.ndarray | None = None
    _transform: Any = None  # rasterio Affine

    def contains(self, x: float, y: float) -> bool:
        left, bottom, right, top = self.bounds
        return left <= x <= right and bottom <= y <= top

    def unload(self) -> None:
        self._array = None
        self._transform = None

    def height_at(self, x: float, y: float) -> float | None:
        if not self.contains(x, y):
            return None
        array, transform = self._load()
        if array.size == 0:
            return None
        col_f, row_f = ~transform * (x, y)
        col_f -= 0.5
        row_f -= 0.5
        height, width = array.shape
        col_f = float(np.clip(col_f, 0.0, width - 1.0))
        row_f = float(np.clip(row_f, 0.0, height - 1.0))
        c0 = int(math.floor(col_f))
        r0 = int(math.floor(row_f))
        c1 = min(c0 + 1, width - 1)
        r1 = min(r0 + 1, height - 1)
        dc = col_f - c0
        dr = row_f - r0
        v00 = float(array[r0, c0])
        v01 = float(array[r0, c1])
        v10 = float(array[r1, c0])
        v11 = float(array[r1, c1])
        if self.nodata is not None:
            nd = float(self.nodata)
            if v00 == nd or v01 == nd or v10 == nd or v11 == nd:
                return None
        if not (math.isfinite(v00) and math.isfinite(v01) and math.isfinite(v10) and math.isfinite(v11)):
            return None
        top = (1.0 - dc) * v00 + dc * v01
        bottom = (1.0 - dc) * v10 + dc * v11
        return float((1.0 - dr) * top + dr * bottom)

    def _load(self) -> tuple[np.ndarray, Any]:
        if self._array is None:
            with rasterio.open(self.path) as src:
                array = src.read(1).astype(np.float32, copy=False)
                self._array = array
                self._transform = src.transform
                if self.nodata is None and src.nodata is not None:
                    self.nodata = float(src.nodata)
        return self._array, self._transform


class ElevationProvider:
    """Local DEM cache that bilinearly interpolates Lantmäteriet height tiles."""

    def __init__(self, tiles: list[_Tile], max_cached_tiles: int = 8) -> None:
        if not tiles:
            raise ValueError("ElevationProvider requires at least one tile")
        self._tiles = tiles
        self._max_cached = max(1, int(max_cached_tiles))
        self._cache: OrderedDict[int, _Tile] = OrderedDict()
        self._last_index: int | None = None

    @property
    def tile_count(self) -> int:
        return len(self._tiles)

    def height_at(self, point: LatLon) -> float | None:
        x, y = project(point.lat, point.lon)
        return self._height_at_xy(x, y)

    def height_at_xy(self, x: float, y: float) -> float | None:
        """Height at SWEREF99 TM coordinates (no reprojection)."""
        return self._height_at_xy(x, y)

    def _height_at_xy(self, x: float, y: float) -> float | None:
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        last = self._last_index
        if last is not None and self._tiles[last].contains(x, y):
            self._touch(last)
            return self._tiles[last].height_at(x, y)
        for i, tile in enumerate(self._tiles):
            if tile.contains(x, y):
                self._last_index = i
                self._touch(i)
                return tile.height_at(x, y)
        return None

    def _touch(self, index: int) -> None:
        tile = self._tiles[index]
        if index in self._cache:
            self._cache.move_to_end(index)
        else:
            self._cache[index] = tile
        while len(self._cache) > self._max_cached:
            _, evicted = self._cache.popitem(last=False)
            evicted.unload()

    # --- construction ------------------------------------------------------

    @classmethod
    def from_cache(cls, cache_dir: str | Path, max_cached_tiles: int = 8) -> "ElevationProvider":
        cache_dir = Path(cache_dir)
        if cache_dir.is_file() and cache_dir.suffix == ".json":
            return cls._from_index(cache_dir, max_cached_tiles)
        index = cache_dir / "index.json"
        if index.exists():
            return cls._from_index(index, max_cached_tiles)
        return cls._from_directory_scan(cache_dir, max_cached_tiles)

    @classmethod
    def _from_index(cls, index_path: Path, max_cached_tiles: int) -> "ElevationProvider":
        index = json.loads(index_path.read_text("utf-8"))
        root = index_path.parent
        tile_paths: list[Path] = []
        for item in index.get("items", []):
            for asset in (item.get("assets") or {}).values():
                if not isinstance(asset, dict):
                    continue
                rel = asset.get("path") or asset.get("href")
                if not rel or "data" not in (asset.get("roles") or []):
                    continue
                p = Path(rel)
                tile_paths.append(p if p.is_absolute() else root / p)
        return cls._from_paths(tile_paths, max_cached_tiles)

    @classmethod
    def _from_directory_scan(cls, cache_dir: Path, max_cached_tiles: int) -> "ElevationProvider":
        if not cache_dir.exists():
            raise FileNotFoundError(f"elevation cache not found: {cache_dir}")
        tile_paths = _select_lantmateriet_tiles(cache_dir)
        return cls._from_paths(tile_paths, max_cached_tiles)

    @classmethod
    def _from_paths(cls, paths: Iterable[Path], max_cached_tiles: int) -> "ElevationProvider":
        tiles: list[_Tile] = []
        for path in paths:
            if not path.exists():
                continue
            with rasterio.open(path) as src:
                bounds = (src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top)
                epsg = src.crs.to_epsg() if src.crs else None
                tiles.append(
                    _Tile(
                        path=path,
                        bounds=bounds,
                        crs_epsg=int(epsg) if epsg else 0,
                        nodata=float(src.nodata) if src.nodata is not None else None,
                    )
                )
        if not tiles:
            raise FileNotFoundError("no GeoTIFF DEM tiles found")
        return cls(tiles, max_cached_tiles=max_cached_tiles)

    @classmethod
    def fetch(
        cls,
        aoi: AOI,
        cache_dir: str | Path,
        *,
        env_path: str | Path | None = "lantmateriet.secret.env",
        stac_url: str | None = None,
        buffer_m: float = 0.0,
        timeout: float = 60.0,
        force: bool = False,
    ) -> "ElevationProvider":
        """Download Lantmäteriet height tiles overlapping ``aoi`` to ``cache_dir``."""
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        url, auth, settings, items = _search(aoi, env_path, stac_url, buffer_m, timeout)
        for item in items:
            _download_data_asset(
                item, cache_dir, auth=auth, settings=settings, force=force, timeout=timeout,
            )
        _write_index(cache_dir, url, aoi, items)
        return cls.from_cache(cache_dir)

    @staticmethod
    def search(
        aoi: AOI,
        *,
        env_path: str | Path | None = "lantmateriet.secret.env",
        stac_url: str | None = None,
        buffer_m: float = 0.0,
        timeout: float = 60.0,
    ) -> list[dict[str, Any]]:
        """The STAC items ``fetch`` would download, without downloading them."""
        return _search(aoi, env_path, stac_url, buffer_m, timeout)[3]


# --- STAC client ---------------------------------------------------------


def _search(
    aoi: AOI, env_path: str | Path | None, stac_url: str | None, buffer_m: float, timeout: float
) -> tuple[str, Auth, dict[str, str], list[dict[str, Any]]]:
    settings = load_settings(Path(env_path) if env_path else None)
    url = stac_url or settings.get("LANTMATERIET_STAC_URL") or DEFAULT_STAC_URL
    auth = auth_from_settings(settings, timeout=timeout)
    if auth is None:
        raise RuntimeError(
            "no Lantmäteriet credentials; fill OAuth2 or basic values in "
            f"{env_path or 'lantmateriet.secret.env'}"
        )
    items = _stac_search(url, aoi.bbox.expand_meters(buffer_m), auth=auth, timeout=timeout)
    return url, auth, settings, items


def _stac_search(
    stac_url: str,
    bbox: BBox,
    *,
    auth: Auth,
    timeout: float,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    search_url = stac_url.rstrip("/") + "/search"
    body: dict[str, Any] = {
        "bbox": list(bbox.as_tuple()),
        "collections": [DTM_COLLECTION],
        "limit": page_size,
    }
    headers = {
        "Accept": "application/geo+json, application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Authorization": auth.header,
    }
    items: list[dict[str, Any]] = []
    while True:
        response = requests.post(search_url, json=body, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        for feature in payload.get("features") or []:
            if isinstance(feature, dict):
                items.append(feature)
        next_body = _next_link_body(payload)
        if next_body is None:
            return items
        body = next_body


def _next_link_body(payload: dict[str, Any]) -> dict[str, Any] | None:
    for link in payload.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "next":
            body = link.get("body")
            if isinstance(body, dict):
                return body
    return None


def _download_data_asset(
    item: dict[str, Any],
    out_dir: Path,
    *,
    auth: Auth,
    settings: dict[str, str],
    force: bool,
    timeout: float,
) -> None:
    asset = _select_data_asset(item)
    if asset is None:
        return
    href = str(asset.get("href") or "")
    if not href:
        return
    filename = Path(href.split("?", 1)[0]).name or f"{item.get('id', 'tile')}.tif"
    target = out_dir / "tiles" / filename
    expected = asset.get("file:size")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        if not isinstance(expected, int) or target.stat().st_size == expected:
            return
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": auth_for_asset_download(settings, auth, href).header,
    }
    part = target.with_suffix(target.suffix + ".part")
    with requests.get(href, headers=headers, stream=True, timeout=timeout) as response:
        if response.status_code == 403:
            raise RuntimeError(
                f"403 Forbidden downloading {href}. "
                "STAC search succeeded but tile download was denied. "
                "Lantmäteriet GeoTIFF files on dl*.lantmateriet.se require Basic auth "
                "with your Geotorget system account (LANTMATERIET_STAC_USERNAME/PASSWORD), "
                "even when OAuth is used for STAC. Also verify that Markhöjdmodell Nedladdning "
                "is approved for that account in Geotorget."
            )
        response.raise_for_status()
        with part.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)
    if isinstance(expected, int) and part.stat().st_size != expected:
        actual = part.stat().st_size
        part.unlink()
        raise RuntimeError(f"size mismatch for {href}: expected {expected}, got {actual}")
    part.replace(target)


def _select_data_asset(item: dict[str, Any]) -> dict[str, Any] | None:
    assets = item.get("assets") or {}
    if not isinstance(assets, dict):
        return None
    preferred = assets.get("data")
    if isinstance(preferred, dict) and "data" in (preferred.get("roles") or []):
        return preferred
    for asset in assets.values():
        if isinstance(asset, dict) and "data" in (asset.get("roles") or []):
            return asset
    return None


def _write_index(
    cache_dir: Path, stac_url: str, aoi: AOI, items: list[dict[str, Any]]
) -> None:
    """Minimal manifest for traceability — not used at read time (we glob *.tif)."""
    manifest = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "product": "Lantmäteriet Markhöjdmodell",
            "stac_url": stac_url,
            "license": "CC-BY-4.0",
            "attribution": "Contains data from Lantmäteriet.",
        },
        "aoi": {"name": aoi.name, "bbox": list(aoi.bbox.as_tuple())},
        "items": [
            {"id": item.get("id"), "collection": item.get("collection")}
            for item in items
        ],
    }
    (cache_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), "utf-8")


def _select_lantmateriet_tiles(cache_dir: Path) -> list[Path]:
    """Prefer small grid tiles; skip 1m parent tiles that OOM on load.

    Tiles downloaded by ``fetch`` live in ``tiles/``; a ``dem.tif`` left from an open fetch
    in the same directory is never one of them.
    """
    root = cache_dir / "tiles" if (cache_dir / "tiles").is_dir() else cache_dir
    all_tifs = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in {".tif", ".tiff"}
    )
    if not all_tifs:
        return []
    grid_tiles = [p for p in all_tifs if "/grid1m/" in p.as_posix().replace("\\", "/")]
    if grid_tiles:
        return grid_tiles
    # Skip Lantmäteriet overview tiles (m688_41.tif etc.) when grid tiles exist elsewhere.
    compact = [p for p in all_tifs if not p.name.startswith("m") or p.stat().st_size < 50_000_000]
    return compact or all_tifs
