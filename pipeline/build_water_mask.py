"""Water mask for the viewer's water effect (web/src/components/Water.tsx), from the render's kinds pass.

    python build_water_mask.py   data/work/frames_kind + mosaic.json -> web/public/data/water_mask.png, .json

In the first draft the water is exactly where the render drew it (render.py kinds pass, KINDS["water"]). The
historic core has only still water (pools, the City Park lake, fountains), so there is no current: the flow is
zero and the viewer draws the soft glints of still water. When the map is painted, find the water in the painted
art instead (as isometric-luxembourg's build_water_mask.py does).

Output (half map resolution, 1 mask px = 2 map px):
  water_mask.png   an atlas of the mask's non-empty CHUNK x CHUNK blocks, each stored with a 1 px apron:
                     R     distance to the bank in steps of BANK_UNIT m (0 = dry). Soft mask = min(1, R / 16).
                     G, B  flow vector x, y as 128 + 127 * v (here 128: still water)
  water_mask.json  {"x0", "y0", "scale", "w", "h", "chunk", "cols", "chunks": [i0, j0, i1, j1, ...], "bank_per_unit"}
"""
from __future__ import annotations

import json

import cv2
import numpy as np
from PIL import Image

from build_scene import KINDS
from common import WEB_DATA, WORK

S = 2              # map px per mask px
CHUNK = 128
BANK_UNIT = 1 / 16  # metres per R step


def load_mask(work=WORK):
    m = json.loads((work / "mosaic.json").read_text())
    F = m["frame"]
    full = np.zeros((m["rows"] * F // S, m["cols"] * F // S), np.uint8)
    for r in range(m["rows"]):
        for c in range(m["cols"]):
            k = cv2.imread(str(work / "frames_kind" / f"{c}_{r}.png"), cv2.IMREAD_GRAYSCALE)
            w = (k == KINDS["water"]).astype(np.uint8) * 255
            full[r * F // S:(r + 1) * F // S, c * F // S:(c + 1) * F // S] = cv2.resize(w, (F // S, F // S), interpolation=cv2.INTER_AREA)
    return (full > 127).astype(np.uint8), m["mpp"]


def write_atlas(img, x0, y0, out):
    H, W = img.shape[:2]
    mask = img[..., 0]
    nx, ny = -(-W // CHUNK), -(-H // CHUNK)
    pad = np.zeros((ny * CHUNK + 2, nx * CHUNK + 2, 3), np.uint8)
    pad[..., 1:] = 128
    pad[1:H + 1, 1:W + 1] = img
    chunks = [(i, j) for j in range(ny) for i in range(nx) if mask[j * CHUNK:(j + 1) * CHUNK, i * CHUNK:(i + 1) * CHUNK].any()]
    cols = 16
    cell = CHUNK + 2
    rows = max(1, -(-len(chunks) // cols))
    atlas = np.zeros((rows * cell, cols * cell, 3), np.uint8)
    atlas[..., 1:] = 128
    for k, (i, j) in enumerate(chunks):
        c, r = k % cols, k // cols
        atlas[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = pad[j * CHUNK:j * CHUNK + cell, i * CHUNK:i * CHUNK + cell]
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(atlas).save(out / "water_mask.png", optimize=True)
    info = {"x0": x0, "y0": y0, "scale": S, "w": W, "h": H, "chunk": CHUNK, "cols": cols,
            "channels": "R: bank distance in bank_per_unit m (0 dry; mask = min(1, R / 16)); G, B: flow vector x, y as 128 + 127 * v",
            "bank_per_unit": BANK_UNIT, "chunks": [c for ij in chunks for c in ij]}
    (out / "water_mask.json").write_text(json.dumps(info, separators=(",", ":")) + "\n")
    print(f"water mask: {len(chunks)} blocks, atlas {atlas.shape[1]} x {atlas.shape[0]}")


def main():
    m, mpp = load_mask()
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    ys, xs = np.nonzero(m)
    if not len(xs):
        raise SystemExit("no water in the render")
    cx0, cy0, cx1, cy1 = max(0, xs.min() - 4), max(0, ys.min() - 4), xs.max() + 5, ys.max() + 5
    crop = np.ascontiguousarray(m[cy0:cy1, cx0:cx1])
    bank_m = cv2.distanceTransform(crop, cv2.DIST_L2, 5) * mpp * S
    enc = np.zeros(crop.shape + (3,), np.uint8)
    enc[..., 0] = np.where(crop > 0, np.clip(np.round(bank_m / BANK_UNIT), 1, 255), 0)
    enc[..., 1:] = 128
    write_atlas(enc, int(cx0 * S), int(cy0 * S), WEB_DATA)


if __name__ == "__main__":
    main()
