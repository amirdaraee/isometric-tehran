"""Building heights: from the data where it has them, else a sensible default by building type and size.

Order of precedence, first one found wins:
  1. OSM `height` (metres; "12 m", "12.5" and "40'" are understood);
  2. OSM `building:levels` (+ `roof:levels`) times a storey height;
  3. Overture `height`;
  4. Overture `num_floors` times a storey height;
  5. a default by type (mosque, school, apartments...) and footprint area, with a small deterministic jitter per
     building so a block of untagged houses does not come out as one flat slab.
The result is clamped to a plausible range for the historic core.
"""
from __future__ import annotations

import hashlib
import re

STOREY_M = 3.2           # one floor, slab to slab
GROUND_EXTRA_M = 0.8     # the ground floor and parapet are a little taller
MIN_M, MAX_M = 2.5, 120.0

# Default floors by OSM/Overture building type (the Overture `class` uses the same words).
TYPE_FLOORS: dict[str, float] = {
    "house": 2, "residential": 3, "apartments": 4, "detached": 2, "terrace": 2,
    "commercial": 3, "retail": 2, "office": 5, "hotel": 5, "industrial": 2, "warehouse": 1.5,
    "school": 2, "university": 3, "college": 3, "hospital": 4, "government": 3, "public": 2,
    "mosque": 3, "church": 3, "shrine": 3, "religious": 2.5, "train_station": 2,
    "roof": 1, "shed": 1, "garage": 1, "kiosk": 1, "parking": 3, "mall": 4, "dormitory": 4,
}


def parse_metres(v: str | float | int | None) -> float | None:
    """'12', '12.5 m', '12,5', '40'' (feet) -> metres; None if it can't be read."""
    if v is None:
        return None
    if isinstance(v, int | float):
        return float(v) if v > 0 else None
    s = str(v).strip().lower().replace(",", ".")
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*(m|meters?|metres?|ft|feet|')?$", s)
    if not m:
        return None
    x = float(m.group(1))
    if m.group(2) in ("ft", "feet", "'"):
        x *= 0.3048
    return x if x > 0 else None


def parse_levels(v: str | float | int | None) -> float | None:
    if v is None:
        return None
    try:
        x = float(str(v).replace(",", ".").split(";")[0])
    except ValueError:
        return None
    return x if 0 < x < 100 else None


def jitter(key: str) -> float:
    """A stable number in [0, 1) for a building id."""
    return int(hashlib.blake2b(key.encode(), digest_size=4).hexdigest(), 16) / 2**32


def default_floors(kind: str | None, area_m2: float, key: str) -> float:
    """Floors for a building without height data: by type, else by footprint area (the historic core is mostly
    low: small plots 2-3 floors, larger ones up to 5; very large footprints are halls, bazaar roofs or yards)."""
    j = jitter(key)
    if kind and kind in TYPE_FLOORS:
        return TYPE_FLOORS[kind] + (j - 0.5)
    if area_m2 < 40:
        return 1 + round(j)
    if area_m2 < 150:
        return 2 + round(j * 1.4)
    if area_m2 < 600:
        return 3 + round(j * 2)
    if area_m2 < 2500:
        return 3 + round(j * 3)
    return 2 + round(j)


def building_height(osm_tags: dict | None, overture: dict | None, area_m2: float, key: str) -> tuple[float, str]:
    """(height in metres, where it came from: "osm:height", "osm:levels", "overture:height", "overture:floors" or
    "default")."""
    t = osm_tags or {}
    o = overture or {}
    h = parse_metres(t.get("height"))
    src = "osm:height"
    if h is None:
        lv = parse_levels(t.get("building:levels"))
        if lv is not None:
            h = lv * STOREY_M + GROUND_EXTRA_M + (parse_levels(t.get("roof:levels")) or 0) * STOREY_M * 0.6
            src = "osm:levels"
    if h is None and o.get("height"):
        h, src = parse_metres(o["height"]), "overture:height"
    if h is None and o.get("num_floors"):
        lv = parse_levels(o["num_floors"])
        if lv is not None:
            h, src = lv * STOREY_M + GROUND_EXTRA_M, "overture:floors"
    if h is None:
        kind = t.get("building") if t.get("building") not in (None, "yes") else o.get("class")
        h, src = default_floors(kind, area_m2, key) * STOREY_M + GROUND_EXTRA_M, "default"
    return max(MIN_M, min(MAX_M, h)), src
