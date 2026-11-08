import { useEffect, useRef } from "react";
import { asset } from "../config";
import { canvasDpr, onQuality, quality, subscribe } from "../frame";
import type { MapHandle } from "./IsometricMap";

/**
 * Moving water on the rivers, drawn only inside the painted water and following the river's course.
 *
 * The water atlas (web/public/data/water_mask.png and .json, from pipeline/build_water_mask.py) is found in the
 * painted art itself, so the effect never spills over banks, trees, bridges or roads. Each texel holds the distance
 * to the bank (R, whose first metre is also the soft mask) and the surface current (G, B: the downstream direction,
 * from the OpenStreetMap centrelines, with a length that is 1 mid-stream and less near the banks). It is drawn with
 * WebGL on its own canvas, in map coordinates, so the pattern sticks to the map:
 *  - current lines: short, slightly bowed strokes lying along the flow, mostly pale with a few faint dark ink
 *    ones, that drift downstream a few map px a second (faster mid-stream) and fade in and out. They are drawn in
 *    two overlapping cycles, each shifting a fixed stroke pattern downstream and starting again with new strokes,
 *    so there is no conveyor belt; a slow noise field makes them gather in some places and thin out in others;
 *  - bank lines: broken lines a little inside each bank, which follow the bank (lines of equal bank distance) and
 *    slide downstream, like water running past the edge;
 *  - crest flashes: now and then a short bright crest along the flow lights up for a moment, with a dark ink line
 *    under it;
 *  - at night the lines catch warm city light, and broken warm reflections hang below lamps on the banks.
 * The canvas is redrawn on the frame clock (src/frame.ts) at most 30 times a second (15 on a lower quality tier),
 * at most 1.5 device pixels per CSS pixel, and only when the map moves if reduced motion is asked for.
 */

interface Props {
  map: React.RefObject<MapHandle>;
  /** 0 day .. 1 full night */
  night: number;
}

/** The atlas description written by build_water_mask.py. */
interface MaskInfo { x0: number; y0: number; scale: number; w: number; h: number; chunk: number; cols: number; chunks: number[] }

const MIN_ZOOM = 0.12; // further out the strokes would be under a pixel: draw nothing
const FADE_TO = 0.16;  // full strength from here in
const FPS = 30;        // redraws per second while the water moves
const WOBBLE_HZ = 6;   // night reflections flicker in steps, like the old 6 Hz shimmer
const LOOP_S = 3600;   // time wraps after an hour (a whole number of every cycle below), to keep floats precise

const VS = `
attribute vec4 a_pos;     // map px relative to the mask origin (xy), atlas uv (zw)
uniform vec2 u_view;      // map px at the screen centre, relative to the mask origin
uniform vec2 u_scale;     // z / half screen size, in clip units per map px
varying vec2 v_map;
varying vec2 v_uv;
void main() {
  v_map = a_pos.xy;
  v_uv = a_pos.zw;
  gl_Position = vec4((a_pos.xy - u_view) * u_scale * vec2(1.0, -1.0), 0.0, 1.0);
}`;

const FS = `
precision highp float;
varying vec2 v_map;
varying vec2 v_uv;
uniform sampler2D u_mask;
uniform float u_t;       // seconds, wrapped every hour
uniform float u_step;    // 6 Hz step number, kept small (night flicker)
uniform float u_cell;    // pattern size multiplier: 1, 2 or 4 as the map zooms out
uniform float u_aa;      // map px per device pixel, for smooth stroke edges
uniform float u_night;   // 0 .. 1
uniform float u_fade;    // 0 .. 1, fades the effect in as the map zooms in; up to 1.4 in rain, less in fog
uniform float u_debug;   // 1: tint the water by flow (?waterdebug in the address)

const float CYCLE = 4.0;  // seconds a stroke lives before the pattern starts again

// Hash without sine (Dave Hoskins), stable for the cell indices used here.
float h21(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

float vnoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), f.x), mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), f.x), f.y);
}

// Premultiplied "over".
void over(inout vec4 acc, vec3 c, float a) { acc = vec4(c * a, a) + acc * (1.0 - a); }

// Coverage of a line of half width hw at distance d, smoothed over one device pixel.
float line(float d, float hw) { return clamp(0.5 + (hw - abs(d)) / u_aa, 0.0, 1.0) * min(1.0, 2.0 * hw / u_aa + 0.35); }

// Strokes along the flow around q: x light, y dark ink. A stroke per cell (if any) with its own place, length and bow,
// new for every cycle; the 3 x 3 neighbourhood lets strokes cross cell borders.
vec2 strokes(vec2 q, float cyc, vec2 dir, vec2 nrm, float dens) {
  float cs = 16.0 * u_cell;
  vec2 base = floor(q / cs);
  vec2 res = vec2(0.0);
  for (int j = -1; j <= 1; j++) {
    for (int i = -1; i <= 1; i++) {
      vec2 c = base + vec2(float(i), float(j));
      vec2 sd = c + vec2(cyc * 17.13, cyc * 3.71);
      if (h21(sd) > dens) continue;
      vec2 ctr = (c + 0.5 + (vec2(h21(sd + 1.7), h21(sd + 4.1)) - 0.5) * 0.9) * cs;
      vec2 d = q - ctr;
      float len = cs * (0.35 + 0.5 * h21(sd + 7.3));
      float u = dot(d, dir) / (0.5 * len);
      if (abs(u) >= 1.0) continue;
      float bow = (h21(sd + 9.1) - 0.5) * 0.16 * len * (1.0 - u * u);
      float s = line(dot(d, nrm) - bow, 0.6 * u_cell * sqrt(1.0 - u * u));
      if (h21(sd + 2.9) < 0.2) res.y = max(res.y, s); else res.x = max(res.x, s);
    }
  }
  return res;
}

void main() {
  vec3 tx = texture2D(u_mask, v_uv).rgb;
  float bank = tx.r * 255.0 / 16.0;            // metres to the bank
  float m = clamp(bank, 0.0, 1.0);              // soft 1 m inner edge
  if (m < 0.02) discard;
  vec2 v = (tx.gb * 255.0 - 128.0) / 127.0;
  float spd = clamp(length(v), 0.0, 1.0);       // 1 mid-stream, ~0.3 at the banks
  vec2 dir = v / max(length(v), 1e-3);          // downstream, in map px axes
  vec2 nrm = vec2(-dir.y, dir.x);
  vec2 down = nrm.y >= 0.0 ? nrm : -nrm;        // the side that is lower on screen
  vec2 p = v_map;
  float t = u_t;

  vec3 lightCol = mix(vec3(0.90, 0.97, 0.98), vec3(1.0, 0.72, 0.38), u_night);
  vec3 darkCol = mix(vec3(0.05, 0.19, 0.24), vec3(0.02, 0.04, 0.10), u_night);
  vec4 acc = vec4(0.0);

  // Current lines: two cycles half a cycle apart, each fading in and out (their weights add up to one).
  float vel = (1.0 + 6.0 * spd * spd) * max(u_cell * 0.75, 1.0);   // map px per second
  float dens = (0.16 + 0.34 * smoothstep(0.35, 0.75, vnoise(p / (110.0 * u_cell) + vec2(t * 0.011, t * 0.006))))
             * (0.55 + 0.45 * spd) * (1.0 - 0.35 * u_night);
  vec2 st = vec2(0.0);
  for (int k = 0; k < 2; k++) {
    float tc = t / CYCLE + 0.5 * float(k);
    float ph = fract(tc);
    float w = 1.0 - abs(2.0 * ph - 1.0);
    st += w * strokes(p - dir * vel * ph * CYCLE, floor(tc) * 2.0 + float(k), dir, nrm, dens);
  }
  over(acc, darkCol, st.y * mix(0.2, 0.24, u_night));
  over(acc, lightCol, st.x * mix(0.42, 0.5, u_night));

  // Bank lines: lines of equal bank distance, broken by noise that slides downstream; a faint dark one nearer the
  // bank. Left out where the river is too narrow for them (they would ring the middle of the stream).
  float across = 1.0 - sqrt(clamp(1.0 - (spd - 0.3) / 0.7, 0.0, 1.0));   // the pipeline's profile, inverted
  float room = 1.0 - smoothstep(0.5, 0.8, across);
  if (room > 0.0 && bank < 3.0) {
    float bl = 0.0, bd = 0.0;
    float vb = 2.5 * max(u_cell * 0.75, 1.0);
    for (int k = 0; k < 2; k++) {
      float tc = t / (1.5 * CYCLE) + 0.5 * float(k);
      float ph = fract(tc);
      float w = 1.0 - abs(2.0 * ph - 1.0);
      vec2 q = (p - dir * vb * ph * 1.5 * CYCLE) / (10.0 * u_cell) + floor(tc) * vec2(5.3, 1.7) + float(k) * 9.1;
      bl += w * smoothstep(0.5, 0.62, vnoise(q));
      bd += w * smoothstep(0.55, 0.7, vnoise(q * 0.7 + 31.0));
    }
    float hw = 0.55 * u_cell;
    over(acc, darkCol, room * bd * line((bank - 0.9) * 4.0, hw) * mix(0.16, 0.2, u_night));
    over(acc, lightCol, room * bl * line((bank - 1.6) * 4.0, hw) * mix(0.34, 0.4, u_night));
  }

  // Night: broken warm reflections, columns of short dashes that hang below a light, lie along the flow and wobble.
  if (u_night > 0.01) {
    vec2 gs = vec2(44.0, 88.0);
    vec2 gc = floor(p / gs);
    if (h21(gc + 3.3) < 0.22 * u_night) {
      float yf = fract(p.y / gs.y);
      float rs = 3.0 * u_cell;
      float ri = floor(p.y / rs);
      float cx = (gc.x + 0.2 + 0.6 * h21(gc + 1.1)) * gs.x + (h21(vec2(ri, u_step)) - 0.5) * 3.0 * u_cell;
      vec2 d = p - vec2(cx, (ri + 0.5) * rs);
      float hw = (1.5 + 2.5 * h21(gc + 2.2)) * (1.0 - 0.6 * yf) * max(u_cell * 0.6, 1.0);
      bool on = h21(vec2(ri * 1.3, gc.x + u_step * 0.21)) > 0.3;
      if (on && abs(dot(d, dir)) < hw) {
        over(acc, vec3(1.0, 0.72, 0.36), line(dot(d, nrm), 0.6 * u_cell) * 0.55 * (1.0 - yf) * u_night);
      }
    }
  }

  // Crest flashes: in a coarse grid, a rare cell lights a short crest along the flow for two thirds of a second.
  vec2 bs = vec2(56.0) * u_cell;
  vec2 bc = floor(p / bs);
  float slot = floor(t * 1.5 + h21(bc + 0.7) * 7.0);
  if (h21(bc + vec2(mod(slot, 97.0) * 1.31, 5.0)) < 0.03) {
    float ph = fract(t * 1.5 + h21(bc + 0.7) * 7.0);
    float env = sin(3.14159 * ph);
    vec2 ctr = (bc + 0.5 + (vec2(h21(bc + slot), h21(bc - slot)) - 0.5) * 0.4) * bs + dir * vel * ph * 0.67;
    vec2 d = p - ctr;
    float len = 7.0 * u_cell * (0.6 + 0.4 * env);
    float u = dot(d, dir) / len;
    if (abs(u) < 1.0) {
      float taper = sqrt(1.0 - u * u);
      over(acc, darkCol, line(dot(d, down) - 1.3 * u_cell, 0.45 * u_cell * taper) * 0.2 * env);
      over(acc, mix(vec3(1.0), vec3(1.0, 0.86, 0.6), u_night), line(dot(d, nrm), 0.55 * u_cell * taper) * 0.62 * env);
    }
  }

  acc = min(acc * clamp(m * 1.2, 0.0, 1.0) * u_fade, vec4(1.0));
  if (u_debug > 0.5) {
    // Hue by downstream direction, brighter mid-stream, so the flow field can be checked against the river.
    vec3 hue = 0.5 + 0.5 * vec3(dir.x, dir.y, -dir.x);
    acc = vec4(hue * (0.25 + 0.4 * spd), 0.6) * m + acc * (1.0 - 0.6 * m);
  }
  if (acc.a < 0.003) discard;
  gl_FragColor = acc;
}`;

/** Centres of the water mask's blocks, in map px: places over the rivers (for example to start bird flocks). */
export async function waterPoints(): Promise<{ x: number; y: number }[]> {
  const info = await maskInfo();
  const span = info.chunk * info.scale, pts: { x: number; y: number }[] = [];
  for (let k = 0; k < info.chunks.length; k += 2) {
    pts.push({ x: info.x0 + (info.chunks[k] + 0.5) * span, y: info.y0 + (info.chunks[k + 1] + 0.5) * span });
  }
  return pts;
}

let maskInfoP: Promise<MaskInfo> | null = null;
/** The water mask's layout (water_mask.json), fetched once for every user. */
function maskInfo(): Promise<MaskInfo> {
  maskInfoP ??= fetch(asset("/data/water_mask.json")).then((r) => (r.ok ? r.json() : Promise.reject(r.status)));
  maskInfoP.catch(() => { maskInfoP = null; });
  return maskInfoP;
}

/**
 * A test for OSM water polygons (map px, flat x, y list): is any of it painted as water? True when one of its
 * points lies in a block of the water mask with water, or such a block's centre lies inside it (blocks of 256 map
 * px). Polygons beyond the mask's area pass. The streets layer outlines only painted water.
 */
export async function paintedWaterTest(): Promise<(p: number[]) => boolean> {
  const info = await maskInfo();
  const span = info.chunk * info.scale, x1 = info.x0 + info.w * info.scale, y1 = info.y0 + info.h * info.scale;
  const wet = new Set<number>();
  const centres: [number, number][] = [];
  for (let k = 0; k < info.chunks.length; k += 2) {
    wet.add(info.chunks[k + 1] * 100000 + info.chunks[k]);
    centres.push([info.x0 + (info.chunks[k] + 0.5) * span, info.y0 + (info.chunks[k + 1] + 0.5) * span]);
  }
  const inside = (px: number, py: number, p: number[]) => {
    let r = false;
    for (let i = 0, n = p.length / 2, j = n - 1; i < n; j = i++) {
      const xi = p[2 * i], yi = p[2 * i + 1], xj = p[2 * j], yj = p[2 * j + 1];
      if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) r = !r;
    }
    return r;
  };
  return (p) => {
    let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
    for (let i = 0; i < p.length; i += 2) {
      const x = p[i], y = p[i + 1];
      if (wet.has(Math.floor((y - info.y0) / span) * 100000 + Math.floor((x - info.x0) / span))) return true;
      bx0 = Math.min(bx0, x); by0 = Math.min(by0, y); bx1 = Math.max(bx1, x); by1 = Math.max(by1, y);
    }
    if (bx1 < info.x0 || bx0 > x1 || by1 < info.y0 || by0 > y1) return true;
    return centres.some(([cx, cy]) => cx >= bx0 && cx <= bx1 && cy >= by0 && cy <= by1 && inside(cx, cy, p));
  };
}

interface GlState {
  gl: WebGLRenderingContext;
  prog: WebGLProgram;
  count: number;
  u: Record<string, WebGLUniformLocation | null>;
}

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader {
  const s = gl.createShader(type)!;
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? "shader");
  return s;
}

/** Program, one static vertex buffer with a quad per mask block, and the atlas texture. */
function setup(gl: WebGLRenderingContext, info: MaskInfo, img: HTMLImageElement): GlState {
  const prog = gl.createProgram()!;
  gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VS));
  gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FS));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) ?? "link");
  gl.useProgram(prog);

  const C = info.chunk, cell = C + 2, span = C * info.scale;
  const AW = img.width, AH = img.height, n = info.chunks.length / 2;
  const v = new Float32Array(n * 6 * 4);
  for (let k = 0; k < n; k++) {
    const i = info.chunks[2 * k], j = info.chunks[2 * k + 1];
    const x0 = i * span, y0 = j * span, x1 = x0 + span, y1 = y0 + span;
    const ax = (k % info.cols) * cell + 1, ay = Math.floor(k / info.cols) * cell + 1;
    const u0 = ax / AW, v0 = ay / AH, u1 = (ax + C) / AW, v1 = (ay + C) / AH;
    v.set([x0, y0, u0, v0, x1, y0, u1, v0, x0, y1, u0, v1, x0, y1, u0, v1, x1, y0, u1, v0, x1, y1, u1, v1], k * 24);
  }
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, v, gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "a_pos");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 4, gl.FLOAT, false, 0, 0);

  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  // The atlas holds numbers, not colours: no colour management on upload.
  gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, img);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 0);
  const u: GlState["u"] = {};
  for (const name of ["u_view", "u_scale", "u_mask", "u_t", "u_step", "u_cell", "u_aa", "u_night", "u_fade", "u_debug"]) {
    u[name] = gl.getUniformLocation(prog, name);
  }
  gl.uniform1i(u.u_mask, 0);
  gl.uniform1f(u.u_debug, new URLSearchParams(location.search).has("waterdebug") ? 1 : 0);
  return { gl, prog, count: n * 6, u };
}

export function Water({ map, night }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const nightRef = useRef(night);
  const dirty = useRef(true);
  const wake = useRef<() => void>(() => {});
  if (nightRef.current !== night) { nightRef.current = night; dirty.current = true; wake.current(); }

  useEffect(() => {
    const cv = canvas.current;
    if (!cv) return;
    let st: GlState | null = null;
    let info: MaskInfo | null = null;
    let img: HTMLImageElement | null = null;
    let alive = true, lastFrame = -1, last = "", cleared = false;
    const still = matchMedia("(prefers-reduced-motion: reduce)");

    const init = () => {
      if (!info || !img) return;
      const gl = cv.getContext("webgl", { alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false });
      if (!gl) return;
      try { st = setup(gl, info, img); } catch (e) { console.warn("water effect off:", e); st = null; }
      dirty.current = true;
    };

    let fps = quality.tier >= 1 ? FPS / 2 : FPS;
    const draw = (now: number) => {
      const v = map.current?.getView();
      if (!st || !info || !v) return;
      const gl = st.gl;
      // the strokes are a pixel or two wide and soft: 1.5 device pixels per CSS pixel are plenty
      const dpr = canvasDpr(1.5), W = window.innerWidth, H = window.innerHeight;
      const bw = Math.round(W * dpr), bh = Math.round(H * dpr);
      if (cv.width !== bw || cv.height !== bh) { cv.width = bw; cv.height = bh; dirty.current = true; }
      // With reduced motion the water stands still (one fixed moment); it is redrawn only when the map moves.
      const frame = still.matches ? 0 : Math.floor((now / 1000) * fps);
      const key = `${v.x},${v.y},${v.z},${bw},${bh}`;
      if (frame === lastFrame && key === last && !dirty.current) return;
      lastFrame = frame; last = key; dirty.current = false;

      gl.viewport(0, 0, bw, bh);
      if (v.z < MIN_ZOOM) {
        if (!cleared) { gl.clear(gl.COLOR_BUFFER_BIT); cleared = true; }
        return;
      }
      cleared = false;
      gl.clear(gl.COLOR_BUFFER_BIT);
      const u = st.u;
      const t = (frame / fps) % LOOP_S;
      gl.uniform2f(u.u_view, v.x - info.x0, v.y - info.y0);
      gl.uniform2f(u.u_scale, v.z / (W / 2), v.z / (H / 2));
      gl.uniform1f(u.u_t, t);
      gl.uniform1f(u.u_step, Math.floor(t * WOBBLE_HZ) % 512);
      gl.uniform1f(u.u_cell, v.z >= 0.6 ? 1 : v.z >= 0.3 ? 2 : 4);
      gl.uniform1f(u.u_aa, 1 / (v.z * dpr));
      gl.uniform1f(u.u_night, nightRef.current);
      const gain = 1; // weather would raise or calm the glints (not part of this project yet)
      gl.uniform1f(u.u_fade, Math.min(1, (v.z - MIN_ZOOM) / (FADE_TO - MIN_ZOOM)) * gain);
      gl.drawArrays(gl.TRIANGLES, 0, st.count);
    };

    Promise.all([
      maskInfo(),
      new Promise<HTMLImageElement>((ok, fail) => {
        const im = new Image();
        im.onload = () => ok(im);
        im.onerror = fail;
        im.src = asset("/data/water_mask.png");
      }),
    ]).then(([i, im]) => {
      if (!alive) return;
      info = i as MaskInfo;
      img = im;
      init();
      wake.current();
    }).catch(() => { /* No mask yet: no water effect. */ });

    const lost = (e: Event) => { e.preventDefault(); st = null; };
    cv.addEventListener("webglcontextlost", lost);
    cv.addEventListener("webglcontextrestored", init);

    // On the frame clock: 30 times a second (15 on a lower quality tier), and in the same frame as the map whenever
    // it moves, so the strokes never trail it. With reduced motion only when the map moves or the light changes.
    const sub = subscribe((t) => draw(t), { fps, still: still.matches, name: "water" });
    wake.current = sub.wake;
    const offQ = onQuality((tier) => { fps = tier >= 1 ? FPS / 2 : FPS; sub.setFps(fps); sub.wake(); });
    return () => {
      alive = false;
      sub();
      offQ();
      wake.current = () => {};
      cv.removeEventListener("webglcontextlost", lost);
      cv.removeEventListener("webglcontextrestored", init);
    };
  }, [map]);

  return <canvas ref={canvas} className="overlay" aria-hidden="true" />;
}
