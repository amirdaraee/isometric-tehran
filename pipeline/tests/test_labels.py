from common import Projection, bilingual, slug
from osm import features, names


def test_bilingual_label():
    assert bilingual("Golestan Palace", "کاخ گلستان") == "Golestan Palace · کاخ گلستان"
    assert bilingual(None, "بازار") == "بازار"
    assert bilingual("Same", "Same") == "Same"
    assert bilingual(" ", None) == ""


def test_names_from_osm_tags():
    assert names({"name": "کاخ گلستان", "name:en": "Golestan Palace"}) == ("Golestan Palace", "کاخ گلستان")
    assert names({"name": "میدان ارک"}) == (None, "میدان ارک")
    assert names({"name": "Ferdowsi", "name:fa": "فردوسی"}) == ("Ferdowsi", "فردوسی")


def test_slug():
    assert slug("Toopkhaneh (Imam Khomeini) Square") == "toopkhaneh-imam-khomeini-square"
    assert slug("Shams-ol-Emāreh") == "shams-ol-emareh"


def test_overpass_parsing(cfg, overpass):
    fs = {f.osm: f for f in features(overpass, Projection(cfg))}
    assert fs["node/1"].geom.geom_type == "Point"
    b = fs["way/10"].geom
    assert b.geom_type == "Polygon" and 300 < b.area < 500     # about 18 m x 22 m
    assert fs["way/11"].geom.geom_type == "LineString"          # roads stay lines
    park = fs["relation/20"].geom
    assert park.geom_type == "Polygon" and park.area > 30000


def test_wikipedia_extract_is_cut_at_a_sentence():
    from build_places import sentences

    text = "First sentence. " * 40
    out = sentences(text, 100)
    assert out.endswith(".") and len(out) <= 100
    assert sentences("Short.") == "Short."
    assert sentences(None) is None
