import pytest

from heights import MAX_M, MIN_M, STOREY_M, building_height, default_floors, parse_levels, parse_metres


@pytest.mark.parametrize("v, m", [("12", 12), ("12.5 m", 12.5), ("12,5", 12.5), ("40'", 12.192), ("10 ft", 3.048),
                                  (7, 7), ("tall", None), ("", None), (None, None), ("-3", None)])
def test_parse_metres(v, m):
    assert parse_metres(v) == (pytest.approx(m) if m else None)


def test_parse_levels():
    assert parse_levels("3") == 3 and parse_levels("2;3") == 2 and parse_levels("x") is None and parse_levels("0") is None


def test_precedence_osm_height_then_levels_then_overture():
    ov = {"height": 30, "num_floors": 9}
    assert building_height({"height": "15"}, ov, 100, "a") == (15, "osm:height")
    h, src = building_height({"building:levels": "4"}, ov, 100, "a")
    assert src == "osm:levels" and h == pytest.approx(4 * STOREY_M + 0.8)
    assert building_height({}, ov, 100, "a") == (30, "overture:height")
    h, src = building_height({}, {"num_floors": 2}, 100, "a")
    assert src == "overture:floors" and h == pytest.approx(2 * STOREY_M + 0.8)


def test_default_by_type_and_area_is_stable_and_plausible():
    h1, src = building_height({"building": "mosque"}, None, 400, "w1")
    assert src == "default" and 8 < h1 < 14
    assert building_height({"building": "mosque"}, None, 400, "w1")[0] == h1   # same building, same height
    for area in (20, 100, 400, 1500, 9000):
        for k in range(30):
            f = default_floors(None, area, f"b{k}")
            assert 1 <= f <= 6
    # the Overture class is used when OSM only says building=yes
    assert building_height({"building": "yes"}, {"class": "apartments"}, 200, "x")[0] > 3 * STOREY_M


def test_clamped():
    assert building_height({"height": "900"}, None, 100, "a")[0] == MAX_M
    assert building_height({"height": "0.5"}, None, 100, "a")[0] == MIN_M
