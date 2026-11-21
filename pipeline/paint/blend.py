"""Putting painted blocks together, and the checks a submitted block must pass.

Blending (as Isometric Luxembourg's build_variant_tiles.py): each painted block is laid over the map with an alpha
that rises linearly over `feather` map px from each of its borders, in plan order. Neighbouring blocks overlap by
1200 px and hold the same pixels there, so a 600 px fade is invisible; towards an unpainted area it fades into the
render instead of ending in a hard edge.

Checks (review.py, on the block's canvas at model scale):
  position   the empty map edge must be exactly where the render has it, and the drawing's edges must line up
             with the render's (phase correlation: shift under 2 px);
  held       where finished neighbours were held, the canvas must equal them (it is pasted back by the painter):
             a large difference means the block was painted against other neighbours;
  seams      across the held area's edge, colour (CIELAB) must not jump more than it changes naturally nearby.

Pure numpy and OpenCV here (tested in CI on tiny images); composite() uses pyvips for the full map.
"""
from __future__ import annotations

import cv2
import numpy as np

BG = (16, 24, 40)


def ramp_alpha(w: int, h: int, f: int) -> np.ndarray:
    """(h, w) alpha 0..1 rising linearly over f px from every border."""
    x = np.arange(w, dtype=np.float32)
    y = np.arange(h, dtype=np.float32)
    dx = np.minimum(x, w - 1 - x)
    dy = np.minimum(y, h - 1 - y)
    return np.clip(np.minimum(dy[:, None], dx[None, :]) / f, 0, 1)


def blend_into(base: np.ndarray, patch: np.ndarray, x0: int, y0: int, f: int) -> np.ndarray:
    """Lay `patch` (already at map scale) over `base` at (x0, y0) with a feathered edge; clipped to the map."""
    H, W = base.shape[:2]
    h, w = patch.shape[:2]
    cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x0 + w, W), min(y0 + h, H)
    if cx1 <= cx0 or cy1 <= cy0:
        return base
    a = ramp_alpha(w, h, f)[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0, None]
    pc = patch[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0].astype(np.float32)
    under = base[cy0:cy1, cx0:cx1].astype(np.float32)
    out = base.copy()
    out[cy0:cy1, cx0:cx1] = (pc * a + under * (1 - a)).round().clip(0, 255).astype(base.dtype)
    return out


def composite(mosaic, patches: list[dict], feather: int, base_dir=None):
    """pyvips: lay painted patches [{"file", "x0", "y0", "w", "h"}] over the mosaic with feathered edges."""
    from pathlib import Path

    import pyvips

    W_, H_ = mosaic.width, mosaic.height
    out = mosaic
    for q in patches:
        f = Path(q["file"]) if base_dir is None else Path(base_dir) / q["file"]
        p = pyvips.Image.new_from_file(str(f))[:3]
        p = p.resize(q["w"] / p.width, vscale=q["h"] / p.height, kernel="lanczos3").cast("float")
        x0, y0 = q["x0"], q["y0"]
        cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x0 + p.width, W_), min(y0 + p.height, H_)
        if cx1 <= cx0 or cy1 <= cy0:
            continue
        xy = pyvips.Image.xyz(p.width, p.height)
        x, y = xy[0], xy[1]
        dx = (x < (p.width - 1 - x)).ifthenelse(x, p.width - 1 - x)
        dy = (y < (p.height - 1 - y)).ifthenelse(y, p.height - 1 - y)
        d = (dx < dy).ifthenelse(dx, dy).cast("float") / feather
        a = (d > 1).ifthenelse(1, d).crop(cx0 - x0, cy0 - y0, cx1 - cx0, cy1 - cy0)
        pc = p.crop(cx0 - x0, cy0 - y0, cx1 - cx0, cy1 - cy0)
        under = out.crop(cx0, cy0, cx1 - cx0, cy1 - cy0).cast("float")
        out = out.insert((pc * a + under * (1 - a)).cast(mosaic.format), cx0, cy0)
    return out


# --- checks -------------------------------------------------------------------------------------------------


def lab(rgb: np.ndarray) -> np.ndarray:
    a = cv2.cvtColor(np.ascontiguousarray(rgb, np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    a[..., 0] *= 100 / 255
    a[..., 1:] -= 128
    return a


def bg_mask(rgb: np.ndarray, tol: int = 6, speck: int = 9) -> np.ndarray:
    """The empty map edge: pixels of the background colour, in regions at least `speck` px across. Painted art has
    dark ink lines and shadows close to that colour; the map edge is one large region, so specks are dropped."""
    m = np.abs(rgb.astype(np.int16) - np.array(BG)).sum(axis=2) < tol
    if speck > 1 and m.any():
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((speck, speck), np.uint8)) > 0
    return m


def position(canvas: np.ndarray, render: np.ndarray) -> dict:
    """Does the canvas sit where the render is? Both at the same size (model scale)."""
    a, b = bg_mask(canvas), bg_mask(render, 30)
    union = (a | b).sum()
    iou = float((a & b).sum() / union) if union else 1.0

    def edges(im):
        g = cv2.cvtColor(np.ascontiguousarray(im, np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
        g = cv2.resize(g, (g.shape[1] // 4, g.shape[0] // 4), interpolation=cv2.INTER_AREA)
        m = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
        return m * np.outer(np.hanning(m.shape[0]), np.hanning(m.shape[1])).astype(np.float32)

    (sx, sy), resp = cv2.phaseCorrelate(edges(render), edges(canvas))
    return {"bg_iou": round(iou, 4), "bg_px": int(a.sum()), "shift_px": [round(sx * 4, 1), round(sy * 4, 1)],
            "response": round(float(resp), 3)}


def held_drift(canvas: np.ndarray, known: np.ndarray, keep: np.ndarray, band: int) -> float | None:
    """Mean colour difference (dE) between the canvas and the held neighbours, away from the free band."""
    inner = cv2.erode(keep.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=band + 8) > 0
    inner &= ~bg_mask(known)
    if inner.sum() < 100:
        return None
    return float(np.linalg.norm(lab(canvas)[inner] - lab(known)[inner], axis=1).mean())


def _edges_of(keep: np.ndarray, axis: int, min_frac: float = 0.25) -> list[int]:
    """Straight borders of the held area: columns (axis 1) or rows (axis 0) where it starts or stops."""
    k = keep.astype(np.int8)
    d = np.abs(np.diff(k, axis=axis))
    counts = d.sum(axis=0 if axis == 1 else 1)
    n = keep.shape[0 if axis == 1 else 1]
    return [int(i) + 1 for i in np.nonzero(counts > n * min_frac)[0]]


def seams(canvas: np.ndarray, keep: np.ndarray, chunk: int = 64, near: int = 4, far: int = 48,
          local: int = 120, factor: float = 1.8, floor: float = 6.0, run: int = 3) -> dict:
    """Colour jumps across the held area's straight borders. A chunk is a candidate when the jump across the border
    is over `floor` dE and over `factor` x the median jump on parallel lines `local` px to each side. A seam runs
    straight along the border, so only runs of `run` or more consecutive candidates count (a building that crosses
    the line makes a single, short jump)."""
    L = lab(canvas)
    bg = bg_mask(canvas)
    H, W = keep.shape
    bad, total = [], 0

    def jump(axis, pos, a):
        if axis == 1:
            if pos - far < 0 or pos + far > W:
                return None
            A, B = L[a:a + chunk, pos - far:pos - near], L[a:a + chunk, pos + near:pos + far]
            sk = bg[a:a + chunk, pos - far:pos + far].mean() > 0.05
        else:
            if pos - far < 0 or pos + far > H:
                return None
            A, B = L[pos - far:pos - near, a:a + chunk], L[pos + near:pos + far, a:a + chunk]
            sk = bg[pos - far:pos + far, a:a + chunk].mean() > 0.05
        if sk:
            return None
        return float(np.linalg.norm(A.reshape(-1, 3).mean(0) - B.reshape(-1, 3).mean(0)))

    for axis in (1, 0):
        n = H if axis == 1 else W
        for pos in _edges_of(keep, axis):
            line = []
            for a in range(0, n - chunk + 1, chunk):
                # only where the border really is the held area's edge along this chunk
                seg = keep[a:a + chunk, pos - 1:pos + 1] if axis == 1 else keep[pos - 1:pos + 1, a:a + chunk]
                d = None if seg.size == 0 or seg.all() or not seg.any() else jump(axis, pos, a)
                if d is None:
                    line.append(None)
                    continue
                ref = [r for r in (jump(axis, pos + o, a) for o in (-local - 80, -local, local, local + 80))
                       if r is not None]
                total += 1
                hit = d > floor and d > factor * (float(np.median(ref)) if ref else 0.0)
                line.append({"axis": "x" if axis == 1 else "y", "at": pos, "from": a, "dE": round(d, 1)} if hit
                            else False)
            cur: list[dict] = []
            for c in line + [False]:
                if c:
                    cur.append(c)
                    continue
                if len(cur) >= run:
                    bad.extend(cur)
                cur = []
    return {"chunks": total, "bad": bad, "bad_fraction": round(len(bad) / total, 3) if total else 0.0}
