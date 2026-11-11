"""Figures for the mgis-layers presentation.

Run from the repository root after fetching the data (see presentation/README.md):

    python presentation/make_figures.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
import shapely
from matplotlib.colors import TwoSlopeNorm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch, Rectangle
from matplotlib.path import Path as MPath
from pyproj import Geod, Proj, Transformer
from shapely.geometry import Point, shape

from gis_layers import (
    AOI,
    BBox,
    LatLon,
    OsmInfraProvider,
    OsmLanduseProvider,
    OsmRoadsProvider,
    TreeHeightProvider,
    load_elevation,
    terrain_profile,
)
from gis_layers.crs import project, unproject
from gis_layers.hillshade import hillshade
from gis_layers.providers.osm_buildings import building_properties
from gis_layers.providers.osm_infra import infra_properties
from gis_layers.providers.osm_landuse import landuse_label
from gis_layers.providers.osm_roads import road_properties

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = HERE / "data"
CACHE = DATA / "cache"
FIG = HERE / "figures"

SKA_OPEN = DATA / "ska_edeby_open"
SKA_LM = DATA / "ska_edeby_lm"
HAMMARBY = DATA / "hammarbybacken"
HAMMARBY_LM = DATA / "hammarbybacken_lm"
COUNTRIES = DATA / "ne_50m_admin_0_countries.geojson"

PROFILE_START = LatLon(59.3457, 17.7187)
PROFILE_END = LatLon(59.3470, 17.7645)

ATTR_OSM = "© OpenStreetMap contributors"
ATTR_LM = "Höjddata © Lantmäteriet"
ATTR_EUDEM = "Höjddata: Copernicus EU-DEM"
ATTR_SKS = "Trädhöjd: Skogsstyrelsen"
ATTR_NE = "Natural Earth"

TO_SWEREF = Transformer.from_crs("EPSG:4326", "EPSG:3006", always_xy=True)
TO_MERC = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
GEOD = Geod(ellps="GRS80")

plt.rcParams.update({
    "font.size": 14,
    "axes.titlesize": 16,
    "axes.titleweight": "bold",
    "axes.edgecolor": "#888888",
    "savefig.dpi": 150,
    "savefig.bbox": "tight",
})

LANDUSE_CLASSES = [
    ("Skog", "#86b86b", ("landuse=forest", "natural=wood")),
    ("Åker", "#f1e2a1", ("landuse=farmland",)),
    ("Gräs och äng", "#cfe6a6", ("landuse=meadow", "landuse=grass", "natural=grassland", "leisure=park")),
    ("Buskmark", "#b1c98b", ("natural=scrub",)),
    ("Bebyggelse", "#d8cfc7", ("landuse=residential", "landuse=farmyard", "landuse=industrial", "landuse=commercial")),
    ("Vatten och våtmark", "#9cc7e8", ("natural=water", "landuse=reservoir", "natural=wetland", "landuse=basin")),
]
LANDUSE_OTHER = ("Övrigt", "#e2e2e2")

PISTE_COLOURS = {"novice": "#2e9e44", "easy": "#1f6fd1", "intermediate": "#d62728", "advanced": "#111111"}


# --- helpers ---------------------------------------------------------------


def features(path: Path) -> list[dict]:
    return json.loads(path.read_text("utf-8")).get("features") or []


def project_geom(geom, transformer):
    return shapely.transform(geom, lambda c: np.column_stack(transformer.transform(c[:, 0], c[:, 1])))


def to_sweref(geometry: dict):
    return project_geom(shape(geometry), TO_SWEREF)


def landuse_class(props: dict) -> tuple[str, str]:
    label = landuse_label(props)
    for name, colour, labels in LANDUSE_CLASSES:
        if label in labels:
            return name, colour
    return LANDUSE_OTHER


def polygon_patch(geom, **kwargs) -> PathPatch:
    polygons = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    vertices: list = []
    codes: list = []
    for polygon in polygons:
        for ring in (polygon.exterior, *polygon.interiors):
            coords = np.asarray(ring.coords)[:, :2]
            vertices.extend(coords)
            codes += [MPath.MOVETO] + [MPath.LINETO] * (len(coords) - 2) + [MPath.CLOSEPOLY]
    return PathPatch(MPath(vertices, codes), **kwargs)


def plot_line(ax, geom, **kwargs) -> None:
    lines = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
    for line in lines:
        x, y = line.xy
        ax.plot(x, y, solid_capstyle="round", **kwargs)


def attribution(ax, *parts: str) -> None:
    ax.text(
        0.995, 0.005, " · ".join(parts), transform=ax.transAxes, ha="right", va="bottom",
        fontsize=9, color="#333333", bbox=dict(fc="white", alpha=0.75, ec="none", pad=2),
    )


def map_axes(ax, extent) -> None:
    left, right, bottom, top = extent
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])


def scalebar(ax, extent, length_m: float = 500.0) -> None:
    left, right, bottom, top = extent
    x0 = left + 0.04 * (right - left)
    y0 = bottom + 0.05 * (top - bottom)
    ax.add_patch(Rectangle((x0, y0), length_m, 0.008 * (top - bottom), color="black", zorder=20))
    ax.text(x0 + length_m / 2, y0 + 0.018 * (top - bottom), f"{length_m:.0f} m", ha="center",
            va="bottom", fontsize=11, zorder=20,
            bbox=dict(fc="white", alpha=0.7, ec="none", pad=1))


def cached_hillshade(name: str, elevation, bbox, resolution_m: float):
    """Hillshade from the library, cached; returns (rgba, extent in SWEREF99 TM)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{name}.npz"
    if path.exists():
        data = np.load(path)
        rgba, corners = data["rgba"], data["corners"]
    else:
        print(f"  hillshade {name} at {resolution_m} m …")
        shade = hillshade(elevation, bbox, resolution_m=resolution_m, max_px=4000)
        rgba, corners = shade.rgba, np.asarray(shade.corners)
        np.savez_compressed(path, rgba=rgba, corners=corners)
    left, top = project(corners[0][1], corners[0][0])
    right, bottom = project(corners[2][1], corners[2][0])
    return rgba, (left, right, bottom, top)


def show_hillshade(ax, shade, alpha: float = 1.0) -> None:
    rgba, extent = shade
    ax.imshow(rgba, extent=extent, alpha=alpha, interpolation="bilinear", zorder=0)


# --- data ------------------------------------------------------------------


class Ska:
    def __init__(self) -> None:
        self.aoi = AOI.from_geojson(REPO / "examples" / "aoi_ska_edeby.geojson")[0]
        self.lm = load_elevation(SKA_LM / "elevation")
        self.open = load_elevation(SKA_OPEN / "elevation")
        osm = SKA_OPEN / "osm"
        self.landuse = [(to_sweref(f["geometry"]), f["properties"]) for f in features(osm / "landuse_ska_edeby.geojson")]
        self.roads = [(to_sweref(f["geometry"]), f["properties"]) for f in features(osm / "roads_ska_edeby.geojson")]
        self.infra = [(to_sweref(f["geometry"]), f["properties"]) for f in features(osm / "infra_ska_edeby.geojson")]
        self.buildings = [(to_sweref(f["geometry"]), f["properties"]) for f in features(osm / "buildings_ska_edeby.geojson")]
        self.hs_lm = cached_hillshade("ska_lm_2m", self.lm, self.aoi.bbox, 2.0)
        self.hs_open = cached_hillshade("ska_open_2m", self.open, self.aoi.bbox, 2.0)
        self.hs_lm_wide = cached_hillshade("ska_lm_wide_4m", self.lm, self.aoi.bbox.expand_meters(1000), 4.0)
        self.extent = self.hs_lm[1]

    def aoi_ring(self):
        b = self.aoi.bbox
        return TO_SWEREF.transform([b.min_lon, b.max_lon, b.max_lon, b.min_lon, b.min_lon],
                                   [b.min_lat, b.min_lat, b.max_lat, b.max_lat, b.min_lat])


def draw_landuse(ax, items, alpha: float = 1.0) -> None:
    polygons = [(g, p) for g, p in items if g.geom_type in ("Polygon", "MultiPolygon")]
    for geom, props in sorted(polygons, key=lambda item: -item[0].area):
        _, colour = landuse_class(props)
        ax.add_patch(polygon_patch(geom, fc=colour, ec="#ffffff", lw=0.3, alpha=alpha, zorder=2))
    for geom, props in items:
        if geom.geom_type in ("LineString", "MultiLineString") and props.get("waterway"):
            plot_line(ax, geom, color="#3c8dd1", lw=1.2, zorder=3)


def road_style(props: dict) -> dict | None:
    road = road_properties(props)
    if road is None:
        return None
    highway = road["highway"]
    if road["snowmobile"]:
        return dict(color="#00a5c8", lw=2.0, ls="--")
    if highway in ("motorway", "trunk", "primary", "secondary"):
        return dict(color="#e07b1f", lw=3.2)
    if highway in ("tertiary", "unclassified", "residential"):
        return dict(color="#4a4a4a", lw=1.9)
    if highway in ("service", "raceway"):
        return dict(color="#7a7a7a", lw=1.0)
    if highway == "track":
        return dict(color="#8b6b3d", lw=1.3, ls="--")
    return dict(color="#8b6b3d", lw=1.0, ls=":")


ROAD_LEGEND = [
    Line2D([], [], color="#e07b1f", lw=3.2, label="Större väg"),
    Line2D([], [], color="#4a4a4a", lw=1.9, label="Mindre väg"),
    Line2D([], [], color="#7a7a7a", lw=1.0, label="Serviceväg"),
    Line2D([], [], color="#8b6b3d", lw=1.3, ls="--", label="Traktorväg"),
    Line2D([], [], color="#8b6b3d", lw=1.0, ls=":", label="Stig, gång- och cykelväg"),
]


def draw_roads(ax, items, zorder: int = 4) -> None:
    for geom, props in items:
        style = road_style(props)
        if style and geom.geom_type in ("LineString", "MultiLineString"):
            plot_line(ax, geom, zorder=zorder, **style)


INFRA_STYLE = {
    "power_line": dict(color="#d62728", lw=2.6),
    "power_minor_line": dict(color="#ff8c1a", lw=1.6),
    "aerialway": dict(color="#111111", lw=2.0),
    "aerialway_surface": dict(color="#111111", lw=1.4, ls="--"),
    "railway_electrified": dict(color="#6a3d9a", lw=2.0),
}


def draw_infra(ax, items, labels: bool = True, zorder: int = 6) -> None:
    for geom, props in items:
        info = infra_properties(props)
        if info is None:
            continue
        if geom.geom_type in ("LineString", "MultiLineString"):
            if info["corridor"]:
                ax.add_patch(polygon_patch(geom.buffer(info["radius_m"]), fc=INFRA_STYLE.get(info["kind"], {}).get("color", "#d62728"),
                                           ec="none", alpha=0.25, zorder=zorder - 1))
            plot_line(ax, geom, zorder=zorder, **INFRA_STYLE.get(info["kind"], dict(color="#d62728", lw=1.5)))
        else:
            ax.plot(geom.x, geom.y, marker="^", ms=9, mfc="#d62728", mec="white", mew=1.0, zorder=zorder + 1, ls="none")
            if labels:
                ax.annotate(f"{info['height_m']:.0f} m", (geom.x, geom.y), xytext=(6, 4), textcoords="offset points",
                            fontsize=10, color="#8b0000", zorder=zorder + 1,
                            bbox=dict(fc="white", alpha=0.7, ec="none", pad=0.5))


def draw_buildings(ax, items, colour: str = "#3d3d3d", zorder: int = 5) -> None:
    for geom, props in items:
        if building_properties(props) is not None and geom.geom_type in ("Polygon", "MultiPolygon"):
            ax.add_patch(polygon_patch(geom, fc=colour, ec="none", zorder=zorder))


def landuse_legend(ax, loc="upper left", **kwargs) -> None:
    handles = [Patch(fc=colour, ec="#999999", label=name) for name, colour, _ in LANDUSE_CLASSES]
    handles.append(Patch(fc=LANDUSE_OTHER[1], ec="#999999", label=LANDUSE_OTHER[0]))
    handles.append(Line2D([], [], color="#3c8dd1", lw=1.5, label="Vattendrag"))
    ax.legend(handles=handles, loc=loc, fontsize=10, framealpha=0.85, **kwargs)


# --- figures: concepts -------------------------------------------------------


def fig_title(ska: Ska) -> None:
    left, right, bottom, top = ska.extent
    cy = 0.5 * (bottom + top)
    height = (right - left) * 9 / 16
    fig, ax = plt.subplots(figsize=(16, 9))
    show_hillshade(ax, ska.hs_lm)
    water = [(g, p) for g, p in ska.landuse if landuse_class(p)[0] == "Vatten och våtmark"]
    draw_landuse(ax, water, alpha=0.8)
    draw_roads(ax, ska.roads)
    map_axes(ax, (left, right, cy - height / 2, cy + height / 2))
    fig.subplots_adjust(0, 0, 1, 1)
    attribution(ax, ATTR_LM, ATTR_OSM)
    fig.savefig(FIG / "title.png", bbox_inches=None, pad_inches=0)
    plt.close(fig)


def fig_aoi(ska: Ska) -> None:
    fig, ax = plt.subplots(figsize=(8, 8))
    show_hillshade(ax, ska.hs_lm)
    b = ska.aoi.bbox
    ring = [(b.min_lon, b.min_lat), (b.max_lon, b.min_lat), (b.max_lon, b.max_lat), (b.min_lon, b.max_lat), (b.min_lon, b.min_lat)]
    xs, ys = TO_SWEREF.transform([p[0] for p in ring], [p[1] for p in ring])
    ax.plot(xs, ys, color="#e4572e", lw=3)
    ax.text(xs[3] + 60, ys[3] - 60, "AOI ska_edeby\n2,8 × 2,8 km", color="#e4572e", fontsize=15, fontweight="bold",
            va="top", bbox=dict(fc="white", alpha=0.8, ec="none"))
    for (lon, lat), x, y in zip(ring[:2], xs[:2], ys[:2]):
        ax.annotate(f"{lat:.3f}° N\n{lon:.3f}° Ö", (x, y), xytext=(0, -38), textcoords="offset points",
                    ha="center", fontsize=11, color="#333333")
    left, right, bottom, top = ska.extent
    pad = 300
    map_axes(ax, (left - pad, right + pad, bottom - pad, top + pad))
    ax.set_facecolor("#f4f4f4")
    attribution(ax, ATTR_LM)
    fig.savefig(FIG / "aoi.png")
    plt.close(fig)


def fig_raster_vs_vector(ska: Ska) -> None:
    cx, cy = 655400.0, 6580900.0
    half = 80.0
    extent = (cx - half, cx + half, cy - half, cy + half)
    with rasterio.open(SKA_OPEN / "treeheight" / "chm.tif") as src:
        window = rasterio.windows.from_bounds(*(extent[0], extent[2], extent[1], extent[3]), transform=src.transform)
        chm = src.read(1, window=window).astype(float)
        win_transform = src.window_transform(window)
    chm[chm < 0] = np.nan
    chm /= 10.0
    w_left, w_top = win_transform * (0, 0)
    w_right, w_bottom = win_transform * (chm.shape[1], chm.shape[0])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 7.2))
    im = ax1.imshow(chm, extent=(w_left, w_right, w_bottom, w_top), cmap="YlGn", vmin=0, vmax=30, interpolation="nearest")
    map_axes(ax1, extent)
    ax1.set_title("Raster: trädhöjd, ett värde per 1 × 1 m")
    cbar = fig.colorbar(im, ax=ax1, fraction=0.046, pad=0.02)
    cbar.set_label("Trädhöjd (m)")
    attribution(ax1, ATTR_SKS)
    scalebar(ax1, extent, 50)

    ax2.set_facecolor("#fafafa")
    draw_landuse(ax2, ska.landuse)
    draw_roads(ax2, ska.roads)
    draw_buildings(ax2, ska.buildings)
    crop = shapely.box(extent[0], extent[2], extent[1], extent[3])
    for geom, _ in ska.buildings + ska.roads + ska.landuse:
        if not geom.intersects(crop):
            continue
        coords = shapely.get_coordinates(geom)
        ax2.plot(coords[:, 0], coords[:, 1], "o", ms=3.5, color="#e4572e", zorder=8, ls="none")
    map_axes(ax2, extent)
    ax2.set_title("Vektor: ytor, linjer och punkter ur OSM")
    ax2.legend(handles=[Line2D([], [], marker="o", ls="none", color="#e4572e", ms=5, label="Hörnpunkter i OSM")],
               loc="upper left", fontsize=11, framealpha=0.9)
    attribution(ax2, ATTR_OSM)
    scalebar(ax2, extent, 50)
    fig.tight_layout()
    fig.savefig(FIG / "raster_vs_vector.png")
    plt.close(fig)


# --- figures: projection ------------------------------------------------------


def country_shapes() -> dict:
    out = {}
    for feature in features(COUNTRIES):
        name = feature["properties"].get("ADMIN")
        if name in ("Sweden", "Norway", "Finland", "Denmark"):
            out[name] = shape(feature["geometry"])
    return out


def fig_cylinder() -> None:
    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(projection="3d")
    lon0 = math.radians(15.0)
    u = np.array([math.cos(lon0), math.sin(lon0), 0.0])
    a = np.array([-math.sin(lon0), math.cos(lon0), 0.0])
    ez = np.array([0.0, 0.0, 1.0])

    lons = np.radians(np.arange(-180, 181, 3))
    lats = np.radians(np.arange(-90, 91, 3))
    for lat in np.radians(np.arange(-60, 61, 30)):
        ax.plot(np.cos(lat) * np.cos(lons), np.cos(lat) * np.sin(lons), np.full_like(lons, np.sin(lat)), color="#bbbbbb", lw=0.6)
    for lon in np.radians(np.arange(-165, 181, 30)):
        ax.plot(np.cos(lats) * np.cos(lon), np.cos(lats) * np.sin(lon), np.sin(lats), color="#bbbbbb", lw=0.6)
    ax.plot(np.cos(lats) * math.cos(lon0), np.cos(lats) * math.sin(lon0), np.sin(lats), color="#d62728", lw=3,
            label="15° Ö: där cylindern rör jorden")

    sweden = country_shapes()["Sweden"]
    for polygon in getattr(sweden, "geoms", [sweden]):
        lon, lat = (np.radians(np.asarray(c)) for c in polygon.exterior.xy)
        ax.plot(1.003 * np.cos(lat) * np.cos(lon), 1.003 * np.cos(lat) * np.sin(lon), 1.003 * np.sin(lat), color="#1b4f8a", lw=1.5)

    t = np.linspace(-1.5, 1.5, 30)
    theta = np.linspace(0, 2 * np.pi, 90)
    tt, th = np.meshgrid(t, theta)
    pts = tt[..., None] * a + np.cos(th)[..., None] * u + np.sin(th)[..., None] * ez
    ax.plot_surface(pts[..., 0], pts[..., 1], pts[..., 2], color="#4c8bd6", alpha=0.13, linewidth=0, shade=False)
    for edge in (-1.5, 1.5):
        ring = edge * a + np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * ez
        ax.plot(ring[:, 0], ring[:, 1], ring[:, 2], color="#4c8bd6", lw=1)

    ax.view_init(elev=28, azim=15 + 35)
    ax.set_box_aspect((1, 1, 1))
    lim = 1.25
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_zlim(-lim, lim)
    ax.set_axis_off()
    ax.legend(loc="lower center", fontsize=13, frameon=False)
    fig.savefig(FIG / "cylinder.png")
    plt.close(fig)


TOWNS = [
    ("Skå-Edeby", 59.343, 17.74),
    ("Strömstad", 58.94, 11.17),
    ("Kiruna", 67.85, 20.23),
    ("Haparanda", 65.83, 24.15),
]


def fig_sweden_scale() -> None:
    countries = country_shapes()
    sweden = project_geom(countries["Sweden"], TO_SWEREF)
    fig, ax = plt.subplots(figsize=(7.5, 10))
    for name in ("Norway", "Finland", "Denmark"):
        ax.add_patch(polygon_patch(project_geom(countries[name], TO_SWEREF), fc="#eeeeee", ec="#bbbbbb", lw=0.6))

    xs = np.arange(200_000, 960_000, 4000.0)
    ys = np.arange(6_100_000, 7_700_000, 4000.0)
    xx, yy = np.meshgrid(xs, ys)
    to_wgs = Transformer.from_crs("EPSG:3006", "EPSG:4326", always_xy=True)
    lon, lat = to_wgs.transform(xx, yy)
    k = np.asarray(Proj("EPSG:3006").get_factors(lon, lat).meridional_scale)
    err = (k - 1.0) * 1000.0
    im = ax.imshow(err, extent=(xs[0], xs[-1], ys[0], ys[-1]), origin="lower", cmap="RdBu_r",
                   norm=TwoSlopeNorm(vmin=-0.4, vcenter=0.0, vmax=1.9), interpolation="bilinear")
    clip = polygon_patch(sweden, fc="none", ec="#333333", lw=0.8)
    ax.add_patch(clip)
    im.set_clip_path(clip)
    contour = ax.contour(xx, yy, err, levels=[0.0], colors="#555555", linewidths=1, linestyles="--")
    for collection in [contour]:
        collection.set_clip_path(clip)
    ax.axvline(500_000, color="#d62728", lw=2)
    ax.text(505_000, 7_660_000, "15° Ö", color="#d62728", fontsize=13, fontweight="bold")

    proj = Proj("EPSG:3006")
    for name, lat0, lon0 in TOWNS:
        x, y = TO_SWEREF.transform(lon0, lat0)
        f = proj.get_factors(lon0, lat0)
        ax.plot(x, y, "o", color="black", ms=6)
        value = f"{(f.meridional_scale - 1) * 1000:+.2f}".replace(".", ",").replace("-", "−")
        text = f"{name}\n{value} m/km"
        ha = "left" if name != "Haparanda" else "right"
        dx = 25_000 if ha == "left" else -25_000
        if name == "Strömstad":
            ha, dx = "left", 20_000
        ax.text(x + dx, y, text, fontsize=12, va="center", ha=ha,
                bbox=dict(fc="white", alpha=0.85, ec="none", pad=1.5))
    cbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02, ticks=[-0.4, 0.0, 0.5, 1.0, 1.5])
    cbar.ax.set_yticklabels(["−0,4", "0", "0,5", "1,0", "1,5"])
    cbar.set_label("Längdfel i SWEREF99 TM (m per km)")
    map_axes(ax, (200_000, 960_000, 6_110_000, 7_700_000))
    ax.set_facecolor("#dfeaf3")
    ax.legend(handles=[Line2D([], [], color="#555555", ls="--", label="Skalan exakt (k = 1)")], loc="upper left",
              bbox_to_anchor=(0.0, 0.93), fontsize=11)
    attribution(ax, ATTR_NE)
    fig.savefig(FIG / "sweden_scale.png")
    plt.close(fig)


def fig_tissot() -> None:
    countries = country_shapes()
    sweden_wgs = countries["Sweden"]
    centres = [(lat, lon) for lat in (56.0, 59.0, 62.0, 65.0, 68.0) for lon in (12.0, 15.0, 18.0, 21.0)
               if sweden_wgs.buffer(0.3).contains(Point(lon, lat))]
    azimuths = np.linspace(0, 360, 73)
    fig, axes = plt.subplots(1, 2, figsize=(13, 9))
    for ax, transformer, title in (
        (axes[0], TO_MERC, "Web Mercator (webbkartan)"),
        (axes[1], TO_SWEREF, "SWEREF99 TM"),
    ):
        for name in ("Norway", "Finland", "Denmark"):
            ax.add_patch(polygon_patch(project_geom(countries[name], transformer), fc="#eeeeee", ec="#bbbbbb", lw=0.6))
        ax.add_patch(polygon_patch(project_geom(sweden_wgs, transformer), fc="#f7f2e4", ec="#333333", lw=0.8))
        for lat, lon in centres:
            lons, lats, _ = GEOD.fwd(np.full_like(azimuths, lon), np.full_like(azimuths, lat), azimuths, np.full_like(azimuths, 60_000.0))
            x, y = transformer.transform(lons, lats)
            ax.fill(x, y, fc="#e4572e", alpha=0.55, ec="#a02c10")
        corner_lons = [9.5, 25.5, 25.5, 9.5]
        corner_lats = [54.8, 54.8, 69.3, 69.3]
        cx, cy = transformer.transform(corner_lons, corner_lats)
        map_axes(ax, (min(cx), max(cx), min(cy), max(cy)))
        ax.set_facecolor("#dfeaf3")
        ax.set_title(title)
    fig.suptitle("Samma cirklar med 60 km radie på marken", fontsize=17, y=0.98)
    attribution(axes[1], ATTR_NE)
    fig.tight_layout()
    fig.savefig(FIG / "tissot.png")
    plt.close(fig)


def _aoi_panel(ax, name: str, lat0: float, lon0: float, size_m: float = 2800.0) -> None:
    dlat = size_m / 111_320.0
    dlon = size_m / (111_320.0 * math.cos(math.radians(lat0)))
    lon_min, lat_min = lon0 - dlon / 2, lat0 - dlat / 2
    lon_max, lat_max = lon0 + dlon / 2, lat0 + dlat / 2
    cx, cy = TO_SWEREF.transform(lon0, lat0)

    def xy(lons, lats):
        x, y = TO_SWEREF.transform(lons, lats)
        return (np.asarray(x) - cx) / 1000.0, (np.asarray(y) - cy) / 1000.0

    for lat in np.linspace(lat_min - dlat, lat_max + dlat, 9):
        x, y = xy(np.linspace(lon_min - dlon, lon_max + dlon, 50), np.full(50, lat))
        ax.plot(x, y, color="#9fb7cc", lw=0.8)
    for lon in np.linspace(lon_min - dlon, lon_max + dlon, 9):
        x, y = xy(np.full(50, lon), np.linspace(lat_min - dlat, lat_max + dlat, 50))
        ax.plot(x, y, color="#9fb7cc", lw=0.8)

    ring_lon = [lon_min, lon_max, lon_max, lon_min, lon_min]
    ring_lat = [lat_min, lat_min, lat_max, lat_max, lat_min]
    x, y = xy(ring_lon, ring_lat)
    ax.fill(x, y, fc="#e4572e", alpha=0.18, ec="#e4572e", lw=2.5)
    ax.add_patch(Rectangle((x[:4].min(), y[:4].min()), x[:4].max() - x[:4].min(), y[:4].max() - y[:4].min(),
                           fill=False, ec="#222222", lw=1.6, ls="--"))
    gamma = Proj("EPSG:3006").get_factors(lon0, lat0).meridian_convergence
    ax.annotate("", xy=(0, 1.0), xytext=(0, 0), arrowprops=dict(arrowstyle="-|>", color="#222222", lw=2))
    ax.text(0.03, 1.02, "rutnätsnorr", fontsize=11, ha="left", va="bottom", color="#222222")
    tx, ty = xy([lon0, lon0], [lat0, lat0 + 1.0 / 111.32])
    ax.annotate("", xy=(tx[1], ty[1]), xytext=(tx[0], ty[0]), arrowprops=dict(arrowstyle="-|>", color="#1f6fd1", lw=2))
    ax.text(tx[1] - 0.05, ty[1] + 0.02, "sant norr", fontsize=11, ha="right", va="bottom", color="#1f6fd1")
    ax.set_title(f"{name}: vriden {abs(gamma):.1f}°".replace(".", ","))
    ax.set_xlim(-2.1, 2.1)
    ax.set_ylim(-2.1, 2.1)
    ax.set_aspect("equal")
    ax.set_xlabel("km i SWEREF99 TM")


def fig_aoi_skew() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.8))
    _aoi_panel(axes[0], "Skå-Edeby", 59.3425, 17.74)
    _aoi_panel(axes[1], "Haparanda", 65.83, 24.15)
    handles = [
        Patch(fc="#e4572e", alpha=0.4, ec="#e4572e", label="AOI-rutan, rak i grader"),
        Line2D([], [], color="#9fb7cc", label="Latitud och longitud"),
        Line2D([], [], color="#222222", ls="--", label="Rutnätet för hillshaden"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=12, frameon=False)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(FIG / "aoi_skew.png")
    plt.close(fig)


def fig_curvature() -> None:
    radius = 6_371_000.0
    d = np.linspace(0, 10_000, 200)
    drop = d**2 / (2 * radius)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(d / 1000, np.zeros_like(d), color="#1f6fd1", lw=2.5, label="Rak siktlinje")
    ax.plot(d / 1000, -drop, color="#3d7a2a", lw=2.5, label="Havsytan, som profilens höjder följer")
    ax.fill_between(d / 1000, -drop, 0, color="#3d7a2a", alpha=0.08)
    for km in (1, 5, 10):
        h = (km * 1000) ** 2 / (2 * radius)
        ax.plot([km, km], [0, -h], color="#555555", lw=1, ls=":")
        right = km == 10
        ax.annotate(f"{h:.2f} m".replace(".", ","), (km, -h), xytext=(-8 if right else 8, -6), textcoords="offset points",
                    ha="right" if right else "left", va="top", fontsize=13)
    ax.set_xlabel("Avstånd (km)")
    ax.set_ylabel("Höjd (m)")
    ax.set_xlim(0, 10.2)
    ax.set_ylim(-9, 1.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="lower left", fontsize=12, frameon=False)
    fig.savefig(FIG / "curvature.png")
    plt.close(fig)


# --- figures: layers ---------------------------------------------------------


def fig_dem_compare(ska: Ska) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(15, 7.6))
    for ax, shade, title, attr in (
        (axes[0], ska.hs_open, "OpenTopoData EU-DEM, 30 m (standard)", ATTR_EUDEM),
        (axes[1], ska.hs_lm, "Lantmäteriet Markhöjdmodell, 1 m", ATTR_LM),
    ):
        show_hillshade(ax, shade)
        map_axes(ax, ska.extent)
        ax.set_title(title)
        attribution(ax, attr)
        scalebar(ax, ska.extent)
    fig.tight_layout()
    fig.savefig(FIG / "dem_compare.png")
    plt.close(fig)


def fig_layers(ska: Ska) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(18, 12.4))
    extent = ska.extent

    def base(ax, title):
        show_hillshade(ax, ska.hs_lm, alpha=0.45)
        map_axes(ax, extent)
        ax.set_title(title)

    ax = axes[0, 0]
    base(ax, "Markanvändning")
    draw_landuse(ax, ska.landuse, alpha=0.8)
    landuse_legend(ax, loc="lower left", ncol=2)
    attribution(ax, ATTR_LM, ATTR_OSM)

    ax = axes[0, 1]
    base(ax, "Vägar och stigar")
    draw_roads(ax, ska.roads)
    ax.legend(handles=ROAD_LEGEND, loc="lower left", fontsize=10, framealpha=0.85)
    attribution(ax, ATTR_LM, ATTR_OSM)

    ax = axes[0, 2]
    show_hillshade(ax, ska.hs_lm_wide, alpha=0.45)
    left, right, bottom, top = ska.extent
    map_axes(ax, (left - 900, right + 300, bottom - 300, top + 900))
    ax.set_title("Hinder (större utsnitt)")
    xs, ys = ska.aoi_ring()
    ax.plot(xs, ys, color="#444444", lw=1.2, ls="--", zorder=3)
    ax.text(xs[2] - 60, ys[2] - 60, "AOI", ha="right", va="top", fontsize=11, color="#444444")
    draw_infra(ax, ska.infra, labels=False)
    ax.legend(handles=[
        Line2D([], [], color="#d62728", lw=2.6, label="Kraftledning, 30 m"),
        Line2D([], [], color="#ff8c1a", lw=1.6, label="Lokal ledning, 12 m"),
        Line2D([], [], color="#d62728", marker="^", ls="none", ms=9, mec="white", label="Kraftledningsstolpe, 30 m"),
        Patch(fc="#d62728", alpha=0.25, label="Räckvidd i sidled"),
    ], loc="lower right", fontsize=10, framealpha=0.85, title="Nominella höjder", title_fontsize=10,
       bbox_to_anchor=(1.0, 0.04))
    attribution(ax, ATTR_LM, ATTR_OSM)

    ax = axes[1, 0]
    base(ax, "Trädhöjd")
    with rasterio.open(SKA_OPEN / "treeheight" / "chm.tif") as src:
        chm = src.read(1).astype(float)
        b = src.bounds
    chm[chm < 0] = np.nan
    chm /= 10.0
    chm[chm < 1.0] = np.nan
    im = ax.imshow(chm, extent=(b.left, b.right, b.bottom, b.top), cmap="YlGn", vmin=0, vmax=30, zorder=2, interpolation="nearest")
    map_axes(ax, extent)
    cax = ax.inset_axes([0.05, 0.06, 0.4, 0.035])
    cbar = fig.colorbar(im, cax=cax, orientation="horizontal")
    cbar.set_label("m", fontsize=10)
    cbar.ax.tick_params(labelsize=9)
    cax.set_facecolor("white")
    attribution(ax, ATTR_LM, ATTR_SKS)

    ax = axes[1, 1]
    base(ax, "Byggnader")
    draw_buildings(ax, ska.buildings)
    attribution(ax, ATTR_LM, ATTR_OSM)

    ax = axes[1, 2]
    fig_pistes_panel(ax)
    fig.tight_layout()
    fig.savefig(FIG / "layers.png")
    plt.close(fig)


def fig_pistes_panel(ax) -> None:
    elevation = load_elevation(HAMMARBY_LM / "elevation")
    cx, cy = project(59.2999, 18.1115)
    half = 420.0
    lat_min, lon_min = unproject(cx - half - 100, cy - half - 100)
    lat_max, lon_max = unproject(cx + half + 100, cy + half + 100)
    shade = cached_hillshade("hammarby_lm_1m", elevation, BBox(lon_min, lat_min, lon_max, lat_max), 1.0)
    show_hillshade(ax, shade, alpha=0.55)
    osm = HAMMARBY / "osm"
    landuse = [(to_sweref(f["geometry"]), f["properties"]) for f in features(osm / "landuse_hammarbybacken.geojson")]
    water = [(g, p) for g, p in landuse if landuse_class(p)[0] == "Vatten och våtmark"]
    draw_landuse(ax, water, alpha=0.6)
    for f in features(osm / "pistes_hammarbybacken.geojson"):
        geom = to_sweref(f["geometry"])
        colour = PISTE_COLOURS.get(f["properties"].get("piste:difficulty"), "#555555")
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            ax.add_patch(polygon_patch(geom, fc=colour, alpha=0.45, ec=colour, lw=1.5, zorder=4))
        else:
            plot_line(ax, geom, color=colour, lw=4, zorder=4)
    infra_path = osm / "infra_hammarbybacken.geojson"
    if infra_path.exists():
        lifts = [(to_sweref(f["geometry"]), f["properties"]) for f in features(infra_path)]
        draw_infra(ax, lifts, labels=False)
    for f in features(osm / "pistes_hammarbybacken.geojson"):
        geom = to_sweref(f["geometry"])
        point = geom.interpolate(0.5, normalized=True)
        ax.annotate(f["properties"].get("name", ""), (point.x, point.y), xytext=(6, -2), textcoords="offset points",
                    fontsize=10, zorder=9, bbox=dict(fc="white", alpha=0.75, ec="none", pad=0.5))
    map_axes(ax, (cx - half, cx + half, cy - half, cy + half))
    ax.set_title("Pistar och liftar (Hammarbybacken)")
    ax.legend(handles=[
        Line2D([], [], color=PISTE_COLOURS["novice"], lw=4, label="Grön pist"),
        Line2D([], [], color=PISTE_COLOURS["easy"], lw=4, label="Blå pist"),
        Line2D([], [], color=PISTE_COLOURS["intermediate"], lw=4, label="Röd pist"),
        Line2D([], [], color="#111111", lw=1.4, ls="--", label="Släplift (hinder)"),
        Line2D([], [], color="#6a3d9a", lw=2.0, label="Spårväg (hinder)"),
    ], loc="lower left", fontsize=10, framealpha=0.85)
    attribution(ax, ATTR_LM, ATTR_OSM)


# --- figures: combined and profile ------------------------------------------


def _profile_xy():
    sx, sy = project(PROFILE_START.lat, PROFILE_START.lon)
    ex, ey = project(PROFILE_END.lat, PROFILE_END.lon)
    return (sx, sy), (ex, ey)


def fig_combined(ska: Ska) -> None:
    fig, ax = plt.subplots(figsize=(13, 11))
    show_hillshade(ax, ska.hs_lm)
    draw_landuse(ax, ska.landuse, alpha=0.45)
    draw_roads(ax, ska.roads)
    draw_buildings(ax, ska.buildings)
    draw_infra(ax, ska.infra, labels=False)
    (sx, sy), (ex, ey) = _profile_xy()
    ax.plot([sx, ex], [sy, ey], color="#1f6fd1", lw=3.5, zorder=10)
    for label, x, y in (("A", sx, sy), ("B", ex, ey)):
        ax.text(x, y, label, fontsize=15, fontweight="bold", color="white", ha="center", va="center", zorder=11,
                bbox=dict(boxstyle="circle", fc="#1f6fd1", ec="white"))
    map_axes(ax, ska.extent)
    handles = [Patch(fc=colour, ec="#999999", label=name) for name, colour, _ in LANDUSE_CLASSES]
    handles += ROAD_LEGEND[:3]
    handles += [
        Patch(fc="#3d3d3d", label="Byggnad"),
        Line2D([], [], color="#3c8dd1", lw=1.5, label="Vattendrag"),
        Line2D([], [], color="#ff8c1a", lw=1.6, label="Lokal kraftledning"),
        Line2D([], [], color="#1f6fd1", lw=3.5, label="Terrängprofil A–B"),
    ]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=12, frameon=False)
    scalebar(ax, ska.extent)
    attribution(ax, ATTR_LM, ATTR_OSM)
    fig.savefig(FIG / "combined.png")
    plt.close(fig)


def fig_profile(ska: Ska) -> None:
    landuse = OsmLanduseProvider.from_geojson(SKA_OPEN / "osm" / "landuse_ska_edeby.geojson")
    roads = OsmRoadsProvider.from_geojson(SKA_OPEN / "osm" / "roads_ska_edeby.geojson")
    infra = OsmInfraProvider.from_geojson(SKA_OPEN / "osm" / "infra_ska_edeby.geojson")
    trees = TreeHeightProvider.from_cache(SKA_OPEN / "treeheight")

    fine = terrain_profile(PROFILE_START, PROFILE_END, step_m=2.0, elevation=ska.lm, landuse=landuse)
    coarse = terrain_profile(PROFILE_START, PROFILE_END, step_m=2.0, elevation=ska.open)
    d = np.array([s.distance_m for s in fine.samples])
    ground = np.array([s.elevation_m if s.elevation_m is not None else np.nan for s in fine.samples])
    ground_open = np.array([s.elevation_m if s.elevation_m is not None else np.nan for s in coarse.samples])
    tree = np.array([trees.height_at(s.point) or 0.0 for s in fine.samples])

    fig, (ax, band) = plt.subplots(2, 1, figsize=(13, 6.6), sharex=True, gridspec_kw=dict(height_ratios=[9, 1], hspace=0.05))
    ax.fill_between(d, ground, ground + tree, color="#86b86b", alpha=0.6, lw=0, label="Trädhöjd ovanpå marken")
    ax.plot(d, ground, color="#222222", lw=1.6, label="Lantmäteriet 1 m")
    ax.plot(d, ground_open, color="#e07b1f", lw=2.2, ls="--", label="EU-DEM 30 m")

    top = np.nanmax(ground + tree)
    crossings = []
    last = -1e9
    for s in fine.samples:
        hit = roads.nearest_road(s.point, max_distance_m=3.0)
        if hit and s.distance_m - last > 40:
            crossings.append((s.distance_m, hit.highway))
            last = s.distance_m
    for dist, _ in crossings:
        ax.axvline(dist, color="#7a7a7a", lw=1, ls=":", zorder=0)
        ax.text(dist, top + 2, "väg", rotation=90, fontsize=10, ha="center", va="bottom", color="#555555")
    seen = -1e9
    for i, s in enumerate(fine.samples):
        lines = [h for h in infra.features_near(s.point, radius_m=3.0) if h.corridor]
        if lines and s.distance_m - seen > 40:
            h = max(lines, key=lambda hit: hit.height_m)
            g = ground[i]
            ax.plot([s.distance_m, s.distance_m], [g, g + h.height_m], color="#d62728", lw=2.5)
            ax.text(s.distance_m + 15, g + h.height_m, f"ledning {h.height_m:.0f} m", color="#d62728", fontsize=11, va="center")
            seen = s.distance_m

    ax.set_ylabel("Höjd över havet (m)")
    ax.set_ylim(0, top + 12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper left", fontsize=12, frameon=False, ncol=3)
    ax.text(0, -0.5, "A", fontsize=14, fontweight="bold", color="#1f6fd1", ha="center", va="top")
    ax.text(d[-1], -0.5, "B", fontsize=14, fontweight="bold", color="#1f6fd1", ha="center", va="top")

    step = d[1] - d[0]
    for s in fine.samples:
        _, colour = landuse_class({s.landuse.split("=")[0]: s.landuse.split("=")[1]} if s.landuse and "=" in s.landuse else {})
        band.axvspan(s.distance_m - step / 2, s.distance_m + step / 2, color=colour if s.landuse else "#ffffff", lw=0)
    band.set_yticks([])
    band.set_ylabel("Mark", rotation=0, ha="right", va="center", fontsize=11)
    band.legend(handles=[
        Patch(fc="#f1e2a1", label="Åker"),
        Patch(fc="#86b86b", label="Skog"),
        Patch(fc="#d8cfc7", label="Bebyggelse"),
        Patch(fc="#ffffff", ec="#999999", label="Ingen markpolygon (flygfältet)"),
    ], loc="upper center", bbox_to_anchor=(0.5, -1.6), ncol=4, fontsize=11, frameon=False)
    band.set_xlabel("Avstånd från A (m)")
    band.set_xlim(0, d[-1])
    fig.savefig(FIG / "profile.png")
    plt.close(fig)


def main() -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    print("projection figures")
    fig_cylinder()
    fig_sweden_scale()
    fig_tissot()
    fig_aoi_skew()
    fig_curvature()
    print("loading Skå-Edeby")
    ska = Ska()
    print("map figures")
    fig_title(ska)
    fig_aoi(ska)
    fig_raster_vs_vector(ska)
    fig_dem_compare(ska)
    fig_layers(ska)
    fig_combined(ska)
    fig_profile(ska)
    print(f"figures in {FIG}")


if __name__ == "__main__":
    main()
