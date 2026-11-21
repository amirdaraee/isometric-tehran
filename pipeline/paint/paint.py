"""Paint a block of the map: estimate, run (on your GPU or a rented one), submit; and a dry run for reviewers.

  python paint/paint.py estimate b03 [--version day]              time and cost, per GPU
  python paint/paint.py run b03 --backend local [--memory nf4]    on this machine's CUDA GPU (free)
  python paint/paint.py run b03 --backend runpod --yes            on a pod rented on YOUR RunPod account (paid)
  python paint/paint.py submit b03 --by <github-user> [--upload]  checksum, submission JSON, upload to your fork
  python paint/paint.py dry-run                                   the whole flow on the CPU with a stand-in model

run fetches the accepted neighbours, prepares the inputs (blocks.py prepare), paints, and cleans up the canvas
(post.py). Everything is in data/work/paint/<block>/. See docs/PAINTING.md for the whole flow.
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
from pathlib import Path

import numpy as np
from PIL import Image

from paint import blocks, config, registry

Image.MAX_IMAGE_PIXELS = None
PIPE = Path(__file__).resolve().parent.parent

# Seconds per window (one 1024 px window, one model call, no guidance). Measured: Isometric Luxembourg on an H200
# (Modal), 0.85-1.15 s with Lightning. Everything else is an ESTIMATE from relative GPU speed, to be replaced by
# contributors' measurements (the painter logs it, and the submission records it).
GPUS = {
    "H200":         {"vram": 141, "memory": "full",    "s": 0.9,  "measured": True},
    "H100":         {"vram": 80,  "memory": "full",    "s": 1.2,  "measured": False},
    "A100-80GB":    {"vram": 80,  "memory": "full",    "s": 2.32, "measured": True},  # b00, RunPod, 63.6 GB peak
    "L40S":         {"vram": 48,  "memory": "offload", "s": 3.5,  "measured": False},
    "RTX 6000 Ada": {"vram": 48,  "memory": "offload", "s": 3.5,  "measured": False},
    "RTX A6000":    {"vram": 48,  "memory": "offload", "s": 5.0,  "measured": False},
    "RTX 4090":     {"vram": 24,  "memory": "nf4",     "s": 6.0,  "measured": False},
    "RTX 3090":     {"vram": 24,  "memory": "nf4",     "s": 10.0, "measured": False},
}
CFG_FACTOR = 1.6       # measured on Luxembourg: 1.45 s per window at cfg 3 against 0.9 s without guidance
LOAD_MIN = 4           # model load and conditioning, minutes (estimate)
POD_GPU = "A100-80GB"  # the slowest GPU runpod.gpuTypes may give: caps assume it


def window_runs(bid: str, version: str, root: Path | None = None) -> tuple[int, int]:
    """(model calls, windows) for a prepared block, skipping held windows like the painter does."""
    from paint.md_core import Plan, hold_mask

    d = (root or config.PAINT_WORK) / bid
    job = json.loads((d / f"job_{version}.json").read_text(encoding="utf-8"))
    M = job["model_size"]
    v = job["settings"]
    src = Image.open(d / ("src.png" if version == "day" else f"canvas_{v.get('from', 'day')}.png")).convert("RGB")
    small = np.asarray(src.resize((M // 8, M // 8), Image.BOX), np.int16)
    bg = np.abs(small - np.array(config.BG)).sum(axis=2) < 6
    keep = bg.copy()
    if (d / f"keep_{version}.png").exists():
        keep |= np.asarray(Image.open(d / f"keep_{version}.png").convert("L").resize((M // 8, M // 8))) > 127
    hold = hold_mask(np.repeat(np.repeat(keep, 8, 0), 8, 1), config.settings()["band"])
    steps = v["steps"]
    plan = Plan(H=M // 8, W=M // 8, sigmas=[1.0] * steps + [0.0])
    return plan.window_runs(hold), len(plan.windows(hold))


def minutes(runs: int, gpu: str, cfg: float) -> float:
    return runs * GPUS[gpu]["s"] * (CFG_FACTOR if cfg > 1 else 1) / 60 + LOAD_MIN


def pod_hours(runs: int, cfg: float) -> tuple[float, float]:
    """(expected hours, cap hours) on a pod: setup + download + painting on the slowest allowed GPU."""
    setup = config.settings()["runpod"]["setupMinutes"]
    exp = (setup + minutes(runs, POD_GPU, cfg)) / 60
    return exp, exp * 1.5 + 0.25


def print_estimate(bid: str, version: str) -> tuple[int, float]:
    v = config.version(version)
    runs, wins = window_runs(bid, version)
    cfg = v["cfg"]
    print(f"{bid} {version}: {wins} windows, {runs} model calls over {v['steps']} steps"
          + (f", cfg {cfg} (two calls each)" if cfg > 1 else ", no guidance"))
    print("  GPU            VRAM  profile   minutes (est.)")
    for g, x in GPUS.items():
        tag = "speed measured" if x["measured"] else "estimate"
        print(f"  {g:13s} {x['vram']:4d}  {x['memory']:8s}  {minutes(runs, g, cfg):5.0f}  ({tag})")
    exp, cap = pod_hours(runs, cfg)
    print(f"  RunPod: about {exp * 60:.0f} min with setup; capped at {cap:.2f} h (cost = hours x the pod's price)")
    return runs, cfg


def ensure_inputs(plan: dict, bid: str, version: str, force: bool) -> Path:
    rows = {r["id"]: r for r in blocks.states(plan, registry.load_claims(), registry.load_submissions(), version)}
    r = rows.get(bid) or blocks.get(plan, bid)
    if r.get("state") == "waiting" and not force:
        raise SystemExit(f"{bid} is waiting: {r['detail']}. Painting it now would not hold those neighbours and "
                         f"leave seams; pick a ready block (blocks.py status), or pass --force for a test.")
    if r.get("state") == "done" and not force:
        raise SystemExit(f"{bid} {version} is already done; pass --force to paint it again anyway")
    blocks.fetch(plan, bid, version)
    if version != "day" and not blocks.canvas_path(bid, "day").exists():
        blocks.fetch(plan, bid, "day", include_self=True)
    return blocks.prepare(plan, bid, version)


def postprocess(d: Path, version: str) -> None:
    from paint import post

    c = d / f"canvas_{version}.png"
    rgb = np.asarray(Image.open(c).convert("RGB"))
    if version != "day":
        rgb, n = post.despeckle(rgb)
        print(f"despeckle: {n} magenta px filled")
    else:
        lab = config.settings().get("water", {}).get("lab")
        if lab is not None and (d / "kind.png").exists():
            rgb, n = post.water_tone(rgb, post.water_mask(d / "kind.png", (rgb.shape[1], rgb.shape[0])), lab)
            print(f"water: {n} px toned")
    Image.fromarray(rgb).save(c)


def cmd_estimate(args) -> int:
    plan = blocks.load_plan()
    if not (config.PAINT_WORK / args.block / f"job_{args.version}.json").exists():
        ensure_inputs(plan, args.block, args.version, force=True)
    print_estimate(args.block, args.version)
    return 0


def cmd_run(args) -> int:
    if args.backend == "runpod" and len(args.block) > 1:
        return run_session(args)
    for bid in args.block:
        rc = run_one(args, bid)
        if rc != 0:
            return rc
    return 0


def run_session(args) -> int:
    """Several blocks in order on one pod: the model is installed and downloaded once (runpod.session)."""
    from paint import runpod

    plan = blocks.load_plan()
    extra = ["--memory", "full"] + (["--model", "stub"] if args.stub else [])
    total = 0.0
    for n, bid in enumerate(args.block):  # inputs as they are now, for the estimate; prepared again before painting
        ensure_inputs(plan, bid, args.version, args.force)
        runs, cfg = print_estimate(bid, args.version)
        total += pod_hours(runs, cfg)[0] if n == 0 else minutes(runs, POD_GPU, cfg) / 60
    cap = args.max_cost if args.max_cost is not None else float("inf")
    print(f"\nRunPod, on your account (RUNPOD_API_KEY): ONE pod for {len(args.block)} blocks at up to "
          f"${args.max_price:.2f}/h.\n  expected about {total * 60:.0f} min, about ${total * args.max_price:.2f} at that "
          f"price;\n  every block has its own deadline (the pod ends itself if left alone)"
          + (f"; no block starts that could take the pod past ${cap:.2f}" if args.max_cost is not None else "") + ".")
    if not args.yes:
        print("Nothing rented. Add --yes to rent the pod and paint.")
        return 1

    def hours_each(bid: str, first: bool) -> float:
        runs, wins = window_runs(bid, args.version)
        cfg = config.version(args.version)["cfg"]
        if first:
            return pod_hours(runs, cfg)[1]
        return minutes(runs, POD_GPU, cfg) / 60 * 1.5 + 0.25

    def finish(bid: str, d: Path) -> None:
        postprocess(d, args.version)
        print(f"painted: {d / f'canvas_{args.version}.png'}", flush=True)

    done = runpod.session(args.block, args.version, extra, hours_each, args.max_price,
                          lambda bid: ensure_inputs(plan, bid, args.version, args.force), finish, args.gpu,
                          args.max_cost)
    print(f"session: painted {', '.join(done) or 'nothing'}; not painted: "
          f"{', '.join(b for b in args.block if b not in done) or 'none'}")
    return 0 if len(done) == len(args.block) else 1


def run_one(args, bid: str) -> int:
    plan = blocks.load_plan()
    d = ensure_inputs(plan, bid, args.version, args.force)
    runs, cfg = print_estimate(bid, args.version)
    extra = ["--memory", args.memory] + (["--model", "stub"] if args.stub else [])
    if args.backend == "local":
        print(f"painting on this machine ({args.memory}); the state is saved after every step, so Ctrl-C and run "
              f"again resumes", flush=True)
        rc = subprocess.call([sys.executable, "-m", "paint.painter", "--block-dir", str(d), "--version",
                              args.version, *extra], cwd=PIPE)
    else:
        exp, cap = pod_hours(runs, cfg)
        print(f"\nRunPod, on your account (RUNPOD_API_KEY): one pod at up to ${args.max_price:.2f}/h.\n"
              f"  expected about {exp * 60:.0f} min, about ${exp * args.max_price:.2f} at that price;\n"
              f"  at most {cap:.2f} h = ${cap * args.max_price:.2f} (the pod deletes itself at the deadline).")
        if not args.yes:
            print("Nothing rented. Add --yes to rent the pod and paint.")
            return 1
        from paint import runpod

        rc = runpod.run(d, args.version, extra, cap, args.max_price, args.gpu)
    if rc == 3:
        print("paused with the state saved: run the same command again to resume")
        return 3
    if rc != 0 or not (d / f"canvas_{args.version}.png").exists():
        print(f"painting failed (exit {rc}); see the log above" + (f" and {d}/pod_{args.version}.log"
                                                                     if args.backend == "runpod" else ""))
        return rc or 1
    postprocess(d, args.version)
    print(f"\npainted: {d / f'canvas_{args.version}.png'}\nlook at it, then: python paint/paint.py submit "
          f"{bid} --version {args.version} --by <your GitHub user>")
    return 0


def make_submission(plan: dict, bid: str, version: str, by: str, url: str, root: Path | None = None,
                    allow_file: bool = False) -> dict:
    d = (root or config.PAINT_WORK) / bid
    c = d / f"canvas_{version}.png"
    job = json.loads((d / f"job_{version}.json").read_text(encoding="utf-8"))
    st_f = d / f"live_{version}" / "status.json"
    st = json.loads(st_f.read_text()) if st_f.exists() else {}
    with Image.open(c) as im:
        w, h = im.size
    s = config.settings()
    v = job["settings"]
    lo = s["loras"][v["lora"]]
    sub = {"block": bid, "version": version, "by": by, "url": url, "sha256": blocks.sha256(c),
           "bytes": c.stat().st_size, "width": w, "height": h, "plan": job["plan"], "x0": job["x0"], "y0": job["y0"],
           "size": job["size"], "neighbours": job["neighbours"],
           "settings": {"model": s["model"]["repo"], "lora": f"{lo['repo']}/{lo['file']}", "steps": st.get("steps"),
                        "mode": st.get("mode"), "seed": s["seed"], "minutes": st.get("minutes"),
                        "per_window_s": st.get("per_window_s"), "peak_gpu_gb": st.get("peak_gpu_gb")},
           "licence": registry.LICENCE, "agree": True}
    return registry.parse_submission(sub, allow_file=allow_file)


def cmd_submit(args) -> int:
    plan = blocks.load_plan()
    d = config.PAINT_WORK / args.block
    c = d / f"canvas_{args.version}.png"
    if not c.exists():
        raise SystemExit(f"{c} is missing: paint it first")
    repo_name = config.city()["repo"].rstrip("/").rsplit("/", 1)[-1]
    tag, name = f"paint-{args.block}-{args.version}", f"{args.block}-{args.version}.png"
    url = args.url or f"https://github.com/{args.by}/{repo_name}/releases/download/{tag}/{name}"
    sub = make_submission(plan, args.block, args.version, args.by, url)
    max_mb = config.settings()["release"]["maxCanvasMB"]
    if sub["bytes"] > max_mb * 1e6:
        raise SystemExit(f"{sub['bytes'] / 1e6:.0f} MB is over {max_mb} MB: is this the canvas the painter wrote?")
    dest = config.SUBMISSIONS / registry.submission_name(args.block, args.version)
    dest.write_text(json.dumps(sub, indent=1) + "\n")
    print(f"wrote {dest}")
    if args.upload:
        tmp = d / name
        shutil.copy(c, tmp)
        repo = f"{args.by}/{repo_name}"
        print(f"uploading {name} to a release {tag!r} on your fork {repo} (gh, your own login)")
        subprocess.check_call(["gh", "release", "create", tag, str(tmp), "--repo", repo, "--title",
                               f"Painted block {args.block} ({args.version})", "--notes",
                               "A painted block for Isometric Tehran, offered under CC BY-NC 4.0."])
        tmp.unlink()
    else:
        print(f"\nupload the canvas so it is at {url}:\n  cp {c} {name}\n  gh release create {tag} {name} "
              f"--repo {args.by}/{repo_name} --title 'Painted block {args.block} ({args.version})'\n"
              f"(or pass --upload, or pass --url with another GitHub release download URL)")
    print(f"then open a pull request that adds {dest.relative_to(config.ROOT)} (docs/PAINTING.md, 'Submit').")
    return 0


def cmd_dry_run(args) -> int:
    from paint.dry_run import dry_run

    return dry_run(Path(args.out) if args.out else config.WORK / "paint-dry")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("estimate")
    e.add_argument("block")
    e.add_argument("--version", default="day")
    r = sub.add_parser("run")
    r.add_argument("block", nargs="+", help="block id; several on RunPod: painted in order on one pod")
    r.add_argument("--version", default="day")
    r.add_argument("--backend", required=True, choices=["local", "runpod"])
    r.add_argument("--max-cost", type=float, help="RunPod session: start no block that could take the pod past this "
                   "many dollars")
    r.add_argument("--memory", default="full", choices=["full", "offload", "nf4"],
                   help="GPU memory profile (local; a pod always has 80 GB): full 80 GB, offload 48 GB, nf4 24 GB")
    r.add_argument("--max-price", type=float, default=3.0, help="RunPod: most you will pay per hour (USD)")
    r.add_argument("--yes", action="store_true", help="RunPod: really rent the pod (it costs money)")
    r.add_argument("--gpu", action="append", help="RunPod: GPU type to try, in order (repeat the option; default "
                   "paint.json runpod.gpuTypes), e.g. --gpu 'NVIDIA A100 80GB PCIe'")
    r.add_argument("--force", action="store_true", help="paint even if the block is waiting or done")
    r.add_argument("--stub", action="store_true", help=argparse.SUPPRESS)
    s = sub.add_parser("submit")
    s.add_argument("block")
    s.add_argument("--version", default="day")
    s.add_argument("--by", required=True, help="your GitHub user name")
    s.add_argument("--url", help="a GitHub release download URL for the canvas (default: a release on your fork)")
    s.add_argument("--upload", action="store_true", help="create that release with gh")
    d = sub.add_parser("dry-run")
    d.add_argument("--out", help="folder for the dry run (default data/work/paint-dry)")
    args = ap.parse_args(argv)
    if args.cmd == "run" and args.backend == "runpod" and args.memory != "full":
        args.memory = "full"
    return {"estimate": cmd_estimate, "run": cmd_run, "submit": cmd_submit, "dry-run": cmd_dry_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
