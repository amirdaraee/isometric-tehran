"""For the maintainer: fetch submitted blocks, check them, blend the accepted ones into the map and rebuild tiles.

  python paint/review.py check pipeline/paint/submissions/b03-day.json   download, verify and check one submission
                                                                         (from a pull request's checkout)
  python paint/review.py fetch [--version day]     download every accepted canvas of a version
  python paint/review.py blend [--version day]     data/work/tiles/painted.json (day) or patches_<v>.json
  python paint/review.py tiles [--version day]     rebuild the tiles with the painted blocks (build_tiles.py or
                                                   build_variant_tiles.py)
  python paint/review.py publish b03 [--version day] --yes   copy an accepted canvas to this repo's release
                                                   (paint.json release.tag) with gh, and print its new URL

check writes data/work/paint/review/<block>-<version>.json (the report) and .jpg (a preview with the held area and
any seams marked), and exits 1 if a check fails. What the checks mean: docs/PAINTING.md, "Review".
"""
from __future__ import annotations

if __package__ in (None, ""):
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from paint import blend, blocks, config, registry, source

Image.MAX_IMAGE_PIXELS = None
PY = sys.executable
PIPE = Path(__file__).resolve().parent.parent


def check_canvas(plan: dict, bid: str, version: str, canvas: Path, grid: blocks.Grid, root: Path,
                 sub: dict | None = None, work: Path = config.WORK, band: int | None = None) -> dict:
    """All checks for one painted canvas; returns the report ({"ok": bool, "problems": [...], ...})."""
    s = config.settings()
    band = band or s["band"]
    rv = s["review"]
    b = blocks.get(plan, bid)
    M = grid.model_size
    problems, notes = [], []
    im = Image.open(canvas)
    rep: dict = {"block": bid, "version": version, "file": str(canvas), "size": list(im.size)}
    if im.size != (M, M):
        problems.append(f"size {im.size[0]} x {im.size[1]}, expected {M} x {M}")
        return {**rep, "ok": False, "problems": problems}
    rgb = np.asarray(im.convert("RGB"))
    if sub:
        if sub.get("plan") != plan["plan"]:
            problems.append(f"painted against plan {sub.get('plan')}, the current plan is {plan['plan']}")
        if (sub.get("x0"), sub.get("y0"), sub.get("size")) != (b["x0"], b["y0"], b["w"]):
            problems.append("x0, y0 or size do not match the block in the plan")
    # position: against the render (day) or the block's own day canvas (versions)
    if version == "day":
        ref = source.render(b["x0"], b["y0"], b["w"], b["h"], work).resize((M, M), Image.LANCZOS)
    else:
        day = blocks.canvas_path(bid, "day", root)
        ref = Image.open(day) if day.exists() else None
    if ref is not None:
        pos = blend.position(rgb, np.asarray(ref.convert("RGB")))
        rep["position"] = pos
        if pos["bg_px"] > 1000 and pos["bg_iou"] < 0.97:
            problems.append(f"the empty map edge is not where the render has it (overlap {pos['bg_iou']:.0%})")
        if max(abs(pos["shift_px"][0]), abs(pos["shift_px"][1])) > 2 and pos["response"] > 0.05:
            problems.append(f"the drawing is shifted by {pos['shift_px']} px against the render")
    # held neighbours and seams
    blocks.known(plan, bid, version, grid, root)
    kf, keepf = root / bid / f"known_{version}.png", root / bid / f"keep_{version}.png"
    if kf.exists():
        known = np.asarray(Image.open(kf).convert("RGB"))
        keep = np.asarray(Image.open(keepf).convert("L")) > 127
        drift = blend.held_drift(rgb, known, keep, band)
        rep["held_dE"] = None if drift is None else round(drift, 2)
        if drift is not None and drift > 2.0:
            problems.append(f"the held neighbours differ by {drift:.1f} dE: painted against other neighbours?")
        sm = blend.seams(rgb, keep, floor=rv["seamDeltaE"])
        rep["seams"] = sm
        if sm["bad_fraction"] > 0.05:
            problems.append(f"{len(sm['bad'])} of {sm['chunks']} border chunks have a visible seam")
        elif sm["bad"]:
            notes.append(f"{len(sm['bad'])} border chunk(s) to look at")
        if sub is not None:
            used = {o: blocks.sha256(blocks.canvas_path(o, version, root))
                    for o in sub.get("neighbours", {}) if blocks.canvas_path(o, version, root).exists()}
            if sub.get("neighbours", {}) != used:
                notes.append("the submission's neighbour checksums differ from the accepted canvases")
    else:
        keep = None
        notes.append("no finished neighbours: nothing held, no seams to check")
    # magenta specks (versions) and a preview
    rep["magenta_px"] = int(_magenta(rgb).sum())
    if rep["magenta_px"] > 200:
        notes.append(f"{rep['magenta_px']} magenta px: run paint.post despeckle")
    rep.update(ok=not problems, problems=problems, notes=notes)
    out = root / "review"
    out.mkdir(parents=True, exist_ok=True)
    prev = Image.fromarray(rgb)
    d = ImageDraw.Draw(prev)
    if keep is not None:
        for axis, pos_ in [("x", p) for p in blend._edges_of(keep, 1)] + [("y", p) for p in blend._edges_of(keep, 0)]:
            line = (pos_, 0, pos_, M) if axis == "x" else (0, pos_, M, pos_)
            d.line(line, fill=(60, 120, 210), width=3)
        for bad in rep.get("seams", {}).get("bad", []):
            a, p = bad["from"], bad["at"]
            box = (p - 8, a, p + 8, a + 64) if bad["axis"] == "x" else (a, p - 8, a + 64, p + 8)
            d.rectangle(box, outline=(255, 0, 255), width=4)
    prev.thumbnail((2048, 2048))
    prev.save(out / f"{bid}-{version}.jpg", quality=88)
    (out / f"{bid}-{version}.json").write_text(json.dumps(rep, indent=1))
    return rep


def _magenta(rgb):
    import cv2

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = (hsv[..., i].astype(int) for i in range(3))
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    return (h >= 140) & (h <= 170) & (s >= 90) & (v >= 70) & (r > g + 40) & (b > g + 40)


def cmd_check(args) -> int:
    sub = registry.parse_submission(json.loads(Path(args.submission).read_text(encoding="utf-8")), args.submission)
    plan = blocks.load_plan()
    grid = blocks.grid_from_settings()
    bid, v = sub["block"], sub["version"]
    # neighbours as accepted in main, then the submitted canvas into the review folder
    blocks.fetch(plan, bid, v)
    if v != "day":
        blocks.fetch(plan, bid, "day", include_self=True)
    dest = config.PAINT_WORK / "review" / f"{bid}-{v}.png"
    max_b = int(config.settings()["release"]["maxCanvasMB"] * 1e6)
    if sub["bytes"] > max_b:
        raise SystemExit(f"{sub['bytes'] / 1e6:.0f} MB is over the {max_b / 1e6:.0f} MB limit")
    if not (dest.exists() and blocks.sha256(dest) == sub["sha256"]):
        print("downloading", sub["url"], flush=True)
        blocks.download(sub["url"], dest, sub["sha256"], max_b)
    if dest.stat().st_size != sub["bytes"]:
        raise SystemExit(f"{dest}: {dest.stat().st_size} bytes, the submission says {sub['bytes']}")
    rep = check_canvas(plan, bid, v, dest, grid, config.PAINT_WORK, sub)
    print(json.dumps({k: rep[k] for k in rep if k not in ("seams",)}, indent=1))
    if "seams" in rep:
        print(f"seams: {len(rep['seams']['bad'])} of {rep['seams']['chunks']} border chunks")
    print("preview:", config.PAINT_WORK / "review" / f"{bid}-{v}.jpg")
    print("PASS" if rep["ok"] else "FAIL: " + "; ".join(rep["problems"]))
    return 0 if rep["ok"] else 1


def patches(plan: dict, version: str, root: Path, rel_to: Path) -> list[dict]:
    """Accepted canvases of a version present under root, in plan order, as build_tiles patches."""
    subs = registry.load_submissions()
    out = []
    for b in plan["blocks"]:
        p = blocks.canvas_path(b["id"], version, root)
        if (b["id"], version) in subs and p.exists():
            out.append({"file": str(Path(_relpath(p, rel_to))), "x0": b["x0"], "y0": b["y0"], "w": b["w"],
                        "h": b["h"], "block": b["id"]})
    return out


def _relpath(p: Path, base: Path) -> str:
    import os

    return os.path.relpath(p, base)


def cmd_blend(args) -> int:
    plan = blocks.load_plan()
    tiles = config.WORK / "tiles"
    tiles.mkdir(parents=True, exist_ok=True)
    ps = patches(plan, args.version, config.PAINT_WORK, tiles)
    dest = tiles / ("painted.json" if args.version == "day" else f"patches_{args.version}.json")
    dest.write_text(json.dumps({"feather": config.settings()["review"]["feather"], "patches": ps}, indent=1))
    print(dest, len(ps), "blocks:", ", ".join(p["block"] for p in ps) or "none (review.py fetch first?)")
    return 0


def cmd_fetch(args) -> int:
    plan = blocks.load_plan()
    for (bid, v) in sorted(registry.load_submissions()):
        if v == args.version:
            blocks.fetch(plan, bid, v, include_self=True)
    return 0


def cmd_tiles(args) -> int:
    cmd_blend(args)
    script = ["build_tiles.py"] if args.version == "day" else ["build_variant_tiles.py", args.version]
    return subprocess.call([PY, *script], cwd=PIPE)


def cmd_publish(args) -> int:
    """Owner only: keep accepted canvases in this repo's release, so they outlive the contributor's fork."""
    subs = registry.load_submissions()
    sub = subs.get((args.block, args.version))
    if not sub:
        raise SystemExit(f"no accepted submission for {args.block} {args.version}")
    src = blocks.canvas_path(args.block, args.version)
    if not src.exists() or blocks.sha256(src) != sub["sha256"]:
        raise SystemExit(f"{src} missing or not the accepted canvas: review.py fetch --version {args.version}")
    tag = config.settings()["release"]["tag"]
    repo = config.city()["repo"].removeprefix("https://github.com/")
    name = f"{args.block}-{args.version}.png"
    print(f"upload {src} as {name} to the {tag!r} release of {repo}")
    if not args.yes:
        print("add --yes to upload")
        return 1
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / name
        shutil.copy(src, f)
        if subprocess.call(["gh", "release", "view", tag, "--repo", repo], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) != 0:
            subprocess.check_call(["gh", "release", "create", tag, "--repo", repo, "--title", "Painted blocks",
                                   "--notes", "Painted block canvases (CC BY-NC 4.0). See docs/PAINTING.md."])
        subprocess.check_call(["gh", "release", "upload", tag, str(f), "--repo", repo, "--clobber"])
    print("new url:", f"https://github.com/{repo}/releases/download/{tag}/{name}",
          f"(put it in pipeline/paint/submissions/{registry.submission_name(args.block, args.version)})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("submission")
    for name in ("fetch", "blend", "tiles"):
        sub.add_parser(name).add_argument("--version", default="day")
    p = sub.add_parser("publish")
    p.add_argument("block")
    p.add_argument("--version", default="day")
    p.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)
    return {"check": cmd_check, "fetch": cmd_fetch, "blend": cmd_blend, "tiles": cmd_tiles,
            "publish": cmd_publish}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
