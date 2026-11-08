"""Download the open data the map is built from, for the bbox in city.json only, into data/raw/ (not in git).

    python fetch_data.py all          # everything below, in order
    python fetch_data.py osm          # OpenStreetMap via the Overpass API          -> data/raw/osm/overpass.json
    python fetch_data.py overture     # Overture Maps buildings (GeoParquet)        -> data/raw/overture/buildings.parquet
    python fetch_data.py dem          # Copernicus GLO-30 DEM, the bbox window only -> data/raw/dem/glo30.tif
    python fetch_data.py wikidata     # Wikidata items with coordinates in the bbox, and the Wikipedia summaries of
                                      # the landmarks in pipeline/landmarks.json  -> data/raw/wikidata/*.json

Each step writes what it fetched, from where, when and under which licence to data/raw/SOURCES.json, so a
download can be traced and repeated. Re-running a step replaces its files. Needs the "fetch" extras
(uv pip install -r pyproject.toml --extra fetch).

Be kind to the public services: one request at a time, a clear User-Agent, and only the bbox.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from common import RAW, ROOT, bbox, city

UA = "isometric-tehran/0.1 (https://github.com/amirdaraee/isometric-tehran; open-source map project)"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
DEM_TILE = "Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM"
DEM_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{t}/{t}.tif"
DEM_PAD_DEG = 0.01  # a little terrain round the bbox, so the edges are not cut on a slope
LANDMARKS = Path(__file__).with_name("landmarks.json")


def now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def record(key: str, entry: dict) -> None:
    """Note a download in data/raw/SOURCES.json."""
    p = RAW / "SOURCES.json"
    src = json.loads(p.read_text()) if p.exists() else {}
    src[key] = {**entry, "fetched": now(), "bbox": city()["bbox"]}
    p.write_text(json.dumps(src, indent=2, ensure_ascii=False) + "\n")


def get(url: str, data: bytes | None = None, timeout: int = 300, tries: int = 3) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - retried, then raised
            if i == tries - 1:
                raise
            print(f"  retry {i + 1} after {e}", flush=True)
            time.sleep(10 * (i + 1))
    raise RuntimeError("unreachable")


def overpass_query() -> str:
    w, s, e, n = bbox()
    return f"""[out:json][timeout:300][bbox:{s},{w},{n},{e}];
(
  nwr["building"];
  nwr["building:part"];
  way["highway"];
  way["railway"];
  nwr["railway"~"^(station|halt|subway_entrance)$"];
  nwr["public_transport"="station"];
  nwr["natural"];
  nwr["waterway"];
  nwr["water"];
  nwr["landuse"];
  nwr["leisure"];
  nwr["amenity"];
  nwr["tourism"];
  nwr["historic"];
  nwr["heritage"];
  nwr["place"];
  nwr["man_made"];
  way["area:highway"];
);
out body geom qt;"""


def fetch_osm() -> None:
    out = RAW / "osm" / "overpass.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    q = overpass_query()
    for url in OVERPASS:
        try:
            print("overpass:", url, flush=True)
            body = get(url, urllib.parse.urlencode({"data": q}).encode(), timeout=400)
            d = json.loads(body)
            break
        except Exception as e:  # noqa: BLE001 - try the next mirror
            print("  failed:", e, flush=True)
    else:
        raise SystemExit("every Overpass mirror failed")
    out.write_bytes(body)
    stamp = d.get("osm3s", {}).get("timestamp_osm_base", "")
    print(f"  {len(d['elements'])} elements, {len(body) / 1e6:.1f} MB, data as of {stamp}")
    record("osm", {"source": url, "query": q, "file": str(out.relative_to(ROOT)), "data_timestamp": stamp,
                   "licence": "ODbL 1.0, © OpenStreetMap contributors", "elements": len(d["elements"])})


def fetch_overture() -> None:
    out = RAW / "overture" / "buildings.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    w, s, e, n = bbox()
    exe = Path(sys.executable).with_name("overturemaps")
    cmd = [str(exe), "download", f"--bbox={w},{s},{e},{n}", "-f", "geoparquet", "--type=building", "-o", str(out)]
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    release = ""
    try:
        from overturemaps import core

        release = getattr(core, "get_latest_release", lambda: "")() or ""
    except Exception:  # noqa: BLE001 - the release name is a nice-to-have
        pass
    import pyarrow.parquet as pq

    rows = pq.read_metadata(out).num_rows
    print(f"  {rows} buildings, {out.stat().st_size / 1e6:.1f} MB")
    record("overture", {"source": "Overture Maps Foundation, buildings theme (s3://overturemaps-us-west-2/release/)",
                        "release": release, "file": str(out.relative_to(ROOT)), "rows": rows,
                        "licence": "ODbL 1.0 (buildings theme; it includes OpenStreetMap data). Per-feature sources "
                                   "are in the 'sources' column."})


def fetch_dem() -> None:
    import rasterio
    from rasterio.windows import from_bounds

    out = RAW / "dem" / "glo30.tif"
    out.parent.mkdir(parents=True, exist_ok=True)
    w, s, e, n = bbox()
    w, s, e, n = w - DEM_PAD_DEG, s - DEM_PAD_DEG, e + DEM_PAD_DEG, n + DEM_PAD_DEG
    import math

    if math.floor(w) != math.floor(e) or math.floor(s) != math.floor(n):
        raise SystemExit("the bbox crosses a 1-degree DEM tile edge: add mosaicking here")
    t = DEM_TILE.format(lat=math.floor(s), lon=math.floor(w))
    url = DEM_URL.format(t=t)
    print("dem:", url, flush=True)
    with rasterio.Env(GDAL_HTTP_USERAGENT=UA, GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open("/vsicurl/" + url) as src:
            win = from_bounds(w, s, e, n, src.transform).round_offsets().round_lengths()
            z = src.read(1, window=win)
            prof = src.profile | {"driver": "GTiff", "width": z.shape[1], "height": z.shape[0],
                                  "transform": src.window_transform(win), "compress": "deflate", "tiled": False}
            prof.pop("blockxsize", None)
            prof.pop("blockysize", None)
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(z, 1)
    print(f"  {z.shape[1]} x {z.shape[0]} px, {float(z.min()):.0f}..{float(z.max()):.0f} m, {out.stat().st_size / 1e6:.2f} MB")
    record("dem", {"source": url, "product": "Copernicus DEM GLO-30 (DSM), public AWS bucket copernicus-dem-30m",
                   "file": str(out.relative_to(ROOT)), "window_deg": [w, s, e, n],
                   "licence": "Copernicus DEM licence: free of charge, worldwide, for any lawful use, with attribution "
                              "'© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under "
                              "COPERNICUS by the European Union and ESA; all rights reserved'"})


SPARQL = "https://query.wikidata.org/sparql"


def fetch_wikidata() -> None:
    out_dir = RAW / "wikidata"
    out_dir.mkdir(parents=True, exist_ok=True)
    w, s, e, n = bbox()
    q = f"""SELECT ?item ?coord ?en ?fa ?den ?dfa ?enwiki ?fawiki (GROUP_CONCAT(DISTINCT ?classLabel; separator="|") AS ?classes) WHERE {{
  SERVICE wikibase:box {{ ?item wdt:P625 ?coord.
    bd:serviceParam wikibase:cornerWest "Point({w} {s})"^^geo:wktLiteral.
    bd:serviceParam wikibase:cornerEast "Point({e} {n})"^^geo:wktLiteral. }}
  OPTIONAL {{ ?item rdfs:label ?en FILTER(LANG(?en) = "en") }}
  OPTIONAL {{ ?item rdfs:label ?fa FILTER(LANG(?fa) = "fa") }}
  OPTIONAL {{ ?item schema:description ?den FILTER(LANG(?den) = "en") }}
  OPTIONAL {{ ?item schema:description ?dfa FILTER(LANG(?dfa) = "fa") }}
  OPTIONAL {{ ?enwiki schema:about ?item; schema:isPartOf <https://en.wikipedia.org/> }}
  OPTIONAL {{ ?fawiki schema:about ?item; schema:isPartOf <https://fa.wikipedia.org/> }}
  OPTIONAL {{ ?item wdt:P31 ?class. ?class rdfs:label ?classLabel FILTER(LANG(?classLabel) = "en") }}
}} GROUP BY ?item ?coord ?en ?fa ?den ?dfa ?enwiki ?fawiki"""
    print("wikidata: items in the bbox", flush=True)
    body = get(SPARQL + "?" + urllib.parse.urlencode({"query": q, "format": "json"}), timeout=120)
    rows = json.loads(body)["results"]["bindings"]
    items = {}
    for r in rows:
        v = {k: x["value"] for k, x in r.items()}
        qid = v["item"].rsplit("/", 1)[1]
        lon, lat = map(float, v["coord"].removeprefix("Point(").rstrip(")").split())
        items.setdefault(qid, {"qid": qid, "lon": lon, "lat": lat, "en": v.get("en"), "fa": v.get("fa"),
                               "description_en": v.get("den"), "description_fa": v.get("dfa"),
                               "enwiki": v.get("enwiki"), "fawiki": v.get("fawiki"),
                               "classes": [c for c in v.get("classes", "").split("|") if c]})
    (out_dir / "items.json").write_text(json.dumps(sorted(items.values(), key=lambda x: x["qid"]), ensure_ascii=False, indent=1))
    print(f"  {len(items)} items")
    record("wikidata", {"source": SPARQL, "query": q, "file": "data/raw/wikidata/items.json", "items": len(items),
                        "licence": "Wikidata: CC0 1.0"})

    # Wikipedia summaries (first paragraph) of the landmarks, English and Persian.
    wanted = json.loads(LANDMARKS.read_text(encoding="utf-8"))
    summaries = {}
    for lm in wanted:
        it = items.get(lm["qid"]) or {}
        for lang in ("en", "fa"):
            url = it.get(f"{lang}wiki") or lm.get(f"{lang}wiki")
            if not url:
                continue
            title = urllib.parse.unquote(url.rsplit("/wiki/", 1)[1])
            api = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title, safe='')}"
            try:
                d = json.loads(get(api, timeout=60))
            except Exception as e:  # noqa: BLE001 - a missing summary is not fatal
                print("  no summary:", lang, title, e)
                continue
            summaries[f"{lm['qid']}:{lang}"] = {"title": d.get("title"), "extract": d.get("extract"),
                                               "url": d.get("content_urls", {}).get("desktop", {}).get("page", url),
                                               "revision": d.get("revision"), "timestamp": d.get("timestamp")}
            time.sleep(0.3)
    (out_dir / "summaries.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=1))
    print(f"  {len(summaries)} Wikipedia summaries")
    record("wikipedia", {"source": "Wikipedia REST API page/summary (en, fa)", "file": "data/raw/wikidata/summaries.json",
                         "pages": len(summaries), "licence": "CC BY-SA 4.0 (text), credited with a link on each card"})


STEPS = {"osm": fetch_osm, "overture": fetch_overture, "dem": fetch_dem, "wikidata": fetch_wikidata}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=[*STEPS, "all"])
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    for name, fn in STEPS.items():
        if a.what in (name, "all"):
            fn()


if __name__ == "__main__":
    main()
