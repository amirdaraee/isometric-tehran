"""Pins, landmark cards and street lines for the viewer, in map pixels, with English and Persian names.

    python build_places.py
      -> web/public/data/places.json     pins and search entries
      -> web/public/data/landmarks.json  landmark cards (Wikidata + Wikipedia, with sources)
      -> web/public/data/lines.json      streets and water outlines for the Streets layer

Needs data/work/mosaic.json (render.py) and data/work/scene.npz (build_scene.py) for the projection, and the raw
downloads (fetch_data.py). The landmarks are the ones listed in pipeline/landmarks.json.

Place (places.json):  {"n": label, "en", "fa", "c": category, "x", "y", "f": 1 featured, "q": 1 quarter label,
                       "s": landmark slug}. The label shows both names: "Golestan Palace · کاخ گلستان".
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

import osm
from build_scene import ROADS, Terrain
from common import RAW, WEB_DATA, WORK, Camera, Projection, bilingual, city

LANDMARKS = Path(__file__).with_name("landmarks.json")

# OSM tag -> category, in order; only these named things become pins.
CATEGORIES = [
    (("historic", None), "history"), (("tourism", "museum"), "museum"), (("tourism", "attraction"), "landmark"),
    (("amenity", "place_of_worship"), "religion"), (("leisure", "park"), "park"), (("place", "square"), "square"),
    (("railway", "station"), "transport"), (("amenity", "theatre"), "culture"), (("amenity", "cinema"), "culture"),
    (("amenity", "arts_centre"), "culture"), (("amenity", "library"), "culture"), (("amenity", "marketplace"), "bazaar"),
    (("amenity", "university"), "education"), (("amenity", "college"), "education"), (("amenity", "townhall"), "government"),
    (("amenity", "courthouse"), "government"), (("office", "government"), "government"), (("tourism", "hotel"), "hotel"),
    (("amenity", "hospital"), "health"),
]
QUARTERS = ("neighbourhood", "quarter", "suburb")
KIND_CATEGORY = {"palace": "landmark", "bazaar": "bazaar", "mosque": "religion", "church": "religion", "square": "square",
                 "gate": "history", "complex": "history", "government": "government", "museum": "museum", "street": "street",
                 "school": "education", "park": "park", "site": "history", "tower": "history"}


class MapProjection:
    """Local metres (+ ground height) -> map pixels of the rendered mosaic."""

    def __init__(self, mosaic: dict, cam: Camera, terrain: Terrain):
        self.m, self.cam, self.t = mosaic, cam, terrain

    def px(self, x, y, z=None):
        z = self.t.at(x, y) if z is None else z
        sx, sy = self.cam.to_screen(x, y, z)
        return (np.asarray(sx) - self.m["sx0"]) / self.m["mpp"], (self.m["sy1"] - np.asarray(sy)) / self.m["mpp"]


def sentences(text: str | None, max_chars: int = 420) -> str | None:
    """The first sentences of a Wikipedia extract, up to max_chars, cut at a sentence end."""
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    cut = [m.end() for m in re.finditer(r"[.!?؟。](?=\s)", text[: max_chars + 1])]
    return text[: cut[-1]] if cut else text[:max_chars].rsplit(" ", 1)[0] + "…"


def main():
    cfg = city()
    proj, cam = Projection(cfg), Camera(cfg)
    mosaic = json.loads((WORK / "mosaic.json").read_text())
    mp = MapProjection(mosaic, cam, Terrain.load())
    W, H = mosaic["cols"] * mosaic["frame"], mosaic["rows"] * mosaic["frame"]
    data = osm.load()
    feats = list(osm.features(data, proj))
    x0, y0, x1, y1 = proj.local_bounds(cfg)

    def inside(x, y):
        return x0 <= x <= x1 and y0 <= y <= y1

    # landmarks
    items = {i["qid"]: i for i in json.loads((RAW / "wikidata" / "items.json").read_text())}
    summ = json.loads((RAW / "wikidata" / "summaries.json").read_text())
    landmarks, places = [], []
    for lm in json.loads(LANDMARKS.read_text()):
        it = items.get(lm["qid"])
        if not it:
            print("not in the bbox's Wikidata items:", lm["qid"])
            continue
        x, y = proj.to_local(it["lon"], it["lat"])
        px, py = mp.px(float(x), float(y))
        en, fa = lm.get("en") or it["en"], it["fa"]
        wiki = {}
        for lang in ("en", "fa"):
            s = summ.get(f"{lm['qid']}:{lang}")
            if s and s.get("extract"):
                wiki[lang] = {"title": s["title"], "url": s["url"], "extract": sentences(s["extract"])}
        sources = [{"title": "Wikidata", "url": f"https://www.wikidata.org/wiki/{lm['qid']}", "licence": "CC0 1.0"}]
        sources += [{"title": f"Wikipedia ({lang})", "url": w["url"], "licence": "CC BY-SA 4.0"} for lang, w in wiki.items()]
        card = {"slug": lm["slug"], "qid": lm["qid"], "kind": lm["kind"], "category": KIND_CATEGORY.get(lm["kind"], "landmark"),
                "name": {"en": en, "fa": fa}, "x": round(float(px)), "y": round(float(py)),
                "lon": it["lon"], "lat": it["lat"],
                "description": {"en": it.get("description_en"), "fa": it.get("description_fa")},
                "wikipedia": wiki, "note": lm.get("note"), "sources": sources}
        landmarks.append(card)
        places.append({"n": bilingual(en, fa), "en": en, "fa": fa, "c": card["category"], "x": card["x"], "y": card["y"],
                       "s": lm["slug"], **({"f": 1} if lm.get("featured") else {})})
    taken = [(p["x"], p["y"]) for p in places]

    # other named places from OSM
    seen = set()
    for f in feats:
        t = f.tags
        en, fa = osm.names(t)
        if not (en or fa):
            continue
        c = f.geom.representative_point() if f.geom.geom_type != "Point" else f.geom
        if not inside(c.x, c.y):
            continue
        px, py = (round(float(v)) for v in mp.px(c.x, c.y))
        if t.get("place") in QUARTERS:
            places.append({"n": bilingual(en, fa), "en": en, "fa": fa, "c": "quarter", "x": px, "y": py, "q": 1})
            continue
        cat = next((cat for (k, v), cat in CATEGORIES if k in t and (v is None or t[k] == v)), None)
        if not cat:
            continue
        label = bilingual(en, fa)
        if label in seen or any(abs(px - a) < 60 and abs(py - b) < 60 for a, b in taken):
            continue  # a landmark or the same place already has this spot
        seen.add(label)
        places.append({"n": label, "en": en, "fa": fa, "c": cat, "x": px, "y": py})
    for p in places:  # drop empty fields
        for k in ("en", "fa"):
            if not p.get(k):
                p.pop(k, None)
    places = [p for p in places if 0 <= p["x"] < W and 0 <= p["y"] < H]

    # streets and water for the Streets layer
    def line(g):
        xy = np.asarray(g.coords)
        px, py = mp.px(xy[:, 0], xy[:, 1])
        return [round(float(v), 1) for pair in zip(px, py, strict=True) for v in pair]

    from shapely import geometry as sg

    clip = sg.box(x0, y0, x1, y1)
    roads, water, rid = [], [], 0
    for f in feats:
        t = f.tags
        if "highway" in t and t["highway"] in ROADS and f.geom.geom_type == "LineString":
            g = f.geom.intersection(clip)
            for part in getattr(g, "geoms", [g]):
                if part.geom_type != "LineString" or part.is_empty:
                    continue
                en, fa = osm.names(t)
                r = {"id": rid, "k": t["highway"], "p": line(part)}
                if en:
                    r["n"] = en
                if fa:
                    r["nf"] = fa
                if t.get("tunnel") in ("yes", "building_passage"):
                    r["t"] = True
                roads.append(r)
                rid += 1
        elif (t.get("natural") == "water" or "water" in t) and f.geom.geom_type == "Polygon":
            g = f.geom.intersection(clip)
            if g.geom_type == "Polygon" and not g.is_empty:
                water.append({"id": rid, "p": line(g.exterior)})
                rid += 1

    # streets in the search: each named street once, at the middle of its longest piece
    best: dict[str, dict] = {}
    for r in roads:
        key = r.get("n") or r.get("nf")
        if not key or r["k"] in ("footway", "path", "steps", "service"):
            continue
        pts = np.array(r["p"]).reshape(-1, 2)
        length = float(np.hypot(*np.diff(pts, axis=0).T).sum())
        if key not in best or length > best[key]["len"]:
            mid = pts[len(pts) // 2]
            best[key] = {"len": length, "en": r.get("n"), "fa": r.get("nf"), "x": round(float(mid[0])), "y": round(float(mid[1]))}
    have = {p["n"] for p in places}
    for b in best.values():
        label = bilingual(b["en"], b["fa"])
        if label not in have:
            places.append({k: v for k, v in {"n": label, "en": b["en"], "fa": b["fa"], "c": "street", "x": b["x"], "y": b["y"]}.items() if v})

    WEB_DATA.mkdir(parents=True, exist_ok=True)
    dump = lambda name, obj: (WEB_DATA / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    dump("places.json", places)
    (WEB_DATA / "landmarks.json").write_text(json.dumps(landmarks, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    dump("lines.json", {"roads": roads, "rail": [], "waterways": [], "water": water})
    print(f"places {len(places)} ({sum(1 for p in places if p.get('f'))} featured, {sum(1 for p in places if p.get('q'))} quarters); "
          f"landmarks {len(landmarks)}; roads {len(roads)}; water {len(water)}")


if __name__ == "__main__":
    main()
