"""Pick the right elevation cache reader (open DEM vs Lantmäteriet tiles)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from gis_layers.providers.base import ElevationLayer

if TYPE_CHECKING:
    from gis_layers.providers.elevation import ElevationProvider
    from gis_layers.providers.open_elevation import OpenElevationProvider


def load_elevation(cache_dir: str | Path) -> ElevationLayer:
    """Load elevation from ``dem.tif`` (open) or Lantmäteriet tile cache."""
    cache_dir = Path(cache_dir)
    if _is_open_cache(cache_dir):
        from gis_layers.providers.open_elevation import OpenElevationProvider

        return OpenElevationProvider.from_cache(cache_dir)
    from gis_layers.providers.elevation import ElevationProvider

    return ElevationProvider.from_cache(cache_dir)


def _is_open_cache(cache_dir: Path) -> bool:
    if (cache_dir / "dem.tif").exists():
        manifest = cache_dir / "manifest.json"
        if not manifest.exists():
            return True
        try:
            data = json.loads(manifest.read_text("utf-8"))
        except json.JSONDecodeError:
            return True
        source = data.get("source")
        if source == "opentopodata":
            return True
        if isinstance(source, dict) and source.get("product") == "OpenTopoData":
            return True
    return False
