"""Read the Overpass download (data/raw/osm/overpass.json) into shapely geometries in local metres."""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from shapely import geometry as sg
from shapely.ops import linemerge, polygonize, unary_union

from common import RAW, Projection

AREA_KEYS = ("building", "landuse", "leisure", "natural", "amenity", "water", "area:highway", "man_made", "tourism",
             "historic", "place")


@dataclass
class Feature:
    osm: str                 # "way/123"
    tags: dict
    geom: sg.base.BaseGeometry
    lonlat: tuple[float, float] = field(default=(0.0, 0.0))   # a representative point


def load(path: Path | None = None) -> dict:
    return json.loads((path or RAW / "osm" / "overpass.json").read_text(encoding="utf-8"))


def _line(proj: Projection, pts: list[dict]) -> np.ndarray:
    lon = np.array([p["lon"] for p in pts])
    lat = np.array([p["lat"] for p in pts])
    x, y = proj.to_local(lon, lat)
    return np.column_stack((x, y))


def is_area(tags: dict, closed: bool) -> bool:
    if not closed:
        return False
    if tags.get("area") == "no":
        return False
    if tags.get("area") == "yes":
        return True
    if "highway" in tags and "area:highway" not in tags:
        return tags.get("highway") in ("pedestrian",) and tags.get("area") == "yes"
    if tags.get("natural") in ("tree_row", "coastline", "cliff"):
        return False
    if "barrier" in tags or "waterway" in tags and tags.get("waterway") not in ("riverbank", "dock"):
        return False
    return any(k in tags for k in AREA_KEYS)


def features(data: dict, proj: Projection) -> Iterator[Feature]:
    """Every element as a Feature: nodes as points, ways as lines or polygons, multipolygon relations as polygons."""
    for e in data["elements"]:
        tags = e.get("tags") or {}
        t = e["type"]
        if t == "node":
            x, y = proj.to_local(e["lon"], e["lat"])
            yield Feature(f"node/{e['id']}", tags, sg.Point(float(x), float(y)), (e["lon"], e["lat"]))
        elif t == "way" and "geometry" in e and len(e["geometry"]) >= 2:
            xy = _line(proj, e["geometry"])
            closed = len(xy) >= 4 and np.allclose(xy[0], xy[-1])
            g = sg.Polygon(xy) if is_area(tags, closed) else sg.LineString(xy)
            if not g.is_valid:
                g = g.buffer(0)
            c = e["geometry"][len(e["geometry"]) // 2]
            yield Feature(f"way/{e['id']}", tags, g, (c["lon"], c["lat"]))
        elif t == "relation" and tags.get("type") in ("multipolygon", "building"):
            outers, inners = [], []
            for m in e.get("members", []):
                if m.get("type") != "way" or "geometry" not in m or len(m["geometry"]) < 2:
                    continue
                (inners if m.get("role") == "inner" else outers).append(sg.LineString(_line(proj, m["geometry"])))
            if not outers:
                continue
            outer = unary_union(list(polygonize(linemerge(outers))))
            if inners:
                outer = outer.difference(unary_union(list(polygonize(linemerge(inners)))))
            if outer.is_empty:
                continue
            c = outer.representative_point()
            lon, lat = proj.to_lonlat(c.x, c.y)
            yield Feature(f"relation/{e['id']}", tags, outer, (float(lon), float(lat)))


def names(tags: dict) -> tuple[str | None, str | None]:
    """(English, Persian) names of an element. OSM `name` in Tehran is usually Persian."""
    en = tags.get("name:en") or tags.get("int_name")
    fa = tags.get("name:fa")
    name = tags.get("name")
    if name and not fa and any("؀" <= ch <= "ۿ" for ch in name):
        fa = name
    if name and not en and not any("؀" <= ch <= "ۿ" for ch in name):
        en = name
    return en, fa
