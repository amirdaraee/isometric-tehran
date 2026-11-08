import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { gaAvailable, readConsent, saveConsent, type Consent } from "./analytics";
import { IsometricMap, type MapHandle, type MapMeta, type View } from "./components/IsometricMap";
import { Pins, type Place } from "./components/Pins";
import { Search } from "./components/Search";
import { Streets } from "./components/Streets";
import { Water } from "./components/Water";
import { Clouds } from "./components/Clouds";
import { LandmarkPanel, type Landmark } from "./components/LandmarkPanel";
import { SceneControl } from "./components/SceneControl";
import { Feedback, feedbackConfigured } from "./components/Feedback";
import { cityHour, mix, seasonOf, sunTimes, type SceneState, type Variant } from "./scene";
import { asset, tilesBase } from "./config";
import { city } from "./city";
import { on } from "./features";
import { bilingual, setLang, t, useLang } from "./i18n";
import { testParams, today } from "./testParams";
import { setView, useView } from "./viewStore";

const FOCUS_ZOOM = 0.75;
/** The map opens at midday; the "Live" button switches to Tehran's real clock. */
const START_HOUR = 13;
const REPO_URL = city.repo;

function startScene(): SceneState {
  return { hour: on("timeOfDay") ? testParams.hour ?? START_HOUR : START_HOUR, season: seasonOf() };
}

/** A note while the view is centred on a part of the map not painted yet (re-rendered only when that changes). */
function Unpainted({ painted }: { painted: NonNullable<MapMeta["painted"]> }) {
  const show = useView((v) => !!v && v.z >= 0.1 &&
    !painted.some((a) => v.x >= a.x0 && v.x <= a.x0 + a.w && v.y >= a.y0 && v.y <= a.y0 + a.h));
  return show ? <div className="unpainted glass" role="note">{t("badgeDraft")}</div> : null;
}

function readHash(): View | null {
  const m = location.hash.match(/^#(-?\d+),(-?\d+),([\d.]+)$/);
  return m ? { x: +m[1], y: +m[2], z: +m[3] } : null;
}

export default function App() {
  const lang = useLang();
  const [meta, setMeta] = useState<MapMeta | null>(null);
  const [places, setPlaces] = useState<Place[] | null>(null);
  const [error, setError] = useState(false);
  const [ready, setReady] = useState(false);
  // Everything but the map waits until its first view has drawn, so the map gets the line to itself first.
  const [drawn, setDrawn] = useState(false);
  const [active, setActive] = useState<Place | null>(null);
  const [toast, setToast] = useState("");
  const [consent, setConsent] = useState<Consent>(readConsent);
  const choose = (c: "yes" | "no") => { saveConsent(c); setConsent(c); };
  const [about, setAbout] = useState(false);
  const [streets, setStreets] = useState(false);
  const [lm, setLm] = useState<Landmark | null>(null);
  const [marking, setMarking] = useState(false);
  useEffect(() => { if (marking) { setLm(null); setActive(null); } }, [marking]);
  const landmarksRef = useRef<Landmark[]>([]);
  useEffect(() => {
    fetch(asset("/data/landmarks.json")).then((r) => r.json()).then((l: Landmark[]) => { landmarksRef.current = l; }).catch(() => {});
  }, []);
  // Time of day: the map opens at START_HOUR; "Live" follows Tehran's clock until the person moves the slider.
  const [live, setLive] = useState(false);
  const [scene, setScene] = useState<SceneState>(startScene);
  useEffect(() => {
    if (!live) return;
    const tick = () => setScene((s) => ({ ...s, hour: on("timeOfDay") ? cityHour() : START_HOUR }));
    const timer = setInterval(tick, 20000);
    tick();
    return () => clearInterval(timer);
  }, [live]);
  const date = useMemo(() => today(), []);
  const sun = useMemo(() => sunTimes(date), [date]);
  const map = useRef<MapHandle>(null);
  const lastSurprise = useRef<Place | null>(null);

  useEffect(() => { document.title = t("title"); }, [lang]);

  useEffect(() => {
    Promise.all([fetch(`${tilesBase}/meta.json`).then((r) => r.json()), fetch(asset("/data/places.json")).then((r) => r.json())])
      .then(([m, p]) => { setMeta(m); setPlaces(p); })
      .catch(() => setError(true));
  }, []);

  const initial = useMemo<View | null>(() => {
    if (!meta || !places) return null;
    const fromLink = readHash();
    if (fromLink) return fromLink;
    const start = places.find((p) => p.s === city.openingPlace);
    return start ? { x: start.x, y: start.y, z: city.openingZoom } : { x: meta.width / 2, y: meta.height / 2, z: 0.2 };
  }, [meta, places]);

  // Every view has a URL, so any view can be sent to someone.
  const hashTimer = useRef<ReturnType<typeof setTimeout>>();
  const onViewChange = useCallback((v: View) => {
    setView(v);
    clearTimeout(hashTimer.current);
    hashTimer.current = setTimeout(() => {
      history.replaceState(null, "", `${location.search}#${Math.round(v.x)},${Math.round(v.y)},${v.z.toFixed(3)}`);
    }, 300);
  }, []);

  const pick = useCallback((p: Place) => {
    setActive(p);
    // A landmark card: the one the pin names, else the nearest within ~50 m.
    let best: Landmark | null = (p.s && landmarksRef.current.find((l) => l.slug === p.s)) || null, bd = best ? -1 : 200;
    for (const l of landmarksRef.current) {
      const d = Math.hypot(l.x - p.x, l.y - p.y);
      if (d < bd) { bd = d; best = l; }
    }
    setLm(on("landmarks") ? best : null);
    map.current?.flyTo(p.x, p.y, p.c === "quarter" ? 0.3 : FOCUS_ZOOM);
  }, []);

  const surprise = useCallback(() => {
    const pool = (places ?? []).filter((p) => (p.f || p.s) && p !== lastSurprise.current);
    if (!pool.length) return;
    const p = pool[Math.floor(Math.random() * pool.length)];
    lastSurprise.current = p;
    pick(p);
  }, [places, pick]);

  const share = useCallback(async () => {
    const url = location.href;
    const title = active ? `${bilingual(active, lang)}, ${t("title")}` : t("title");
    try {
      if (navigator.share && matchMedia("(pointer: coarse)").matches) {
        await navigator.share({ title, url });
      } else {
        await navigator.clipboard.writeText(url);
        setToast(t("linkCopied"));
        setTimeout(() => setToast(""), 2200);
      }
    } catch {
      /* The person closed the share sheet. */
    }
  }, [active, lang]);

  const have = useMemo(() => new Set(Object.keys(meta?.variants ?? {}) as Variant[]), [meta]);
  const sceneMix = useMemo(() => mix(scene, have, sun), [scene, have, sun]);
  useEffect(() => {
    if (!ready) return;
    const alpha: Record<string, number> = {};
    for (const v of have) alpha[v] = 0;
    for (const l of sceneMix.layers) if (l.v !== "day") alpha[l.v] = l.a;
    map.current?.setLayers(alpha);
  }, [sceneMix, ready, have]);

  if (error) return <div className="splash">{t("loadError")}</div>;
  if (!meta || !places || !initial) return <div className="splash">{t("loading")}</div>;

  return (
    <div className="app">
      <div className="graded" style={{ filter: sceneMix.grade || undefined }}>
        <IsometricMap ref={map} meta={meta} initial={initial} label={t("mapLabel")} onViewChange={onViewChange} onReady={() => setReady(true)}
          onDrawn={() => { setDrawn(true); dispatchEvent(new Event("map-drawn")); }} />
      </div>
      {ready && drawn && on("water") && <Water map={map} night={sceneMix.night} />}
      {ready && drawn && on("streets") && <Streets map={map} on={streets} />}
      {ready && drawn && on("clouds") && <Clouds map={map} night={sceneMix.night} />}
      {on("timeOfDay") && <SceneControl scene={scene} live={live} sun={sun} onLive={() => setLive(true)}
        onChange={(c) => { if (c.hour !== undefined) setLive(false); setScene((s) => ({ ...s, ...c })); }} />}
      {ready && drawn && on("pins") && <Pins places={places} map={map} active={active} onPick={pick} />}
      {ready && drawn && on("comments") && feedbackConfigured && <Feedback map={map} marking={marking} setMarking={setMarking}
        onToast={(m) => { setToast(m); setTimeout(() => setToast(""), 3200); }} />}
      {on("search") && <Search places={places} onPick={pick} onSurprise={on("surprise") ? surprise : undefined} />}

      {lm && <LandmarkPanel key={lm.slug} lm={lm} onClose={() => { setLm(null); setActive(null); }} />}

      {active && !lm && (
        <div className="card glass" role="status">
          <button type="button" className="ibtn close" aria-label={t("close")} onClick={() => setActive(null)}>×</button>
          <b dir="auto">{bilingual(active, lang)}</b>
        </div>
      )}

      {on("streets") && (
        <div className="layers glass" role="group" aria-label={t("layers")}>
          <button type="button" className={streets ? "on" : ""} aria-pressed={streets} onClick={() => setStreets((v) => !v)}>{t("streets")}</button>
        </div>
      )}

      <div className="tools">
        {on("languageSwitch") && <button type="button" className="round glass lang" lang={lang === "fa" ? "en" : "fa"}
          aria-label={t("switchToLabel")} title={t("switchToLabel")} onClick={() => setLang(lang === "fa" ? "en" : "fa")}>
          {lang === "fa" ? "EN" : "فا"}
        </button>}
        <button type="button" className="round glass" aria-label={t("zoomIn")} onClick={() => map.current?.zoomBy(1.6)}>+</button>
        <button type="button" className="round glass" aria-label={t("zoomOut")} onClick={() => map.current?.zoomBy(1 / 1.6)}>−</button>
        {on("comments") && feedbackConfigured && <button type="button" className={`round glass mark${marking ? " on" : ""}`}
          aria-label={t("markSpot")} title={`${t("markSpot")} (M)`} aria-pressed={marking}
          aria-keyshortcuts="M" onClick={() => setMarking((v) => !v)}>
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path d="M4 8V5a1 1 0 0 1 1-1h3m8 0h3a1 1 0 0 1 1 1v3m0 8v3a1 1 0 0 1-1 1h-3m-8 0H5a1 1 0 0 1-1-1v-3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            <path d="M12 9v6m-3-3h6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>}
        {on("share") && <button type="button" className="round share" aria-label={t("share")} onClick={share}>
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path d="M12 15V4m0 0L8 8m4-4 4 4M5 13v6a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>}
        <a className="round glass gh" href={REPO_URL} target="_blank" rel="noopener" aria-label={t("sourceCode")} title={t("sourceCode")}>
          <svg viewBox="0 0 16 16" width="20" height="20" aria-hidden="true">
            <path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
          </svg>
        </a>
      </div>

      <div className="foot">
        {on("previewBadge") && meta.art !== "final" && <span className="badge">{t("badgeDraft")}</span>}
        <button type="button" className="link" onClick={() => setAbout(true)}>{t("about")}</button>
      </div>

      {toast && <div className="toast glass" role="status">{toast}</div>}
      {drawn && gaAvailable && consent === null && (
        <div className="consent glass" role="dialog" aria-label={t("aboutPrivacy")}>
          <span>{t("consentText")}</span>
          <span className="consent-btns">
            <button type="button" onClick={() => choose("no")}>{t("consentNo")}</button>
            <button type="button" className="on" onClick={() => choose("yes")}>{t("consentYes")}</button>
          </span>
        </div>
      )}
      {on("tiltShift") && <><div className="tilt tilt-top" aria-hidden="true" /><div className="tilt tilt-bottom" aria-hidden="true" /></>}
      {on("previewBadge") && !!meta.painted?.length && <Unpainted painted={meta.painted} />}

      {about && (
        <div className="about-wrap" onClick={() => setAbout(false)}>
          <section className="about glass" role="dialog" aria-label={t("about")} onClick={(e) => e.stopPropagation()}>
            <button type="button" className="ibtn close" aria-label={t("close")} onClick={() => setAbout(false)}>×</button>
            <h1>{t("title")}</h1>
            <p>{t("aboutIntro")}</p>
            <p>{t("aboutDraft")}</p>
            <h2>{t("aboutData")}</h2>
            <ul dir="ltr" lang="en">
              <li>Streets, parks, water, places and building tags: <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap contributors</a>, ODbL 1.0</li>
              <li>Building footprints and heights: <a href="https://overturemaps.org" target="_blank" rel="noopener">Overture Maps Foundation</a> (OpenStreetMap and Microsoft ML Buildings), ODbL 1.0</li>
              <li>Terrain: Copernicus DEM GLO-30, © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA</li>
              <li>Landmarks: <a href="https://www.wikidata.org" target="_blank" rel="noopener">Wikidata</a> (CC0) and <a href="https://www.wikipedia.org" target="_blank" rel="noopener">Wikipedia</a> (CC BY-SA 4.0), linked on each card</li>
            </ul>
            <h2>{t("aboutCode")}</h2>
            <ul dir="ltr" lang="en">
              <li>Built on <a href="https://github.com/amirdaraee/isometric-luxembourg" target="_blank" rel="noopener">Isometric Luxembourg</a>, whose viewer started from <a href="https://github.com/cannoneyed/isometric-nyc" target="_blank" rel="noopener">isometric-nyc</a> by Andy Coenen (MIT)</li>
              <li><a href="https://openseadragon.github.io" target="_blank" rel="noopener">OpenSeadragon</a>, React and Vite</li>
              <li><a href={REPO_URL} target="_blank" rel="noopener">Source on GitHub</a>: code MIT, art CC BY-NC 4.0</li>
            </ul>
            {gaAvailable && <>
              <h2>{t("aboutPrivacy")}</h2>
              <p>{t("aboutGa", { state: consent === "yes" ? t("allowed") : t("notAllowed") })}{" "}
                <button type="button" className="link" onClick={() => choose(consent === "yes" ? "no" : "yes")}>{consent === "yes" ? t("withdraw") : t("allow")}</button></p>
            </>}
          </section>
        </div>
      )}
    </div>
  );
}
