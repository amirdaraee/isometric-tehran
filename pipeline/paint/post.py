"""Fixes applied to a painted canvas before it is submitted (paint.py run does this; also runnable by hand).

  despeckle   the light model sometimes leaves strong magenta specks on lawns in a night or golden version: those
              pixels (grown by 2 px) are filled from their surroundings. Magenta never belongs on this map.
  water       painted water gets the map's pool colour (paint.json water.lab, CIELAB as OpenCV stores it), keeping
              its painted ripples. Only where the kinds pass says water is visible and the paint is already bluish.
              Skipped while water.lab is null (set it from the first painted block whose water looks right).

  python -m paint.post despeckle <in.png> <out.png>
  python -m paint.post water <canvas.png> <kind.png> <out.png>
"""
from __future__ import annotations

if __package__ in (None, ""):
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sys

import cv2
import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
WATER_KIND = 3  # build_scene.KINDS["water"]


def despeckle(rgb: np.ndarray) -> tuple[np.ndarray, int]:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = (hsv[..., i].astype(int) for i in range(3))
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    magenta = (h >= 140) & (h <= 170) & (s >= 90) & (v >= 70) & (r > g + 40) & (b > g + 40)
    n = int(magenta.sum())
    if n == 0:
        return rgb, 0
    m = cv2.dilate(magenta.astype(np.uint8) * 255, np.ones((5, 5), np.uint8))
    fixed = cv2.inpaint(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), m, 5, cv2.INPAINT_TELEA)
    return cv2.cvtColor(fixed, cv2.COLOR_BGR2RGB), n


def water_tone(rgb: np.ndarray, water: np.ndarray, target_lab) -> tuple[np.ndarray, int]:
    """Move painted water (mask, same size) to the target colour; returns the image and pixels changed."""
    a = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    m = water & (a[..., 2] < 129) & (a[..., 0] > 25)  # bluish, not dark: trees over the edge stay green
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
    if m.sum() < 50:
        return rgb, 0
    t = np.asarray(target_lab, np.float32)
    cur = a[m].mean(axis=0)
    new = a.copy()
    new[..., 0] = t[0] + (a[..., 0] - cur[0]) * 0.8
    new[..., 1] = t[1] + (a[..., 1] - cur[1]) * 0.5
    new[..., 2] = t[2] + (a[..., 2] - cur[2]) * 0.5
    alpha = cv2.GaussianBlur(m.astype(np.float32), (0, 0), 2.0)[..., None]
    mixed = a * (1 - alpha) + new * alpha
    return cv2.cvtColor(np.clip(mixed, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB), int(m.sum())


def water_mask(kind_png, size: tuple[int, int]) -> np.ndarray:
    k = Image.open(kind_png).convert("L").resize(size, Image.NEAREST)
    return np.asarray(k) == WATER_KIND


def main(argv: list[str]) -> None:
    from paint import config

    cmd = argv[0]
    if cmd == "despeckle":
        out, n = despeckle(np.asarray(Image.open(argv[1]).convert("RGB")))
        Image.fromarray(out).save(argv[2])
        print(f"magenta px filled: {n}")
    elif cmd == "water":
        lab = config.settings().get("water", {}).get("lab")
        im = np.asarray(Image.open(argv[1]).convert("RGB"))
        if lab is None:
            Image.fromarray(im).save(argv[3])
            print("water.lab is not set in paint.json: water left as painted")
            return
        out, n = water_tone(im, water_mask(argv[2], (im.shape[1], im.shape[0])), lab)
        Image.fromarray(out).save(argv[3])
        print(f"water px toned: {n}")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
