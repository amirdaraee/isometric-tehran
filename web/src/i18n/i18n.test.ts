// node --test src/i18n/i18n.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { en } from "./en.ts";
import { fa } from "./fa.ts";
import { bilingual, fold, formatNumber, formatTime, nameIn, pickLang, translate } from "./core.ts";

test("every English message has a Persian one, and no extra keys", () => {
  assert.deepEqual(Object.keys(fa).sort(), Object.keys(en).sort());
  for (const [k, v] of Object.entries(fa)) assert.ok(v.trim(), `fa.${k} is empty`);
});

test("placeholders survive translation", () => {
  for (const k of Object.keys(en) as (keyof typeof en)[]) {
    const ph = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();
    assert.deepEqual(ph(fa[k]), ph(en[k]), k);
  }
  assert.equal(translate("en", "aboutGa", { state: "allowed" }), "Google Analytics: allowed.");
});

test("place labels show both names, the interface language first", () => {
  const p = { en: "Golestan Palace", fa: "کاخ گلستان" };
  assert.equal(bilingual(p, "en"), "Golestan Palace · کاخ گلستان");
  assert.equal(bilingual(p, "fa"), "کاخ گلستان · Golestan Palace");
  assert.equal(bilingual({ fa: "بازار" }, "en"), "بازار");
  assert.equal(bilingual({ en: "Same", fa: "Same" }, "fa"), "Same");
  assert.equal(nameIn({ en: "Lalehzar Street" }, "fa"), "Lalehzar Street");
});

test("numbers and times in Persian numerals", () => {
  assert.equal(formatNumber("fa", 1404), "۱۴۰۴");
  assert.equal(formatTime("fa", 13.5), "۱۳:۳۰");
  assert.equal(formatTime("en", 0.25), "00:15");
});

test("search folding makes Persian and Arabic letter forms and Latin accents one", () => {
  assert.equal(fold("كاخ گلستاني"), fold("کاخ گلستانی"));
  assert.equal(fold("کاخ‌گلستان"), fold("کاخگلستان"));
  assert.equal(fold("Golestān"), "golestan");
  assert.equal(fold("خیابان ۱۵ خرداد"), "خیابان 15 خرداد");
});

test("language at start: ?lang, then saved choice, then browser", () => {
  assert.equal(pickLang(null, null, ["fa-IR", "en"]), "fa");
  assert.equal(pickLang("en", null, ["fa-IR"]), "en");
  assert.equal(pickLang("en", "fa", []), "fa");
  assert.equal(pickLang(null, "de", ["de-DE"]), "en");
});
