// Interface language: English and Persian. Pure functions only (no DOM), so they run in the Node tests too.
// React glue (the current language, the switch, <html dir>) is in ./index.ts.
import { en, type Messages } from "./en.ts";
import { fa } from "./fa.ts";

export type Lang = "en" | "fa";
export const LANGS: Lang[] = ["en", "fa"];
export const DIR: Record<Lang, "ltr" | "rtl"> = { en: "ltr", fa: "rtl" };
const MESSAGES: Record<Lang, Messages> = { en, fa };

export type Key = keyof Messages;

/** A message in a language, with {name} placeholders filled. Falls back to English, then to the key itself. */
export function translate(lang: Lang, key: Key, vars: Record<string, string | number> = {}): string {
  const s = MESSAGES[lang][key] ?? en[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? formatNumber(lang, vars[k]) : m));
}

const FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹";

/** Digits in the language's own numerals (Persian: ۰-۹). Strings pass through with their digits converted. */
export function formatNumber(lang: Lang, v: string | number): string {
  const s = String(v);
  return lang === "fa" ? s.replace(/[0-9]/g, (d) => FA_DIGITS[+d]) : s;
}

/** A clock time, hours since midnight -> "13:05" (or "۱۳:۰۵"). */
export function formatTime(lang: Lang, hours: number): string {
  const m = Math.round(hours * 60) % 1440;
  return formatNumber(lang, `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`);
}

/** Something with an English and a Persian name. */
export interface Named { en?: string | null; fa?: string | null; n?: string }

export const LABEL_SEP = " · ";

/**
 * A place label with both names, the interface language's first: "Golestan Palace · کاخ گلستان" in English,
 * "کاخ گلستان · Golestan Palace" in Persian. One name alone when the other is missing or the same.
 */
export function bilingual(p: Named, lang: Lang): string {
  const e = (p.en ?? "").trim(), f = (p.fa ?? "").trim();
  if (e && f && e !== f) return lang === "fa" ? `${f}${LABEL_SEP}${e}` : `${e}${LABEL_SEP}${f}`;
  return e || f || p.n || "";
}

/** The name in one language, falling back to the other. */
export function nameIn(p: Named, lang: Lang): string {
  const e = (p.en ?? "").trim(), f = (p.fa ?? "").trim();
  return (lang === "fa" ? f || e : e || f) || p.n || "";
}

/** The language a visitor starts in: a saved choice, else ?lang=, else the browser's, else English. */
export function pickLang(saved: string | null, query: string | null, browser: readonly string[]): Lang {
  for (const c of [query, saved]) if (c === "en" || c === "fa") return c;
  for (const b of browser) if (b.toLowerCase().startsWith("fa")) return "fa";
  return "en";
}

/**
 * Text folded for search: no case, no Latin accents, and the Persian/Arabic letter variants made one (Arabic yeh
 * and kaf as Persian, alef forms as alef, teh marbuta as heh), with diacritics, tatweel and zero-width joiners
 * dropped and Persian/Arabic digits as Latin. "Golestān", "golestan", "كاخ گلستان" and "کاخ‌گلستان" all match.
 */
export function fold(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[يى]/g, "ی")          // ي ى -> ی
    .replace(/ك/g, "ک")                  // ك -> ک
    .replace(/[أإآٱ]/g, "ا") // أ إ آ ٱ -> ا
    .replace(/ة/g, "ه")                  // ة -> ه
    .replace(/ؤ/g, "و")                  // ؤ -> و
    .replace(/[ً-ٰٟـ]/g, "")   // harakat, superscript alef, tatweel
    .replace(/[‌‍‎‏]/g, "")    // ZWNJ, ZWJ, direction marks
    .replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06f0))
    .replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660))
    .replace(/\s+/g, " ")
    .trim();
}
