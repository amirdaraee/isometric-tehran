// The interface language for the app: the current one, a switch, and <html lang dir> kept in step.
// The pure parts (messages, labels, search folding) are in ./core.ts.
import { useSyncExternalStore } from "react";
import { DIR, pickLang, translate, type Key, type Lang } from "./core.ts";

export * from "./core.ts";

const STORE = "isotehran.lang";

function saved(): string | null {
  try { return localStorage.getItem(STORE); } catch { return null; }
}

let lang: Lang = pickLang(saved(), new URLSearchParams(location.search).get("lang"), navigator.languages ?? [navigator.language]);
const listeners = new Set<() => void>();

function apply() {
  document.documentElement.lang = lang;
  document.documentElement.dir = DIR[lang];
}
apply();

export function getLang(): Lang { return lang; }

export function setLang(l: Lang) {
  if (l === lang) return;
  lang = l;
  try { localStorage.setItem(STORE, l); } catch { /* private mode: the choice lasts this visit */ }
  apply();
  for (const fn of listeners) fn();
}

/** The current language; re-renders the component when it changes. */
export function useLang(): Lang {
  return useSyncExternalStore((fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; }, () => lang);
}

/** A message in the current language (call inside a component that uses useLang, so it re-renders). */
export function t(key: Key, vars?: Record<string, string | number>): string {
  return translate(lang, key, vars);
}
