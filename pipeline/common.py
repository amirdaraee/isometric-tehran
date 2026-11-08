"""Shared paths, the city config (../city.json), the metric projection and the isometric camera.

Everything that depends on the city lives in city.json at the repository root: its bounding box, the metric CRS
used for geometry (a UTM zone), the camera and the map resolution. Change the city there, not in code.
"""
from __future__ import annotations

import json
import math
from functools import cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
WORK = ROOT / "data" / "work"
WEB = ROOT / "web"
WEB_DATA = WEB / "public" / "data"
CITY_FILE = ROOT / "city.json"


@cache
def city(path: str | None = None) -> dict:
    """The city config (city.json)."""
    return json.loads(Path(path or CITY_FILE).read_text(encoding="utf-8"))


def bbox(cfg: dict | None = None) -> tuple[float, float, float, float]:
    """(west, south, east, north) in degrees."""
    b = (cfg or city())["bbox"]
    w, s, e, n = float(b["west"]), float(b["south"]), float(b["east"]), float(b["north"])
    if not (w < e and s < n and -180 <= w <= 180 and -90 <= s <= 90):
        raise ValueError(f"bad bbox: {b}")
    return w, s, e, n


class Projection:
    """Degrees <-> local metres (east, north) in the city's metric CRS, with the origin at the bbox centre, so
    float32 vertices stay precise."""

    def __init__(self, cfg: dict | None = None):
        from pyproj import Transformer

        cfg = cfg or city()
        self.crs = cfg["crs"]
        self._fwd = Transformer.from_crs(4326, self.crs, always_xy=True)
        self._inv = Transformer.from_crs(self.crs, 4326, always_xy=True)
        w, s, e, n = bbox(cfg)
        self.origin = self._fwd.transform((w + e) / 2, (s + n) / 2)

    def to_local(self, lon, lat):
        e, n = self._fwd.transform(lon, lat)
        return np.asarray(e) - self.origin[0], np.asarray(n) - self.origin[1]

    def to_lonlat(self, x, y):
        return self._inv.transform(np.asarray(x) + self.origin[0], np.asarray(y) + self.origin[1])

    def local_bounds(self, cfg: dict | None = None) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1): the bbox's extent in local metres (the projected corners' envelope)."""
        w, s, e, n = bbox(cfg)
        xs, ys = self.to_local(np.array([w, e, w, e]), np.array([s, s, n, n]))
        return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


class Camera:
    """Orthographic 2:1 dimetric camera: it looks along `lookAzimuthDeg` (clockwise from north) and down by
    `elevationDeg`. Screen x is to the right, screen y is up, both in metres."""

    def __init__(self, cfg: dict | None = None):
        cam = (cfg or city())["camera"]
        a = math.radians(float(cam["lookAzimuthDeg"]))
        self.elev = math.radians(float(cam["elevationDeg"]))
        self.fwd = np.array([math.sin(a), math.cos(a)])      # into the screen, on the ground (east, north)
        self.right = np.array([math.cos(a), -math.sin(a)])   # screen right, on the ground
        self.se, self.ce = math.sin(self.elev), math.cos(self.elev)

    def to_screen(self, x, y, z):
        """Local metres (east, north, up) to screen metres (right, up)."""
        x, y, z = np.asarray(x, float), np.asarray(y, float), np.asarray(z, float)
        sx = x * self.right[0] + y * self.right[1]
        f = x * self.fwd[0] + y * self.fwd[1]
        return sx, f * self.se + z * self.ce

    def depth(self, x, y, z):
        """Distance along the view ray: smaller is nearer the camera."""
        x, y, z = np.asarray(x, float), np.asarray(y, float), np.asarray(z, float)
        f = x * self.fwd[0] + y * self.fwd[1]
        return f * self.ce - z * self.se

    def view_matrix(self) -> np.ndarray:
        """3x3 matrix taking (x, y, z) to (screen x, screen y, depth), for the GPU."""
        r, f = self.right, self.fwd
        return np.array([
            [r[0], r[1], 0.0],
            [f[0] * self.se, f[1] * self.se, self.ce],
            [f[0] * self.ce, f[1] * self.ce, -self.se],
        ])


def slug(s: str) -> str:
    """URL-safe ASCII slug of an English title."""
    import re
    import unicodedata

    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def bilingual(en: str | None, fa: str | None, sep: str = " · ") -> str:
    """A place label in both languages: "Golestan Palace · کاخ گلستان". One of them alone if the other is missing
    or the same."""
    en, fa = (en or "").strip(), (fa or "").strip()
    if en and fa and en != fa:
        return f"{en}{sep}{fa}"
    return en or fa
