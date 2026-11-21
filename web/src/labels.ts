// Which place labels the map shows at a zoom, and which of them make room for the others. Pure functions, no DOM:
// Pins.tsx measures the labels and moves them; this decides.
//
// Priority, most important first: featured landmarks, district names (far out only), the other landmarks,
// squares and other major places, everything else. Streets are never labels here (they belong to the Streets
// layer). Each kind comes in from its own zoom level; minor places first show as a dot, and get a name once the
// map is close enough and there is room.
//
// The choice depends only on the zoom, in steps of an eighth of a doubling, never on where the map is: panning
// cannot make a label come or go, and a label only changes when the zoom crosses a step.

export interface LabelPlace {
  c: string;
  x: number;
  y: number;
  /** Featured landmark. */
  f?: 1;
  /** District (quarter) name. */
  q?: 1;
  /** Has a landmark card (landmarks.json slug). */
  s?: string;
}

/** How a place shows: a pin with its name, a dot without one, a district name, or not at all. */
export type Mode = "pin" | "dot" | "quarter" | null;

/** Categories that get a name as soon as places of their rank do; the rest are minor (a dot until close). */
const MAJOR = new Set(["landmark", "square", "bazaar", "museum", "history", "government", "park", "culture", "transport"]);

/** Zoom levels (screen px per map px) at which each kind comes in. */
export const ZOOM = {
  /** Districts: only from far out, up to this zoom. */
  quarterUntil: 0.22,
  featured: 0.09,
  landmark: 0.22,
  /** Major places: a dot, then their name. */
  majorDot: 0.45,
  majorName: 0.9,
  /** Minor places: a dot, then their name, where there is room. */
  minorDot: 0.7,
  minorName: 1.1,
};

/** Rank: 0 most important. -1: never a label. */
export function rank(p: LabelPlace): number {
  if (p.c === "street") return -1;
  if (p.f) return 0;
  if (p.q) return 1;
  if (p.s) return 2;
  if (MAJOR.has(p.c)) return 3;
  return 4;
}

/**
 * How `p` shows at zoom `z` (before collisions). The active (picked) place always shows, as a pin. A place with
 * no name in the interface language (`named` false) is only ever a dot: its name is in the tooltip and the card.
 */
export function modeAt(p: LabelPlace, z: number, opts: { active?: boolean; named?: boolean } = {}): Mode {
  if (opts.active) return "pin";
  const named = opts.named ?? true;
  const pin = (dotFrom: number, nameFrom: number): Mode =>
    z >= nameFrom ? (named ? "pin" : "dot") : z >= dotFrom ? "dot" : null;
  switch (rank(p)) {
    case 0: return pin(ZOOM.featured, ZOOM.featured);
    case 1: return z < ZOOM.quarterUntil ? (named ? "quarter" : null) : null;
    case 2: return pin(ZOOM.landmark, ZOOM.landmark);
    case 3: return pin(ZOOM.majorDot, ZOOM.majorName);
    case 4: return pin(ZOOM.minorDot, ZOOM.minorName);
    default: return null;
  }
}

/** Places that can ever show as a label, most important first (the original order within a rank). */
export function candidates<T extends LabelPlace>(places: readonly T[]): T[] {
  return places
    .map((p, i) => ({ p, i, r: rank(p) }))
    .filter((e) => e.r >= 0)
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .map((e) => e.p);
}

/** The zoom rounded down to an eighth of a doubling: labels are laid out again only when this changes. */
export function zoomStep(z: number): number {
  return Math.floor(Math.log2(Math.max(z, 1e-6)) * 8 + 1e-9);
}
export function stepZoom(step: number): number {
  return 2 ** (step / 8);
}

/** A label to place: its map point and its box around that point on screen, px [left, top, right, bottom]. */
export interface Item {
  x: number;
  y: number;
  box: readonly [number, number, number, number];
}

/**
 * Greedy placement in the given order (most important first): an item shows unless its box, at zoom `z`, overlaps
 * one already shown (with `gap` px between). Works in screen px relative to the map's origin, so the answer is
 * the same wherever the map is panned. A grid keeps it near linear.
 */
export function declutter(items: readonly Item[], z: number, gap = 4): boolean[] {
  const CELL = 128;
  const grid = new Map<number, number[]>();
  const placed: [number, number, number, number][] = [];
  const out = new Array<boolean>(items.length).fill(false);
  const key = (cx: number, cy: number) => cx * 73856093 ^ cy * 19349663;
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const sx = it.x * z, sy = it.y * z;
    const l = sx + it.box[0] - gap / 2, t = sy + it.box[1] - gap / 2, r = sx + it.box[2] + gap / 2, b = sy + it.box[3] + gap / 2;
    const cx0 = Math.floor(l / CELL), cx1 = Math.floor(r / CELL), cy0 = Math.floor(t / CELL), cy1 = Math.floor(b / CELL);
    let hit = false;
    for (let cx = cx0; cx <= cx1 && !hit; cx++) {
      for (let cy = cy0; cy <= cy1 && !hit; cy++) {
        for (const j of grid.get(key(cx, cy)) ?? []) {
          const o = placed[j];
          if (l < o[2] && r > o[0] && t < o[3] && b > o[1]) { hit = true; break; }
        }
      }
    }
    if (hit) continue;
    out[i] = true;
    const j = placed.push([l, t, r, b]) - 1;
    for (let cx = cx0; cx <= cx1; cx++) {
      for (let cy = cy0; cy <= cy1; cy++) {
        const k = key(cx, cy);
        const cell = grid.get(k);
        if (cell) cell.push(j); else grid.set(k, [j]);
      }
    }
  }
  return out;
}

/** Longest name shown on a pin, px; longer names are cut with an ellipsis (the whole name is in the tooltip). */
export const PIN_TEXT_MAX = 180;

/** The box of a label on screen around its point, from the width of its text (see the .pin and .quarter CSS). */
export function boxFor(mode: Exclude<Mode, null>, textWidth: number): [number, number, number, number] {
  if (mode === "dot") return [-6, -6, 6, 6];
  if (mode === "quarter") { const w = textWidth / 2 + 4; return [-w, -10, w, 10]; }
  // the name sits centred 18 px above the point (padding 9 px, a 1 px border, about 25 px tall)
  const w = Math.min(textWidth, PIN_TEXT_MAX) / 2 + 10;
  return [-w, -44, w, 6];
}
