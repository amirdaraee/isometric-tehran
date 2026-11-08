"""The painter's inputs, cut from the map: the render, the kinds pass and the layout map.

The render is cut from data/work/frames (render.py) when it is there, otherwise from the committed full-resolution
tiles (web/public/tiles/map_files, the published render). Both are the same picture; the frames are sharper (JPEG
95 rather than WebP 78), so render the map first for real painting. The tiles are enough for the dry run and to
look at a block.

The layout map is a colour-coded second input the style model was trained with (Isometric Luxembourg's layout.py):
one flat colour per kind of surface, only where that surface is really visible. Tehran builds it from the render's
kinds pass (build_scene.KINDS); it needs data/work/frames_kind, so the layout needs a local render.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from common import WORK
from paint.config import BG, TILES_DIR, TILES_META

Image.MAX_IMAGE_PIXELS = None

# build_scene.KINDS -> layout colours (the Luxembourg scheme the style model learnt)
LAYOUT = {
    0: BG,                 # empty map edge
    1: (226, 218, 198),    # ground
    2: (150, 150, 155),    # road
    3: (60, 120, 210),     # water
    4: (110, 170, 90),     # green
    5: (232, 196, 140),    # wall
    6: (96, 108, 150),     # roof
    7: (110, 170, 90),     # tree (green, as in Luxembourg's layout)
    8: (205, 190, 160),    # square (pedestrian)
}


def mosaic(work: Path = WORK) -> dict | None:
    p = work / "mosaic.json"
    return json.loads(p.read_text()) if p.exists() else None


def canvas_size(work: Path = WORK) -> tuple[int, int]:
    """(W, H) of the map in pixels: the committed tiles' size (what everyone paints against)."""
    m = json.loads(TILES_META.read_text())
    return int(m["width"]), int(m["height"])


def _cut_frames(folder: Path, ext: str, mode: str, fill, x0: int, y0: int, w: int, h: int, F: int) -> Image.Image:
    im = Image.new(mode, (w, h), fill)
    for r in range(max(y0, 0) // F, max(y0 + h, 0) // F + 1):
        for c in range(max(x0, 0) // F, max(x0 + w, 0) // F + 1):
            p = folder / f"{c}_{r}.{ext}"
            if p.exists():
                with Image.open(p) as t:
                    im.paste(t.convert(mode), (c * F - x0, r * F - y0))
    return im


def _cut_tiles(x0: int, y0: int, w: int, h: int, tiles: Path = TILES_DIR, tile: int = 512) -> Image.Image:
    W, H = canvas_size()
    level = math.ceil(math.log2(max(W, H)))
    d = tiles / str(level)
    im = Image.new("RGB", (w, h), BG)
    for ty in range(max(y0, 0) // tile, min(y0 + h, H) // tile + 1):
        for tx in range(max(x0, 0) // tile, min(x0 + w, W) // tile + 1):
            p = d / f"{tx}_{ty}.webp"
            if p.exists():
                with Image.open(p) as t:
                    im.paste(t.convert("RGB"), (tx * tile - x0, ty * tile - y0))
    return im


def has_render(work: Path = WORK) -> bool:
    return mosaic(work) is not None and (work / "frames").is_dir()


def render(x0: int, y0: int, w: int, h: int, work: Path = WORK) -> Image.Image:
    """The render in map pixels [x0, x0 + w) x [y0, y0 + h); off-map parts are the background colour."""
    m = mosaic(work)
    if m is not None and (work / "frames").is_dir():
        return _cut_frames(work / "frames", "jpg", "RGB", BG, x0, y0, w, h, int(m["frame"]))
    return _cut_tiles(x0, y0, w, h)


def kinds(x0: int, y0: int, w: int, h: int, work: Path = WORK) -> Image.Image | None:
    """The kinds pass (one byte per pixel, build_scene.KINDS), or None without a local render."""
    m = mosaic(work)
    if m is None or not (work / "frames_kind").is_dir():
        return None
    return _cut_frames(work / "frames_kind", "png", "L", 0, x0, y0, w, h, int(m["frame"]))


def layout_from_kinds(kind: np.ndarray) -> np.ndarray:
    """Layout map (H, W, 3) from a kinds array (H, W)."""
    lut = np.zeros((256, 3), np.uint8)
    lut[:] = BG
    for k, c in LAYOUT.items():
        lut[k] = c
    return lut[kind]


def check_render_matches(work: Path = WORK) -> str | None:
    """A warning if the local render is not the size of the committed map (blocks would not line up)."""
    m = mosaic(work)
    if m is None:
        return None
    W, H = canvas_size(work)
    if (m["cols"] * m["frame"], m["rows"] * m["frame"]) != (W, H):
        return (f"the local render is {m['cols'] * m['frame']} x {m['rows'] * m['frame']} px but the committed map "
                f"is {W} x {H}: re-fetch and re-render, or blocks will not line up")
    return None
