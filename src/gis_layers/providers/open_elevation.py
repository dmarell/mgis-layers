"""Open elevation provider — EU-DEM / SRTM via OpenTopoData.

Builds a single compact GeoTIFF (~30 m) per AOI instead of loading
Lantmäteriet 1 m parent tiles. Suitable for route planning and overview
visualisation; switch to ``ElevationProvider`` when you need 1 m LM data.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import requests
from rasterio.transform import from_origin

from gis_layers.crs import project
from gis_layers.types import AOI, BBox, LatLon

DEFAULT_API_URL = "https://api.opentopodata.org/v1"
DEFAULT_DATASET = "eudem25m"  # ~25 m, Europe incl. Sweden
FALLBACK_DATASET = "srtm30m"
DEFAULT_RESOLUTION_M = 30.0
BATCH_SIZE = 100
REQUEST_INTERVAL_S = 1.1  # OpenTopoData free tier: max 1 req/s
MAX_RETRIES = 8
USER_AGENT = "gis_layers/0.1"


@dataclass(frozen=True)
class _RasterDem:
    array: np.ndarray
    transform: Any
    nodata: float | None
    bounds: tuple[float, float, float, float]  # left, bottom, right, top

    def height_at_xy(self, x: float, y: float) -> float | None:
        left, bottom, right, top = self.bounds
        if not (left <= x <= right and bottom <= y <= top):
            return None
        col_f, row_f = ~self.transform * (x, y)
        col_f -= 0.5
        row_f -= 0.5
        height, width = self.array.shape
        col_f = float(np.clip(col_f, 0.0, width - 1.0))
        row_f = float(np.clip(row_f, 0.0, height - 1.0))
        c0 = int(math.floor(col_f))
        r0 = int(math.floor(row_f))
        c1 = min(c0 + 1, width - 1)
        r1 = min(r0 + 1, height - 1)
        dc = col_f - c0
        dr = row_f - r0
        v00 = float(self.array[r0, c0])
        v01 = float(self.array[r0, c1])
        v10 = float(self.array[r1, c0])
        v11 = float(self.array[r1, c1])
        if self.nodata is not None:
            nd = float(self.nodata)
            if v00 == nd or v01 == nd or v10 == nd or v11 == nd:
                return None
        if not (math.isfinite(v00) and math.isfinite(v01) and math.isfinite(v10) and math.isfinite(v11)):
            return None
        top = (1.0 - dc) * v00 + dc * v01
        bottom = (1.0 - dc) * v10 + dc * v11
        return float((1.0 - dr) * top + dr * bottom)


class OpenElevationProvider:
    """Compact cached DEM sampled from OpenTopoData (EU-DEM / SRTM)."""

    def __init__(self, dem: _RasterDem) -> None:
        self._dem = dem

    def height_at(self, point: LatLon) -> float | None:
        x, y = project(point.lat, point.lon)
        return self._dem.height_at_xy(x, y)

    def height_at_xy(self, x: float, y: float) -> float | None:
        """Height at SWEREF99 TM coordinates (no reprojection)."""
        return self._dem.height_at_xy(x, y)

    @classmethod
    def from_cache(cls, cache_dir: str | Path) -> "OpenElevationProvider":
        cache_dir = Path(cache_dir)
        dem_path = cache_dir / "dem.tif"
        if not dem_path.exists():
            raise FileNotFoundError(f"open elevation cache missing dem.tif: {cache_dir}")
        manifest_path = cache_dir / "manifest.json"
        schema_version = 1
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                schema_version = int(manifest.get("schema_version", 1))
            except (json.JSONDecodeError, TypeError, ValueError):
                schema_version = 1
        with rasterio.open(dem_path) as src:
            array = src.read(1).astype(np.float32, copy=True)
            if schema_version < 2:
                array = np.flipud(array)
            bounds = (src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top)
            nodata = float(src.nodata) if src.nodata is not None else None
            dem = _RasterDem(array=array, transform=src.transform, nodata=nodata, bounds=bounds)
        return cls(dem)

    @classmethod
    def fetch(
        cls,
        aoi: AOI,
        cache_dir: str | Path,
        *,
        api_url: str = DEFAULT_API_URL,
        dataset: str = DEFAULT_DATASET,
        resolution_m: float = DEFAULT_RESOLUTION_M,
        buffer_m: float = 0.0,
        timeout: float = 60.0,
        force: bool = False,
    ) -> "OpenElevationProvider":
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        dem_path = cache_dir / "dem.tif"
        manifest_path = cache_dir / "manifest.json"
        if dem_path.exists() and manifest_path.exists() and not force:
            if _cached_dem_covers_aoi(dem_path, aoi):
                return cls.from_cache(cache_dir)

        bbox = aoi.bbox.expand_meters(buffer_m)
        heights, transform, shape = _sample_bbox_to_grid(
            bbox,
            resolution_m=resolution_m,
            api_url=api_url,
            dataset=dataset,
            timeout=timeout,
        )
        rows, cols = shape
        height, width = rows, cols
        left = transform.c
        top = transform.f
        pixel_w = transform.a
        pixel_h = -transform.e
        bottom = top - height * pixel_h
        right = left + width * pixel_w

        dem_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            dem_path,
            "w",
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype="float32",
            crs="EPSG:3006",
            transform=transform,
            nodata=-9999.0,
        ) as dst:
            dst.write(np.flipud(heights).astype(np.float32), 1)

        manifest = {
            "schema_version": 2,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "opentopodata",
            "dataset": dataset,
            "resolution_m": resolution_m,
            "api_url": api_url,
            "attribution": "Elevation from OpenTopoData (EU-DEM / SRTM).",
            "aoi": {"name": aoi.name, "bbox": list(aoi.bbox.as_tuple())},
            "dem_file": "dem.tif",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return cls.from_cache(cache_dir)


def _sample_bbox_to_grid(
    bbox: BBox,
    *,
    resolution_m: float,
    api_url: str,
    dataset: str,
    timeout: float,
) -> tuple[np.ndarray, Any, tuple[int, int]]:
    # A lat/lon box is skewed in SWEREF99 TM; all four corners must fall inside the grid.
    corners = [project(lat, lon) for lat in (bbox.min_lat, bbox.max_lat) for lon in (bbox.min_lon, bbox.max_lon)]
    min_x, max_x = min(x for x, _ in corners), max(x for x, _ in corners)
    min_y, max_y = min(y for _, y in corners), max(y for _, y in corners)

    cols = max(2, int(math.ceil((max_x - min_x) / resolution_m)))
    rows = max(2, int(math.ceil((max_y - min_y) / resolution_m)))
    transform = from_origin(min_x, max_y, resolution_m, resolution_m)

    xs = min_x + (np.arange(cols) + 0.5) * resolution_m
    ys = min_y + (np.arange(rows) + 0.5) * resolution_m
    xx, yy = np.meshgrid(xs, ys, indexing="xy")

    points: list[tuple[float, float]] = []
    for row in range(rows):
        for col in range(cols):
            lat, lon = _unproject_fast(float(xx[row, col]), float(yy[row, col]))
            points.append((lat, lon))

    elevations = _query_opentopodata(
        points,
        api_url=api_url,
        dataset=dataset,
        timeout=timeout,
    )
    grid = np.full((rows, cols), -9999.0, dtype=np.float32)
    idx = 0
    for row in range(rows):
        for col in range(cols):
            value = elevations[idx]
            idx += 1
            if value is not None and math.isfinite(value):
                grid[row, col] = float(value)
    return grid, transform, (rows, cols)


def _query_opentopodata(
    points: list[tuple[float, float]],
    *,
    api_url: str,
    dataset: str,
    timeout: float,
) -> list[float | None]:
    try:
        return _query_opentopodata_batches(points, api_url=api_url, dataset=dataset, timeout=timeout)
    except requests.HTTPError as exc:
        if (
            exc.response is not None
            and exc.response.status_code == 400
            and dataset != FALLBACK_DATASET
        ):
            return _query_opentopodata_batches(
                points, api_url=api_url, dataset=FALLBACK_DATASET, timeout=timeout
            )
        raise


def _query_opentopodata_batches(
    points: list[tuple[float, float]],
    *,
    api_url: str,
    dataset: str,
    timeout: float,
) -> list[float | None]:
    url = f"{api_url.rstrip('/')}/{dataset}"
    headers = {"User-Agent": USER_AGENT}
    out: list[float | None] = []
    n_batches = math.ceil(len(points) / BATCH_SIZE)
    for batch_idx, start in enumerate(range(0, len(points), BATCH_SIZE)):
        batch = points[start : start + BATCH_SIZE]
        locations = "|".join(f"{lat},{lon}" for lat, lon in batch)
        response = _get_with_retry(url, params={"locations": locations}, headers=headers, timeout=timeout)
        payload = response.json()
        if payload.get("status") != "OK":
            raise RuntimeError(f"OpenTopoData error: {payload.get('error') or payload}")
        for result in payload.get("results") or []:
            elevation = result.get("elevation")
            if isinstance(elevation, (int, float)) and math.isfinite(float(elevation)):
                out.append(float(elevation))
            else:
                out.append(None)
        if batch_idx + 1 < n_batches:
            time.sleep(REQUEST_INTERVAL_S)
    if len(out) != len(points):
        raise RuntimeError(f"OpenTopoData returned {len(out)} values for {len(points)} points")
    return out


def _get_with_retry(
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str],
    timeout: float,
) -> requests.Response:
    for attempt in range(MAX_RETRIES):
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", min(60.0, 2.0 ** attempt)))
            time.sleep(retry_after)
            continue
        response.raise_for_status()
        return response
    raise RuntimeError(
        f"OpenTopoData rate limit exceeded after {MAX_RETRIES} retries; try again later"
    )


def _cached_dem_covers_aoi(dem_path: Path, aoi: AOI) -> bool:
    """Return False when an expanded AOI needs a fresh OpenTopoData fetch."""
    bbox = aoi.bbox
    corners = (
        (bbox.min_lat, bbox.min_lon),
        (bbox.min_lat, bbox.max_lon),
        (bbox.max_lat, bbox.min_lon),
        (bbox.max_lat, bbox.max_lon),
    )
    with rasterio.open(dem_path) as src:
        left, bottom, right, top = src.bounds
        for lat, lon in corners:
            x, y = project(lat, lon)
            if not (left <= x <= right and bottom <= y <= top):
                return False
    return True


def _unproject_fast(x: float, y: float) -> tuple[float, float]:
    from gis_layers.crs import unproject

    return unproject(x, y)
