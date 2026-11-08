import { useEffect, useRef } from "react";
import { asset } from "../config";
import { canvasDpr, onQuality, quality, subscribe } from "../frame";
import type { MapHandle } from "./IsometricMap";

/**
 * A sea of cloud seen from above, filling everything outside the map data, so the city rises out of it like a
 * mountain top over a sea of fog.
 *
 * Assets from pipeline/build_clouds.py:
 *  - web/public/clouds/sea.webp: one seamless cloud-top texture, lit like the map (RGB: day colour; A: height);
 *  - web/public/clouds/mask.png: signed distance to the edge of the data, 1 px = 32 map px;
 *  - web/public/data/clouds.json: where the mask sits on the map.
 * Drawn with WebGL on one full-screen canvas above the map tiles and below the pins, in map coordinates, with the
 * map's own view: a low layer, a higher layer of bigger heads that drifts a little faster and moves a little more
 * when panning (parallax) and casts its shadow on the low one, and a fine layer that adds detail close up. At the
 * edge of the data the cloud thins out over the map along its billows, so the edge is soft and irregular.
 * Redrawn on the frame clock (src/frame.ts): in the map's own frame when it moves and, for the drift, every frame
 * while any cloud is on screen, at half the CSS resolution (the cloud is soft). The cloud texture is fetched the
 * first time a cloud can show. With reduced motion, and on the lower quality tiers, the clouds stand still.
 */

interface Props {
  map: React.RefObject<MapHandle>;
  /** 0 day .. 1 full night */
  night: number;
}

interface CloudInfo {
  mask: { file: string; x0: number; y0: number; scale: number; w: number; h: number; range: number };
  texture: { file: string; size: number };
}

// Map px covered by one repeat of the texture, per layer; the scales are far from simple ratios, so the repeats
// never line up.
const SCALE_LOW = 22000;
const SCALE_HIGH = 13700;
const SCALE_FINE = 4300;
const PARALLAX_HIGH = 0.035;  // the higher layer moves this much more than the map when panning
const PARALLAX_FINE = 0.012;
const WIND_LOW: [number, number] = [-5, 1.6];   // drift, map px per second
const WIND_HIGH: [number, number] = [-8, 2.4];
const FEATHER = 600;          // map px: how far, on average, the cloud reaches in over the data

const VS = `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }`;

const FS = `
precision highp float;
uniform sampler2D u_sea;
uniform sampler2D u_mask;
uniform vec2 u_res;        // canvas size, device px
uniform float u_dpr;
uniform vec2 u_view;       // map px at the screen centre
uniform float u_z;         // screen px per map px
uniform vec2 u_offLow;     // drift of each layer, map px (kept small on the CPU)
uniform vec2 u_offHigh;
uniform vec4 u_mask0;      // mask origin (xy, map px), mask size (zw, map px)
uniform float u_range;     // map px of the distance at the ends of the mask's range
uniform float u_feather;
uniform float u_night;
uniform float u_fine;      // 0 .. 1: weight of the fine layer, which grows as the map zooms in

const float SQUASH = 1.6;  // the cloud tops are seen at a slant, like the map: shorter than they are wide

vec4 sea(vec2 q, float scale) { return texture2D(u_sea, q / scale * vec2(1.0, SQUASH)); }

void main() {
  vec2 px = vec2(gl_FragCoord.x, u_res.y - gl_FragCoord.y) / u_dpr;
  vec2 d = (px - u_res / u_dpr * 0.5) / u_z;        // map px from the screen centre
  vec2 m = u_view + d;

  vec2 mu = (m - u_mask0.xy) / u_mask0.zw;
  float sd = (texture2D(u_mask, mu).r * 255.0 - 128.0) / 127.0 * u_range;   // > 0 outside the data
  if (sd < -u_feather * 2.2) { gl_FragColor = vec4(0.0); return; }

  // Low layer: sits on the map.
  vec4 low = sea(m + u_offLow, ${SCALE_LOW.toFixed(1)});
  // High layer: bigger heads above it, a little parallax.
  vec2 mh = u_view + d * (1.0 + ${PARALLAX_HIGH.toFixed(3)});
  vec4 high = sea(mh + u_offHigh + vec2(5311.0, 2917.0), ${SCALE_HIGH.toFixed(1)});
  // its shadow on the low layer: the sun is to the upper left, so the shadow falls down and to the right
  float hs = sea(mh + u_offHigh + vec2(5311.0, 2917.0) - vec2(260.0, 200.0), ${SCALE_HIGH.toFixed(1)}).a;
  float cover = smoothstep(0.66, 0.80, high.a);
  float shade = smoothstep(0.60, 0.80, hs) * (1.0 - cover);
  // the low layer is further down: a little hazier and bluer than the heads above it
  vec3 col = mix(low.rgb, vec3(0.80, 0.85, 0.93), 0.18) * mix(vec3(1.0), vec3(0.80, 0.84, 0.93), shade * 0.85);
  col = mix(col, high.rgb, cover);
  float h = max(low.a * 0.9, high.a * cover);

  // Fine layer: close up, small curls in the light and shade of the big forms.
  vec4 fine = sea(u_view + d * (1.0 + ${PARALLAX_FINE.toFixed(3)}) + u_offLow * 1.3 + vec2(911.0, 4513.0), ${SCALE_FINE.toFixed(1)});
  float fl = dot(fine.rgb, vec3(0.3, 0.59, 0.11));
  col *= mix(1.0, clamp(0.55 + fl * 0.5, 0.75, 1.15), u_fine * 0.55);

  // Broad patches, thousands of map px across, a little brighter or duller: the average of the texture
  // (a coarse mipmap level) over a much larger repeat.
  float broad = texture2D(u_sea, (m + u_offLow * 0.5) / 61000.0 * vec2(1.0, SQUASH), 7.0).a;
  float bn = clamp((broad - 0.42) / 0.16, 0.0, 1.0);
  col *= 0.95 + 0.09 * bn;

  // Night: deep blue-grey, with a faint moonlit sheen on the highest, brightest tops.
  float lum = dot(col, vec3(0.3, 0.59, 0.11));
  vec3 nightCol = mix(vec3(0.05, 0.07, 0.12), vec3(0.26, 0.32, 0.45), pow(lum, 1.7));
  nightCol += vec3(0.45, 0.53, 0.72) * pow(lum, 6.0) * 0.32 * smoothstep(0.4, 0.9, h);
  col = mix(col, nightCol, u_night);

  // The edge: the cloud reaches in over the data along its billows, further where the heads are high and the
  // broad patches thick, so the edge has bays and headlands; it ends in a soft fringe and a faint mist beyond.
  float wisp = clamp((high.a * 0.45 + low.a * 0.4 + fine.a * 0.15 - 0.3) / 0.45, 0.0, 1.0);
  float reach = u_feather * (0.62 + 0.48 * (wisp - 0.5) * 2.0 + 0.3 * (bn - 0.5) * 2.0);
  reach = max(reach, 160.0);   // always over the outline itself, so the background never shows
  float a = smoothstep(-reach - 110.0, -reach + 110.0, sd);
  float mist = 0.2 * smoothstep(-reach - 420.0, -reach - 60.0, sd) * smoothstep(0.35, 0.8, fine.a);
  a = max(a, mist);
  // a soft shadow of the cloud on the ground just inside it
  float sh = smoothstep(-reach - 380.0, -reach + 40.0, sd) * (1.0 - a) * mix(0.22, 0.35, u_night);
  vec3 shCol = mix(vec3(0.10, 0.14, 0.24), vec3(0.01, 0.02, 0.05), u_night);
  gl_FragColor = vec4(col * a + shCol * sh, a + sh);
}`;

function compile(gl: WebGLRenderingContext, type: number, src: string) {
  const s = gl.createShader(type)!;
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? "shader");
  return s;
}

async function bitmap(url: string) {
  const blob = await (await fetch(url)).blob();
  // the alpha channel is the cloud height, not transparency: keep the colours exactly as stored
  return createImageBitmap(blob, { premultiplyAlpha: "none", colorSpaceConversion: "none" });
}

export function Clouds({ map, night }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const nightRef = useRef(night);
  const draw = useRef<() => void>(() => {});
  // drift of the low and the high layer, map px per second
  const wind = useRef<[[number, number], [number, number]]>([WIND_LOW, WIND_HIGH]);
  useEffect(() => {
    nightRef.current = night;
    draw.current();
  }, [night]);

  useEffect(() => {
    const cv = canvas.current;
    if (!cv) return;
    const gl = cv.getContext("webgl", { alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false });
    if (!gl) return;
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    let alive = true;
    let info: CloudInfo | null = null;
    let dist: Uint8Array | null = null;   // the mask on the CPU, to tell whether any cloud is on screen

    const prog = gl.createProgram()!;
    try {
      gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VS));
      gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FS));
    } catch (e) {
      console.warn("clouds:", e);
      return;
    }
    gl.linkProgram(prog);
    const textures: WebGLTexture[] = [];
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, "a_pos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
    const U = (n: string) => gl.getUniformLocation(prog, n);
    const u = {
      res: U("u_res"), dpr: U("u_dpr"), view: U("u_view"), z: U("u_z"), offLow: U("u_offLow"), offHigh: U("u_offHigh"),
      mask0: U("u_mask0"), range: U("u_range"), feather: U("u_feather"), night: U("u_night"), fine: U("u_fine"),
    };
    gl.uniform1i(U("u_sea"), 0);
    gl.uniform1i(U("u_mask"), 1);

    /** Whether any cloud can show in the view: the furthest point of the view from the data, from the mask. */
    const visible = (v: { x: number; y: number; z: number }, W: number, H: number) => {
      if (!info || !dist) return false;
      const mk = info.mask;
      const hw = W / 2 / v.z, hh = H / 2 / v.z;
      const c0 = Math.floor((v.x - hw - mk.x0) / mk.scale) - 1, c1 = Math.ceil((v.x + hw - mk.x0) / mk.scale) + 1;
      const r0 = Math.floor((v.y - hh - mk.y0) / mk.scale) - 1, r1 = Math.ceil((v.y + hh - mk.y0) / mk.scale) + 1;
      if (c0 < 0 || r0 < 0 || c1 >= mk.w || r1 >= mk.h) return true;
      const limit = 128 - (FEATHER * 2.2 / mk.range) * 127;
      const step = Math.max(1, Math.floor((c1 - c0) / 64));
      for (let r = r0; r <= r1; r += step)
        for (let c = c0; c <= c1; c += step) if (dist[r * mk.w + c] > limit) return true;
      return false;
    };

    // The cloud texture (650 KB) is only fetched the first time a cloud can show, not at start-up.
    let sea: "none" | "loading" | "ready" = "none";
    const loadSea = () => {
      if (sea !== "none" || !info) return;
      sea = "loading";
      bitmap(asset(info.texture.file)).then((img) => {
        if (!alive) { img.close(); return; }
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
        gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
        const tSea = gl.createTexture()!;
        textures.push(tSea);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, tSea);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
        img.close();
        sea = "ready";
        redraw();
      }).catch((e) => { console.warn("clouds:", e); });
    };

    // drift so far, map px (added up frame by frame, so a change of wind never makes the clouds jump)
    const offLow = [0, 0], offHigh = [0, 0];
    const moving = () => !still && quality.tier < 1;
    const paint = (dt: number) => {
      const m = map.current, v = m?.getView();
      if (!v || !info) return false;
      // The cloud is soft: half the CSS resolution is enough, scaled up by the browser.
      const res = canvasDpr(2) * 0.5;
      const W = window.innerWidth, H = window.innerHeight;
      const bw = Math.round(W * res), bh = Math.round(H * res);
      if (cv.width !== bw || cv.height !== bh) { cv.width = bw; cv.height = bh; }
      gl.viewport(0, 0, bw, bh);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      if (!visible(v, W, H)) return false;
      if (sea !== "ready") { loadSea(); return false; }
      if (moving()) {
        const s = Math.min(dt, 100) / 1000;
        const [wl, wh] = wind.current;
        offLow[0] = (offLow[0] + wl[0] * s) % SCALE_LOW; offLow[1] = (offLow[1] + wl[1] * s) % (SCALE_LOW / 1.6);
        offHigh[0] = (offHigh[0] + wh[0] * s) % SCALE_HIGH; offHigh[1] = (offHigh[1] + wh[1] * s) % (SCALE_HIGH / 1.6);
      }
      const mk = info.mask;
      gl.uniform2f(u.res, bw, bh);
      gl.uniform1f(u.dpr, res);
      gl.uniform2f(u.view, v.x, v.y);
      gl.uniform1f(u.z, v.z);
      gl.uniform2f(u.offLow, offLow[0], offLow[1]);
      gl.uniform2f(u.offHigh, offHigh[0], offHigh[1]);
      gl.uniform4f(u.mask0, mk.x0, mk.y0, mk.w * mk.scale, mk.h * mk.scale);
      gl.uniform1f(u.range, mk.range);
      gl.uniform1f(u.feather, FEATHER);
      gl.uniform1f(u.night, nightRef.current);
      const s = Math.min(Math.max((v.z - 0.08) / (0.35 - 0.08), 0), 1);
      gl.uniform1f(u.fine, s * s * (3 - 2 * s));
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      return true;
    };

    // On the frame clock: in the map's own frame whenever it moves, and every frame while a cloud is on screen and
    // drifting. Still with reduced motion, and on the lower quality tiers.
    let showing = false, dirty = true;
    const sub = subscribe((_t, dt, moved) => {
      if (!moved && !dirty && !(showing && moving())) return;
      dirty = false;
      showing = paint(dt);
    }, { name: "clouds" });
    const redraw = () => { dirty = true; sub.wake(); };
    draw.current = redraw;
    const offQ = onQuality(redraw);

    fetch(asset("/data/clouds.json"))
      .then((r) => r.json())
      .then(async (d: CloudInfo) => {
        const mask = await bitmap(asset(d.mask.file));
        if (!alive) return;
        // the mask's values on the CPU as well
        const oc = new OffscreenCanvas(mask.width, mask.height);
        const g = oc.getContext("2d")!;
        g.drawImage(mask, 0, 0);
        const px = g.getImageData(0, 0, mask.width, mask.height).data;
        dist = new Uint8Array(mask.width * mask.height);
        for (let i = 0; i < dist.length; i++) dist[i] = px[i * 4];

        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
        gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
        const tMask = gl.createTexture()!;
        textures.push(tMask);
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, tMask);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, gl.LUMINANCE, gl.UNSIGNED_BYTE, mask);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        mask.close();
        info = d;
        redraw();
      })
      .catch((e) => console.warn("clouds:", e));

    window.addEventListener("resize", redraw);
    return () => {
      alive = false;
      sub();
      offQ();
      window.removeEventListener("resize", redraw);
      draw.current = () => {};
      // the canvas (and its context) may be used again by the next mount: free what this one made
      for (const t of textures) gl.deleteTexture(t);
      gl.deleteBuffer(buf);
      gl.deleteProgram(prog);
    };
  }, [map]);

  return <canvas ref={canvas} aria-hidden="true"
    style={{ position: "fixed", inset: 0, width: "100vw", height: "100vh", pointerEvents: "none", zIndex: 1 }} />;
}
