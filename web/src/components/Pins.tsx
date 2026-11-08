import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { forwardWheel } from "../forwardWheel";
import type { MapHandle } from "./IsometricMap";
import { useView } from "../viewStore";
import { gauge, span } from "../perf";
import { bilingual, useLang } from "../i18n";

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

const LABEL_W = 210;
const LABEL_H = 34;

// Places in priority order, keeping only those whose labels don't collide.
// React decides which labels show; their positions are written straight to the DOM in the map's own redraw
// (map.onFrame), so they never lag a frame behind the map while it moves.
export function Pins({ places, map, active, onPick }: Props) {
  // re-rendered on every move of the map (only this component, not the page)
  const view = useView((v) => v);
  const lang = useLang();
  const featured = useMemo(() => places.filter((p) => p.f || p.q || p.s), [places]);
  const els = useRef(new Map<string, { el: HTMLElement; p: Place }>());
  const trackers = useRef(new Map<Place, (el: HTMLElement | null) => void>());

  const place = useRef(() => {
    const m = map.current;
    if (!m) return;
    const end = span("pins");
    for (const { el, p } of els.current.values()) {
      const s = m.toScreen(p.x, p.y);
      if (s) el.style.transform = `translate(${s.x}px,${s.y}px)`;
    }
    end();
  }).current;
  useEffect(() => gauge("pins", () => els.current.size), []);
  useLayoutEffect(place); // newly shown labels get their position before the browser paints
  useEffect(() => map.current?.onFrame(place), [map, place]);

  if (!view || !map.current) return null;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const quartersOnly = view.z < 0.09;
  const showPins = view.z >= 0.09;
  const placed: { x: number; y: number }[] = [];
  const out: JSX.Element[] = [];
  const ordered = [...(active ? [active] : []), ...featured.filter((p) => p !== active)];
  // one ref callback per place for the page's life: a new one each render (every frame while the map moves) would
  // make React detach and attach every label each time
  const track = (p: Place) => {
    let fn = trackers.current.get(p);
    if (!fn) {
      fn = (el: HTMLElement | null) => { if (el) els.current.set(p.n, { el, p }); else els.current.delete(p.n); };
      trackers.current.set(p, fn);
    }
    return fn;
  };
  for (const p of ordered) {
    const isQuarter = !!p.q;
    if (p !== active) {
      if (quartersOnly && !isQuarter) continue;
      if (isQuarter && view.z > 0.3) continue;
      if (!isQuarter && !showPins) continue;
      // landmarks that are not featured wait until the map is zoomed in a little more
      if (!p.f && !isQuarter && view.z < 0.22) continue;
    }
    const s = map.current.toScreen(p.x, p.y);
    if (!s || s.x < -LABEL_W || s.x > vw + LABEL_W || s.y < -60 || s.y > vh + 60) continue;
    if (placed.some((o) => Math.abs(o.x - s.x) < LABEL_W && Math.abs(o.y - s.y) < LABEL_H)) continue;
    placed.push(s);
    out.push(
      isQuarter ? (
        <div key={p.n} ref={track(p)} className="quarter">
          <span dir="auto">{bilingual(p, lang)}</span>
        </div>
      ) : (
        <button
          key={p.n}
          ref={track(p)}
          type="button"
          className={`pin cat-${p.c}${p === active ? " on" : ""}`}
          onClick={() => onPick(p)}
        >
          <span dir="auto">{bilingual(p, lang)}</span>
          <i aria-hidden="true" />
        </button>
      ),
    );
  }
  // Many labels at once: plain backgrounds instead of each label blurring what is behind it (costly on phones).
  return <div className={out.length > 20 ? "pins many" : "pins"} onWheel={forwardWheel}>{out}</div>;
}
