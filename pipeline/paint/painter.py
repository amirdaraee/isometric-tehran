"""Paint one block: the whole block in one pass (overlapping windows, shifted grid), with resume and held neighbours.

Runs where the GPU is (your machine, or a rented pod via paint.py run --backend runpod). Reads the inputs that
blocks.py prepare wrote into the block's folder and writes canvas_<version>.png there (model scale: 4096 px for a
4800 px block).

Every window sees its crop of the render outlined in red all round, and the layout map (day), exactly like a
full-view training example of the style model. Prompt per window: the base prompt (prompts/<version>.txt) plus the
hints of the landmarks inside that window (prompts/landmarks.json).

  python -m paint.painter --block-dir ../data/work/paint/b03 --version day [--memory full|offload|nf4]
                          [--steps 8 --cfg 1 --lightning] [--deadline EPOCH] [--model stub]
Exit codes: 0 done, 3 paused at the deadline (run again with the same folder to resume from the saved step).
Writes <block-dir>/live_<version>/status.json after every step (step, seconds per window, peak GPU memory).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from paint.blocks import model_px
from paint.config import BG
from paint.md_core import Plan, feather, hold_mask, paint

Image.MAX_IMAGE_PIXELS = None


class Status:
    def __init__(self, folder: Path, **kw):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.s = {"state": "loading", "started": time.time(), **kw}
        self.save()

    def save(self, **kw):
        self.s.update(kw)
        tmp = self.folder / "status.tmp"
        tmp.write_text(json.dumps(self.s))
        os.replace(tmp, self.folder / "status.json")


def window_prompt(job: dict, key: tuple[int, int], win: int) -> str:
    """Base prompt plus the hints of the landmarks inside this window (key in latent px, hints in model px)."""
    y0, x0 = key[0] * 8, key[1] * 8
    hints = [h["text"] for h in job.get("hints", []) if x0 <= h["x"] < x0 + win and y0 <= h["y"] < y0 + win]
    return " ".join([job["prompt"], *hints])


def paint_block(block_dir: Path, version: str, model, steps: int, cfg: float, seed: int = 1867, band: int = 64,
                deadline: float = 0, stop_after: int | None = None, log=print) -> Path | None:
    """Paint the block; returns the canvas path, or None when paused (state saved, run again to resume)."""
    job = json.loads((block_dir / f"job_{version}.json").read_text(encoding="utf-8"))
    v = job["settings"]
    if version == "day":
        src = Image.open(block_dir / "src.png").convert("RGB")
    else:
        src = Image.open(block_dir / f"canvas_{v.get('from', 'day')}.png").convert("RGB")
    k = job["model_cell"] / job["src_cell"]
    win = model.window
    Wpx, Hpx = max(win, model_px(src.width, k)), max(win, model_px(src.height, k))
    render = src if src.size == (Wpx, Hpx) else src.resize((Wpx, Hpx), Image.LANCZOS)
    layout = None
    if v.get("layout") and (block_dir / "layout.png").exists():
        layout = Image.open(block_dir / "layout.png").convert("RGB").resize((Wpx, Hpx), Image.NEAREST)
    H, W = Hpx // 8, Wpx // 8
    live = Status(block_dir / f"live_{version}", block=job["block"], version=version, steps=steps,
                  size=[Wpx, Hpx], mode=model.name)
    log(f"{job['block']} {version}: canvas {Wpx} x {Hpx} px, latent {W} x {H}, {steps} steps, cfg {cfg}"
        + (", with layout" if layout is not None else ""))

    # Held pixels: finished neighbours (known/keep) and the empty map edge, which costs nothing to paint.
    rarr = np.asarray(render, np.int16)
    bg_px = np.abs(rarr - np.array(BG)).sum(axis=2) < 6
    known = hold = None
    known_img, keep_np = None, None
    kf, keepf = block_dir / f"known_{version}.png", block_dir / f"keep_{version}.png"
    if kf.exists():
        known_img = Image.open(kf).convert("RGB")
        keep_np = np.asarray(Image.open(keepf).convert("L")) > 127
        if known_img.size != (Wpx, Hpx):
            raise SystemExit(f"{kf}: {known_img.size}, expected {Wpx} x {Hpx}")
    elif bg_px.mean() > 0.01:
        known_img, keep_np = render, np.zeros((Hpx, Wpx), bool)
    if known_img is not None:
        ka = np.asarray(known_img).copy()
        ka[bg_px] = BG
        known_img = Image.fromarray(ka)
        live.save(state="encoding held pixels")
        known = model.encode(known_img)
        hold = hold_mask(keep_np | bg_px, band)
        log(f"holding {hold.mean() * 100:.0f}% of the block")

    sigmas = model.sigmas(steps)
    t = win // 8
    plan = Plan(H=H, W=W, sigmas=sigmas, size=t, stride=t * 3 // 4, fade=t // 4, seed=seed)
    keys = plan.windows(hold)
    log(f"{len(keys)} windows to condition, {plan.window_runs(hold)} model calls in all"
        + (" (x2 with guidance)" if cfg > 1 else ""))

    def crops(key):
        y, x = key
        box = (x * 8, y * 8, x * 8 + win, y * 8 + win)
        return [render.crop(box)] + ([layout.crop(box)] if layout is not None else [])

    live.save(state="conditioning")
    model.condition(keys, crops, lambda key: window_prompt(job, key, win), cfg)
    state_dir = block_dir / f"state_{version}"
    state_dir.mkdir(exist_ok=True)
    live.save(state="painting", started=time.time())
    t0 = time.time()
    count = {"n": 0}

    def on_step(i, n, ran):
        count["n"] += ran
        el = time.time() - t0
        per = el / max(1, count["n"])
        live.save(step=i, windows_last_step=ran, per_window_s=round(per, 3), elapsed_s=round(el),
                  peak_gpu_gb=model.peak_memory_gb())
        log(f"step {i}/{n}: {ran} windows, {per:.2f} s per window, {el / 60:.1f} min")

    def stop():
        if stop_after is not None and live.s.get("step", 0) >= stop_after:
            return True
        return bool(deadline) and time.time() > deadline - 600

    z = paint(plan, model.velocity, channels=model.channels, known=known, hold=hold,
              state_path=str(state_dir / "state.npz"), on_step=on_step, stop=stop)
    if z is None:
        live.save(state="paused")
        log("PAUSED: state saved; run again with the same block folder to resume")
        return None

    live.save(state="decoding")
    out = model.decode(z)
    if kf.exists():  # put the finished neighbours back, feathered across the free band
        a = feather(keep_np, band)[..., None]
        out = (np.asarray(known_img) * a + out * (1 - a)).round().astype(np.uint8)
    out[bg_px] = BG  # outside the map stays background
    dest = block_dir / f"canvas_{version}.png"
    tmp = block_dir / f"canvas_{version}.tmp.png"
    Image.fromarray(out).save(tmp)
    os.replace(tmp, dest)
    live.save(state="done", out=str(dest), minutes=round((time.time() - t0) / 60, 1),
              peak_gpu_gb=model.peak_memory_gb())
    log(f"FINISHED {dest}")
    return dest


def make_model(kind: str, version: str, memory: str = "full", lightning: bool = False, mu: float | None = None):
    if kind == "stub":
        from paint.stub import StubModel

        return StubModel()
    from paint import config
    from paint.qwen import QwenModel

    v = config.version(version)
    extra = config.weights("lightning") if lightning else None
    return QwenModel(config.settings()["model"]["repo"], config.weights(v["lora"]), extra_lora=extra,
                     memory=memory, mu=mu)


def main(argv=None) -> int:
    from paint import config

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--block-dir", required=True, type=Path)
    ap.add_argument("--version", default="day")
    ap.add_argument("--model", default="qwen", choices=["qwen", "stub"])
    ap.add_argument("--memory", default="full", choices=["full", "offload", "nf4"],
                    help="full: all on the GPU (80 GB); offload: one part at a time (48 GB); nf4: 4-bit (24 GB)")
    ap.add_argument("--steps", type=int)
    ap.add_argument("--cfg", type=float)
    ap.add_argument("--mu", type=float, help="fixed schedule shift (Lightning: 1.0986)")
    ap.add_argument("--lightning", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--band", type=int)
    ap.add_argument("--deadline", type=float, default=0, help="epoch seconds; pause (state saved) 10 min before")
    ap.add_argument("--stop-after", type=int, help="pause after this many steps (to test resuming)")
    args = ap.parse_args(argv)
    s = config.settings()
    v = config.version(args.version)
    lightning = v.get("lightning", False) if args.lightning is None else args.lightning
    steps = args.steps or v["steps"]
    cfg = v["cfg"] if args.cfg is None else args.cfg
    mu = args.mu if args.mu is not None else (v.get("mu") if lightning else None)
    model = make_model(args.model, args.version, args.memory, lightning, mu)
    out = paint_block(args.block_dir, args.version, model, steps, cfg, args.seed or s["seed"], args.band or s["band"],
                      args.deadline, args.stop_after, log=lambda m: print(m, flush=True))
    return 0 if out else 3


if __name__ == "__main__":
    sys.exit(main())
