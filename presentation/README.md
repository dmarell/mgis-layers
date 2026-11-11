# Presentation

A 15-minute presentation of mgis-layers, in Swedish: [`slides.md`](slides.md) for
[Marp](https://marp.app/), with speaker notes. The figures in [`figures/`](figures/) are made from
real data by [`make_figures.py`](make_figures.py).

## Data

From the repository root, with the library installed (`pip install -e ".[plot]"`). The 1 m
elevation model needs a Lantmäteriet login ([Lantmäteriet](../README.md#lantmäteriet)).

```bash
gis-layers fetch --aoi examples/aoi_ska_edeby.geojson --out presentation/data/ska_edeby_open \
    --layers elevation,landuse,roads,infra,treeheight,buildings --buffer-m 300
gis-layers fetch --aoi examples/aoi_ska_edeby.geojson --out presentation/data/ska_edeby_lm \
    --layers elevation --elevation-source lantmateriet --buffer-m 300
gis-layers fetch --aoi presentation/aoi_hammarbybacken.geojson --out presentation/data/hammarbybacken \
    --layers elevation,landuse,roads,infra,buildings,pistes --buffer-m 200
gis-layers fetch --aoi presentation/aoi_hammarbybacken.geojson --out presentation/data/hammarbybacken_lm \
    --layers elevation --elevation-source lantmateriet --buffer-m 500
curl -L -o presentation/data/ne_50m_admin_0_countries.geojson \
    https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson
```

## Figures and slides

```bash
python presentation/make_figures.py
cd presentation
npx @marp-team/marp-cli slides.md --html --allow-local-files -o build/slides.html
npx @marp-team/marp-cli slides.md --html --allow-local-files --pdf --pdf-notes -o build/slides.pdf
```

`--pptx` gives PowerPoint. Hillshades are cached in `data/cache/`; delete it after fetching new
elevation data.
