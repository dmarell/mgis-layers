from __future__ import annotations

from typing import Protocol

from gis_layers.types import LatLon


class ElevationLayer(Protocol):
    def height_at(self, point: LatLon) -> float | None: ...


class LanduseLayer(Protocol):
    def landuse_at(self, point: LatLon) -> str | None: ...


class RoadsLayer(Protocol):
    def nearest_road(
        self, point: LatLon, max_distance_m: float
    ) -> "object | None": ...
