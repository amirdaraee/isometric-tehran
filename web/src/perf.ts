// Frame-time measurements, for the readout (?on=perfHud) and the timing harness (scripts/perf.mjs, ?perftest).
//
// Nothing here costs anything unless one of the two is asked for: `span` then returns a no-op.
//
//   const end = span("water");  draw();  end();      // time one layer's work in this frame
//   time("clouds", () => paint());                     // the same, around a function
//   gauge("cars", () => sim.alive);                    // a number read when the readout or the harness asks
//
// Per frame, the time of every named span is added up; the last 600 frames are kept for each name, so the
// readout shows each layer's average and 95th percentile, next to the frame interval itself. Long tasks (over
// 50 ms) are counted with a PerformanceObserver. The harness reads everything through window.__perf.
import { on } from "./features";

const q = new URLSearchParams(location.search);
/** True when frame times are being collected. */
export const perfOn = on("perfHud") || q.has("perftest");

const N = 600;

class Ring {
  a = new Float32Array(N);
  n = 0;
  i = 0;
  push(v: number) { this.a[this.i] = v; this.i = (this.i + 1) % N; this.n = Math.min(this.n + 1, N); }
  values(): number[] { return Array.from(this.a.subarray(0, this.n)); }
  clear() { this.n = 0; this.i = 0; }
}

function pct(sorted: number[], p: number): number {
  if (!sorted.length) return 0;
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * p)))];
}

export interface Summary { n: number; mean: number; p50: number; p90: number; p95: number; p99: number; max: number }

export function summarise(v: number[]): Summary {
  const s = [...v].sort((a, b) => a - b);
  const mean = s.length ? s.reduce((a, b) => a + b, 0) / s.length : 0;
  return { n: s.length, mean, p50: pct(s, 0.5), p90: pct(s, 0.9), p95: pct(s, 0.95), p99: pct(s, 0.99), max: s[s.length - 1] ?? 0 };
}

const acc = new Map<string, number>();        // ms spent in each named span during the current frame
const rings = new Map<string, Ring>();        // per frame, per name
const intervals = new Ring();                 // ms between animation frames
const gauges = new Map<string, () => number>();
let longTasks: { start: number; ms: number }[] = [];
// The harness also wants every interval of a phase, not just the last 600.
let record: number[] | null = null;

const noop = () => {};

/** Starts timing `name`; call the returned function when the work is done. Nested or repeated spans add up. */
export function span(name: string): () => void {
  if (!perfOn) return noop;
  const t0 = performance.now();
  return () => { acc.set(name, (acc.get(name) ?? 0) + performance.now() - t0); };
}

/** Runs `fn`, timed as `name`. */
export function time<T>(name: string, fn: () => T): T {
  if (!perfOn) return fn();
  const end = span(name);
  try { return fn(); } finally { end(); }
}

/** A number to show in the readout and the harness (cars alive, tiles cached and so on). */
export function gauge(name: string, read: () => number): () => void {
  gauges.set(name, read);
  return () => { if (gauges.get(name) === read) gauges.delete(name); };
}

export interface Snapshot {
  frame: Summary;
  fps: number;
  layers: Record<string, Summary>;
  gauges: Record<string, number>;
  longTasks: { count: number; maxMs: number; over200: number };
  heapMB: number | null;
}

/** The last 600 frames, the gauges and the long tasks since the last reset. */
export function snapshot(): Snapshot {
  const frame = summarise(intervals.values());
  const layers: Record<string, Summary> = {};
  for (const [k, r] of rings) layers[k] = summarise(r.values());
  const g: Record<string, number> = {};
  for (const [k, read] of gauges) { try { g[k] = read(); } catch { /* not ready */ } }
  const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory;
  return {
    frame, fps: frame.mean ? 1000 / frame.mean : 0, layers, gauges: g,
    longTasks: { count: longTasks.length, maxMs: Math.max(0, ...longTasks.map((t) => t.ms)), over200: longTasks.filter((t) => t.ms > 200).length },
    heapMB: mem ? mem.usedJSHeapSize / 1048576 : null,
  };
}

/** Forgets every measurement so far (the harness calls this at the start of each phase). */
export function reset() {
  intervals.clear();
  for (const r of rings.values()) r.clear();
  longTasks = [];
}

if (perfOn) {
  // Every animation-frame callback is timed together as "raf" (the overlay, water and cloud loops and the like;
  // the map's own loop took its reference to requestAnimationFrame before this ran, and is timed as "map").
  const raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (cb: FrameRequestCallback) => raf((t) => {
    const t0 = performance.now();
    try { cb(t); } finally { acc.set("raf", (acc.get("raf") ?? 0) + performance.now() - t0); }
  });
  let last = -1;
  const tick = (t: number) => {
    raf(tick);
    if (last >= 0) {
      const dt = t - last;
      intervals.push(dt);
      record?.push(dt);
    }
    last = t;
    // close the frame for every name seen so far (0 for a frame in which a layer did nothing)
    for (const k of acc.keys()) if (!rings.has(k)) rings.set(k, new Ring());
    for (const [k, r] of rings) { r.push(acc.get(k) ?? 0); }
    acc.clear();
  };
  raf(tick);
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) longTasks.push({ start: e.startTime, ms: e.duration });
    }).observe({ type: "longtask", buffered: true });
  } catch { /* long tasks are not reported in this browser */ }
  // Canvases on the page and the memory their pixels take (4 bytes a pixel, for 2D and WebGL alike).
  gauge("canvases", () => document.querySelectorAll("canvas").length);
  gauge("canvasMB", () => {
    let px = 0;
    document.querySelectorAll("canvas").forEach((c) => { px += c.width * c.height; });
    return px * 4 / 1048576;
  });
  (window as unknown as { __perf: unknown }).__perf = {
    snapshot, reset,
    /** Starts keeping every frame interval until stopRecord(). */
    startRecord: () => { record = []; },
    stopRecord: () => { const r = record ?? []; record = null; return r; },
    longTasksSince: (t: number) => longTasks.filter((x) => x.start >= t),
  };
  if (on("perfHud")) startHud();
}

/** The readout: a small fixed panel, rewritten twice a second (plain DOM, so it costs the page next to nothing). */
function startHud() {
  const el = document.createElement("pre");
  el.setAttribute("aria-hidden", "true");
  el.style.cssText = "position:fixed;left:8px;top:8px;z-index:20;margin:0;padding:6px 8px;border-radius:8px;" +
    "background:rgba(10,14,24,.82);color:#d8f3dc;font:11px/1.35 ui-monospace,Menlo,monospace;pointer-events:none;white-space:pre";
  const add = () => document.body.appendChild(el);
  if (document.body) add(); else addEventListener("DOMContentLoaded", add);
  const f1 = (x: number) => x.toFixed(1);
  setInterval(() => {
    const s = snapshot();
    const lines = [
      `${f1(s.fps)} fps   frame p50 ${f1(s.frame.p50)}  p95 ${f1(s.frame.p95)}  max ${f1(s.frame.max)} ms`,
      `long tasks ${s.longTasks.count} (max ${Math.round(s.longTasks.maxMs)} ms)${s.heapMB !== null ? `   heap ${Math.round(s.heapMB)} MB` : ""}`,
      "layer        avg   p95  ms/frame",
      ...Object.entries(s.layers).sort((a, b) => b[1].mean - a[1].mean)
        .map(([k, v]) => `${k.padEnd(11)} ${v.mean.toFixed(2).padStart(5)} ${v.p95.toFixed(2).padStart(5)}`),
      Object.entries(s.gauges).map(([k, v]) => `${k} ${Number.isInteger(v) ? v : v.toFixed(1)}`).join("  "),
    ];
    el.textContent = lines.join("\n");
  }, 500);
}
