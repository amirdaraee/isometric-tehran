"""A sea of cloud seen from above, filling everything outside the map data, with the city rising out of it.

  build_clouds.py texture   -> web/public/clouds/sea.webp: one seamless, tileable cloud-top texture (2048 x 2048)
  build_clouds.py mask      -> web/public/clouds/mask.png and web/public/data/clouds.json: where the data ends
  build_clouds.py           -> both

Texture (procedural, no outside service): a height field of heaped cumulus tops, built on a torus so it repeats
without seams. Each octave is a field of domes, one per cell of a periodic Voronoi diagram (cKDTree with a box
size), sunk into soft creases where neighbouring cells meet; smaller octaves sit on the larger ones like the
heads of a cauliflower, everything is domain-warped by smooth periodic noise, and a broad swell lifts and lowers
whole fields of billows. The height is then lit like the map, by warm daylight from the upper left: soft wrapped
diffuse light, shadows cast across the tops (marched along the light), occlusion in the valleys, and cool
blue-grey in the shade and in the low, hazy hollows. No outlines and no flat steps.
  RGB: the day colour.  A: the height (0 low .. 1 high), which the viewer uses to lay one layer over another, to
  cast the upper layer's shadow on the lower and to make the edge over the map follow the billows. Night colours
  are worked out in the viewer from the day brightness.

Mask: the outline of the data, found in the painted tiles themselves (web/public/tiles/map_files/12, where the
empty background is #101828). Only the largest connected piece counts, so stray stubs of bridges outside the data
stay under the clouds. Stored as a signed distance to that outline, 1 px = 32 map px, with a wide border so the
texture clamps to "far outside":
  value = 128 + distance / RANGE * 127, distance in map px, positive outside the data, negative inside.

clouds.json: {"mask": {"file", "x0", "y0", "scale", "w", "h", "range"}, "texture": {"file", "size"}}
(x0, y0: map px of the mask's top-left corner; scale: map px per mask px.)
The viewer is web/src/components/Clouds.tsx.
"""
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image

from common import WEB

OUT_DIR = WEB / "public" / "clouds"
OUT_JSON = WEB / "public" / "data" / "clouds.json"
TILES = WEB / "public" / "tiles"
N = 2048                     # texture size
SEED = 1867

# --- texture -----------------------------------------------------------------------------------------------------


def periodic_noise(rng, n, freq, power=2.0):
    """Smooth tileable noise: white noise filtered in the frequency domain, about `freq` cycles across the tile."""
    k = np.fft.fftfreq(n) * n
    kk = np.hypot(*np.meshgrid(k, k))
    spec = np.exp(-(kk / freq) ** power)
    spec[0, 0] = 0
    f = np.real(np.fft.ifft2(np.fft.fft2(rng.standard_normal((n, n))) * spec))
    return (f / f.std()).astype(np.float32)


def blur_wrap(a, sigma):
    """Gaussian blur on the torus, so the texture stays seamless."""
    pad = int(sigma * 3) + 1
    p = np.pad(a, pad, mode="wrap")
    return cv2.GaussianBlur(p, (0, 0), sigma)[pad:-pad, pad:-pad]


def stamp(h, rng, count, r_range, sink, soft, squash=1.0):
    """Heap `count` balls of cloud on the surface h (on the torus): each ball sits on the surface where it lands,
    sunk by `sink` of its radius, and the surface becomes the higher of the two. Returns the new surface."""
    out = h.copy()
    for _ in range(count):
        r = rng.uniform(*r_range)
        cx, cy = rng.uniform(0, N), rng.uniform(0, N)
        ix, iy = int(cx), int(cy)
        z = h[iy % N, ix % N] - sink * r
        R = int(np.ceil(r)) + 1
        xs = np.arange(ix - R, ix + R + 1)
        ys = np.arange(iy - R, iy + R + 1)
        dx = (xs + 0.5 - cx)[None, :]
        dy = (ys + 0.5 - cy)[:, None]
        cap = r * r - dx * dx - dy * dy
        ball = np.where(cap > 0, z + np.sqrt(np.maximum(cap, 0)) * squash, -1e9)
        win = np.ix_(ys % N, xs % N)
        # smooth maximum, so a ball melts into what it sits on instead of meeting it in a hard crease
        a, k = out[win], soft * r
        hh = np.clip(0.5 + 0.5 * (ball - a) / k, 0, 1)
        out[win] = a + (ball - a) * hh + k * hh * (1 - hh)
    return out


def height_field(rng):
    """Heaped cumulus tops, in texture px: a gently swelling floor, big billows on it, heads on the billows,
    curls on the heads. Balls are united by taking the higher surface, which leaves soft creases between them."""
    swell = periodic_noise(rng, N, 3.0) * 95 + periodic_noise(rng, N, 9.0) * 22
    h = swell.astype(np.float32)
    # (count, radius range px, sink and softness as fractions of the radius)
    layers = [
        (110, (150, 260), 0.55, 0.30),
        (480, (60, 120), 0.58, 0.25),
        (1700, (24, 50), 0.68, 0.30),
        (4000, (10, 20), 0.78, 0.45),
    ]
    for count, rr, sink, soft in layers:
        h = stamp(h, rng, count, rr, sink, soft)
        h = blur_wrap(h, rr[0] * 0.08)             # soften, as a painter would
    # a faint grain on the surface
    h += periodic_noise(rng, N, 90) * 0.5
    return h


def shade(hp):
    """Day colours for a height field (texture px): warm light from the upper left, cool blue-grey shade."""
    h = (hp - hp.min()) / (hp.max() - hp.min())
    gx = (np.roll(hp, -1, 1) - np.roll(hp, 1, 1)) / 2
    gy = (np.roll(hp, -1, 0) - np.roll(hp, 1, 0)) / 2
    gx, gy = blur_wrap(gx, 1.2), blur_wrap(gy, 1.2)
    nrm = np.dstack([-gx, -gy, np.ones_like(gx)])
    nrm /= np.linalg.norm(nrm, axis=2, keepdims=True)
    # Light from the upper left and a little in front. The viewer squashes the texture vertically by 1.6 for the
    # oblique view, so the light is a little steeper here to come out at the map's angle on screen.
    L = np.array([-0.62, -0.78 * 1.3, 0.62])
    L /= np.linalg.norm(L)
    ndl = (nrm @ L).astype(np.float32)
    diffuse = np.clip((ndl + 0.35) / 1.35, 0, 1) ** 1.3

    # Shadows cast across the tops: march towards the light; a higher point in the way shades this one.
    d2 = L[:2] / np.linalg.norm(L[:2])
    slope = L[2] / np.linalg.norm(L[:2])          # rise of the light ray per px travelled
    occ = np.zeros_like(h)
    for s in range(2, 90, 3):
        dx, dy = int(round(-d2[0] * s)), int(round(-d2[1] * s))   # roll by -offset samples p + offset
        ahead = np.roll(hp, (dy, dx), (0, 1))
        occ = np.maximum(occ, (ahead - hp - s * slope * 0.55) / 10.0)
    shadow = blur_wrap(np.clip(occ, 0, 1), 3.0)

    # Occlusion: hollows below their surroundings get less sky light.
    cav = np.clip((blur_wrap(hp, 14) - hp) / 9.0, 0, 1)
    cav2 = np.clip((blur_wrap(hp, 50) - hp) / 18.0, 0, 1)
    ao = 1 - 0.45 * cav - 0.35 * cav2

    light = diffuse * (1 - 0.75 * shadow)
    lit = np.array([255, 249, 236], np.float32)       # warm sunlit tops
    mid = np.array([226, 230, 238], np.float32)
    shade_c = np.array([162, 177, 203], np.float32)   # blue-grey shade
    deep = np.array([112, 128, 162], np.float32)      # hollows
    t = light[..., None]
    col = np.where(t > 0.5, mid + (lit - mid) * ((t - 0.5) / 0.5), shade_c + (mid - shade_c) * (t / 0.5))
    a = ao[..., None]
    col = deep + (col - deep) * (0.35 + 0.65 * a)
    # Aerial haze: the low hollows are further away and paler, bluer.
    low = np.clip(1 - h / 0.45, 0, 1)[..., None] ** 1.5
    haze = np.array([196, 208, 228], np.float32)
    col = col + (haze - col) * low * 0.55
    # A thin warm glow on the edges that face the sun, as light comes through the thin outer fringes.
    rim = np.clip(ndl - 0.55, 0, 1)[..., None] * (1 - shadow[..., None])
    col = col + (np.array([255, 236, 205], np.float32) - col) * rim * 0.5
    return np.clip(col, 0, 255)


def texture():
    rng = np.random.default_rng(SEED)
    hp = height_field(rng)
    rgb = shade(hp)
    h = (hp - hp.min()) / (hp.max() - hp.min())
    rgba = np.dstack([rgb, 1 + h * 254]).astype(np.uint8)   # never fully transparent, so no colour is dropped
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "sea.webp"
    Image.fromarray(rgba, "RGBA").save(p, quality=86, alpha_quality=90, method=6, exact=True)
    print(f"texture {N} x {N}: {p.stat().st_size / 1024:.0f} KB")
    return rgba


# --- mask --------------------------------------------------------------------------------------------------------

LEVEL = 12                   # tile level used for the outline (8 map px per px for the full map)
MASK_SCALE = 32              # map px per mask px
BORDER = 96                  # mask px of "far outside" round the map (3072 map px)
RANGE = 3000                 # map px: the signed distance saturates beyond this
BG = np.array([16, 24, 40])  # the empty background of the tiles


def mask():
    meta = json.loads((TILES / "meta.json").read_text())
    W, H = meta["width"], meta["height"]
    d = TILES / "map_files" / str(LEVEL)
    fs = os.listdir(d)
    cols = max(int(f.split("_")[0]) for f in fs) + 1
    rows = max(int(f.split("_")[1].split(".")[0]) for f in fs) + 1
    ts = meta["tileSize"]
    img = np.zeros((rows * ts, cols * ts, 3), np.uint8)
    for f in fs:
        x, y = (int(v) for v in f.split(".")[0].split("_"))
        t = np.asarray(Image.open(d / f).convert("RGB"))
        img[y * ts:y * ts + t.shape[0], x * ts:x * ts + t.shape[1]] = t
    k = 2 ** (int(np.ceil(np.log2(max(W, H)))) - LEVEL)    # map px per px at this level
    img = img[:int(np.ceil(H / k)), :int(np.ceil(W / k))]
    data = (np.abs(img.astype(int) - BG).sum(2) > 24).astype(np.uint8)
    # tidy: close small gaps, keep the largest piece, fill holes
    data = cv2.morphologyEx(data, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(data)
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    data = (lab == big).astype(np.uint8)
    cs, _ = cv2.findContours(data, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    data = np.zeros_like(data)
    cv2.drawContours(data, cs, -1, 1, -1)
    # pad with the border and measure signed distance at this resolution, then reduce
    pb = BORDER * MASK_SCALE // k
    data = np.pad(data, pb)
    out_d = cv2.distanceTransform(1 - data, cv2.DIST_L2, 5) * k
    in_d = cv2.distanceTransform(data, cv2.DIST_L2, 5) * k
    sd = out_d - in_d
    r = MASK_SCALE // k
    hh, ww = sd.shape[0] // r * r, sd.shape[1] // r * r
    sd = sd[:hh, :ww].reshape(hh // r, r, ww // r, r).mean((1, 3))
    val = np.clip(128 + sd / RANGE * 127, 0, 255).astype(np.uint8)
    # the outermost ring is always "far outside", so the texture clamps to clouds beyond it
    val[[0, -1], :] = 255
    val[:, [0, -1]] = 255
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "mask.png"
    Image.fromarray(val, "L").save(p, optimize=True)
    x0 = y0 = -BORDER * MASK_SCALE
    info = {
        "mask": {"file": "/clouds/mask.png", "x0": x0, "y0": y0, "scale": MASK_SCALE,
                 "w": int(val.shape[1]), "h": int(val.shape[0]), "range": RANGE},
        "texture": {"file": "/clouds/sea.webp", "size": N},
    }
    OUT_JSON.write_text(json.dumps(info, separators=(",", ":")))
    print(f"mask {val.shape[1]} x {val.shape[0]}: {p.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("texture", "all"):
        texture()
    if what in ("mask", "all"):
        mask()
