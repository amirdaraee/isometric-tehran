"""The map cut into blocks for painting, and where each block stands (free, claimed, done).

The map (20480 x 10240 px at 0.25 m/px) is cut into square blocks of `blocks.size` render px overlapping by
`blocks.overlap` (paint.json: 4800 and 1200). At the training scale (600 render px -> 512 model px) a block is
4096 model px, 25 to 36 windows of 1024 px per step, and the overlap is exactly one window (1024 model px). The
grid is aligned so the city's opening place (Golestan Palace) is the centre of block b00, and blocks are numbered
outward from it. A block holds the pixels its finished neighbours already painted, so it can start only when every
overlapping block with a lower number is done ("ready"); blocks that do not touch can be painted at the same time.

The plan depends only on committed files (the tiles' size, a small tile level for where the map has data, the
landmarks), so everyone gets the same block ids.

  python paint/blocks.py plan                    -> data/work/blocks.json and blocks.jpg (preview)
  python paint/blocks.py status [--version day]  which blocks are done, claimed, ready or waiting
  python paint/blocks.py prepare b03 [--version day]   painter inputs in data/work/paint/b03/
  python paint/blocks.py fetch b03 [--version day]     download the accepted canvases of b03's neighbours
"""
from __future__ import annotations

if __package__ in (None, ""):  # run as a script: make `paint` and `common` importable
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import hashlib
import json
import math
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from paint import config, registry, source

Image.MAX_IMAGE_PIXELS = None


@dataclass(frozen=True)
class Grid:
    size: int = 4800        # block, render px
    overlap: int = 1200     # render px
    src_cell: int = 600     # render px ...
    model_cell: int = 512   # ... per model px (the training scale)

    @property
    def stride(self) -> int:
        return self.size - self.overlap

    @property
    def k(self) -> float:
        return self.model_cell / self.src_cell

    @property
    def model_size(self) -> int:
        return model_px(self.size, self.k)

    def to_model(self, render_px: int) -> int:
        v = render_px * self.k
        if abs(v - round(v)) > 1e-6:
            raise ValueError(f"{render_px} render px is not a whole number of model px at {self.k}")
        return round(v)


def model_px(render_px: int, k: float) -> int:
    """Model size of a render length: a multiple of 16 so latents pack into 2x2 patches."""
    return max(16, round(render_px * k / 16) * 16)


def grid_from_settings(s: dict | None = None) -> Grid:
    s = s or config.settings()
    return Grid(s["blocks"]["size"], s["blocks"]["overlap"], s["scale"]["srcCell"], s["scale"]["modelCell"])


def axis(start: int, n: int, size: int, stride: int) -> list[int]:
    """Block starts along one axis: through `start`, every `stride`, covering [0, n)."""
    p = start
    while p > -stride:
        p -= stride
    p += stride
    ps = []
    while p < n:
        ps.append(p)
        p += stride
    return ps


def overlaps(a: dict, b: dict) -> bool:
    return abs(a["x0"] - b["x0"]) < a["w"] and abs(a["y0"] - b["y0"]) < a["h"]


def plan_blocks(W: int, H: int, anchor: tuple[int, int], grid: Grid, mask: np.ndarray, s: int,
                min_fill: float = 0.03, landmarks: list[dict] | None = None) -> list[dict]:
    """Blocks covering a W x H map, b00 centred on `anchor`, numbered by distance from it. `mask` is True where
    the map has data, at 1/s scale; blocks with less than `min_fill` data are left out."""
    size = grid.size
    ax, ay = anchor[0] - size // 2, anchor[1] - size // 2
    blocks = []
    for y0 in axis(ay, H, size, grid.stride):
        for x0 in axis(ax, W, size, grid.stride):
            a = mask[max(0, y0) // s:max(0, y0 + size) // s, max(0, x0) // s:max(0, x0 + size) // s]
            fill = float(a.mean()) if a.size else 0.0
            if fill < min_fill:
                continue
            dist = math.hypot(x0 - ax, y0 - ay)
            blocks.append({"x0": x0, "y0": y0, "w": size, "h": size, "fill": round(fill, 3), "dist": round(dist)})
    blocks.sort(key=lambda b: (b["dist"], b["y0"], b["x0"]))
    for i, b in enumerate(blocks):
        b["id"] = f"b{i:02d}"
    for i, b in enumerate(blocks):
        b["before"] = [o["id"] for o in blocks[:i] if overlaps(o, b)]
        b["landmarks"] = [lm["slug"] for lm in (landmarks or [])
                          if b["x0"] <= lm["x"] < b["x0"] + size and b["y0"] <= lm["y"] < b["y0"] + size]
    return blocks


def plan_hash(blocks: list[dict], W: int, H: int, grid: Grid) -> str:
    key = json.dumps({"W": W, "H": H, "grid": [grid.size, grid.overlap, grid.src_cell, grid.model_cell],
                      "blocks": [[b["id"], b["x0"], b["y0"]] for b in blocks]}, sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def data_mask(level: int = 10, tiles: Path = config.TILES_DIR, tile: int = 512) -> tuple[np.ndarray, int]:
    """Where the map has data (not the empty edge), from a small level of the committed tiles; and its scale."""
    W, H = source.canvas_size()
    top = math.ceil(math.log2(max(W, H)))
    s = 2 ** (top - level)
    w, h = math.ceil(W / s), math.ceil(H / s)
    im = Image.new("RGB", (w, h), config.BG)
    for f in (tiles / str(level)).glob("*.webp"):
        tx, ty = map(int, f.stem.split("_"))
        with Image.open(f) as t:
            im.paste(t.convert("RGB"), (tx * tile, ty * tile))
    a = np.asarray(im).astype(int)
    return np.abs(a - np.array(config.BG)).sum(axis=2) > 24, s


def make_plan() -> dict:
    grid = grid_from_settings()
    W, H = source.canvas_size()
    slug, x, y = config.opening_place()
    mask, s = data_mask()
    blocks = plan_blocks(W, H, (x, y), grid, mask, s, config.settings()["blocks"]["minFill"], config.landmarks())
    return {"canvas": [W, H], "grid": {"size": grid.size, "overlap": grid.overlap, "srcCell": grid.src_cell,
                                       "modelCell": grid.model_cell, "modelSize": grid.model_size},
            "anchor": {"slug": slug, "x": x, "y": y}, "plan": plan_hash(blocks, W, H, grid), "blocks": blocks}


def load_plan(write: bool = False) -> dict:
    """The block plan: data/work/blocks.json if it is current, otherwise made now (and written if asked)."""
    p = make_plan()
    if config.BLOCKS_JSON.exists():
        old = json.loads(config.BLOCKS_JSON.read_text())
        if old.get("plan") == p["plan"] and not write:
            return old
    if write:
        config.BLOCKS_JSON.parent.mkdir(parents=True, exist_ok=True)
        config.BLOCKS_JSON.write_text(json.dumps(p, indent=1))
    return p


def get(plan: dict, bid: str) -> dict:
    for b in plan["blocks"]:
        if b["id"] == bid:
            return b
    raise SystemExit(f"no block {bid!r} in the plan (python paint/blocks.py plan)")


def cmd_plan(_args) -> None:
    p = load_plan(write=True)
    W, H = p["canvas"]
    mask, s = data_mask()
    k = 1 / 32
    im = Image.fromarray((mask * 255).astype(np.uint8)).resize((round(W * k), round(H * k))).convert("RGB")
    d = ImageDraw.Draw(im)
    for b in p["blocks"]:
        box = [b["x0"] * k, b["y0"] * k, (b["x0"] + b["w"]) * k, (b["y0"] + b["h"]) * k]
        d.rectangle(box, outline=(239, 51, 64) if b["id"] == "b00" else (60, 120, 210), width=2)
        d.text((box[0] + 4, box[1] + 4), b["id"], fill=(239, 51, 64))
    im.save(config.WORK / "blocks.jpg", quality=90)
    fill = sum(b["fill"] for b in p["blocks"])
    print(f"{len(p['blocks'])} blocks of {p['grid']['size']} px (overlap {p['grid']['overlap']}), b00 centred on "
          f"{p['anchor']['slug']}; {fill:.1f} full blocks of data. Plan {p['plan']} -> {config.BLOCKS_JSON}")
    for b in p["blocks"]:
        print(f"  {b['id']}  x0 {b['x0']:6d}  y0 {b['y0']:6d}  fill {b['fill']:4.0%}  after {','.join(b['before']) or '-'}"
              f"  {' '.join(b['landmarks'])}")


def states(plan: dict, claims: list[dict], subs: dict, version: str = "day") -> list[dict]:
    """Per block: done (accepted submission), claimed, ready (free, every earlier neighbour done) or waiting.
    A version other than day also needs the block's own day canvas."""
    claimed = {c["block"]: c for c in claims if c["version"] == version}
    done = {bid for bid, v in subs if v == version}
    day_done = {bid for bid, v in subs if v == "day"}
    out = []
    for b in plan["blocks"]:
        bid = b["id"]
        need = [o for o in b["before"] if o not in done]
        if version != "day" and bid not in day_done:
            need = [f"{bid} (day)"] + need
        if bid in done:
            st, detail = "done", f"by {subs[(bid, version)]['by']}"
        elif bid in claimed:
            c = claimed[bid]
            st = "claimed"
            detail = f"by {c['by']} since {c['since']}" + (" (stale)" if registry.is_stale(c) else "")
            if c.get("issue"):
                detail += f", issue #{c['issue']}"
        elif need:
            st, detail = "waiting", "needs " + ", ".join(need)
        else:
            st, detail = "ready", "free to claim"
        out.append({"id": bid, "state": st, "detail": detail, "fill": b["fill"], "landmarks": b["landmarks"]})
    return out


def cmd_status(args) -> None:
    plan = load_plan()
    versions = set(config.settings()["versions"])
    rows = states(plan, registry.load_claims(versions=versions), registry.load_submissions(), args.version)
    print(f"{args.version}: plan {plan['plan']}, {len(rows)} blocks")
    for r in rows:
        lm = r["landmarks"]
        more = f" +{len(lm) - 3}" if len(lm) > 3 else ""
        print(f"  {r['id']}  {r['state']:8s} {r['detail']}" + (f"  [{' '.join(lm[:3])}{more}]" if lm else ""))
    n = {s: sum(1 for r in rows if r["state"] == s) for s in ("done", "claimed", "ready", "waiting")}
    print("  " + ", ".join(f"{v} {k}" for k, v in n.items()))


# --- inputs for one block -----------------------------------------------------------------------------------


def prompt_hints(b: dict, grid: Grid, k: float) -> list[dict]:
    """Landmark hints inside the block, at model px (prompts/landmarks.json; windows that contain the point get
    the hint after the base prompt)."""
    hints = json.loads((config.PROMPTS / "landmarks.json").read_text(encoding="utf-8"))["hints"]
    out = []
    for lm in config.landmarks():
        text = hints.get(lm["slug"])
        if text and b["x0"] <= lm["x"] < b["x0"] + b["w"] and b["y0"] <= lm["y"] < b["y0"] + b["h"]:
            out.append({"slug": lm["slug"], "x": round((lm["x"] - b["x0"]) * k), "y": round((lm["y"] - b["y0"]) * k),
                        "text": text})
    return out


def base_prompt(version: str) -> str:
    v = config.version(version)
    text = (config.PROMPTS / v["prompt"]).read_text(encoding="utf-8").strip()
    return text.replace("{style}", config.settings()["trigger"])


def canvas_path(bid: str, version: str, root: Path | None = None) -> Path:
    return (root or config.PAINT_WORK) / bid / f"canvas_{version}.png"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def known(plan: dict, bid: str, version: str, grid: Grid, root: Path | None = None) -> dict[str, str]:
    """known_<version>.png and keep_<version>.png (model scale) from the finished neighbours' canvases found in
    <root>/<id>/canvas_<version>.png. Returns {neighbour id: sha256 of its canvas}."""
    root = root or config.PAINT_WORK
    b = get(plan, bid)
    M = grid.model_size
    canvas = Image.new("RGB", (M, M), config.BG)
    keep = Image.new("L", (M, M), 0)
    used = {}
    for o in plan["blocks"]:
        if o["id"] == bid or not overlaps(o, b):
            continue
        p = canvas_path(o["id"], version, root)
        if not p.exists():
            continue
        dx, dy = grid.to_model(o["x0"] - b["x0"]), grid.to_model(o["y0"] - b["y0"])
        with Image.open(p) as im:
            if im.size != (M, M):
                raise SystemExit(f"{p}: {im.size}, expected {M} x {M}")
            canvas.paste(im.convert("RGB"), (dx, dy))
        keep.paste(255, (max(dx, 0), max(dy, 0), min(dx + M, M), min(dy + M, M)))
        used[o["id"]] = sha256(p)
    d = root / bid
    d.mkdir(parents=True, exist_ok=True)
    for f in (d / f"known_{version}.png", d / f"keep_{version}.png"):
        f.unlink(missing_ok=True)
    if used:
        canvas.save(d / f"known_{version}.png")
        keep.save(d / f"keep_{version}.png")
        print(f"{bid} {version}: holds {', '.join(sorted(used))} ({np.asarray(keep).mean() / 255:.0%} of the block)")
    else:
        print(f"{bid} {version}: no finished neighbours here, painted from scratch")
    return used


def prepare(plan: dict, bid: str, version: str = "day", grid: Grid | None = None, root: Path | None = None,
            work: Path = config.WORK) -> Path:
    """Write the painter's inputs for one block into <root>/<bid>/."""
    grid = grid or grid_from_settings()
    root = root or config.PAINT_WORK
    b = get(plan, bid)
    d = root / bid
    d.mkdir(parents=True, exist_ok=True)
    v = config.version(version)
    if version == "day":
        warn = source.check_render_matches(work)
        if warn:
            raise SystemExit(warn)
        if not source.has_render(work):
            print("note: no local render (data/work/frames): the source is cut from the committed tiles, and there "
                  "is no layout map. Render the map first for real painting (README, 'How the map is made').")
        src = source.render(b["x0"], b["y0"], b["w"], b["h"], work)
        src.save(d / "src.png")
        kind = source.kinds(b["x0"], b["y0"], b["w"], b["h"], work)
        if kind is not None:
            kind.save(d / "kind.png")
            if v.get("layout"):
                Image.fromarray(source.layout_from_kinds(np.asarray(kind))).save(d / "layout.png")
        k = grid.k
    else:
        day = canvas_path(bid, v.get("from", "day"), root)
        if not day.exists():
            raise SystemExit(f"{bid}: the {version} version starts from its day canvas, {day} is missing "
                             f"(python paint/blocks.py fetch {bid} --version day --self)")
        k = 1.0
    used = known(plan, bid, version, grid, root)
    hints = prompt_hints(b, grid, grid.k)
    job = {"block": bid, "version": version, "plan": plan["plan"], "x0": b["x0"], "y0": b["y0"], "size": b["w"],
           "model_size": grid.model_size, "src_cell": grid.src_cell if version == "day" else grid.model_cell,
           "model_cell": grid.model_cell, "scale": k, "prompt": base_prompt(version), "hints": hints,
           "neighbours": used, "settings": v}
    (d / f"job_{version}.json").write_text(json.dumps(job, indent=1, ensure_ascii=False))
    print(f"{bid} {version}: inputs ready in {d}")
    return d


def download(url: str, dest: Path, sha: str | None = None, max_bytes: int | None = None) -> Path:
    """Download (https or file) to dest, checking the size limit and the checksum."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    n = 0
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:  # noqa: S310 (https/file only)
        for chunk in iter(lambda: r.read(1 << 20), b""):
            n += len(chunk)
            if max_bytes and n > max_bytes:
                tmp.unlink(missing_ok=True)
                raise SystemExit(f"{url}: larger than {max_bytes / 1e6:.0f} MB, refused")
            h.update(chunk)
            f.write(chunk)
    if sha and h.hexdigest() != sha:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"{url}: checksum {h.hexdigest()} does not match {sha}")
    tmp.replace(dest)
    return dest


def fetch(plan: dict, bid: str, version: str, include_self: bool = False, root: Path | None = None) -> list[str]:
    """Download the accepted canvases of a block's neighbours (and, with include_self, its own) for a version."""
    root = root or config.PAINT_WORK
    subs = registry.load_submissions()
    b = get(plan, bid)
    max_b = int(config.settings()["release"]["maxCanvasMB"] * 1e6)
    got = []
    for o in plan["blocks"]:
        if not (overlaps(o, b) and (o["id"] != bid or include_self)):
            continue
        s = subs.get((o["id"], version))
        if not s:
            continue
        dest = canvas_path(o["id"], version, root)
        if dest.exists() and sha256(dest) == s["sha256"]:
            got.append(o["id"])
            continue
        print(f"downloading {o['id']} {version} from {s['url']}", flush=True)
        download(s["url"], dest, s["sha256"], max_b)
        got.append(o["id"])
    print(f"{bid} {version}: have {', '.join(got) or 'nothing'}")
    return got


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    s = sub.add_parser("status")
    s.add_argument("--version", default="day")
    for name in ("prepare", "fetch"):
        s = sub.add_parser(name)
        s.add_argument("block")
        s.add_argument("--version", default="day")
        if name == "fetch":
            s.add_argument("--self", dest="include_self", action="store_true", help="also the block's own canvas")
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        cmd_plan(args)
    elif args.cmd == "status":
        cmd_status(args)
    elif args.cmd == "prepare":
        prepare(load_plan(), args.block, args.version)
    elif args.cmd == "fetch":
        fetch(load_plan(), args.block, args.version, args.include_self)


if __name__ == "__main__":
    main()
