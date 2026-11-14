"""``gis-layers`` command-line entry point."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from gis_layers.profile import terrain_profile
from gis_layers.providers.elevation import ElevationProvider
from gis_layers.providers.elevation_loader import load_elevation
from gis_layers.providers.nmd_landcover import NmdLandcoverProvider
from gis_layers.providers.open_elevation import OpenElevationProvider
from gis_layers.providers.osm_buildings import OsmBuildingsProvider
from gis_layers.providers.osm_infra import OsmInfraProvider
from gis_layers.providers.osm_landuse import OsmLanduseProvider
from gis_layers.providers.osm_pistes import OsmPistesProvider
from gis_layers.providers.osm_roads import OsmRoadsProvider
from gis_layers.providers.tree_height import TreeHeightProvider
from gis_layers.types import AOI, LatLon


def _default_env_path() -> Path:
    """Prefer gis/config/ in the monorepo layout; fall back to cwd."""
    for candidate in (
        Path("config/lantmateriet.secret.env"),
        Path("lantmateriet.secret.env"),
    ):
        if candidate.is_file():
            return candidate
    return Path("config/lantmateriet.secret.env")


LAYERS = ("elevation", "landuse", "roads", "infra")
OPTIONAL_LAYERS = ("treeheight", "buildings", "pistes", "landcover")
AOI_INDEX = "aoi.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gis-layers")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Download layers for an AOI.")
    fetch.add_argument("--aoi", type=Path, required=True, help="GeoJSON AOI file")
    fetch.add_argument("--out", type=Path, required=True, help="Cache root directory")
    fetch.add_argument(
        "--layers",
        default=",".join(LAYERS),
        help=f"Comma list of {', '.join(LAYERS + OPTIONAL_LAYERS)} (default {','.join(LAYERS)})",
    )
    fetch.add_argument(
        "--elevation-source",
        choices=("open", "lantmateriet"),
        default="open",
        help="DEM source: EU-DEM 25 m via OpenTopoData, 30 m grid (default), or Lantmäteriet 1 m",
    )
    fetch.add_argument("--buffer-m", type=float, default=0.0)
    fetch.add_argument("--env", type=Path, default=None, help="Lantmäteriet credentials file")
    fetch.add_argument("--force", action="store_true")
    fetch.set_defaults(func=_cmd_fetch)

    profile = sub.add_parser("profile", help="Sample a terrain profile between two points.")
    profile.add_argument("--from", dest="start", type=LatLon.parse, required=True, help="lat,lon")
    profile.add_argument("--to", dest="end", type=LatLon.parse, required=True, help="lat,lon")
    profile.add_argument("--step", type=float, default=10.0)
    profile.add_argument("--elevation", type=Path, help="DEM cache dir or index.json")
    profile.add_argument("--landuse", type=Path, help="Cached landuse GeoJSON")
    profile.add_argument("--format", choices=("table", "json"), default="table")
    profile.set_defaults(func=_cmd_profile)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


def _cmd_fetch(args: argparse.Namespace) -> int:
    env_path = args.env or _default_env_path()
    aois = AOI.from_geojson(args.aoi)
    layers = {name.strip() for name in args.layers.split(",") if name.strip()}
    unknown = layers - set(LAYERS) - set(OPTIONAL_LAYERS)
    if unknown:
        raise ValueError(f"unknown layers: {', '.join(sorted(unknown))}")
    args.out.mkdir(parents=True, exist_ok=True)

    for aoi in aois:
        print(f"aoi: {aoi.name} bbox={aoi.bbox.as_tuple()}")
        if "elevation" in layers:
            cache_dir = args.out / "elevation"
            if args.elevation_source == "open":
                OpenElevationProvider.fetch(
                    aoi,
                    cache_dir=cache_dir,
                    buffer_m=args.buffer_m,
                    force=args.force,
                )
                print(f"  elevation (EU-DEM via OpenTopoData, 30 m grid): cached in {cache_dir}")
            else:
                ElevationProvider.fetch(
                    aoi,
                    cache_dir=cache_dir,
                    env_path=env_path,
                    buffer_m=args.buffer_m,
                    force=args.force,
                )
                print(f"  elevation (Lantmäteriet 1 m): cached in {cache_dir}")
        osm_aoi = AOI(name=aoi.name, bbox=aoi.bbox.expand_meters(args.buffer_m))
        if "landuse" in layers:
            OsmLanduseProvider.fetch(osm_aoi, cache_dir=args.out / "osm", force=args.force)
            print(f"  landuse: cached in {args.out / 'osm'}")
        if "roads" in layers:
            OsmRoadsProvider.fetch(osm_aoi, cache_dir=args.out / "osm", force=args.force)
            print(f"  roads: cached in {args.out / 'osm'}")
        if "infra" in layers:
            infra = OsmInfraProvider.fetch(osm_aoi, cache_dir=args.out / "osm", force=args.force)
            print(f"  infra: {infra.feature_count} features cached in {args.out / 'osm'}")
        if "buildings" in layers:
            buildings = OsmBuildingsProvider.fetch(osm_aoi, cache_dir=args.out / "osm", force=args.force)
            print(f"  buildings: {buildings.building_count} footprints cached in {args.out / 'osm'}")
        if "pistes" in layers:
            pistes = OsmPistesProvider.fetch(osm_aoi, cache_dir=args.out / "osm", force=args.force)
            print(f"  pistes: {pistes.piste_count} pistes cached in {args.out / 'osm'}")
        if "treeheight" in layers:
            cache_dir = args.out / "treeheight"
            TreeHeightProvider.fetch(aoi, cache_dir=cache_dir, buffer_m=args.buffer_m, force=args.force)
            m = json.loads((cache_dir / "manifest.json").read_text("utf-8"))
            years = sorted({t["year"] for t in m["tiles"]})
            print(
                f"  treeheight (Skogsstyrelsen, 1 m): {len(m['tiles'])} tiles from {years}, "
                f"{len(m['missing_tiles'])} missing, {m['covered']:.0%} covered, cached in {cache_dir}"
            )
        if "landcover" in layers:
            cache_dir = args.out / "landcover"
            NmdLandcoverProvider.fetch(aoi, cache_dir=cache_dir, buffer_m=args.buffer_m, force=args.force)
            m = json.loads((cache_dir / "manifest.json").read_text("utf-8"))
            print(f"  landcover (NMD2023, 10 m): {len(m['codes'])} classes, {m['covered']:.0%} covered, "
                  f"cached in {cache_dir}")
    write_aoi_index(
        args.out,
        aois,
        layers,
        elevation_source=args.elevation_source if "elevation" in layers else None,
    )
    print(f"  index: {args.out / AOI_INDEX}")
    return 0


def write_aoi_index(
    out: Path,
    aois: list[AOI],
    layers: set[str],
    *,
    elevation_source: str | None = None,
) -> dict:
    """Record what this data dir holds; merges with an earlier fetch into the same dir."""
    path = out / AOI_INDEX
    index: dict = {}
    if path.exists():
        try:
            index = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            index = {}
    by_name = {a["name"]: a for a in index.get("aois") or [] if isinstance(a, dict) and "name" in a}
    for aoi in aois:
        by_name[aoi.name] = {"name": aoi.name, "bbox": list(aoi.bbox.as_tuple())}
    index["schema_version"] = 1
    index["aois"] = list(by_name.values())
    index["layers"] = sorted(set(index.get("layers") or []) | layers)
    index["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if elevation_source:
        index["elevation_source"] = elevation_source
    path.write_text(json.dumps(index, indent=2), "utf-8")
    return index


def _cmd_profile(args: argparse.Namespace) -> int:
    elevation = load_elevation(args.elevation) if args.elevation else None
    landuse = OsmLanduseProvider.from_geojson(args.landuse) if args.landuse else None

    profile = terrain_profile(
        args.start, args.end, step_m=args.step, elevation=elevation, landuse=landuse,
    )

    if args.format == "json":
        out = {
            "start": [args.start.lat, args.start.lon],
            "end": [args.end.lat, args.end.lon],
            "step_m": profile.step_m,
            "length_m": profile.length_m,
            "samples": [
                {
                    "distance_m": s.distance_m,
                    "lat": s.point.lat,
                    "lon": s.point.lon,
                    "elevation_m": s.elevation_m,
                    "landuse": s.landuse,
                }
                for s in profile.samples
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        print(f"{'dist_m':>8} {'lat':>10} {'lon':>10} {'elev_m':>8}  landuse")
        for sample in profile.samples:
            elev = "—" if sample.elevation_m is None else f"{sample.elevation_m:8.2f}"
            land = sample.landuse or "—"
            print(
                f"{sample.distance_m:8.1f} {sample.point.lat:10.5f} "
                f"{sample.point.lon:10.5f} {elev}  {land}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
