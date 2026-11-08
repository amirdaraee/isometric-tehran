"""A stand-in for the painting model, on the CPU, in numpy: for the dry run and the tests, no GPU, no weights.

It has the real model's interface (see qwen.QwenModel). Its "VAE" averages 8 x 8 pixels into one latent pixel (3
channels) and scales back up. Its "model" knows the picture it wants for each window, a posterised, ink-edged
version of the window's render crop, and returns the exact flow-matching velocity towards it,
v = (x_t - target) / t. So the whole flow (windows, shifted grid, held neighbours, resume, feathering, blending,
review) runs for real; only the drawing is fake.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def inked(rgb: np.ndarray) -> np.ndarray:
    """A cheap "drawing" of a render crop: smoothed, posterised colours with dark edges."""
    a = cv2.bilateralFilter(rgb, 9, 60, 9)
    a = (np.round(a.astype(np.float32) / 24) * 24).clip(0, 255).astype(np.uint8)
    e = cv2.dilate(cv2.Canny(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), 60, 140), np.ones((2, 2), np.uint8)) > 0
    a[e] = (a[e] * 0.35).astype(np.uint8)
    return a


class StubModel:
    name = "stub"
    window = 1024
    channels = 3

    def __init__(self):
        self.targets: dict[tuple[int, int], np.ndarray] = {}
        self.sig: list[float] = []

    def sigmas(self, steps: int) -> list[float]:
        self.sig = [float(s) for s in np.linspace(1.0, 1 / steps, steps)] + [0.0]
        return self.sig

    def encode(self, img: Image.Image) -> np.ndarray:
        a = np.asarray(img.convert("RGB"), np.float32) / 127.5 - 1
        H, W = a.shape[0] // 8, a.shape[1] // 8
        return a[:H * 8, :W * 8].reshape(H, 8, W, 8, 3).mean(axis=(1, 3)).transpose(2, 0, 1).astype(np.float32)

    def decode(self, z: np.ndarray) -> np.ndarray:
        a = cv2.resize(z.transpose(1, 2, 0), (z.shape[2] * 8, z.shape[1] * 8), interpolation=cv2.INTER_LINEAR)
        return ((a + 1) * 127.5).round().clip(0, 255).astype(np.uint8)

    def condition(self, keys, crops, prompt, cfg: float) -> None:
        for key in keys:
            prompt(key)  # built like the real model's, unused
            self.targets[key] = self.encode(Image.fromarray(inked(np.asarray(crops(key)[0]))))

    def velocity(self, key, xw: np.ndarray, i: int) -> np.ndarray:
        t = self.targets[key][:, :xw.shape[1], :xw.shape[2]]
        return (xw - t) / self.sig[i]

    def peak_memory_gb(self) -> float:
        return 0.0
