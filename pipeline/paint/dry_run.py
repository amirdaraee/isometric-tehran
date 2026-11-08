"""The whole painting flow on the CPU with a stand-in model, on a small crop round the opening place.

No GPU, no weights, no network, no torch: reviewers can try every step of a contribution in a minute or two.

  1. plan two small blocks (2400 px, 600 px overlap) round Golestan Palace;
  2. prepare their inputs from the render (or the committed tiles);
  3. paint the first with the stand-in (stub.py), pausing after 3 steps and resuming, as after a deadline;
  4. "submit" it (checksum, submission JSON with a file:// URL) and review it (download, checksum, checks);
  5. paint the second holding the first, and review it (held pixels, seams);
  6. blend both into the map crop with feathered edges and build a small tile pyramid.

  python paint/paint.py dry-run [--out DIR]       (default data/work/paint-dry; nothing outside it is touched)
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image

from paint import blend, blocks, config, registry, source
from paint.painter import paint_block
from paint.stub import StubModel

GRID = blocks.Grid(size=2400, overlap=600, src_cell=600, model_cell=512)   # 2048 model px per block


def mini_plan(ox: int, oy: int) -> dict:
    """Two side-by-side blocks, in map coordinates, from a 4200 x 2400 crop starting at (ox, oy)."""
    bl = blocks.plan_blocks(4200, 2400, (GRID.size // 2, GRID.size // 2), GRID, np.ones((2400, 4200), bool), 1,
                            0.0, None)
    two = [b for b in bl if b["y0"] == 0][:2]
    for b in two:
        b["x0"] += ox
        b["y0"] += oy
    two[1]["before"] = [two[0]["id"]]
    for b in two:
        b["landmarks"] = [lm["slug"] for lm in config.landmarks()
                          if b["x0"] <= lm["x"] < b["x0"] + b["w"] and b["y0"] <= lm["y"] < b["y0"] + b["h"]]
    return {"plan": "dry-run", "blocks": two, "canvas": [4200, 2400], "origin": [ox, oy]}


def _job_settings(d: Path) -> None:
    """The dry run paints 8 steps without guidance, like the Lightning day setting."""
    f = d / "job_day.json"
    job = json.loads(f.read_text(encoding="utf-8"))
    job["settings"] = {**job["settings"], "steps": 8, "cfg": 1.0}
    f.write_text(json.dumps(job, indent=1, ensure_ascii=False))


def dry_run(out: Path) -> int:
    t0 = time.time()
    mark = out / ".paint-dry-run"
    if out.exists() and any(out.iterdir()):  # start clean: a saved state would resume instead of pausing
        if not mark.exists():
            raise SystemExit(f"{out} is not empty and is not an earlier dry run: pick another --out")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    mark.touch()
    root = out / "blocks"
    slug, ax, ay = config.opening_place()
    ox, oy = ax - GRID.size // 2, ay - GRID.size // 2
    plan = mini_plan(ox, oy)
    b0, b1 = plan["blocks"]
    print(f"1. plan: {b0['id']} at ({b0['x0']}, {b0['y0']}) centred on {slug}, {b1['id']} at ({b1['x0']}, "
          f"{b1['y0']}); {GRID.size} px blocks, {GRID.overlap} px overlap, {GRID.model_size} model px")
    src_from = "the local render" if source.has_render() else "the committed tiles (no local render)"
    print(f"2. inputs from {src_from}")
    for b in (b0,):
        blocks.prepare(plan, b["id"], "day", GRID, root)
        _job_settings(root / b["id"])

    print("3. paint", b0["id"], "with the stand-in model: pause after 3 steps, then resume")
    model = StubModel()
    r = paint_block(root / b0["id"], "day", model, steps=8, cfg=1.0, stop_after=3, log=lambda m: print("   ", m))
    assert r is None, "expected a pause"
    c0 = paint_block(root / b0["id"], "day", StubModel(), steps=8, cfg=1.0, log=lambda m: print("   ", m))

    print("4. submit and review", b0["id"])
    from paint.paint import make_submission
    from paint.review import check_canvas

    subs = out / "submissions"
    subs.mkdir(exist_ok=True)
    sub0 = make_submission(plan, b0["id"], "day", "dry-run", c0.resolve().as_uri(), root)
    (subs / registry.submission_name(b0["id"], "day")).write_text(json.dumps(sub0, indent=1))
    got = blocks.download(sub0["url"], out / "review" / f"{b0['id']}-day.png", sub0["sha256"])
    rep0 = check_canvas(plan, b0["id"], "day", got, GRID, root, sub0)
    print("    ", "PASS" if rep0["ok"] else "FAIL", rep0.get("position"), rep0["problems"] or "", rep0["notes"] or "")

    print("5. paint", b1["id"], "holding", b0["id"])
    blocks.prepare(plan, b1["id"], "day", GRID, root)
    _job_settings(root / b1["id"])
    c1 = paint_block(root / b1["id"], "day", StubModel(), steps=8, cfg=1.0, log=lambda m: print("   ", m))
    sub1 = make_submission(plan, b1["id"], "day", "dry-run", c1.resolve().as_uri(), root)
    rep1 = check_canvas(plan, b1["id"], "day", c1, GRID, root, sub1)
    print("    ", "PASS" if rep1["ok"] else "FAIL", f"held dE {rep1.get('held_dE')}",
          f"seams {len(rep1.get('seams', {}).get('bad', []))}/{rep1.get('seams', {}).get('chunks')}",
          rep1["problems"] or "", rep1["notes"] or "")

    print("6. blend into the map crop and build tiles")
    base = np.asarray(source.render(ox, oy, 4200, 2400))
    mixed = base
    for b, c in ((b0, c0), (b1, c1)):
        patch = np.asarray(Image.open(c).convert("RGB").resize((b["w"], b["h"]), Image.LANCZOS))
        mixed = blend.blend_into(mixed, patch, b["x0"] - ox, b["y0"] - oy, GRID.overlap // 2)
    Image.fromarray(mixed).save(out / "blended.png")
    Image.fromarray(np.hstack([base[:, :2100], mixed[:, 2100:]])).resize((2100, 600)).save(out / "before_after.jpg")
    import pyvips

    vi = pyvips.Image.new_from_file(str(out / "blended.png"))
    vi.dzsave(str(out / "tiles" / "map"), tile_size=512, overlap=0, suffix=".webp[Q=78]", layout="dz")
    ok = rep0["ok"] and rep1["ok"]
    print(f"\n{'DRY RUN PASSED' if ok else 'DRY RUN FAILED'} in {time.time() - t0:.0f} s. Look at:\n"
          f"  {out / 'before_after.jpg'}   (left: render, right: the stand-in's 'painting')\n"
          f"  {out / 'review'}            (review previews and reports)\n"
          f"  {out / 'submissions'}       (the submission JSON a contributor would send)\n"
          f"  {out / 'tiles'}             (the tile pyramid)")
    return 0 if ok else 1
