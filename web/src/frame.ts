// One frame clock for everything that animates: a single requestAnimationFrame loop that calls each subscriber at
// most once per frame, at most `fps` times a second, and at once when the map has moved.
//
//   const stop = subscribe((t, dt, moved) => draw(t), { fps: 30, name: "water" });
//   stop.wake();    // ask for one call next frame (a "still" layer whose content changed)
//   stop();
//
// t: the requestAnimationFrame timestamp, ms. dt: ms since this subscriber's previous call (0 on the first call).
// moved: the map moved since the previous frame. IsometricMap reports every redraw of the map (its
// "update-viewport") with markMoved(); the clock's own frame then runs right after the map's in the same frame, so
// a layer drawn here never trails the map, and is not drawn twice. Options:
//   fps    highest rate wanted while nothing moves (omit for every frame);
//   still  call only when the map moved or after wake(), never just because time passed (pins, a frozen layer);
//   name   the name under which its time shows in the readout (perf.ts, ?on=perfHud).
// The loop stops when nobody is subscribed, and the browser stops it while the tab is hidden.
//
// Adaptive quality (switch adaptiveQuality): the clock watches its own frame intervals. Over each 3 s of frames, if
// the 90th percentile is over 22 ms twice running, it steps down a tier and remembers it for the session:
//   tier 1: tilt-shift off, clouds stand still, water at 15 fps;
//   tier 2: canvases at device pixel ratio 1, half the cars and no birds (read by the overlay).
// ?quality=1 or 2 forces a tier, for tests. Layers read quality.tier, or follow it with onQuality().
import { on } from "./features";
import { gauge, span } from "./perf";

export type FrameFn = (t: number, dt: number, moved: boolean) => void;
export interface FrameOptions {
  /** Highest rate wanted, frames per second. Omit for every frame. */
  fps?: number;
  /** Call only when the map moved or after wake(). */
  still?: boolean;
  /** Name in the timing readout. */
  name?: string;
}
export type Subscription = (() => void) & {
  /** One call next frame, even for a still subscriber. */
  wake: () => void;
  /** A new highest rate (for example when the quality tier changes). */
  setFps: (fps?: number) => void;
};

interface Sub { fn: FrameFn; minGap: number; still: boolean; name: string; last: number; woken: boolean }

const subs: Sub[] = [];
let raf = 0, moved = false, lastTick = -1;

function tick(t: number) {
  raf = subs.length ? requestAnimationFrame(tick) : 0;
  if (lastTick >= 0) watch(t - lastTick);
  lastTick = t;
  run(t);
}

function run(t: number) {
  const was = moved;
  moved = false;
  for (const s of subs.slice()) {
    // a small tolerance so that 30 fps on a 60 Hz screen doesn't drop to 20
    const due = s.still ? was || s.woken : was || s.woken || s.last < 0 || t - s.last >= s.minGap - 2;
    if (!due) continue;
    const dt = s.last < 0 ? 0 : t - s.last;
    s.last = t;
    s.woken = false;
    const end = span(s.name);
    try { s.fn(t, dt, was); } catch (e) { console.error(e); } finally { end(); }
  }
}

function start() { if (!raf && subs.length) { lastTick = -1; raf = requestAnimationFrame(tick); } }

/** Calls `fn` once per animation frame (at most `fps` times a second, and whenever the map moves) until the
 * returned function is called. */
export function subscribe(fn: FrameFn, opts: FrameOptions = {}): Subscription {
  const s: Sub = { fn, minGap: opts.fps && opts.fps > 0 ? 1000 / opts.fps : 0, still: !!opts.still, name: opts.name ?? "frame", last: -1, woken: true };
  subs.push(s);
  start();
  const stop = (() => {
    const i = subs.indexOf(s);
    if (i >= 0) subs.splice(i, 1);
    if (!subs.length && raf) { cancelAnimationFrame(raf); raf = 0; }
  }) as Subscription;
  stop.wake = () => { s.woken = true; start(); };
  stop.setFps = (fps?: number) => { s.minGap = fps && fps > 0 ? 1000 / fps : 0; };
  return stop;
}

/**
 * The map has been redrawn at a new place (called by IsometricMap from inside the map's own frame). Subscribers
 * hear it in this frame's tick. If this frame's tick has already run (the clock's callback came before the map's),
 * the subscribers are called once more now, and the clock moves its callback after the map's for the frames to come.
 */
export function markMoved() {
  moved = true;
  const now = (document.timeline?.currentTime as number | null | undefined) ?? null;
  if (now !== null && now === lastTick) {
    // the map's frame came after the clock's: catch up now, and line up behind the map from the next frame on
    // (a request made after the map's own callback has asked for the next frame comes after it)
    run(now);
    queueMicrotask(() => { if (raf) { cancelAnimationFrame(raf); raf = requestAnimationFrame(tick); } });
  } else start();
}

// ---- Adaptive quality ----

const q = new URLSearchParams(location.search);
const forced = q.get("quality");
const KEY = "isotehran.quality";
function stored(): number {
  try { return Number(sessionStorage.getItem(KEY) ?? 0) || 0; } catch { return 0; }
}
export const quality = { tier: forced !== null ? Math.max(0, Math.min(2, Number(forced) || 0)) : on("adaptiveQuality") ? stored() : 0 };
const listeners = new Set<(tier: number) => void>();
document.documentElement.dataset.quality = String(quality.tier);
gauge("tier", () => quality.tier);

/** Calls `fn` with the new tier whenever it changes. Returns an unsubscribe. */
export function onQuality(fn: (tier: number) => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** Device pixels per CSS pixel for a canvas: the screen's, at most `cap`, and 1 on tier 2. */
export function canvasDpr(cap = 2): number {
  return Math.min(window.devicePixelRatio || 1, cap, quality.tier >= 2 ? 1 : cap);
}

const WINDOW_MS = 3000, LIMIT_MS = 22;
let win: number[] = [], winMs = 0, bad = 0, warm = 0;
function watch(dt: number) {
  if (!on("adaptiveQuality") || forced !== null || quality.tier >= 2) return;
  if (dt > 250) return; // a stall (tab switch, debugger), not the drawing
  // the first 3 s after start are tile loading, not drawing
  if (warm < WINDOW_MS) { warm += dt; return; }
  win.push(dt);
  winMs += dt;
  if (winMs < WINDOW_MS) return;
  const s = win.sort((a, b) => a - b);
  const p90 = s[Math.floor((s.length - 1) * 0.9)];
  win = []; winMs = 0;
  bad = p90 > LIMIT_MS ? bad + 1 : 0;
  if (bad < 2) return;
  bad = 0;
  quality.tier++;
  try { sessionStorage.setItem(KEY, String(quality.tier)); } catch { /* private mode */ }
  document.documentElement.dataset.quality = String(quality.tier);
  for (const fn of listeners) fn(quality.tier);
}
