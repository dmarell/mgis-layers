"""Tree height from Skogsstyrelsen's laser data (Trädhöjd laserdata skog), 1 m raster in decimetres.

Skogsstyrelsen publishes the raster as 2.5 km tiles on its open FTP, one folder per scan year and
100 km block (``<year>/<N/100 km>_<E/100 km>/THL_<block>_<N/100 m>_<E/100 m>_<year>.mrf``, MRF with
LERC, SWEREF99 TM). ``fetch`` takes the newest scan of every tile under the AOI and writes one
mosaic, ``chm.tif``. Pixels without laser returns, and tiles that were never scanned, are nodata.
"""
from __future__ import annotations

import ftplib
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import rasterio
from rasterio.transform import from_origin

from gis_layers.crs import project
from gis_layers.types import AOI, LatLon

FTP_HOST = "ftpsks.skogsstyrelsen.se"
# Public credentials, published on skogsstyrelsen.se (Ladda ner geodata, FTP).
FTP_USER = "SGD"
FTP_PASSWORD = "0N!nd=I9EJ"
FTP_ROOT = "Tradhojd_LaserdataSkog"
TILE_M = 2500
RESOLUTION_M = 1.0
NODATA = -1
TILE_PARTS = (".mrf", ".idx", ".lrc")
MOSAIC = "chm.tif"
NAME_RE = re.compile(r"^THL_(?P<block>\w+?)_(?P<n>\d{5})_(?P<e>\d{4})_(?P<year>\d{4})\.mrf$")
BLOCK_RE = re.compile(r"^\d{2}_\d$")


@dataclass(frozen=True)
class Tile:
    north_m: int  # lower left corner, SWEREF99 TM
    east_m: int
    year: int
    path: str  # on the FTP server, without extension

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def tile_keys(left: float, bottom: float, right: float, top: float) -> list[tuple[int, int]]:
    """(north, east) of the lower left corner of every tile that overlaps the bounds."""
    n0, n1 = math.floor(bottom / TILE_M), math.ceil(top / TILE_M)
    e0, e1 = math.floor(left / TILE_M), math.ceil(right / TILE_M)
    return [(n * TILE_M, e * TILE_M) for n in range(n0, n1) for e in range(e0, e1)]


def block_name(north_m: int, east_m: int) -> str:
    return f"{north_m // 100_000}_{east_m // 100_000}"


def choose_tiles(listing: dict[str, list[str]], keys: Iterable[tuple[int, int]]) -> tuple[list[Tile], list[tuple[int, int]]]:
    """Newest scan per tile. ``listing`` maps a folder on the server to its file names."""
    wanted = set(keys)
    best: dict[tuple[int, int], Tile] = {}
    for folder, names in listing.items():
        for name in names:
            m = NAME_RE.match(name)
            if not m:
                continue
            key = (int(m["n"]) * 100, int(m["e"]) * 100)
            if key not in wanted:
                continue
            tile = Tile(key[0], key[1], int(m["year"]), f"{folder}/{name[:-4]}")
            if key not in best or tile.year > best[key].year:
                best[key] = tile
    missing = sorted(wanted - set(best))
    return [best[k] for k in sorted(best)], missing


def _connect(timeout: float) -> ftplib.FTP_TLS:
    ftp = ftplib.FTP_TLS(FTP_HOST, timeout=timeout)
    ftp.login(FTP_USER, FTP_PASSWORD)
    ftp.prot_p()
    return ftp


def _list_blocks(ftp: ftplib.FTP_TLS, blocks: set[str]) -> dict[str, list[str]]:
    """File names in every folder for the given 100 km blocks, in all scan years."""
    listing: dict[str, list[str]] = {}

    def walk(folder: str, depth: int) -> None:
        for entry in ftp.nlst(folder):
            name = entry.rsplit("/", 1)[-1]
            if name in (".", ".."):
                continue
            sub = f"{folder}/{name}"
            if BLOCK_RE.match(name):
                if name in blocks:
                    listing[sub] = [n.rsplit("/", 1)[-1] for n in ftp.nlst(sub)]
            elif depth < 2 and "." not in name:
                walk(sub, depth + 1)

    walk(FTP_ROOT, 0)
    return listing


def _download(ftp: ftplib.FTP_TLS, tile: Tile, tile_dir: Path, force: bool) -> Path:
    tile_dir.mkdir(parents=True, exist_ok=True)
    for ext in TILE_PARTS:
        dst = tile_dir / (tile.name + ext)
        if dst.exists() and dst.stat().st_size > 0 and not force:
            continue
        tmp = dst.with_suffix(dst.suffix + ".part")
        with tmp.open("wb") as f:
            ftp.retrbinary(f"RETR {tile.path}{ext}", f.write)
        tmp.replace(dst)
    return tile_dir / (tile.name + ".mrf")


def mosaic(tile_paths: Iterable[Path], bounds: tuple[float, float, float, float], out: Path) -> dict[str, Any]:
    """Tiles cut and pasted into one int16 raster (dm) over ``bounds`` snapped to whole metres."""
    left, bottom = math.floor(bounds[0]), math.floor(bounds[1])
    right, top = math.ceil(bounds[2]), math.ceil(bounds[3])
    width, height = int(right - left), int(top - bottom)
    data = np.full((height, width), NODATA, dtype=np.int16)
    for path in tile_paths:
        with rasterio.open(path) as src:
            if abs(src.res[0] - RESOLUTION_M) > 1e-6 or abs(src.res[1] - RESOLUTION_M) > 1e-6:
                raise ValueError(f"{path}: resolution {src.res}, expected {RESOLUTION_M} m")
            b = src.bounds
            c0, c1 = max(int(round(b.left - left)), 0), min(int(round(b.right - left)), width)
            r0, r1 = max(int(round(top - b.top)), 0), min(int(round(top - b.bottom)), height)
            if c0 >= c1 or r0 >= r1:
                continue
            tile = src.read(1)
            tc0, tr0 = int(round(left + c0 - b.left)), int(round(b.top - (top - r0)))
            part = tile[tr0:tr0 + (r1 - r0), tc0:tc0 + (c1 - c0)]
            nodata = src.nodata
            if nodata is not None:
                part = np.where(part == nodata, NODATA, part)
            data[r0:r1, c0:c1] = part
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        out, "w", driver="GTiff", width=width, height=height, count=1, dtype="int16", crs="EPSG:3006",
        transform=from_origin(left, top, RESOLUTION_M, RESOLUTION_M), nodata=NODATA,
        compress="deflate", tiled=True, blockxsize=256, blockysize=256,
    ) as dst:
        dst.write(data, 1)
    return {"bounds": [left, bottom, right, top], "width": width, "height": height,
            "covered": round(float((data != NODATA).mean()), 4)}


class TreeHeightProvider:
    """Cached tree height mosaic: height above ground in metres, ``None`` without data."""

    def __init__(self, heights_m: np.ndarray, transform: Any) -> None:
        self.heights_m = heights_m  # float32, NaN without data
        self.transform = transform

    @classmethod
    def from_cache(cls, cache_dir: str | Path) -> "TreeHeightProvider":
        path = Path(cache_dir) / MOSAIC
        if not path.exists():
            raise FileNotFoundError(f"tree height cache missing {MOSAIC}: {cache_dir}")
        with rasterio.open(path) as src:
            raw = src.read(1)
            heights = np.where(raw == src.nodata, np.nan, raw / 10.0).astype(np.float32)
            return cls(heights, src.transform)

    def height_at_xy_np(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Height of the pixel under each SWEREF99 TM point, NaN outside or without data."""
        inv = ~self.transform
        col, row = inv * (np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
        col, row = np.floor(col).astype(np.int64), np.floor(row).astype(np.int64)
        h, w = self.heights_m.shape
        ok = (col >= 0) & (col < w) & (row >= 0) & (row < h)
        out = np.full(col.shape, np.nan, dtype=np.float32)
        out[ok] = self.heights_m[row[ok], col[ok]]
        return out

    def height_at(self, point: LatLon) -> float | None:
        x, y = project(point.lat, point.lon)
        v = float(self.height_at_xy_np(np.array([x]), np.array([y]))[0])
        return None if math.isnan(v) else v

    @classmethod
    def fetch(
        cls,
        aoi: AOI,
        cache_dir: str | Path,
        *,
        buffer_m: float = 0.0,
        force: bool = False,
        timeout: float = 120.0,
        connect: Callable[[float], Any] = _connect,
    ) -> "TreeHeightProvider":
        cache_dir = Path(cache_dir)
        out, manifest_path = cache_dir / MOSAIC, cache_dir / "manifest.json"
        bbox = aoi.bbox.expand_meters(buffer_m)
        corners = [project(lat, lon) for lat in (bbox.min_lat, bbox.max_lat) for lon in (bbox.min_lon, bbox.max_lon)]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        bounds = (min(xs), min(ys), max(xs), max(ys))
        if out.exists() and manifest_path.exists() and not force:
            m = json.loads(manifest_path.read_text("utf-8"))
            b = m.get("bounds") or [0, 0, 0, 0]
            if b[0] <= bounds[0] and b[1] <= bounds[1] and b[2] >= bounds[2] and b[3] >= bounds[3]:
                return cls.from_cache(cache_dir)
        keys = tile_keys(*bounds)
        ftp = connect(timeout)
        try:
            listing = _list_blocks(ftp, {block_name(n, e) for n, e in keys})
            tiles, missing = choose_tiles(listing, keys)
            paths = [_download(ftp, t, cache_dir / "tiles", force) for t in tiles]
        finally:
            try:
                ftp.quit()
            except Exception:
                pass
        info = mosaic(paths, bounds, out)
        manifest = {
            "schema_version": 1,
            "source": "skogsstyrelsen",
            "dataset": "Trädhöjd laserdata skog",
            "url": f"ftps://{FTP_HOST}/{FTP_ROOT}",
            "crs": "EPSG:3006",
            "resolution_m": RESOLUTION_M,
            "unit": "dm",
            "aoi": {"name": aoi.name, "bbox": list(bbox.as_tuple())},
            "tiles": [{"name": t.name, "year": t.year} for t in tiles],
            "missing_tiles": [{"north": n, "east": e} for n, e in missing],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **info,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")
        return cls.from_cache(cache_dir)
