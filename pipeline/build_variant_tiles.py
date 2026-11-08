"""Tile pyramid for a painted version of the map (golden, night): the painted blocks of that version inside, the
rest of the map graded to match, so switching to night darkens the whole map, not only the painted part.

    python build_variant_tiles.py night      data/work/tiles/patches_night.json -> web/public/tiles/map_night_files

The patches file is written by paint/review.py blend --version night: {"feather": 600, "patches": [{"file", "x0",
"y0", "w", "h", "block"}]}. Outside the version's blocks, the graded base is the painted day map
(data/work/tiles/painted.json over the render), so a block painted by day but not yet in this version still looks
like the same drawing. Ported from Isometric Luxembourg.
"""
import json
import shutil
import sys

import pyvips

from common import ROOT, WORK, city
from paint.blend import composite

TILE = int(city()["tileSize"])
OUT = ROOT / "web" / "public" / "tiles"


def bj(b):
    return b[0].bandjoin(b[1:])


def lum(im):
    return im[0] * 0.3 + im[1] * 0.55 + im[2] * 0.15


GRADE = {
    "night": lambda im: bj([lum(im) * 0.16 + 4, lum(im) * 0.2 + 8, lum(im) * 0.34 + 18]),
    "golden": lambda im: bj([im[0] * 1.12 + 10, im[1] * 0.98 + 2, im[2] * 0.72]),
}


def patches_of(path) -> tuple[list[dict], int]:
    d = json.loads(path.read_text()) if path.exists() else {}
    if isinstance(d, list):
        return d, 600
    return d.get("patches", []), int(d.get("feather", 600))


def main(variant: str) -> None:
    if variant not in GRADE:
        raise SystemExit(f"variant must be one of {', '.join(GRADE)}")
    m = json.loads((WORK / "mosaic.json").read_text())
    frames = [pyvips.Image.new_from_file(str(WORK / "frames" / f"{c}_{r}.jpg"), access="sequential")
              for r in range(m["rows"]) for c in range(m["cols"])]
    mosaic = pyvips.Image.arrayjoin(frames, across=m["cols"])
    day, f = patches_of(WORK / "tiles" / "painted.json")
    if day:
        mosaic = composite(mosaic, day, f, WORK / "tiles")
    mosaic = mosaic.cast("float")
    graded = GRADE[variant](mosaic).cast("uchar")
    # keep the empty map edge dark in every version
    bg = (mosaic[0] - 16).abs() + (mosaic[1] - 24).abs() + (mosaic[2] - 40).abs() < 20
    graded = bg.ifthenelse([16, 24, 40] if variant != "night" else [6, 9, 18], graded)
    mine, f = patches_of(WORK / "tiles" / f"patches_{variant}.json")
    graded = composite(graded, mine, f, WORK / "tiles")
    dest = OUT / f"map_{variant}"
    shutil.rmtree(str(dest) + "_files", ignore_errors=True)
    graded.dzsave(str(dest), tile_size=TILE, overlap=0, suffix=".webp[Q=82,effort=4]", layout="dz")
    for d in (OUT / f"map_{variant}_files").iterdir():
        if d.is_dir() and int(d.name) < 9:
            shutil.rmtree(d)
    (OUT / f"map_{variant}.dzi").unlink(missing_ok=True)
    meta = json.loads((OUT / "meta.json").read_text())
    meta.setdefault("variants", {})[variant] = f"map_{variant}_files"
    (OUT / "meta.json").write_text(json.dumps(meta))
    print("variant", variant, "done:", len(mine), "painted blocks")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
