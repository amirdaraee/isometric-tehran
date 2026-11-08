"""Join rendered frames into a deep-zoom pyramid for the web viewer (OpenSeadragon).

    python build_tiles.py      data/work/frames + mosaic.json -> web/public/tiles/map_files/<level>/<x>_<y>.webp, meta.json

Painted blocks listed in data/work/tiles/painted.json (written by paint/review.py blend) are laid over the render
with feathered edges (paint/blend.py), so they fade into each other and into the unpainted render.
"""
import json
import shutil

import pyvips

from common import ROOT, WORK, city
from paint.blend import composite

TILE = int(city()["tileSize"])

OUT = ROOT / "web" / "public" / "tiles"


def main():
    m = json.loads((WORK / "mosaic.json").read_text())
    frames = [pyvips.Image.new_from_file(str(WORK / "frames" / f"{c}_{r}.jpg"), access="sequential")
              for r in range(m["rows"]) for c in range(m["cols"])]
    mosaic = pyvips.Image.arrayjoin(frames, across=m["cols"])
    # Painted blocks laid over the render: data/work/tiles/painted.json lists {"file", "x0", "y0", "w", "h"} in
    # mosaic pixels ("file" relative to data/work/tiles), in plan order, and the feather in map px.
    pj = WORK / "tiles" / "painted.json"
    pdata = json.loads(pj.read_text()) if pj.exists() else {}
    painted = pdata.get("patches", []) if isinstance(pdata, dict) else pdata
    if painted:
        mosaic = composite(mosaic, painted, int(pdata.get("feather", 600)) if isinstance(pdata, dict) else 600,
                           WORK / "tiles")
        print("painted blocks:", ", ".join(p.get("block", p["file"]) for p in painted))
    # Replace only the day map: the other painted versions (build_variant_tiles.py) live next to it and stay.
    OUT.mkdir(parents=True, exist_ok=True)
    old_meta = json.loads((OUT / "meta.json").read_text()) if (OUT / "meta.json").exists() else {}
    for p in (OUT / "map_files", OUT / "map.dzi"):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    mosaic.dzsave(str(OUT / "map"), tile_size=TILE, overlap=0, suffix=".webp[Q=78,effort=4]", layout="dz")
    # Levels below one tile are never requested: the viewer stops at the 512 px level.
    for d in (OUT / "map_files").iterdir():
        if d.is_dir() and int(d.name) < 9:
            shutil.rmtree(d)
    meta = {"width": mosaic.width, "height": mosaic.height, "tileSize": TILE, "mpp": m["mpp"],
            "art": "painted" if painted else "preview",
            # where the map is painted (the viewer notes the rest as a draft); no local file paths
            "painted": [{k: p[k] for k in ("block", "x0", "y0", "w", "h") if k in p} for p in painted]}
    if old_meta.get("variants"):
        meta["variants"] = old_meta["variants"]
    (OUT / "meta.json").write_text(json.dumps(meta))
    print("pyramid", mosaic.width, mosaic.height)


if __name__ == "__main__":
    main()
