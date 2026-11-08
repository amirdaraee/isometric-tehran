"""Render the scene (build_scene.py) as an isometric map, cut into square frames for build_tiles.py.

    python render.py                  -> data/work/frames/{c}_{r}.jpg, frames_kind/{c}_{r}.png, mosaic.json
    python render.py --only 51.4203,35.6797   one frame centred there -> data/work/test.jpg (quick look)

The first draft: no textures and no painting. The ground is the colour image from build_scene.py draped on the
terrain; buildings and trees are flat-coloured, lit by a low afternoon sun from the west-south-west with cast
shadows (a shadow map), a soft contact shadow at the foot of walls, and thin ink lines where surfaces meet, so it
already reads like a drawing. The camera is the one in city.json (common.Camera): orthographic, 2:1 dimetric.

The kinds pass (frames_kind) stores, per pixel, what is there (build_scene.KINDS: ground, road, water, green, wall,
roof, tree, square), for the water mask and, later, as a guide for the painter.
"""
from __future__ import annotations

import argparse
import json
import math
import time

import cv2
import moderngl
import numpy as np
from PIL import Image

from build_scene import KINDS, Terrain
from common import WORK, Camera, Projection, city

FRAME = 2048
BG = (16, 24, 40)             # the empty map edge; the viewer's clouds and build_clouds.py look for this colour
SUN_AZIMUTH = 240.0           # degrees clockwise from north, where the sun is
SUN_ELEVATION = 40.0
SHADOW = 12288
AMBIENT = 0.58
SUN = 0.55

VS = """#version 410
in vec3 p; in vec3 col; in float kind; in float ao;
uniform mat3 V; uniform vec4 frame; uniform vec2 dr;
out vec3 wp; out vec3 vcol; out float vkind; out float vao; out vec2 vuv;
uniform vec4 gb;   // ground image bounds: x0, y0, x1, y1
void main(){
  vec3 s = V * p;
  gl_Position = vec4((s.x-frame.x)/(frame.z-frame.x)*2.-1., (s.y-frame.y)/(frame.w-frame.y)*2.-1., (s.z-dr.x)/(dr.y-dr.x)*2.-1., 1.);
  wp = p; vcol = col; vkind = kind; vao = ao;
  vuv = vec2((p.x-gb.x)/(gb.z-gb.x), (gb.w-p.y)/(gb.w-gb.y));
}"""

FS = """#version 410
in vec3 wp; in vec3 vcol; in float vkind; in float vao; in vec2 vuv;
uniform int mode;            // 0 colour, 1 g-buffer (normal xy, depth, kind)
uniform int ground;          // 1: terrain, coloured from the ground image
uniform sampler2D gtex; uniform sampler2D gkind;
uniform sampler2DShadow smap; uniform mat3 L; uniform vec3 lmin; uniform vec3 lmax;
uniform vec3 sun; uniform vec3 tocam; uniform mat3 V;
uniform float ambient; uniform float sunk;
out vec4 c;
void main(){
  vec3 n = normalize(cross(dFdx(wp), dFdy(wp)));
  if (dot(n, tocam) < 0.) n = -n;
  float kind = ground == 1 ? floor(texture(gkind, vuv).r * 255. + .5) : vkind;
  if (mode == 1) { c = vec4(n.x, n.y, (V * wp).z, kind); return; }
  vec3 base = ground == 1 ? texture(gtex, vuv).rgb : vcol;
  // shadow: compare with the sun's depth map, 5 taps
  vec3 l = (L * (wp + n * 0.6) - lmin) / (lmax - lmin);
  float bias = 0.6 / (lmax.z - lmin.z);
  float lit = 0.;
  vec2 px = 1. / vec2(textureSize(smap, 0));
  lit += texture(smap, vec3(l.xy, l.z - bias));
  lit += texture(smap, vec3(l.xy + vec2(px.x, 0.), l.z - bias));
  lit += texture(smap, vec3(l.xy - vec2(px.x, 0.), l.z - bias));
  lit += texture(smap, vec3(l.xy + vec2(0., px.y), l.z - bias));
  lit += texture(smap, vec3(l.xy - vec2(0., px.y), l.z - bias));
  lit /= 5.;
  float d = max(dot(n, sun), 0.) * lit;
  // sunlit a touch warm, shade a touch cool, as in a watercolour
  vec3 light = vec3(ambient) * vec3(0.92, 0.96, 1.06) + sunk * d * vec3(1.08, 1.0, 0.88);
  c = vec4(base * light * vao, 1.);
}"""

VS_SHADOW = """#version 410
in vec3 p; uniform mat3 L; uniform vec3 lmin; uniform vec3 lmax;
void main(){ vec3 l = (L * p - lmin) / (lmax - lmin); gl_Position = vec4(l.xy * 2. - 1., l.z * 2. - 1., 1.); }"""
FS_SHADOW = """#version 410
out vec4 c; void main(){ c = vec4(1.); }"""


def sun_basis():
    az, el = math.radians(SUN_AZIMUTH), math.radians(SUN_ELEVATION)
    to_sun = np.array([math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)])
    d = -to_sun                                  # the light travels this way
    a = np.cross(d, [0, 0, 1.0])
    a /= np.linalg.norm(a)
    b = np.cross(a, d)
    return to_sun, np.array([a, b, d])           # rows: light u, v, depth


def terrain_mesh(t: Terrain, bounds) -> np.ndarray:
    """Terrain triangles inside the bounds (n * 3, 3)."""
    h, w = t.z.shape
    X, Y = np.meshgrid(t.x0 + np.arange(w) * t.step, t.y0 + np.arange(h) * t.step)
    x0, y0, x1, y1 = bounds
    X, Y = np.clip(X, x0, x1), np.clip(Y, y0, y1)       # the grid's outer ring is pulled onto the edge
    v = np.dstack((X, Y, t.at(X, Y))).reshape(-1, 3)
    i = np.arange(h * w).reshape(h, w)
    a, b, c, d = i[:-1, :-1].ravel(), i[:-1, 1:].ravel(), i[1:, :-1].ravel(), i[1:, 1:].ravel()
    tris = v[np.column_stack((a, b, c, b, d, c)).ravel()].reshape(-1, 3, 3)
    u, w_ = tris[:, 1, :2] - tris[:, 0, :2], tris[:, 2, :2] - tris[:, 0, :2]
    area = np.abs(u[:, 0] * w_[:, 1] - u[:, 1] * w_[:, 0])
    return tris[area > 1e-6].reshape(-1, 3).astype("f4")


def ink(g: np.ndarray, kind: np.ndarray) -> np.ndarray:
    """Edge strength 0..1 from the g-buffer: jumps in depth anywhere, jumps in normal except inside tree crowns."""
    nx, ny, dep = g[..., 0], g[..., 1], g[..., 2]
    empty = kind < 0.5
    e = np.zeros(nx.shape, np.float32)
    tree = kind == KINDS["tree"]
    for ax in (0, 1):
        dn = np.abs(np.diff(nx, axis=ax)) + np.abs(np.diff(ny, axis=ax))
        dd = np.abs(np.diff(np.where(empty, 0, dep), axis=ax))
        t2 = tree[1:] & tree[:-1] if ax == 0 else tree[:, 1:] & tree[:, :-1]
        j = ((dn > 0.3) & ~t2).astype(np.float32) * 0.8 + (dd > 1.5).astype(np.float32)
        j = np.minimum(j, 1)
        if ax == 0:
            e[1:] = np.maximum(e[1:], j)
        else:
            e[:, 1:] = np.maximum(e[:, 1:], j)
    e[empty] = 0
    return cv2.GaussianBlur(e, (3, 3), 0.6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="lon,lat: render one frame centred here, to data/work/test.jpg")
    args = ap.parse_args()
    t0 = time.time()
    cfg = city()
    cam, proj = Camera(cfg), Projection(cfg)
    mpp = float(cfg["metresPerPixel"])
    s = np.load(WORK / "scene.npz")
    terr = Terrain(s["terrain"], float(s["terrain_x0"]), float(s["terrain_y0"]), float(s["terrain_step"]))
    bounds = tuple(float(x) for x in s["ground_bounds"])
    ter = terrain_mesh(terr, bounds)
    pos, col, kind, ao = s["pos"], s["col"], s["kind"].astype("f4"), s["ao"]
    print("scene", len(pos) // 3, "triangles; terrain", len(ter) // 3, flush=True)

    ctx = moderngl.create_standalone_context()
    ctx.enable(moderngl.DEPTH_TEST)
    prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
    sprog = ctx.program(vertex_shader=VS_SHADOW, fragment_shader=FS_SHADOW)
    V = cam.view_matrix()

    # vertex buffers: x y z r g b kind ao
    def vbuf(p, c, k, a):
        return ctx.buffer(np.column_stack((p, c, k, a)).astype("f4").tobytes())

    tb = vbuf(ter, np.zeros((len(ter), 3)), np.zeros(len(ter)), np.ones(len(ter)))
    mb = vbuf(pos, col, kind, ao)
    fmt = (("3f 3f 1f 1f", "p", "col", "kind", "ao"))
    tvao, mvao = ctx.vertex_array(prog, [(tb, *fmt)]), ctx.vertex_array(prog, [(mb, *fmt)])
    stvao = ctx.vertex_array(sprog, [(tb, "3f 20x", "p")])
    smvao = ctx.vertex_array(sprog, [(mb, "3f 20x", "p")])

    # textures: the ground's colour and kind
    gimg = cv2.cvtColor(cv2.imread(str(WORK / "ground.png")), cv2.COLOR_BGR2RGB)
    gtex = ctx.texture((gimg.shape[1], gimg.shape[0]), 3, np.ascontiguousarray(gimg).tobytes())
    gtex.build_mipmaps()
    gtex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
    gtex.anisotropy = 8.0
    kimg = cv2.imread(str(WORK / "ground_kind.png"), cv2.IMREAD_GRAYSCALE)
    ktex = ctx.texture((kimg.shape[1], kimg.shape[0]), 1, np.ascontiguousarray(kimg).tobytes())
    ktex.filter = (moderngl.NEAREST, moderngl.NEAREST)
    del gimg, kimg

    # sun shadow map over the whole scene
    to_sun, Lm = sun_basis()
    allp = np.concatenate((pos, ter))
    lp = allp @ Lm.T
    lmin, lmax = lp.min(0) - 5, lp.max(0) + 5
    sdepth = ctx.depth_texture((SHADOW, SHADOW))
    sdepth.compare_func = "<="
    sdepth.filter = (moderngl.LINEAR, moderngl.LINEAR)
    sfbo = ctx.framebuffer(depth_attachment=sdepth)
    sprog["L"].write(Lm.T.astype("f4").tobytes())
    sprog["lmin"].value, sprog["lmax"].value = tuple(lmin), tuple(lmax)
    sfbo.use()
    sfbo.clear(depth=1.0)
    stvao.render()
    smvao.render()
    print(f"shadow map {SHADOW}^2, {(lmax[0] - lmin[0]) / SHADOW:.2f} m per texel", flush=True)

    prog["V"].write(V.T.astype("f4").tobytes())
    prog["L"].write(Lm.T.astype("f4").tobytes())
    prog["lmin"].value, prog["lmax"].value = tuple(lmin), tuple(lmax)
    prog["sun"].value = tuple(to_sun)
    prog["tocam"].value = tuple(-V[2])
    prog["ambient"].value, prog["sunk"].value = AMBIENT, SUN
    prog["gb"].value = bounds
    prog["gtex"].value, prog["gkind"].value, prog["smap"].value = 0, 1, 2
    gtex.use(0)
    ktex.use(1)
    sdepth.use(2)

    # the mosaic's extent on screen: the ground's corners and everything standing on it
    sc = allp @ V.T
    pad = 40.0
    sx0, sx1 = sc[:, 0].min() - pad, sc[:, 0].max() + pad
    sy0, sy1 = sc[:, 1].min() - pad, sc[:, 1].max() + pad
    dr = (float(sc[:, 2].min()) - 100, float(sc[:, 2].max()) + 100)
    prog["dr"].value = dr
    span = FRAME * mpp
    cols, rows = math.ceil((sx1 - sx0) / span), math.ceil((sy1 - sy0) / span)
    print("mosaic", cols * FRAME, "x", rows * FRAME, "px;", cols, "x", rows, "frames", flush=True)

    fbo_ms = ctx.framebuffer(ctx.renderbuffer((FRAME, FRAME), 4, samples=4), ctx.depth_renderbuffer((FRAME, FRAME), samples=4))
    fbo = ctx.framebuffer(ctx.renderbuffer((FRAME, FRAME), 4))
    gfbo = ctx.framebuffer(ctx.renderbuffer((FRAME, FRAME), 4, dtype="f4"), ctx.depth_renderbuffer((FRAME, FRAME)))

    def draw(fx0, fy0):
        prog["frame"].value = (fx0, fy0, fx0 + span, fy0 + span)
        # colour
        prog["mode"].value = 0
        fbo_ms.use()
        ctx.clear(BG[0] / 255, BG[1] / 255, BG[2] / 255, 1)
        prog["ground"].value = 1
        tvao.render()
        prog["ground"].value = 0
        mvao.render()
        ctx.copy_framebuffer(fbo, fbo_ms)
        rgb = np.frombuffer(fbo.read(components=3), np.uint8).reshape(FRAME, FRAME, 3)[::-1].astype(np.float32)
        # g-buffer: normals, depth, kind
        prog["mode"].value = 1
        gfbo.use()
        gfbo.clear(0, 0, 0, 0)
        prog["ground"].value = 1
        tvao.render()
        prog["ground"].value = 0
        mvao.render()
        g = np.frombuffer(gfbo.read(components=4, dtype="f4"), np.float32).reshape(FRAME, FRAME, 4)[::-1]
        k = np.round(g[..., 3]).astype(np.uint8)
        e = ink(g, k)
        inkc = np.array([52, 40, 36], np.float32)
        rgb = rgb * (1 - 0.6 * e[..., None]) + inkc * 0.6 * e[..., None]
        return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)), Image.fromarray(k)

    if args.only:
        lon, lat = map(float, args.only.split(","))
        x, y = proj.to_local(lon, lat)
        cx, cy = cam.to_screen(x, y, terr.at(x, y))
        im, _ = draw(float(cx) - span / 2, float(cy) - span / 2)
        im.save(WORK / "test.jpg", quality=92)
        print("test frame", f"{time.time() - t0:.0f}s")
        return

    out, kout = WORK / "frames", WORK / "frames_kind"
    out.mkdir(exist_ok=True)
    kout.mkdir(exist_ok=True)
    for r in range(rows):
        for c in range(cols):
            im, k = draw(sx0 + c * span, sy1 - (r + 1) * span)
            im.save(out / f"{c}_{r}.jpg", quality=95, subsampling=0)
            k.save(kout / f"{c}_{r}.png")
        print(f"row {r + 1}/{rows} {time.time() - t0:.0f}s", flush=True)
    (WORK / "mosaic.json").write_text(json.dumps({
        "cols": cols, "rows": rows, "frame": FRAME, "mpp": mpp, "sx0": float(sx0), "sy1": float(sy1),
        "camera": cfg["camera"], "crs": cfg["crs"], "origin": list(proj.origin)}, indent=1))
    print("done", f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
