import { useEffect, useRef } from "react";
import { asset } from "../config";
import { canvasDpr, subscribe } from "../frame";
import { span as perfSpan } from "../perf";
import { useLang, type Lang } from "../i18n";
import type { MapHandle } from "./IsometricMap";

// Projected OSM lines (web/public/data/lines.json, from pipeline/build_places.py), in map pixels.
// n: English name, nf: Persian name, k: highway class, t: in a tunnel.
interface Line { id: number; n?: string; nf?: string; k?: string; t?: boolean; p: number[] }
interface Lines { roads: Line[]; water: Line[] }
interface Prepared extends Line { box: [number, number, number, number]; len: number; cum: number[]; path?: Path2D }

const ROAD_STYLE: Record<string, [string, number]> = {
  trunk: ["#ff8c42", 3], primary: ["#ffb347", 2.6], secondary: ["#ffd166", 2.2], tertiary: ["#fff3b0", 1.8],
  residential: ["#ffffff", 1.4], unclassified: ["#ffffff", 1.4], living_street: ["#e0f2fe", 1.2],
  pedestrian: ["#f5e6c8", 1.4], busway: ["#ff8f80", 1.6],
};
const MAJOR = new Set(["trunk", "primary", "secondary"]);

function prepare(l: Line): Prepared {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const cum = [0];
  for (let i = 0; i < l.p.length; i += 2) {
    const x = l.p[i], y = l.p[i + 1];
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
    if (i) cum.push(cum[cum.length - 1] + Math.hypot(x - l.p[i - 2], y - l.p[i - 1]));
  }
  return { ...l, box: [x0, y0, x1, y1], len: cum[cum.length - 1], cum };
}

function mapPath(r: Prepared, closed = false): Path2D {
  if (!r.path) {
    const p = new Path2D();
    p.moveTo(r.p[0], r.p[1]);
    for (let i = 2; i < r.p.length; i += 2) p.lineTo(r.p[i], r.p[i + 1]);
    if (closed) p.closePath();
    r.path = p;
  }
  return r.path;
}

/** Point and heading at distance s along a polyline. */
function at(r: Prepared, s: number): [number, number, number] {
  let i = 1;
  while (i < r.cum.length - 1 && r.cum[i] < s) i++;
  const a = (i - 1) * 2, seg = r.cum[i] - r.cum[i - 1] || 1, f = Math.min(Math.max((s - r.cum[i - 1]) / seg, 0), 1);
  const dx = r.p[a + 2] - r.p[a], dy = r.p[a + 3] - r.p[a + 1];
  return [r.p[a] + dx * f, r.p[a + 1] + dy * f, Math.atan2(dy, dx)];
}

const nameOf = (r: Line, lang: Lang) => (lang === "fa" ? r.nf || r.n : r.n || r.nf);

/**
 * The Streets layer: roads coloured by class and water outlines, with street names in the interface language
 * (the other language when a street has no name in it). Drawn on the frame clock, in the same frame as the map.
 */
export function Streets({ map, on }: { map: React.RefObject<MapHandle>; on: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const st = useRef<{ roads?: Prepared[]; water?: Prepared[] }>({});
  const lang = useLang();
  const flags = useRef({ on, lang });
  flags.current = { on, lang };
  const asked = useRef(false);
  const wake = useRef<() => void>(() => {});

  useEffect(() => {
    if (!on || asked.current) return;
    asked.current = true;
    fetch(asset("/data/lines.json")).then((r) => r.json()).then((L: Lines) => {
      st.current = { roads: L.roads.filter((r) => !r.t).map(prepare), water: L.water.map(prepare) };
      wake.current();
    }).catch(() => { asked.current = false; });
  }, [on]);

  useEffect(() => { wake.current(); }, [on, lang]);

  useEffect(() => {
    const draw = () => {
      const cv = canvas.current, v = map.current?.getView(), { roads, water } = st.current;
      if (!cv || !v) return;
      const dpr = canvasDpr(2), W = window.innerWidth, H = window.innerHeight;
      const bw = Math.round(W * dpr), bh = Math.round(H * dpr);
      if (cv.width !== bw || cv.height !== bh) { cv.width = bw; cv.height = bh; }
      const g = cv.getContext("2d")!;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);
      if (!flags.current.on || !roads || !water) return;
      const end = perfSpan("streets");
      const z = v.z, ox = W / 2 - v.x * z, oy = H / 2 - v.y * z;
      const vis = (b: number[]) => b[2] * z + ox > -50 && b[0] * z + ox < W + 50 && b[3] * z + oy > -50 && b[1] * z + oy < H + 50;
      g.setTransform(dpr * z, 0, 0, dpr * z, dpr * ox, dpr * oy);
      g.lineJoin = "round"; g.lineCap = "round";
      g.fillStyle = "rgba(56,152,255,.3)"; g.strokeStyle = "rgba(56,152,255,.9)"; g.lineWidth = 1.5 / z;
      for (const w of water) if (vis(w.box)) { const p = mapPath(w, true); g.fill(p); g.stroke(p); }
      const zs = Math.min(Math.max(z * 4, 0.6), 2.2);
      for (const r of roads) {
        const sty = ROAD_STYLE[r.k ?? ""];
        if (!sty || !vis(r.box)) continue;
        if (z < 0.12 && !MAJOR.has(r.k!)) continue;
        const p = mapPath(r);
        g.strokeStyle = "rgba(20,24,32,.55)"; g.lineWidth = (sty[1] * zs + 2) / z; g.stroke(p);
        g.strokeStyle = sty[0]; g.lineWidth = (sty[1] * zs) / z; g.stroke(p);
      }
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (z >= 0.22) {
        const lang = flags.current.lang;
        g.font = `600 ${Math.round(11 + Math.min(z, 1.2) * 4)}px ${lang === "fa" ? "Vazirmatn, Tahoma, " : ""}system-ui, sans-serif`;
        g.textAlign = "center"; g.textBaseline = "middle";
        const used: [number, number, number, number][] = [], seen = new Set<string>();
        for (const r of roads) {
          const name = nameOf(r, lang);
          if (!name || seen.has(name) || r.len < 120 || !vis(r.box) || r.k === "footway" || r.k === "steps") continue;
          const [x, y, ang] = at(r, r.len / 2);
          const sx = x * z + ox, sy = y * z + oy;
          if (sx < 40 || sx > W - 40 || sy < 40 || sy > H - 40) continue;
          const tw = g.measureText(name).width;
          if (tw > r.len * z * 0.9) continue;
          const bx: [number, number, number, number] = [sx - tw / 2 - 4, sy - 12, sx + tw / 2 + 4, sy + 12];
          if (used.some((u) => bx[0] < u[2] && bx[2] > u[0] && bx[1] < u[3] && bx[3] > u[1])) continue;
          used.push(bx); seen.add(name);
          let a = ang;
          if (a > Math.PI / 2) a -= Math.PI; else if (a < -Math.PI / 2) a += Math.PI;
          g.save(); g.translate(sx, sy); g.rotate(a);
          g.lineWidth = 3.5; g.strokeStyle = "rgba(20,24,32,.85)"; g.strokeText(name, 0, 0);
          g.fillStyle = "#fff"; g.fillText(name, 0, 0);
          g.restore();
        }
      }
      end();
    };
    const sub = subscribe(draw, { still: true, name: "streets" });
    wake.current = sub.wake;
    return () => { sub(); };
  }, [map]);

  return <canvas ref={canvas} className="overlay" aria-hidden="true" />;
}
