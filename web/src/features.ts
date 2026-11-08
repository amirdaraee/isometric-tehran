/**
 * Feature switches. Every optional part of the map can be turned off without touching the code that uses it.
 *
 * Three ways to turn a feature off, later ones win:
 *   1. Edit DEFAULTS below (the release default).
 *   2. At build time: VITE_FEATURES_OFF="clouds,water" npm run build   (or VITE_FEATURES_ON to force on).
 *   3. In the browser, for testing: ?off=clouds,water  or  ?on=tiltShift
 */
export const DEFAULTS = {
  /** Time slider and live Tehran clock. Until the night and golden-hour versions are painted, the evening and night
   * are a colour grade over the day map. Off = always day. */
  timeOfDay: true,
  /** Light glinting on the pools and the City Park lake (drawn over the water in the render). */
  water: true,
  /** A soft sea of cloud seen from above, filling everything outside the map. */
  clouds: true,
  /** Tilt-shift: the screen's top and bottom edges softly blurred, like a miniature photo. */
  tiltShift: false,
  /** "Streets" layer with street names (in the interface language). */
  streets: true,
  /** Place pins, named in the interface language (both names in the tooltip and the card). */
  pins: true,
  /** Search box for places and streets (English and Persian names). */
  search: true,
  /** "Surprise me" button. */
  surprise: true,
  /** Share button (copies the link to the current view). */
  share: true,
  /** Landmark cards (Wikidata and Wikipedia text, with sources). Off = a short name card instead. */
  landmarks: true,
  /** English / Persian switch. Off = the language picked from the browser (or ?lang=) only. */
  languageSwitch: true,
  /** "Preview" badge in the corner while the art is not final. */
  previewBadge: true,
  /** "Mark a spot" button: draw a box on the map and send the author a private note. Needs a feedback worker
   * (VITE_FEEDBACK_API), which this project does not run yet, so it is off. */
  comments: false,
  /** Automatically lowers the effect quality on slow devices. */
  adaptiveQuality: true,
  /** Frame-time readout for testing. Off everywhere unless asked for with ?on=perfHud. */
  perfHud: false,
};

export type Feature = keyof typeof DEFAULTS;

function list(s: string | null | undefined): string[] {
  return (s ?? "").split(",").map((x) => x.trim()).filter(Boolean);
}

function resolve(): Record<Feature, boolean> {
  const f = { ...DEFAULTS };
  const q = new URLSearchParams(location.search);
  const apply = (names: string[], on: boolean) => {
    for (const n of names) if (n in f) f[n as Feature] = on;
  };
  apply(list(import.meta.env.VITE_FEATURES_OFF), false);
  apply(list(import.meta.env.VITE_FEATURES_ON), true);
  apply(list(q.get("off")), false);
  apply(list(q.get("on")), true);
  return f;
}

export const features = resolve();
export const on = (f: Feature) => features[f];
