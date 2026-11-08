import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MapHandle } from "./IsometricMap";
import { features } from "../features";

/**
 * "Mark a spot": drag a box on the map, then send the map's author a private note about it.
 * The box is kept in map pixels and drawn in the map's own redraw (map.onFrame), so it stays pinned to the map.
 */

const API = (import.meta.env.VITE_FEEDBACK_API || (import.meta.env.DEV ? "http://localhost:8787" : "")).replace(/\/+$/, "");
// Turnstile's always-pass test key in development.
const SITEKEY = import.meta.env.VITE_TURNSTILE_SITEKEY || (import.meta.env.DEV ? "1x00000000000000000000AA" : "");
export const feedbackConfigured = !!API && !!SITEKEY;

const MAP_W = 26624;
const MAP_H = 14336;
const MIN_SIDE = 4; // map px, same as the server
const MAX_AREA = 64_000_000; // map px², same as the server
const MIN_DRAG = 8; // screen px: shorter drags count as a click
const MAX_TEXT = 1000;
const MAX_CONTACT = 120;
const CARD_W = 300;

interface Box { x0: number; y0: number; x1: number; y1: number }

interface Turnstile {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  reset: (id: string) => void;
  remove: (id: string) => void;
}
declare global { interface Window { turnstile?: Turnstile } }

let turnstileLoad: Promise<Turnstile> | null = null;
/** Load Turnstile's script once, the first time a card opens. */
function loadTurnstile(): Promise<Turnstile> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  turnstileLoad ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    s.async = true;
    s.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("turnstile")));
    s.onerror = () => { turnstileLoad = null; s.remove(); reject(new Error("turnstile")); };
    document.head.appendChild(s);
  });
  return turnstileLoad;
}

const norm = (a: { x: number; y: number }, b: { x: number; y: number }): Box => {
  const c = (v: number, max: number) => Math.min(Math.max(v, 0), max);
  return {
    x0: c(Math.min(a.x, b.x), MAP_W), y0: c(Math.min(a.y, b.y), MAP_H),
    x1: c(Math.max(a.x, b.x), MAP_W), y1: c(Math.max(a.y, b.y), MAP_H),
  };
};

interface Props {
  map: React.RefObject<MapHandle>;
  marking: boolean;
  setMarking: (on: boolean) => void;
  onToast: (msg: string) => void;
}

export function Feedback({ map, marking, setMarking, onToast }: Props) {
  const [box, setBox] = useState<Box | null>(null); // the finished box; the card is open while it is set
  const [hint, setHint] = useState("");
  const boxRef = useRef<Box | null>(null);
  const boxEl = useRef<HTMLDivElement>(null);
  const cardEl = useRef<HTMLElement>(null);
  const drag = useRef<{ id: number; sx: number; sy: number; a: { x: number; y: number }; b: { x: number; y: number } } | null>(null);

  // Draw the box (and move the card next to it) in the same frame as the map.
  const place = useRef(() => {
    const m = map.current;
    const el = boxEl.current;
    const b = boxRef.current;
    if (!m || !el) return;
    if (!b) { el.style.display = "none"; return; }
    const p = m.toScreen(b.x0, b.y0);
    const q = m.toScreen(b.x1, b.y1);
    if (!p || !q) return;
    el.style.display = "block";
    el.style.transform = `translate(${p.x}px,${p.y}px)`;
    el.style.width = `${Math.max(q.x - p.x, 1)}px`;
    el.style.height = `${Math.max(q.y - p.y, 1)}px`;
    const card = cardEl.current;
    if (!card) return;
    const vw = window.innerWidth, vh = window.innerHeight;
    if (vw <= 640) { card.style.transform = ""; return; } // a bottom sheet on phones (CSS)
    const h = card.offsetHeight || 340;
    let x = q.x + 14; // right of the box, else left of it, else over it
    if (x + CARD_W > vw - 12) x = p.x - 14 - CARD_W;
    if (x < 12) x = Math.min(Math.max(q.x - CARD_W, 12), vw - CARD_W - 12);
    const y = Math.min(Math.max(p.y, 76), vh - h - 60);
    card.style.transform = `translate(${Math.round(x)}px,${Math.round(Math.max(y, 12))}px)`;
  }).current;
  useEffect(() => map.current?.onFrame(place), [map, place]);

  const setLiveBox = useCallback((b: Box | null) => { boxRef.current = b; place(); }, [place]);

  // Mark mode: the map stops panning while a box is drawn, and pans again afterwards.
  useEffect(() => {
    if (!marking) return;
    map.current?.setPanEnabled(false);
    setHint("Drag a box around the spot. Esc cancels.");
    return () => { map.current?.setPanEnabled(true); setHint(""); drag.current = null; };
  }, [marking, map]);

  const close = useCallback(() => { setBox(null); setLiveBox(null); }, [setLiveBox]);

  // M toggles mark mode; Esc cancels mark mode or closes the card.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if (e.key === "Escape") {
        if (marking) { e.preventDefault(); setMarking(false); if (!box) setLiveBox(null); }
        else if (box) { e.preventDefault(); close(); }
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
      if (e.key === "m" || e.key === "M") {
        e.preventDefault();
        if (marking) { setMarking(false); if (!box) setLiveBox(null); }
        else { close(); setMarking(true); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [marking, box, setMarking, close, setLiveBox]);

  const toMap = (e: React.PointerEvent) => map.current?.toMap(e.clientX, e.clientY) ?? null;

  const onDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current || (e.pointerType === "mouse" && e.button !== 0)) return;
    const a = toMap(e);
    if (!a) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { id: e.pointerId, sx: e.clientX, sy: e.clientY, a, b: a };
    setLiveBox(null);
  };
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const b = toMap(e);
    if (!b) return;
    d.b = b;
    setLiveBox(norm(d.a, b));
  };
  const onUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    drag.current = null;
    const b = norm(d.a, toMap(e) ?? d.b);
    if (Math.hypot(e.clientX - d.sx, e.clientY - d.sy) < MIN_DRAG || b.x1 - b.x0 < MIN_SIDE || b.y1 - b.y0 < MIN_SIDE) {
      setLiveBox(null);
      setHint("Drag to draw a box around the spot. Esc cancels.");
      return;
    }
    if ((b.x1 - b.x0) * (b.y1 - b.y0) > MAX_AREA) {
      setLiveBox(null);
      setHint("That box is too large: zoom in and mark a smaller area.");
      return;
    }
    setLiveBox(b);
    setBox(b);
    setMarking(false);
  };
  const onCancelDrag = () => { drag.current = null; setLiveBox(null); };

  return (
    <>
      <div className="mark-pins" aria-hidden="true"><div ref={boxEl} className="mark-box" /></div>
      {marking && (
        <div className="mark-layer" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onCancelDrag}
          onContextMenu={(e) => e.preventDefault()} />
      )}
      {hint && <div className="mark-hint glass" role="status">{hint}</div>}
      {box && <NoteCard key={`${box.x0},${box.y0},${box.x1},${box.y1}`} box={box} map={map} cardRef={cardEl} onLayout={place}
        onClose={close} onSent={() => { close(); onToast("Thanks, your note was sent"); }} />}
    </>
  );
}

interface CardProps {
  box: Box;
  map: React.RefObject<MapHandle>;
  cardRef: React.RefObject<HTMLElement>;
  onLayout: () => void;
  onClose: () => void;
  onSent: () => void;
}

function NoteCard({ box, map, cardRef, onLayout, onClose, onSent }: CardProps) {
  const [text, setText] = useState("");
  const [contact, setContact] = useState("");
  const [token, setToken] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "error">("idle");
  const [message, setMessage] = useState("");
  const textEl = useRef<HTMLTextAreaElement>(null);
  const widgetEl = useRef<HTMLDivElement>(null);
  const widgetId = useRef<string | null>(null);

  useLayoutEffect(onLayout, [onLayout]);
  useEffect(() => { textEl.current?.focus(); }, []);

  useEffect(() => {
    let gone = false;
    loadTurnstile().then((ts) => {
      if (gone || !widgetEl.current) return;
      widgetId.current = ts.render(widgetEl.current, {
        sitekey: SITEKEY,
        action: "feedback",
        appearance: "interaction-only",
        theme: "dark",
        callback: (t: string) => setToken(t),
        "expired-callback": () => setToken(""),
        "error-callback": () => { setToken(""); setMessage("The spam check could not run. Please try again in a moment."); },
      });
      onLayout();
    }).catch(() => { if (!gone) setMessage("The spam check could not load. Please check your connection."); });
    return () => {
      gone = true;
      if (widgetId.current) window.turnstile?.remove(widgetId.current);
      widgetId.current = null;
    };
  }, [onLayout]);

  const trimmed = text.trim();
  const canSend = trimmed.length > 0 && !!token && state !== "sending";

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSend) return;
    setState("sending");
    setMessage("");
    const v = map.current?.getView();
    const on = Object.entries(features).filter(([, x]) => x).map(([k]) => k).join(",");
    const round = (n: number) => Math.round(n * 10) / 10;
    const body = {
      box: { x0: round(box.x0), y0: round(box.y0), x1: round(box.x1), y1: round(box.y1) },
      text: trimmed,
      ...(contact.trim() ? { contact: contact.trim() } : {}),
      ...(v ? { view: { x: Math.round(v.x), y: Math.round(v.y), z: Math.round(v.z * 1000) / 1000 } } : {}),
      features: on,
      turnstileToken: token,
    };
    try {
      const r = await fetch(`${API}/api/comments`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), credentials: "omit",
      });
      if (r.ok) { onSent(); return; }
      setMessage(
        r.status === 429 ? "You have sent several notes in a short time. Please try again later."
          : r.status === 503 ? "Lots of notes today. Please try again tomorrow."
            : r.status === 403 ? "The spam check failed. Please try again."
              : "Your note could not be sent. Please try again.",
      );
    } catch {
      setMessage("Your note could not be sent. Please check your connection.");
    }
    setState("error");
    // A Turnstile token is good for one try only.
    setToken("");
    if (widgetId.current) window.turnstile?.reset(widgetId.current);
  };

  return (
    <section ref={cardRef} className="note glass" role="dialog" aria-labelledby="note-title"
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
      <button type="button" className="ibtn close" aria-label="Cancel" onClick={onClose}>×</button>
      <h2 id="note-title">A note about this spot</h2>
      <form onSubmit={send}>
        <label htmlFor="note-text">What should the author know?</label>
        <textarea id="note-text" ref={textEl} value={text} maxLength={MAX_TEXT} rows={4} required aria-describedby="note-count"
          placeholder="Something missing, wrong, or just lovely" onChange={(e) => setText(e.target.value)} />
        <div id="note-count" className="note-count">{text.length} / {MAX_TEXT}</div>
        <label htmlFor="note-contact">Your email or name (optional, only the map's author sees it)</label>
        <input id="note-contact" type="text" value={contact} maxLength={MAX_CONTACT} autoComplete="email"
          onChange={(e) => setContact(e.target.value)} />
        <div ref={widgetEl} className="note-turnstile" />
        {message && <p className="note-msg" role="alert">{message}</p>}
        <div className="note-actions">
          <button type="button" className="note-btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="note-btn primary" disabled={!canSend} aria-disabled={!canSend}>
            {state === "sending" ? "Sending…" : !token && trimmed ? "Checking…" : "Send"}
          </button>
        </div>
        <p className="note-small">Notes are private: only the map's author reads them.</p>
      </form>
    </section>
  );
}
