// The map's view (centre and zoom), kept outside React state. IsometricMap reports every redraw while the map
// moves, up to 60 or 120 times a second; held in the App's state, that re-rendered the whole page each frame. Here
// each component reads only the part of the view it needs, and re-renders only when that part changes:
//
//   const view = useView((v) => v);                      // every move (the pins)
//   const far = useView((v) => (v?.z ?? 1) < 0.1);       // only when the answer changes
import { useSyncExternalStore } from "react";
import type { View } from "./components/IsometricMap";

let current: View | null = null;
const subs = new Set<() => void>();

/** The map moved (IsometricMap's onViewChange). */
export function setView(v: View) {
  current = v;
  for (const fn of subs) fn();
}

/** The view now, or null before the map has opened. */
export function getView(): View | null {
  return current;
}

function subscribe(fn: () => void) {
  subs.add(fn);
  return () => { subs.delete(fn); };
}

/** A part of the view, as React state: the component re-renders when `select`'s answer changes (compared with
 * Object.is, so return a plain value, or the view itself). */
export function useView<T>(select: (v: View | null) => T): T {
  return useSyncExternalStore(subscribe, () => select(current));
}
