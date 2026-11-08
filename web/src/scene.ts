// Time of day: which painted map versions to show and how strongly, plus a colour grade for the moments that are
// not painted separately (morning, blue hour, and, until the night version is painted, the whole night).
import { city } from "./city.ts";
import { today } from "./testParams.ts";

export type Season = "spring" | "summer" | "autumn" | "winter";
/** Painted versions of the map. Only "day" (the first-draft render) exists so far; the others are the plan. */
export type Variant = "day" | "golden" | "night";
export interface SceneState { hour: number; season: Season }

// Tehran (35.7 N, UTC+3:30, no daylight saving since 2022): sunrise and sunset by month, local time (rough, enough
// for lighting).
const SUN: [number, number][] = [[7.2, 17.3], [6.9, 17.8], [6.4, 18.2], [5.8, 18.6], [5.2, 19.0], [5.0, 19.4],
  [5.1, 19.4], [5.5, 19.0], [5.9, 18.4], [6.3, 17.7], [6.7, 17.1], [7.1, 17.0]];

export function sunTimes(d = today()): [number, number] {
  return SUN[d.getMonth()];
}

export function seasonOf(d = today()): Season {
  const m = d.getMonth();
  return m <= 1 || m === 11 ? "winter" : m <= 4 ? "spring" : m <= 7 ? "summer" : "autumn";
}

/** Hours since midnight in the city's own time zone (fixed offset from city.json). */
export function cityHour(now = new Date(), offsetHours = city.utcOffsetHours): number {
  const h = now.getUTCHours() + now.getUTCMinutes() / 60 + now.getUTCSeconds() / 3600 + offsetHours;
  return ((h % 24) + 24) % 24;
}

export interface Mix { layers: { v: Variant; a: number }[]; grade: string; night: number }

/** How to mix the painted versions for an hour. Only versions in `have` are used; the rest is a CSS grade. */
export function mix(s: SceneState, have: Set<Variant>, sun: [number, number]): Mix {
  const [rise, set] = sun;
  const h = s.hour;
  // night amount: 0 by day, 1 at night, ramps over ~45 min around sunset/sunrise (blue hour in between)
  const ramp = (x: number) => Math.min(Math.max(x, 0), 1);
  const night = Math.max(ramp((h - set) / 0.75), ramp((rise - h) / 0.75), h >= 0 && h < rise - 0.75 ? 1 : 0);
  const golden = night < 1 ? ramp(1 - Math.abs(h - (set - 0.6)) / 1.2) * (1 - night) : 0;
  const layers: { v: Variant; a: number }[] = [{ v: "day", a: 1 }];
  if (golden > 0.01 && have.has("golden")) layers.push({ v: "golden", a: golden });
  if (night > 0.01 && have.has("night")) layers.push({ v: "night", a: night });
  let grade = "";
  const morning = ramp(1 - Math.abs(h - (rise + 1)) / 1.5) * (1 - night);
  if (morning > 0) grade += ` saturate(${1 - 0.12 * morning}) brightness(${1 + 0.04 * morning}) hue-rotate(${-6 * morning}deg)`;
  if (night > 0 && !have.has("night")) grade += ` brightness(${1 - 0.62 * night}) saturate(${1 - 0.35 * night}) hue-rotate(${15 * night}deg)`;
  if (golden > 0 && !have.has("golden")) grade += ` sepia(${0.3 * golden}) saturate(${1 + 0.25 * golden})`;
  return { layers, grade: grade.trim(), night };
}
