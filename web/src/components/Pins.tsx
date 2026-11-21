import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { forwardWheel } from "../forwardWheel";
import type { MapHandle } from "./IsometricMap";
import { gauge, span } from "../perf";
import { bilingual, nameIn, useLang, type Lang } from "../i18n";
import { boxFor, candidates, declutter, modeAt, stepZoom, zoomStep, type Item, type Mode } from "../labels";

export interface Place {
  /** Both names, "English · Persian" (the pipeline's label; the viewer orders them by the interface language). */
  n: string;
  en?: string;
  fa?: string;
  c: string;
  x: number;
  y: number;
  d?: string;
  f?: 1;
  q?: 1;
  a?: string;
  s?: string;
}

interface Props {
  places: Place[];
  map: React.RefObject<MapHandle>;
  active: Place | null;
  onPick: (p: Place) => void;
}

/** How far off screen a label is still kept in the page, px. */
const MARGIN = 200;
/** "Not known yet" for a label's shown state: the next frame writes it whatever it is. */
const UNKNOWN = 2;

/** Widths of label texts, px, measured once per text and font with a canvas (no page layout). */
const widths = new Map<string, number>();
let ctx: CanvasRenderingContext2D | null | undefined;
function textWidth(text: string, font: string, spacing = 0): number {
  const k = `${font}|${text}`;
  let w = widths.get(k);
  if (w === undefined) {
    if (ctx === undefined) ctx = document.createElement("canvas").getContext("2d");
    if (ctx) ctx.font = font;
    w = (ctx ? ctx.measureText(text).width : text.length * 7) + spacing * text.length;
    widths.set(k, w);
  }
  return w;
}

/** Whether a place has a name in this language (not only in the other one). */
function hasName(p: Place, lang: Lang): boolean {
  return !!(lang === "fa" ? p.fa : p.en)?.trim();
}

/** The name on the map: in the interface language only (both names for the picked place, and in the tooltip). */
function labelText(p: Place, active: boolean, lang: Lang): string {
  return active ? bilingual(p, lang) : nameIn(p, lang);
}

/**
 * Place labels. React renders one element per place that can ever show (streets never do), and renders again
 * only when the language or the picked place changes. Everything that follows the map happens in the map's own
 * redraw (map.onFrame), in the same frame as the map is drawn: every label is moved there with one offset and
 * scale for the whole frame, so no label trails the map. Which labels show (src/labels.ts: by rank, zoom and
 * room) is worked out again only when the zoom crosses a step; panning never changes it.
 */
export function Pins({ places, map, active, onPick }: Props) {
  const lang = useLang();
  const list = useMemo(() => {
    const c = candidates(places).filter((p) => p !== active);
    // the picked place first; a place picked from the search (a street, say) shows while it is picked
    return active ? [active, ...c] : c;
  }, [places, active]);
  // React keys: one per place for the page's life (two places may share a name)
  const keys = useMemo(() => new Map(places.map((p, i) => [p, `p${i}`])), [places]);
  const els = useRef<(HTMLElement | null)[]>([]);
  const refs = useRef<((el: HTMLElement | null) => void)[]>([]);
  const box = useRef<HTMLDivElement>(null);
  // per label: whether the layout keeps it, whether it is in the page now, and how it shows
  const st = useRef({ step: NaN, keep: new Uint8Array(0), on: new Uint8Array(0), mode: [] as Mode[], count: 0, many: false });

  const layout = (z: number) => {
    const s = st.current, n = list.length;
    s.step = zoomStep(z);
    const zq = stepZoom(s.step);
    const items: Item[] = [], idx: number[] = [];
    const family = getComputedStyle(document.body).fontFamily;
    for (let i = 0; i < n; i++) {
      const p = list[i];
      const mode = modeAt(p, zq, { active: p === active, named: hasName(p, lang) });
      s.mode[i] = mode;
      s.keep[i] = 0;
      if (!mode) continue;
      const text = labelText(p, p === active, lang);
      // .quarter: 800 13px, upper case, letter-spacing .2em; .pin span: 600 12px
      const w = mode === "quarter" ? textWidth(text.toUpperCase(), `800 13px ${family}`, 2.6) : textWidth(text, `600 12px ${family}`);
      items.push({ x: p.x, y: p.y, box: boxFor(mode, w) });
      idx.push(i);
    }
    const keep = declutter(items, zq);
    for (let k = 0; k < keep.length; k++) if (keep[k]) s.keep[idx[k]] = 1;
  };

  // A new function each render (it reads this render's list); the one handed to the map calls the latest.
  const frame = useRef<() => void>(() => {});
  frame.current = () => {
    const m = map.current;
    const v = m?.getView(), o = m?.toScreen(0, 0);
    if (!v || !o) return;
    const end = span("pins");
    const s = st.current;
    if (zoomStep(v.z) !== s.step) layout(v.z);
    const vw = window.innerWidth, vh = window.innerHeight, z = v.z;
    let count = 0;
    for (let i = 0; i < list.length; i++) {
      const el = els.current[i];
      if (!el) continue;
      let on = 0;
      if (s.keep[i]) {
        const x = o.x + list[i].x * z, y = o.y + list[i].y * z;
        if (x > -MARGIN && x < vw + MARGIN && y > -MARGIN && y < vh + MARGIN) {
          on = 1;
          count++;
          el.style.transform = `translate(${x}px,${y}px)`;
          const dot = s.mode[i] === "dot";
          if (el.classList.contains("dot") !== dot) el.classList.toggle("dot", dot);
        }
      }
      if (on !== s.on[i]) { s.on[i] = on; el.style.display = on ? "" : "none"; }
    }
    // many labels at once: plain backgrounds instead of each label blurring what is behind it (costly on phones)
    const many = count > 20;
    if (many !== s.many && box.current) { s.many = many; box.current.classList.toggle("many", many); }
    s.count = count;
    end();
  };

  useEffect(() => gauge("pins", () => st.current.count), []);
  // a new list or language: lay out again, and place the labels before the browser paints
  useLayoutEffect(() => {
    const s = st.current, n = list.length;
    s.step = NaN;
    s.keep = new Uint8Array(n);
    s.on = new Uint8Array(n).fill(UNKNOWN);
    s.mode = new Array<Mode>(n).fill(null);
    frame.current();
  }, [list, lang]);
  useEffect(() => map.current?.onFrame(() => frame.current()), [map]);

  // one ref callback per slot for the page's life (a new one each render would detach and attach every label)
  const refFor = (i: number) => (refs.current[i] ??= (el: HTMLElement | null) => { els.current[i] = el; });

  return (
    <div ref={box} className="pins" onWheel={forwardWheel}>
      {list.map((p, i) => {
        const isActive = p === active;
        const text = labelText(p, isActive, lang);
        const title = bilingual(p, lang);
        // hidden until a frame places it
        return p.q && !isActive ? (
          <div key={keys.get(p)} ref={refFor(i)} className="quarter" style={{ display: "none" }} title={title}>
            <span dir="auto">{text}</span>
          </div>
        ) : (
          <button
            key={keys.get(p) ?? p.n}
            ref={refFor(i)}
            type="button"
            className={`pin cat-${p.c}${isActive ? " on" : ""}`}
            style={{ display: "none" }}
            title={title}
            onClick={() => onPick(p)}
          >
            <span dir="auto">{text}</span>
            <i aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
