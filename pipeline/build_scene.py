"""Build the 3D scene of the bbox from the raw data, for render.py.

    python build_scene.py            -> data/work/scene.npz, data/work/ground.png, data/work/ground_kind.png

  terrain   Copernicus GLO-30 (a surface model: it includes roofs and trees) smoothed into bare-ish ground: a grey
            opening removes the bumps of buildings, a Gaussian blur the 30 m steps. Sampled on a 10 m grid.
  ground    a colour image of the ground in local metres (GROUND_MPP per pixel): base soil, land use, parks,
            water, squares, then roads by class with their pavements. A second image holds the ground's kind
            (KINDS below), for the guide passes and the water mask.
  buildings Overture footprints (OpenStreetMap and Microsoft ML buildings, conflated by Overture), with the OSM
            tags joined in where Overture took the building from OSM. Heights: heights.py. Extruded from the
            lowest ground under the footprint to a flat roof; mosques get a dome.
  trees     OSM trees and tree rows, trees scattered in parks, gardens and woods, and plane trees along the main
            streets (Tehran's avenues are lined with them).
"""
from __future__ import annotations

import json
import math

import cv2
import numpy as np
import pyarrow.parquet as pq
import shapely
from scipy import ndimage
from shapely import geometry as sg
from shapely import wkb
from shapely.strtree import STRtree

import osm
from common import RAW, WORK, Projection, bbox
from heights import building_height, jitter

GROUND_MPP = 0.4
TERRAIN_STEP = 10.0

# Kinds, shared by the ground image, render.py's kinds pass and build_water_mask.py.
KINDS = {"empty": 0, "ground": 1, "road": 2, "water": 3, "green": 4, "wall": 5, "roof": 6, "tree": 7, "square": 8}

SOIL = (214, 202, 178)
LANDUSE = {
    ("landuse", "residential"): (216, 204, 182), ("landuse", "commercial"): (222, 208, 186),
    ("landuse", "retail"): (224, 210, 186), ("landuse", "industrial"): (200, 194, 184),
    ("amenity", "parking"): (190, 186, 178), ("amenity", "school"): (222, 210, 180),
    ("amenity", "hospital"): (222, 212, 196), ("amenity", "place_of_worship"): (226, 214, 190),
    ("amenity", "marketplace"): (226, 210, 180), ("leisure", "pitch"): (132, 178, 96),
    ("leisure", "playground"): (210, 190, 150), ("leisure", "sports_centre"): (196, 190, 176),
}
GREEN = {("landuse", "grass"): (148, 184, 104), ("landuse", "meadow"): (154, 186, 108),
         ("leisure", "park"): (138, 176, 96), ("leisure", "garden"): (126, 170, 90), ("natural", "wood"): (104, 150, 78),
         ("natural", "scrub"): (140, 160, 96), ("natural", "heath"): (160, 168, 110), ("natural", "grassland"): (150, 182, 104)}
WATER = (96, 160, 196)
SQUARE = (226, 216, 196)
# Road class -> (carriageway width m, colour). Pavements are drawn PAVEMENT_M wider on each side.
ROADS = {
    "trunk": (22, (120, 122, 126)), "primary": (18, (124, 126, 130)), "secondary": (14, (128, 130, 134)),
    "tertiary": (11, (132, 134, 137)), "unclassified": (7, (136, 137, 139)), "residential": (6, (140, 140, 141)),
    "living_street": (5, (150, 148, 145)), "service": (4, (150, 148, 144)), "busway": (7, (150, 92, 80)),
    "trunk_link": (8, (124, 126, 130)), "primary_link": (8, (124, 126, 130)), "secondary_link": (7, (128, 130, 134)),
    "tertiary_link": (6, (132, 134, 137)), "pedestrian": (7, SQUARE), "footway": (2.5, (206, 196, 176)),
    "path": (2, (200, 188, 164)), "steps": (2.5, (190, 182, 168)),
}
PAVEMENT_M = 2.5
PAVEMENT = (198, 190, 176)

WALLS = [(222, 196, 156), (210, 178, 136), (232, 220, 196), (200, 170, 130), (226, 210, 178), (214, 200, 182),
         (190, 160, 124), (236, 228, 210)]
ROOFS = [(186, 180, 170), (200, 192, 178), (172, 166, 160), (206, 186, 156), (190, 170, 140), (210, 204, 192)]
DOME = (64, 160, 168)
HERITAGE_WALL = (214, 178, 126)
TREES = [(84, 132, 64), (96, 142, 70), (74, 120, 60), (106, 150, 76), (90, 128, 58)]


# --- terrain -----------------------------------------------------------------------------------------------------

def load_dem():
    import rasterio

    with rasterio.open(RAW / "dem" / "glo30.tif") as src:
        z = src.read(1).astype("f8")
        return z, src.transform


def bare_ground(z: np.ndarray) -> np.ndarray:
    """Surface model -> smooth ground: an opening drops blocks of roofs (3 x 3 cells, 90 m), the blur smooths."""
    return ndimage.gaussian_filter(ndimage.grey_opening(z, size=(3, 3)), 1.2)


class Terrain:
    """Ground height on a regular grid in local metres (row 0 = south)."""

    def __init__(self, z: np.ndarray, x0: float, y0: float, step: float):
        self.z, self.x0, self.y0, self.step = z, x0, y0, step

    def at(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        fx = np.clip((x - self.x0) / self.step, 0, self.z.shape[1] - 1.001)
        fy = np.clip((y - self.y0) / self.step, 0, self.z.shape[0] - 1.001)
        i, j = fy.astype(int), fx.astype(int)
        ty, tx = fy - i, fx - j
        z = self.z
        return (z[i, j] * (1 - tx) * (1 - ty) + z[i, j + 1] * tx * (1 - ty) + z[i + 1, j] * (1 - tx) * ty
                + z[i + 1, j + 1] * tx * ty)

    @classmethod
    def load(cls, path=None):
        t = np.load(path or WORK / "scene.npz")
        return cls(t["terrain"], float(t["terrain_x0"]), float(t["terrain_y0"]), float(t["terrain_step"]))


def build_terrain(proj: Projection, bounds) -> Terrain:
    z, tr = load_dem()
    g = bare_ground(z)
    x0, y0, x1, y1 = bounds
    xs = np.arange(x0 - TERRAIN_STEP, x1 + 2 * TERRAIN_STEP, TERRAIN_STEP)
    ys = np.arange(y0 - TERRAIN_STEP, y1 + 2 * TERRAIN_STEP, TERRAIN_STEP)
    X, Y = np.meshgrid(xs, ys)
    lon, lat = proj.to_lonlat(X, Y)
    col = (np.asarray(lon) - tr.c) / tr.a - 0.5
    row = (np.asarray(lat) - tr.f) / tr.e - 0.5
    zz = ndimage.map_coordinates(g, [row, col], order=1, mode="nearest")
    print(f"terrain {zz.shape[1]} x {zz.shape[0]} @ {TERRAIN_STEP} m, {zz.min():.0f}..{zz.max():.0f} m", flush=True)
    return Terrain(zz.astype("f8"), float(xs[0]), float(ys[0]), TERRAIN_STEP)


# --- ground image ------------------------------------------------------------------------------------------------

class Ground:
    def __init__(self, bounds):
        self.x0, self.y0, self.x1, self.y1 = bounds
        self.w = int(math.ceil((self.x1 - self.x0) / GROUND_MPP))
        self.h = int(math.ceil((self.y1 - self.y0) / GROUND_MPP))
        rng = np.random.default_rng(7)
        noise = ndimage.gaussian_filter(rng.normal(0, 1, (self.h // 8 + 1, self.w // 8 + 1)), 2)
        noise = cv2.resize(noise, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
        noise = noise / (np.abs(noise).max() + 1e-6)
        self.rgb = np.empty((self.h, self.w, 3), np.uint8)
        for c in range(3):
            self.rgb[..., c] = np.clip(SOIL[c] + noise * 10, 0, 255)
        self.kind = np.full((self.h, self.w), KINDS["ground"], np.uint8)

    def px(self, xy: np.ndarray) -> np.ndarray:
        return np.column_stack(((xy[:, 0] - self.x0) / GROUND_MPP, (self.y1 - xy[:, 1]) / GROUND_MPP))

    def _rings(self, g):
        polys = [g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))
        out = []
        for p in polys:
            if p.geom_type != "Polygon" or p.is_empty:
                continue
            out.append(np.round(self.px(np.asarray(p.exterior.coords)) * 4).astype(np.int32))
            out += [np.round(self.px(np.asarray(r.coords)) * 4).astype(np.int32) for r in p.interiors]
        return out

    def fill(self, g, rgb, kind):
        rings = self._rings(g)
        if not rings:
            return
        cv2.fillPoly(self.rgb, rings, rgb, lineType=cv2.LINE_AA, shift=2)
        cv2.fillPoly(self.kind, rings, kind, shift=2)

    def line(self, g, width_m, rgb, kind):
        lines = [g] if g.geom_type == "LineString" else list(getattr(g, "geoms", []))
        t = max(1, int(round(width_m / GROUND_MPP)))
        for ln in lines:
            if ln.geom_type != "LineString":
                continue
            p = np.round(self.px(np.asarray(ln.coords)) * 4).astype(np.int32)
            cv2.polylines(self.rgb, [p], False, rgb, t, lineType=cv2.LINE_AA, shift=2)
            cv2.polylines(self.kind, [p], False, kind, t, shift=2)


def tone(rgb, j, amount=10):
    return tuple(int(np.clip(c + (j - 0.5) * 2 * amount, 0, 255)) for c in rgb)


# --- meshes ------------------------------------------------------------------------------------------------------

class Mesh:
    """Flat triangles: position (x, y, z), colour (r, g, b, 0..1), kind, ambient light (0..1, darker at wall feet)."""

    def __init__(self):
        self.pos, self.col, self.kind, self.ao = [], [], [], []

    def add(self, tris: np.ndarray, rgb, kind: int, ao: np.ndarray | None = None):
        n = len(tris)
        if not n:
            return
        self.pos.append(tris.reshape(-1, 3).astype("f4"))
        self.col.append(np.tile(np.array(rgb, "f4") / 255, (n * 3, 1)))
        self.kind.append(np.full(n * 3, kind, "u1"))
        self.ao.append(np.ones(n * 3, "f4") if ao is None else ao.reshape(-1).astype("f4"))

    def arrays(self):
        if not self.pos:
            return np.zeros((0, 3), "f4"), np.zeros((0, 3), "f4"), np.zeros(0, "u1"), np.zeros(0, "f4")
        return np.concatenate(self.pos), np.concatenate(self.col), np.concatenate(self.kind), np.concatenate(self.ao)


def roof_triangles(poly: sg.Polygon) -> np.ndarray:
    """Triangles (n, 3, 2) covering a polygon (holes kept)."""
    tris = shapely.constrained_delaunay_triangles(poly)
    out = [np.asarray(t.exterior.coords)[:3] for t in tris.geoms]
    return np.array(out) if out else np.zeros((0, 3, 2))


def extrude(mesh: Mesh, poly: sg.Polygon, base: float, top: float, wall_rgb, roof_rgb):
    roof = roof_triangles(poly)
    if len(roof):
        mesh.add(np.dstack((roof, np.full(roof.shape[:2], top))), roof_rgb, KINDS["roof"])
    for ring in [poly.exterior, *poly.interiors]:
        p = np.asarray(ring.coords)
        a, b = p[:-1], p[1:]
        n = len(a)
        lo, hi = np.full(n, base), np.full(n, top)
        q = np.stack([np.column_stack((a, lo)), np.column_stack((b, lo)), np.column_stack((b, hi)),
                      np.column_stack((a, lo)), np.column_stack((b, hi)), np.column_stack((a, hi))], axis=1)
        tris = q.reshape(-1, 3, 3)
        # darker near the foot of the wall (soft contact shadow), full light 4 m up
        ao = np.clip(0.72 + 0.28 * (tris[..., 2] - base) / 4.0, 0.72, 1.0)
        mesh.add(tris, wall_rgb, KINDS["wall"], ao)


def sphere(n_sub: int = 1) -> np.ndarray:
    """Unit icosphere triangles (n, 3, 3)."""
    t = (1 + 5 ** 0.5) / 2
    v = np.array([[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t],
                  [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]], float)
    f = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6],
         [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10],
         [8, 6, 7], [9, 8, 1]]
    tris = v[np.array(f)]
    tris /= np.linalg.norm(tris, axis=2, keepdims=True)
    for _ in range(n_sub):
        a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
        ab, bc, ca = [(m / np.linalg.norm(m, axis=1, keepdims=True)) for m in ((a + b) / 2, (b + c) / 2, (c + a) / 2)]
        tris = np.concatenate([np.stack(x, 1) for x in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
    return tris


SPHERE = sphere(1)
HEMI = SPHERE[SPHERE[:, :, 2].min(axis=1) >= -1e-6]


def add_tree(mesh: Mesh, x, y, ground, key):
    j = jitter(key)
    r = 2.6 + 1.6 * j
    crown = SPHERE * np.array([r, r, r * 0.85]) + np.array([x, y, ground + 2.2 + r * 0.85])
    mesh.add(crown, TREES[int(j * 997) % len(TREES)], KINDS["tree"])


def add_dome(mesh: Mesh, poly: sg.Polygon, top: float):
    c = poly.representative_point()
    r = min(math.sqrt(poly.area) * 0.28, 11.0)
    if r < 2.5:
        return
    dome = HEMI * np.array([r, r, r * 1.1]) + np.array([c.x, c.y, top])
    mesh.add(dome, DOME, KINDS["roof"])


# --- main --------------------------------------------------------------------------------------------------------

def overture_buildings(proj: Projection, clip: sg.Polygon):
    t = pq.read_table(RAW / "overture" / "buildings.parquet",
                      columns=["id", "geometry", "height", "num_floors", "class", "sources", "is_underground"])
    out = []
    for r in t.to_pylist():
        if r.get("is_underground"):
            continue
        g = wkb.loads(r["geometry"])
        g = shapely.transform(g, lambda c: np.column_stack(proj.to_local(c[:, 0], c[:, 1])))
        if not g.intersects(clip):
            continue
        g = g.intersection(clip)
        osm_id = None
        for s in r.get("sources") or []:
            rid = s.get("record_id") or ""
            if s.get("dataset") == "OpenStreetMap" and rid[:1] in "wr":
                osm_id = ("way/" if rid[0] == "w" else "relation/") + rid[1:].split("@")[0]
        out.append((r, g, osm_id))
    return out


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    proj = Projection()
    bounds = proj.local_bounds()
    clip = sg.box(*bounds)
    terrain = build_terrain(proj, bounds)
    data = osm.load()
    feats = [f for f in osm.features(data, proj) if f.geom.is_valid and f.geom.intersects(clip)]
    by_id = {f.osm: f for f in feats}
    print("osm features", len(feats), flush=True)

    # ground
    gr = Ground(bounds)
    order = []
    for f in feats:
        t, g = f.tags, f.geom
        if g.geom_type not in ("Polygon", "MultiPolygon") or "building" in t:
            continue
        for (k, v), rgb in LANDUSE.items():
            if t.get(k) == v:
                order.append((0, f, rgb, KINDS["ground"]))
        for (k, v), rgb in GREEN.items():
            if t.get(k) == v:
                order.append((1, f, rgb, KINDS["green"]))
        if t.get("highway") == "pedestrian" or t.get("area:highway") == "pedestrian" or t.get("place") == "square":
            order.append((2, f, SQUARE, KINDS["square"]))
        if t.get("natural") == "water" or "water" in t or t.get("leisure") == "swimming_pool" or t.get("amenity") == "fountain":
            order.append((3, f, WATER, KINDS["water"]))
    for _, f, rgb, kind in sorted(order, key=lambda o: (o[0], -o[1].geom.area)):
        gr.fill(f.geom.intersection(clip), tone(rgb, jitter(f.osm), 6), kind)
    roads = [f for f in feats if "highway" in f.tags and f.geom.geom_type == "LineString"
             and f.tags["highway"] in ROADS and f.tags.get("tunnel") not in ("yes", "building_passage")]
    roads.sort(key=lambda f: -ROADS[f.tags["highway"]][0])
    car = [f for f in roads if f.tags["highway"] not in ("footway", "path", "steps", "pedestrian")]
    for f in car:
        gr.line(f.geom, ROADS[f.tags["highway"]][0] + 2 * PAVEMENT_M, PAVEMENT, KINDS["square"])
    for f in reversed(roads):  # narrow first, wide on top
        w, rgb = ROADS[f.tags["highway"]]
        kind = KINDS["square"] if f.tags["highway"] in ("pedestrian", "footway", "path", "steps") else KINDS["road"]
        gr.line(f.geom, w, rgb, kind)
    for f in roads:  # centre lines on the wide roads
        if ROADS[f.tags["highway"]][0] >= 14 and f.tags.get("oneway") != "yes":
            gr.line(f.geom, 0.3, (232, 228, 214), KINDS["road"])
    for f in feats:
        if f.tags.get("waterway") and f.geom.geom_type == "LineString" and f.tags.get("tunnel") != "culvert":
            gr.line(f.geom, 2.0 if f.tags["waterway"] == "ditch" else 4.0, WATER, KINDS["water"])
    cv2.imwrite(str(WORK / "ground.png"), cv2.cvtColor(gr.rgb, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(WORK / "ground_kind.png"), gr.kind)
    print("ground", gr.w, "x", gr.h, flush=True)

    # buildings
    mesh = Mesh()
    blds = overture_buildings(proj, clip)
    stats: dict[str, int] = {}
    footprints = []
    for r, g, osm_id in blds:
        tags = by_id[osm_id].tags if osm_id in by_id else {}
        key = r["id"]
        polys = [g] if g.geom_type == "Polygon" else [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]
        for p in polys:
            if p.area < 8:
                continue
            h, src = building_height(tags, r, p.area, key)
            stats[src] = stats.get(src, 0) + 1
            ring = np.asarray(p.exterior.coords)
            base = float(terrain.at(ring[:, 0], ring[:, 1]).min()) - 0.5
            j = jitter(key + "c")
            heritage = "heritage" in tags or "historic" in tags or tags.get("building") in ("mosque", "shrine", "church")
            wall = HERITAGE_WALL if heritage else WALLS[int(j * 101) % len(WALLS)]
            roof = ROOFS[int(j * 37) % len(ROOFS)]
            extrude(mesh, p.simplify(0.15), base, base + 0.5 + h, tone(wall, jitter(key + "w"), 6), tone(roof, j, 6))
            if tags.get("building") == "mosque" or (tags.get("amenity") == "place_of_worship" and tags.get("religion") == "muslim"):
                add_dome(mesh, p, base + 0.5 + h)
            footprints.append(p)
    print("buildings", len(footprints), "heights from", stats, flush=True)

    # domes of mosques mapped only as areas (amenity=place_of_worship without building=*), on the tallest building inside
    tree_idx = STRtree(footprints)

    def free(x, y):
        pt = sg.Point(x, y)
        return not any(footprints[i].contains(pt) for i in tree_idx.query(pt))

    nt = 0
    for f in feats:
        t, g = f.tags, f.geom
        if t.get("natural") == "tree" and g.geom_type == "Point" and clip.contains(g):
            add_tree(mesh, g.x, g.y, float(terrain.at(g.x, g.y)), f.osm)
            nt += 1
        elif t.get("natural") == "tree_row" and g.geom_type == "LineString":
            for d in np.arange(0, g.length, 8.0):
                p = g.interpolate(d)
                if not clip.contains(p):
                    continue
                add_tree(mesh, p.x, p.y, float(terrain.at(p.x, p.y)), f"{f.osm}:{d}")
                nt += 1
        elif (t.get("leisure") in ("park", "garden") or t.get("natural") == "wood" or t.get("landuse") in ("grass",)) \
                and g.geom_type in ("Polygon", "MultiPolygon"):
            g = g.intersection(clip)
            spacing = 9.0 if t.get("landuse") != "grass" else 16.0
            x0, y0, x1, y1 = g.bounds
            rng = np.random.default_rng(int(jitter(f.osm) * 1e9))
            xs, ys = np.meshgrid(np.arange(x0, x1, spacing), np.arange(y0, y1, spacing))
            pts = np.column_stack((xs.ravel(), ys.ravel())) + rng.uniform(-spacing * 0.4, spacing * 0.4, (xs.size, 2))
            keep = shapely.contains_xy(g.buffer(-2), pts[:, 0], pts[:, 1])
            for k, (x, y) in enumerate(pts[keep]):
                if gr.kind[int((gr.y1 - y) / GROUND_MPP) % gr.h, int((x - gr.x0) / GROUND_MPP) % gr.w] in (KINDS["water"], KINDS["road"]):
                    continue
                if free(x, y):
                    add_tree(mesh, x, y, float(terrain.at(x, y)), f"{f.osm}:{k}")
                    nt += 1
    # plane trees along the avenues, on the pavement each side
    for f in roads:
        hw = f.tags["highway"]
        if hw not in ("primary", "secondary", "trunk"):
            continue
        w = ROADS[hw][0] / 2 + PAVEMENT_M * 0.6
        for side in (-1, 1):
            off = f.geom.offset_curve(side * w)
            if off.is_empty or off.geom_type != "LineString":
                continue
            for d in np.arange(4, off.length, 11.0):
                p = off.interpolate(d)
                if clip.contains(p) and free(p.x, p.y):
                    add_tree(mesh, p.x, p.y, float(terrain.at(p.x, p.y)), f"{f.osm}:{side}:{d}")
                    nt += 1
    print("trees", nt, flush=True)

    pos, col, kind, ao = mesh.arrays()
    np.savez_compressed(WORK / "scene.npz", pos=pos, col=col, kind=kind, ao=ao, terrain=terrain.z,
                        terrain_x0=terrain.x0, terrain_y0=terrain.y0, terrain_step=terrain.step,
                        ground_bounds=np.array(bounds), ground_mpp=GROUND_MPP)
    (WORK / "scene.json").write_text(json.dumps({"bbox": bbox(), "bounds": bounds, "triangles": len(pos) // 3,
                                                 "buildings": len(footprints), "trees": nt, "heights": stats}, indent=1))
    print("scene", len(pos) // 3, "triangles", flush=True)


if __name__ == "__main__":
    main()
