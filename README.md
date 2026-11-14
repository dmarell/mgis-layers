# mgis-layers

Python library and CLI that fetches map layers for a Swedish area of interest (AOI) and samples
terrain profiles between two points. Every layer can be fetched once over the network and then
read offline from a cache directory.

Layers:

- **Elevation, default:** OpenTopoData EU-DEM 25 m, with SRTM 30 m as fallback. One `dem.tif` on a
  30 m grid per AOI. No account. `--elevation-source open`.
- **Elevation, optional:** Lantmäteriet Markhöjdmodell 1 m via STAC, collection `dtm-cog`, in
  10 × 10 km tiles (about 250 MB each). Requires a Geotorget account
  ([Lantmäteriet](#lantmäteriet)). `--elevation-source lantmateriet`.
- **Land use:** OSM polygons with `landuse=*` and `natural=*` via Overpass, and waterways
  (`waterway=river|stream|canal|ditch|drain|brook`) as lines in the same file. `landuse_at()`
  reads only the polygons.
- **Roads:** OSM ways with `highway=*` and snowmobile trails (ways with
  `snowmobile=designated|yes`, relations with `route=snowmobile`) via Overpass. `nearest_road()`
  gives the road class and the flags `tunnel`, `bridge` and `snowmobile`, `roads_near()` all roads
  within a radius, and `lines_in_box()` the road lines in SWEREF99 TM within a box (for
  rasterising).
- **Obstacles:** obstacles to low flight via Overpass: power lines and poles, masts, towers,
  chimneys, wind turbines, railways (with `electrified`) and aerial ropeways. Each obstacle gets
  a `kind`, a height above ground (the OSM tag `height`, for wind turbines `height:hub` plus half
  the `rotor:diameter`, otherwise a nominal value per kind in `DEFAULT_INFRA_KINDS`), a
  horizontal reach and the flag `corridor` for lines that are crossed: power lines, catenaries and
  ropeway cables. A railway in a tunnel is kept with height 0 and is not a corridor.
  `features_near(point, radius_m)` lists them nearest first.
- **Tree height, optional:** Skogsstyrelsen's Trädhöjd laserdata skog, 1 m, in 2.5 km tiles from
  their open FTPS server (`ftpsks.skogsstyrelsen.se`, with the published login). The newest scan
  of each tile wins, and the tiles are merged into one `chm.tif` over the AOI. `height_at()` gives
  the height in metres, `None` without forest or outside the scan. Only with
  `--layers …,treeheight`.
- **Buildings, optional:** building footprints (`building=*`, ways and multipolygons) via
  Overpass. `building_properties(tags)` gives the kind (`building`) and, when tagged, the height
  (`height`), the number of storeys (`building:levels`) and the roof shape (`roof:shape`). Only
  with `--layers …,buildings`.
- **Pistes, optional:** downhill runs, cross-country tracks and other pistes (`piste:type=*`,
  ways) via Overpass, as `pistes_<aoi>.geojson`. A closed way with `area=yes` becomes a polygon,
  the rest stay lines. Only with `--layers …,pistes`.
- **Land cover, optional:** Naturvårdsverket's Nationella Marktäckedata (NMD2023 basskikt v2.1),
  10 m, one class code per pixel (forest by type, wetland, open land with and without vegetation,
  built-up land, water). The national file is a 2.7 GB zip, downloaded once to
  `$GIS_LAYERS_DOWNLOAD_DIR/nmd` (default `~/.cache/gis_layers/nmd`, resumed if interrupted); the
  AOI is cut out of it as one `nmd.tif`. `code_at()` gives the code, `0` outside the data. Only
  with `--layers …,landcover`.

The examples use Skå-Edeby airfield west of Stockholm
([`examples/aoi_ska_edeby.geojson`](examples/aoi_ska_edeby.geojson)).

## Installation

Python 3.10 or later. From a clone:

```bash
pip install -e ".[dev]"
```

Or pinned from GitHub, by tag or commit:

```bash
pip install "gis_layers @ git+https://github.com/dmarell/mgis-layers@v0.2.0"
```

## API

```python
from gis_layers import (
    AOI, LatLon,
    OsmInfraProvider, OsmLanduseProvider, OsmRoadsProvider,
    load_elevation, terrain_profile,
)

aoi = AOI.from_geojson("examples/aoi_ska_edeby.geojson")[0]

elevation = load_elevation("data/ska_edeby/elevation")                 # fetched beforehand
landuse   = OsmLanduseProvider.fetch(aoi, cache_dir="data/ska_edeby/osm")
roads     = OsmRoadsProvider.from_geojson("data/ska_edeby/osm/roads_ska_edeby.geojson")
infra     = OsmInfraProvider.from_geojson("data/ska_edeby/osm/infra_ska_edeby.geojson")
print(infra.features_near(LatLon(59.3434, 17.7407), radius_m=50))     # [InfraHit(kind, height_m, corridor, ...)]

profile = terrain_profile(
    LatLon(59.34029, 17.73718), LatLon(59.34653, 17.74423),
    step_m=10.0,
    elevation=elevation,
    landuse=landuse,
)
for sample in profile.samples[:5]:
    print(sample.distance_m, sample.elevation_m, sample.landuse)
```

`load_elevation(directory)` picks the reader from the contents: `OpenElevationProvider` for a
`dem.tif` from OpenTopoData, otherwise `ElevationProvider` for Lantmäteriet tiles in `tiles/`.
`ElevationProvider.search(aoi, …)` lists the tiles `fetch` would download, without downloading
them. The OSM layers have `fetch(aoi, cache_dir=…)` (network, caches to the directory) and
`from_geojson(path)` (offline).

[`examples/profile_demo.py`](examples/profile_demo.py) fetches land use and roads and samples a
profile along a runway.

## CLI

`gis-layers fetch` fetches layers for an AOI into a directory and writes `aoi.json` there
([the format](#aoi-directory)):

| Flag | Default | Meaning |
| --- | --- | --- |
| `--aoi` | required | AOI file (GeoJSON); its bounding box is used |
| `--out` | required | Directory the layers are written to |
| `--layers` | `elevation,landuse,roads,infra` | Comma-separated list of layers; add `treeheight`, `buildings`, `pistes` and `landcover` when needed |
| `--elevation-source` | `open` | `open` (OpenTopoData) or `lantmateriet` (1 m) |
| `--buffer-m` | `0` | Margin around the AOI for all layers, in metres |
| `--env` | `config/lantmateriet.secret.env`, otherwise `./lantmateriet.secret.env` | File with the Lantmäteriet login |
| `--force` | off | Fetch again even what is already in the directory |

```bash
gis-layers fetch --aoi examples/aoi_ska_edeby.geojson --out data/ska_edeby
gis-layers fetch --aoi examples/aoi_ska_edeby.geojson --out data/ska_edeby --layers roads,infra
gis-layers fetch --aoi examples/aoi_ska_edeby.geojson --out data/ska_edeby \
    --elevation-source lantmateriet --buffer-m 200
```

A later fetch into the same directory is merged into `aoi.json`. The elevation cache holds one
elevation model per directory, so each AOI gets its own directory.

`gis-layers profile` samples a terrain profile between two points:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--from`, `--to` | required | Start and end as `lat,lon` |
| `--step` | `10` | Distance between the points, in metres |
| `--elevation` | none | Elevation directory (or Lantmäteriet's `index.json`) |
| `--landuse` | none | The land use layer (GeoJSON) |
| `--format` | `table` | `table` or `json` |

```bash
gis-layers profile --from 59.34029,17.73718 --to 59.34653,17.74423 --step 10 \
    --elevation data/ska_edeby/elevation \
    --landuse data/ska_edeby/osm/landuse_ska_edeby.geojson
```

The table has distance, position, ground elevation and land use per point; `--format json` gives
the same as JSON with `start`, `end`, `step_m`, `length_m` and `samples`.

## AOI directory

What `gis-layers fetch` writes, for example in `data/ska_edeby/`:

| File | Contents |
| --- | --- |
| `aoi.json` | `schema_version`, `aois` (`name`, `bbox` = `[min_lon, min_lat, max_lon, max_lat]`), `layers`, `fetched_at`, `elevation_source` |
| `elevation/` | The elevation model: `dem.tif` and `manifest.json`, or Lantmäteriet's tiles |
| `osm/landuse_<aoi>.geojson`, `osm/roads_<aoi>.geojson`, `osm/infra_<aoi>.geojson` | Land use, roads and obstacles |
| `treeheight/` | Tree height, only with `--layers …,treeheight`: `chm.tif` and `manifest.json` (below), the tiles in `tiles/` |
| `osm/buildings_<aoi>.geojson` | Building footprints, only with `--layers …,buildings` |
| `osm/pistes_<aoi>.geojson` | Pistes, only with `--layers …,pistes` |
| `landcover/` | Land cover, only with `--layers …,landcover`: `nmd.tif` and `manifest.json` (below) |

`<aoi>` is the AOI's name: the feature's `name` or `id` property in the AOI file.

**Tree height** is Skogsstyrelsen's Trädhöjd laserdata skog: 1 m resolution, SWEREF99 TM, in
2.5 km tiles. `chm.tif` is the mosaic over the AOI with buffer, as int16 in decimetres with −1 for
a missing value. `manifest.json` has `source`, `dataset`, `url`, `crs`, `resolution_m`, `unit`
(`dm`), `aoi`, `tiles` (`name`, `year`), `missing_tiles`, `bounds`, `covered` and
`generated_at`.

**Land cover** is NMD2023 basskikt v2.1: 10 m resolution, SWEREF99 TM, on the national grid.
`nmd.tif` is the AOI with buffer as uint16 class codes, `0` where the data has none. Open land
without vegetation (`411`) includes both boulder fields and bare rock. `manifest.json` has
`source`, `dataset`, `url`, `licence`, `crs`, `resolution_m`, `nodata`, `aoi`, `bounds`, `width`,
`height`, `covered`, `codes` (code → pixel count), `legend` (code → name) and `generated_at`.

## Lantmäteriet

Lantmäteriet's elevation model is needed only for `--elevation-source lantmateriet`. The login
lives in a file that must not be committed:

```bash
mkdir -p config
cp lantmateriet.secret.env.example config/lantmateriet.secret.env
```

`gis-layers fetch` reads `config/lantmateriet.secret.env` and otherwise `lantmateriet.secret.env`
in the working directory, or the file given with `--env`. Environment variables with the same
names take precedence over the file.

The elevation model uses two hosts. The STAC search (`api.lantmateriet.se`) takes OAuth2 or Basic.
Downloading GeoTIFF tiles (`dl1.lantmateriet.se`) requires Basic with the Geotorget account, even
when the STAC search uses OAuth2. With a private Geotorget account, Basic with the same user name
and password as the Geotorget login is enough:

```
LANTMATERIET_AUTH_METHOD=basic
LANTMATERIET_STAC_USERNAME=<Geotorget user name>
LANTMATERIET_STAC_PASSWORD=<password>
```

| Variable | Meaning |
| --- | --- |
| `LANTMATERIET_AUTH_METHOD` | `basic` or `oauth2` for the STAC search. Empty: OAuth2 if the OAuth keys are set, otherwise Basic |
| `LANTMATERIET_STAC_USERNAME`, `LANTMATERIET_STAC_PASSWORD` | The Geotorget account. Required for downloading |
| `LANTMATERIET_OAUTH_CLIENT_ID`, `LANTMATERIET_OAUTH_CLIENT_SECRET`, `LANTMATERIET_OAUTH_TOKEN_URL`, `LANTMATERIET_OAUTH_SCOPE`, `LANTMATERIET_OAUTH_AUTH_STYLE` | OAuth2 keys from Lantmäteriet's API portal. `AUTH_STYLE` is `basic` or `post` |
| `LANTMATERIET_STAC_URL` | Another STAC address (default `https://api.lantmateriet.se/stac-hojd/v1`) |

Order the product on Geotorget before the first fetch
([Markhöjdmodell Nedladdning](https://geotorget.lantmateriet.se/geodataprodukter/markhojdmodell-nedladdning-api)):

1. Log in at geotorget.lantmateriet.se.
2. Choose Nationella geodataplattformen → Bli konsument → Ansök.
3. Find Markhöjdmodell Nedladdning and click Beställ (not Prenumerera).
4. Wait until the request is approved.

Without Basic credentials or an order, the download answers 403.

## Map display helpers

For drawing the layers on a map:

- `hillshade(elevation, bbox)` shades the elevation model on a grid in SWEREF99 TM. The default is
  15 m per pixel, at most `max_px` (2000) pixels per side. It returns a `Hillshade` with `rgba`,
  `.png()` and `corners` (WGS84 in the order top left, top right, bottom right, bottom left, as
  MapLibre's image source wants them). Pixels outside the elevation model are transparent.
- `landuse_label(tags)`, `road_properties(tags)`, `infra_properties(tags)` and
  `building_properties(tags)` classify an OSM object the same way as the layers, so that a map
  shows exactly what the queries answer.

## Design

- All sampling happens in **EPSG:3006 (SWEREF99 TM)**, where distances are metres. Input in WGS84
  is converted on entry and results are given in WGS84.
- Every layer can be created with network access (`fetch`) or offline from the cache
  (`from_geojson` for the OSM layers, `from_cache` or `load_elevation` for elevation).
- `terrain_profile` takes any subset of the layers, for example `landuse=None` for elevation only.

## Layout

```
src/gis_layers/
├── types.py              # BBox, LatLon, AOI, ProfileSample, Profile
├── crs.py                # WGS84 ↔ SWEREF99 TM
├── profile.py            # terrain_profile(p1, p2, ...)
├── hillshade.py          # hillshade(elevation, bbox) → PNG and corners in WGS84
├── cli.py                # gis-layers
└── providers/
    ├── base.py               # Protocol classes for the layers
    ├── elevation.py          # Lantmäteriet STAC and tiles (rasterio)
    ├── _lantmateriet_auth.py # Lantmäteriet login (Basic, OAuth2)
    ├── open_elevation.py     # OpenTopoData → one dem.tif
    ├── elevation_loader.py   # load_elevation()
    ├── nmd_landcover.py      # Naturvårdsverket NMD → one nmd.tif
    ├── osm_buildings.py      # building footprints
    ├── osm_infra.py          # power lines, masts, wind turbines, railways, ropeways
    ├── osm_landuse.py
    ├── osm_pistes.py
    ├── osm_roads.py
    ├── overpass.py           # shared Overpass client
    └── tree_height.py        # Skogsstyrelsen tree height → one chm.tif
examples/                 # the example AOI and profile_demo.py
tests/                    # pytest, synthetic data
```

## Tests

```bash
pytest -q
```

The tests need no network.

## Data and licences

The code is MIT licensed ([LICENSE](LICENSE)). The data the library fetches carries the
providers' terms: OpenStreetMap data is © OpenStreetMap contributors under ODbL, the elevation
model contains data from Lantmäteriet, the tree height comes from Skogsstyrelsen, and the land
cover is Naturvårdsverket's Nationella Marktäckedata under CC0.
