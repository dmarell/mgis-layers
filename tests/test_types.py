import json
import pytest

from gis_layers.types import AOI, BBox, LatLon


def test_bbox_rejects_degenerate():
    with pytest.raises(ValueError):
        BBox(2.0, 0.0, 1.0, 1.0)


def test_bbox_expand_meters_grows_in_both_axes():
    bbox = BBox(18.0, 59.0, 18.1, 59.1)
    grown = bbox.expand_meters(1000.0)
    assert grown.min_lon < bbox.min_lon
    assert grown.max_lon > bbox.max_lon
    assert grown.min_lat < bbox.min_lat
    assert grown.max_lat > bbox.max_lat


def test_bbox_contains_handles_inside_and_outside():
    bbox = BBox(18.0, 59.0, 18.1, 59.1)
    assert bbox.contains(LatLon(59.05, 18.05))
    assert not bbox.contains(LatLon(59.5, 18.05))


def test_aoi_from_geojson_extracts_name_and_bbox(tmp_path):
    path = tmp_path / "aoi.geojson"
    path.write_text(json.dumps({
        "type": "Feature",
        "properties": {"name": "test"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [18.0, 59.0], [18.1, 59.0], [18.1, 59.1], [18.0, 59.1], [18.0, 59.0],
        ]]},
    }))
    aois = AOI.from_geojson(path)
    assert len(aois) == 1
    assert aois[0].name == "test"
    assert aois[0].bbox.as_tuple() == (18.0, 59.0, 18.1, 59.1)


def test_latlon_parse():
    assert LatLon.parse("59.5, 18.0") == LatLon(59.5, 18.0)
    with pytest.raises(ValueError):
        LatLon.parse("nope")
