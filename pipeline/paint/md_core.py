"""Whole-canvas painting with overlapping windows (MultiDiffusion with a shifted grid). Model-agnostic, numpy only.

The whole block is one latent. At every step each overlapping window predicts a velocity for its part; the
predictions are blended with weights that are flat in the middle and fall towards the window's inner edges, and
one Euler step (flow matching) moves the whole latent. Every other step the window grid is shifted by half a
stride, so no window edge stays in one place and no seam can form.

Known pixels (finished neighbours, the empty map edge) are held: after every step they are reset to the known
latent re-noised to the current noise level (RePaint, adapted to flow matching: x_t = (1 - t) * x0 + t * noise).
Windows that are entirely held are skipped, so they cost nothing.

State is saved after every step, so a stopped job resumes where it was (same plan, same result).

Shapes: latents are (C, H, W) float32 arrays in latent pixels (1/8 of image pixels). This module has no model in
it: the caller passes `velocity(key, x_window, step_index) -> v_window`. The real model (qwen.py) converts each
window to torch and back; the stand-in (stub.py) and the tests stay in numpy, so CI needs no torch.

Ported from Isometric Luxembourg's painter. Method from Pixel Singapore:
https://www.yadongxie.com/blog/how-pixel-singapore-was-made
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np
from scipy import ndimage


def positions(n: int, size: int, stride: int, offset: int = 0) -> list[int]:
    """Window starts along one axis covering [0, n): a regular grid starting at `offset`, plus both ends."""
    if n <= size:
        return [0]
    ps = {0, n - size}
    p = offset % stride
    while p + size < n:
        ps.add(p)
        p += stride
    return sorted({q - (q % 2) for q in ps})  # even, so latents pack into 2x2 patches


def ramp(size: int, fade: int, at_start: bool, at_end: bool) -> np.ndarray:
    """1D weight: 1 in the middle, falling linearly over `fade` towards each edge that is not a canvas border."""
    i = np.arange(size, dtype=np.float32) + 0.5
    w = np.ones(size, np.float32)
    if not at_start:
        w = np.minimum(w, i / fade)
    if not at_end:
        w = np.minimum(w, (size - i) / fade)
    return w


def window_weight(y: int, x: int, size: int, fade: int, H: int, W: int) -> np.ndarray:
    wy = ramp(min(size, H), fade, y == 0, y + size >= H)
    wx = ramp(min(size, W), fade, x == 0, x + size >= W)
    return wy[:, None] * wx[None, :]


def grid(H: int, W: int, size: int, stride: int, shifted: bool) -> list[tuple[int, int]]:
    off = stride // 2 if shifted else 0
    return [(y, x) for y in positions(H, size, stride, off) for x in positions(W, size, stride, off)]


@dataclass
class Plan:
    H: int                 # latent height
    W: int                 # latent width
    sigmas: list[float]    # noise levels, len = steps + 1, ending at 0
    size: int = 128        # window, latent px (1024 image px)
    stride: int = 96       # 768 image px
    fade: int = 32         # = size - stride: weights fall over the whole overlap
    seed: int = 1867

    def key(self) -> str:
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]

    def windows(self, hold: np.ndarray | None = None) -> list[tuple[int, int]]:
        """Every window either grid uses, minus those entirely held (they are never run)."""
        keys = sorted(set(grid(self.H, self.W, self.size, self.stride, False))
                      | set(grid(self.H, self.W, self.size, self.stride, True)))
        if hold is None:
            return keys
        h, w = min(self.size, self.H), min(self.size, self.W)
        return [(y, x) for y, x in keys if not bool(hold[y:y + h, x:x + w].all())]

    def window_runs(self, hold: np.ndarray | None = None) -> int:
        """Model calls for the whole plan (without guidance; with CFG each call runs the model twice)."""
        steps = len(self.sigmas) - 1
        free = set(self.windows(hold))
        n = 0
        for i in range(steps):
            n += sum(1 for k in grid(self.H, self.W, self.size, self.stride, i % 2 == 1) if k in free)
        return n


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp.npz"
    np.savez(tmp, **state)
    os.replace(tmp, path)


def load_state(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def paint(
    plan: Plan,
    velocity: Callable[[tuple[int, int], np.ndarray, int], np.ndarray],
    channels: int = 16,
    known: np.ndarray | None = None,      # (C, H, W) known latent
    hold: np.ndarray | None = None,       # (H, W) bool, True = hold to `known`
    state_path: str | None = None,
    on_step: Callable[[int, int, int], None] | None = None,   # (step, steps, windows run)
    stop: Callable[[], bool] | None = None,  # return True to save and stop early (deadline)
    init: np.ndarray | None = None,       # (C, H, W) latent to start from (light repaint, "img2img")
    first: int = 0,                       # with init: start at this step, from init noised to sigmas[first]
) -> np.ndarray | None:
    """Run the plan; returns the final latent, or None if `stop` asked to stop (state saved).
    With `init`, painting starts part-way down the schedule from that image, so it keeps its layout and colours
    and only takes on the style (the later the first step, the lighter the repaint)."""
    H, W, steps = plan.H, plan.W, len(plan.sigmas) - 1
    assert H % 2 == 0 and W % 2 == 0, "latent size must be even"
    holdf = None
    if hold is not None:
        assert known is not None and hold.shape == (H, W)
        holdf = hold.astype(np.float32)

    start = 0
    st = load_state(state_path) if state_path else None
    if st is not None and str(st["plan"]) == plan.key():
        x, noise, start = st["x"], st["noise"], int(st["step"])
    else:
        noise = np.random.default_rng(plan.seed).standard_normal((channels, H, W), dtype=np.float32)
        x = noise.copy()
        if init is not None:
            start = first
            x = ((1 - plan.sigmas[first]) * init + plan.sigmas[first] * noise).astype(np.float32)
        if holdf is not None:  # sigma_0 is ~1, but keep the held area exactly on the schedule
            s0 = plan.sigmas[start]
            x = holdf * ((1 - s0) * known + s0 * noise) + (1 - holdf) * x

    weights: dict[tuple[int, int], np.ndarray] = {}
    for i in range(start, steps):
        s, s_next = plan.sigmas[i], plan.sigmas[i + 1]
        acc = np.zeros_like(x)
        wsum = np.zeros((H, W), np.float32)
        ran = 0
        for (y, xx) in grid(H, W, plan.size, plan.stride, shifted=i % 2 == 1):
            h, w = min(plan.size, H), min(plan.size, W)
            if hold is not None and bool(hold[y:y + h, xx:xx + w].all()):
                continue  # nothing to paint in this window
            k = (y, xx)
            if k not in weights:
                weights[k] = window_weight(y, xx, plan.size, plan.fade, H, W)
            wt = weights[k]
            v = velocity(k, x[:, y:y + h, xx:xx + w], i)
            acc[:, y:y + h, xx:xx + w] += np.asarray(v, np.float32) * wt
            wsum[y:y + h, xx:xx + w] += wt
            ran += 1
        free = wsum > 0
        if hold is not None:
            assert bool((free | hold).all()), "a free pixel is in no window"
        else:
            assert bool(free.all()), "a pixel is in no window"
        v = acc / np.maximum(wsum, 1e-8)
        x = np.where(free, x + (s_next - s) * v, x).astype(np.float32)  # Euler step (flow matching)
        if holdf is not None:
            x = (holdf * ((1 - s_next) * known + s_next * noise) + (1 - holdf) * x).astype(np.float32)
        if state_path:
            save_state(state_path, {"plan": np.array(plan.key()), "step": np.array(i + 1), "x": x, "noise": noise})
        if on_step:
            on_step(i + 1, steps, ran)
        if stop and i + 1 < steps and stop():
            return None
    return x


def blend_tiles(shape: tuple[int, int, int], tiles: list[tuple[int, int, np.ndarray]], size: int,
                fade: int) -> np.ndarray:
    """Blend overlapping (C, h, w) tiles placed at (y, x) into one (C, H, W) image with the window weights.
    Used to encode and decode the latent in pieces and blend the pieces."""
    C, H, W = shape
    acc = np.zeros(shape, np.float32)
    wsum = np.zeros((H, W), np.float32)
    for y, x, t in tiles:
        h, w = t.shape[1:]
        wt = window_weight(y, x, size, fade, H, W)[:h, :w]
        acc[:, y:y + h, x:x + w] += np.asarray(t, np.float32) * wt
        wsum[y:y + h, x:x + w] += wt
    assert bool((wsum > 0).all()), "tiles leave a gap"
    return acc / wsum


def hold_mask(known_px: np.ndarray, band_px: int, scale: int = 8) -> np.ndarray:
    """Latent-resolution hold mask from an image-resolution known mask (True = known): a latent pixel is held
    only if its whole 8x8 block is known and it is more than `band_px` from any unknown pixel. The free band
    lets the new paint blend into what is already there."""
    H, W = known_px.shape[0] // scale, known_px.shape[1] // scale
    blk = known_px[:H * scale, :W * scale].reshape(H, scale, W, scale).all(axis=(1, 3))  # block fully known
    r = max(1, round(band_px / scale))
    grown = ndimage.maximum_filter((~blk).astype(np.uint8), size=2 * r + 1, mode="constant", cval=0)
    return grown == 0


def feather(known_px: np.ndarray, band_px: int) -> np.ndarray:
    """Image-resolution alpha for putting the known pixels back at the end: 1 deep inside the known area, falling
    to 0 at the unknown edge across the band (chessboard distance)."""
    k = known_px.astype(bool)
    if k.all():
        return np.ones(k.shape, np.float32)
    if not k.any():
        return np.zeros(k.shape, np.float32)
    d = ndimage.distance_transform_cdt(k, metric="chessboard").astype(np.float32)
    return np.clip(d - 1, 0, band_px) / band_px
