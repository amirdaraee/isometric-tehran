"""Paths and settings for the painter: paint.json (model, style weights, block size, versions) plus city.json.

The style weights are downloaded from the Hugging Face Hub on first use (weights()), into the usual Hugging Face
cache (HF_HOME). Environment overrides, so a fork can point at its own copies without editing paint.json:

  PAINT_MODEL_REPO   base model repo        (default Qwen/Qwen-Image-Edit-2511)
  PAINT_STYLE_REPO   style LoRA repo        (default amirdaraee/isometric-ink-style)
  PAINT_LIGHT_REPO   light LoRA repo        (default the same repo)
  PAINT_LORA_DIR     a local folder holding the LoRA files: used instead of the Hub (offline, or before they are
                     published)
"""
from __future__ import annotations

import copy
import json
import os
from functools import cache
from pathlib import Path

from common import ROOT, WEB_DATA, WORK, city

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"
CLAIMS = HERE / "claims.json"
SUBMISSIONS = HERE / "submissions"
TILES_META = ROOT / "web" / "public" / "tiles" / "meta.json"
TILES_DIR = ROOT / "web" / "public" / "tiles" / "map_files"
LANDMARKS = WEB_DATA / "landmarks.json"
BLOCKS_JSON = WORK / "blocks.json"
PAINT_WORK = WORK / "paint"
BG = (16, 24, 40)   # the empty map edge (render.py)


@cache
def _settings() -> dict:
    return json.loads((HERE / "paint.json").read_text(encoding="utf-8"))


def settings() -> dict:
    """paint.json with the environment overrides applied (a copy: callers may change it)."""
    s = copy.deepcopy(_settings())
    if os.environ.get("PAINT_MODEL_REPO"):
        s["model"]["repo"] = os.environ["PAINT_MODEL_REPO"]
    if os.environ.get("PAINT_STYLE_REPO"):
        s["loras"]["style"]["repo"] = os.environ["PAINT_STYLE_REPO"]
    if os.environ.get("PAINT_LIGHT_REPO"):
        s["loras"]["light"]["repo"] = os.environ["PAINT_LIGHT_REPO"]
    return s


def version(name: str) -> dict:
    v = settings()["versions"]
    if name not in v:
        raise SystemExit(f"unknown version {name!r}; paint.json has {', '.join(v)}")
    return v[name]


def block_dir(bid: str) -> Path:
    return PAINT_WORK / bid


def weights(kind: str) -> str:
    """Local path of a LoRA file ("style", "light" or "lightning"), downloaded from the Hub if needed."""
    lo = settings()["loras"][kind]
    local = os.environ.get("PAINT_LORA_DIR")
    if local and (Path(local) / lo["file"]).exists():
        return str(Path(local) / lo["file"])
    from huggingface_hub import hf_hub_download  # paint extra; imported here so planning and CI never need it

    return hf_hub_download(lo["repo"], lo["file"])


def opening_place() -> tuple[str, int, int]:
    """The city's opening place (city.json openingPlace) and its position in map pixels (landmarks.json)."""
    slug = city()["openingPlace"]
    for lm in json.loads(LANDMARKS.read_text(encoding="utf-8")):
        if lm["slug"] == slug:
            return slug, int(lm["x"]), int(lm["y"])
    raise SystemExit(f"openingPlace {slug!r} is not in {LANDMARKS}")


def landmarks() -> list[dict]:
    """[{slug, x, y, name}] in map pixels."""
    return [{"slug": lm["slug"], "x": int(lm["x"]), "y": int(lm["y"]), "name": lm["name"]["en"]}
            for lm in json.loads(LANDMARKS.read_text(encoding="utf-8"))]
