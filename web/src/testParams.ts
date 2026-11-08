// Test parameters in the address, so that every screenshot can be repeated. For development and headless tests;
// each one changes nothing unless it is present.
//
//   ?hour=21.5          fixes the clock at that hour (the "Live" button starts off); needs the timeOfDay switch
//   ?date=2026-03-21    "today" for the sunrise and sunset times
//   ?lang=fa            the interface language (en or fa)
const q = new URLSearchParams(typeof location === "undefined" ? "" : location.search);

function num(name: string): number | null {
  const v = q.get(name);
  if (v === null || v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

const hour = num("hour");
const dateMatch = q.get("date")?.match(/^(\d{4})-(\d{2})-(\d{2})$/);

export const testParams = {
  /** Fixed hour of the day, 0 to 24, or null. */
  hour: hour !== null && hour >= 0 && hour < 24 ? hour : null,
  /** Fixed calendar day (year, month 1-12, day), or null for the real date. */
  date: dateMatch ? { y: +dateMatch[1], m: +dateMatch[2], d: +dateMatch[3] } : null,
};

/** "Now", unless ?date= is given: then that day, at ?hour= if given (else noon), local time. */
export function today(): Date {
  const d = testParams.date;
  if (!d) return new Date();
  const h = testParams.hour ?? 12;
  return new Date(d.y, d.m - 1, d.d, Math.floor(h), Math.round((h % 1) * 60));
}
