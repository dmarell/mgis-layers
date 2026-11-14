"""Land cover from Naturvårdsverket's Nationella marktäckedata (NMD2023 basskikt v2.1), 10 m.

NMD is published as one national GeoTIFF inside a zip (about 2.7 GB, CC0). ``fetch`` downloads the
zip once to a shared download directory, reads the window under the AOI straight from the zip and
writes ``nmd.tif`` (uint16 class codes, SWEREF99 TM, 10 m) with a ``manifest.json``. The codes are
NMD's own (``CODES``): for example 111–117 forest on firm ground, 200–228 open wetland, 411 open
land without vegetation (bare rock, boulder fields), 4221–4223 heath and 61 inland water.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio
from rasterio.windows import Window

from gis_layers.crs import project
from gis_layers.types import AOI, LatLon

URL = "https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2023/Basskikt_v2_x/NMD2023_basskikt_v2_1.zip"
ZIP_NAME = "NMD2023_basskikt_v2_1.zip"
MEMBER = "NMD2023_basskikt_v2_1/NMD2023bas_v2_1.tif"
DATASET = "NMD2023 basskikt v2.1"
RESOLUTION_M = 10.0
NODATA = 0
MOSAIC = "nmd.tif"
DOWNLOAD_DIR_ENV = "GIS_LAYERS_DOWNLOAD_DIR"

# NMD2023 v2.1 class codes (Naturvårdsverket's product description, appendix 5).
CODES: dict[int, str] = {
    3: "arable land",
    23: "alpine trees on wetland",
    43: "alpine trees on firm ground",
    51: "building",
    52: "artificial surface, not building or road",
    53: "road or railway",
    54: "peat extraction",
    61: "inland water",
    62: "marine water",
    111: "pine forest on firm ground",
    112: "spruce forest on firm ground",
    113: "mixed coniferous on firm ground",
    114: "mixed forest on firm ground",
    115: "deciduous forest on firm ground",
    116: "deciduous hardwood forest on firm ground",
    117: "deciduous with hardwood forest on firm ground",
    118: "temporarily non-forest on firm ground",
    121: "pine forest on wetland",
    122: "spruce forest on wetland",
    123: "mixed coniferous on wetland",
    124: "mixed forest on wetland",
    125: "deciduous forest on wetland",
    126: "deciduous hardwood forest on wetland",
    127: "deciduous with hardwood forest on wetland",
    128: "temporarily non-forest on wetland",
    200: "open wetland",
    211: "shrub dominated mire",
    212: "heath dominated mire",
    213: "grass dominated mire, low",
    214: "grass dominated mire, tall",
    215: "marsh on mire",
    216: "moss dominated mire, firm",
    217: "moss dominated mire, wet",
    218: "other wetland on mire",
    221: "shrub dominated wetland, not on mire",
    222: "heath dominated wetland, not on mire",
    223: "grass dominated wetland, not on mire, low",
    224: "grass dominated wetland, not on mire, medium",
    225: "grass dominated wetland, not on mire, tall",
    226: "moss dominated wetland, not on mire",
    227: "marsh, not on mire",
    228: "other wetland, not on mire",
    411: "open land without vegetation",
    412: "permanent snow",
    413: "glacier",
    4211: "shrub dominated, dry",
    4212: "shrub dominated, mesic",
    4213: "shrub dominated, moist",
    4221: "heath dominated, dry",
    4222: "heath dominated, mesic",
    4223: "heath dominated, moist",
    4231: "grass dominated, dry",
    4232: "grass dominated, mesic",
    4233: "grass dominated, moist",
}


def default_download_dir() -> Path:
    """Where the national zip is kept between fetches: ``$GIS_LAYERS_DOWNLOAD_DIR`` or ``~/.cache/gis_layers``."""
    return Path(os.environ.get(DOWNLOAD_DIR_ENV) or Path.home() / ".cache" / "gis_layers") / "nmd"


def download(url: str, dest: Path, *, timeout: float = 120.0, chunk: int = 1 << 22) -> Path:
    """Download ``url`` to ``dest``, resuming a partial ``.part`` file; returns ``dest``."""
    import requests

    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
        if r.status_code == 416:
            part.replace(dest)
            return dest
        r.raise_for_status()
        mode = "ab" if have and r.status_code == 206 else "wb"
        with part.open(mode) as f:
            for block in r.iter_content(chunk_size=chunk):
                f.write(block)
    part.replace(dest)
    return dest


def source_path(zip_path: Path) -> str:
    """GDAL path to the national raster inside the zip."""
    return f"/vsizip/{Path(zip_path).resolve()}/{MEMBER}"


def crop(source: str, bounds: tuple[float, float, float, float], out: Path) -> dict[str, Any]:
    """The source window over ``bounds`` (SWEREF99 TM), snapped outwards to the source grid."""
    with rasterio.open(source) as src:
        if abs(src.res[0] - RESOLUTION_M) > 1e-6 or abs(src.res[1] - RESOLUTION_M) > 1e-6:
            raise ValueError(f"{source}: resolution {src.res}, expected {RESOLUTION_M} m")
        t = src.transform
        c0 = math.floor((bounds[0] - t.c) / t.a)
        c1 = math.ceil((bounds[2] - t.c) / t.a)
        r0 = math.floor((bounds[3] - t.f) / t.e)
        r1 = math.ceil((bounds[1] - t.f) / t.e)
        window = Window(c0, r0, c1 - c0, r1 - r0)
        data = src.read(1, window=window, boundless=True, fill_value=NODATA).astype(np.uint16)
        transform = src.window_transform(window)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        out, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=1, dtype="uint16",
        crs="EPSG:3006", transform=transform, nodata=NODATA, compress="deflate", tiled=True,
        blockxsize=256, blockysize=256,
    ) as dst:
        dst.write(data, 1)
    left, top = transform.c, transform.f
    codes, counts = np.unique(data, return_counts=True)
    return {
        "bounds": [left, top - data.shape[0] * RESOLUTION_M, left + data.shape[1] * RESOLUTION_M, top],
        "width": int(data.shape[1]),
        "height": int(data.shape[0]),
        "covered": round(float((data != NODATA).mean()), 4),
        "codes": {str(int(c)): int(n) for c, n in zip(codes, counts) if c != NODATA},
    }


class NmdLandcoverProvider:
    """Cached NMD crop: the class code under a point, ``None`` outside or without data."""

    def __init__(self, codes: np.ndarray, transform: Any) -> None:
        self.codes = codes  # uint16, NODATA without data
        self.transform = transform

    @classmethod
    def from_cache(cls, cache_dir: str | Path) -> "NmdLandcoverProvider":
        path = Path(cache_dir) / MOSAIC
        if not path.exists():
            raise FileNotFoundError(f"land cover cache missing {MOSAIC}: {cache_dir}")
        with rasterio.open(path) as src:
            return cls(src.read(1), src.transform)

    def code_at_xy_np(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Code of the pixel under each SWEREF99 TM point, ``NODATA`` (0) outside."""
        inv = ~self.transform
        col, row = inv @ (np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
        col, row = np.floor(col).astype(np.int64), np.floor(row).astype(np.int64)
        h, w = self.codes.shape
        ok = (col >= 0) & (col < w) & (row >= 0) & (row < h)
        out = np.full(col.shape, NODATA, dtype=np.uint16)
        out[ok] = self.codes[row[ok], col[ok]]
        return out

    def code_at(self, point: LatLon) -> int | None:
        x, y = project(point.lat, point.lon)
        v = int(self.code_at_xy_np(np.array([x]), np.array([y]))[0])
        return None if v == NODATA else v

    @classmethod
    def fetch(
        cls,
        aoi: AOI,
        cache_dir: str | Path,
        *,
        buffer_m: float = 0.0,
        force: bool = False,
        download_dir: str | Path | None = None,
        source: str | None = None,
        get: Callable[[str, Path], Path] = download,
    ) -> "NmdLandcoverProvider":
        """Crop NMD to the AOI; ``source`` is a GDAL path that replaces the downloaded zip."""
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
        if source is None:
            zip_path = get(URL, Path(download_dir or default_download_dir()) / ZIP_NAME)
            source = source_path(zip_path)
        info = crop(source, bounds, out)
        manifest = {
            "schema_version": 1,
            "source": "naturvardsverket",
            "dataset": DATASET,
            "url": URL,
            "licence": "CC0",
            "crs": "EPSG:3006",
            "resolution_m": RESOLUTION_M,
            "nodata": NODATA,
            "aoi": {"name": aoi.name, "bbox": list(bbox.as_tuple())},
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **info,
            "legend": {k: CODES.get(int(k), "unknown") for k in info["codes"]},
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")
        return cls.from_cache(cache_dir)
