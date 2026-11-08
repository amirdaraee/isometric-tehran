import { useEffect, useMemo, useRef, useState } from "react";
import { bilingual, t, translate, useLang, type Key } from "../i18n";
import { index, search } from "../search";
import type { Place } from "./Pins";

interface Props {
  places: Place[];
  onPick: (p: Place) => void;
  onSurprise?: () => void;
}

/** Search box: English and Persian names (OSM name, name:en, name:fa), Persian letter variants folded. */
export function Search({ places, onPick, onSurprise }: Props) {
  const lang = useLang();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const idx = useMemo(() => index(places), [places]);
  const results = useMemo(() => search(idx, q), [idx, q]);

  useEffect(() => setSel(0), [q]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
      if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const pick = (p: Place) => {
    onPick(p);
    setOpen(false);
    setQ("");
    input.current?.blur();
  };

  return (
    <div className="top">
      <div className="pill glass">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M16.5 16.5 21 21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
        <input
          ref={input}
          type="search"
          value={q}
          placeholder={t("searchPlaceholder")}
          aria-label={t("searchLabel")}
          aria-controls="results"
          autoComplete="off"
          spellCheck={false}
          dir="auto"
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, results.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
            else if (e.key === "Enter" && results[sel]) pick(results[sel]);
            else if (e.key === "Escape") { setQ(""); input.current?.blur(); }
          }}
        />
        <kbd aria-hidden="true">/</kbd>
        {onSurprise && (<button type="button" className="ibtn" title={t("surprise")} aria-label={t("surprise")} onClick={onSurprise}>
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <rect x="3.5" y="3.5" width="17" height="17" rx="4" fill="none" stroke="currentColor" strokeWidth="2" />
            <circle cx="8.5" cy="8.5" r="1.6" fill="currentColor" />
            <circle cx="12" cy="12" r="1.6" fill="currentColor" />
            <circle cx="15.5" cy="15.5" r="1.6" fill="currentColor" />
          </svg>
        </button>)}
      </div>
      {open && q.trim() !== "" && (
        <ul id="results" className="results glass" role="listbox">
          {results.length === 0 && <li className="none">{t("searchNone")}</li>}
          {results.map((r, i) => (
            <li key={`${r.n}-${r.x}-${r.y}`} role="option" aria-selected={i === sel}>
              <button type="button" className={i === sel ? "on" : ""} onMouseDown={() => pick(r)}>
                <span className="name" dir="auto">{bilingual(r, lang)}</span>
                <span className="sub">{translate(lang, `cat.${r.c}` as Key)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
