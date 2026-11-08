// node --test src/search.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { index, search, type Searchable } from "./search.ts";

const places: Searchable[] = [
  { n: "Golestan Palace · کاخ گلستان", en: "Golestan Palace", fa: "کاخ گلستان", c: "landmark", s: "golestan-palace", f: 1 },
  { n: "Tehran Grand Bazaar · بازار تهران", en: "Tehran Grand Bazaar", fa: "بازار تهران", c: "bazaar", s: "tehran-grand-bazaar" },
  { n: "Lalehzar Street · خیابان لاله‌زار", en: "Lalehzar Street", fa: "خیابان لاله‌زار", c: "street" },
  { n: "گلستان", fa: "گلستان", c: "quarter", q: 1 },
];
const idx = index(places);

test("English and Persian queries find the same place", () => {
  assert.equal(search(idx, "golestan")[0].en, "Golestan Palace");
  assert.equal(search(idx, "کاخ گلستان")[0].en, "Golestan Palace");
  assert.equal(search(idx, "كاخ")[0].en, "Golestan Palace"); // Arabic kaf
});

test("every word must match, and ZWNJ does not matter", () => {
  assert.deepEqual(search(idx, "grand bazaar").map((p) => p.c), ["bazaar"]);
  assert.equal(search(idx, "لالهزار")[0].en, "Lalehzar Street");
  assert.equal(search(idx, "palace bazaar").length, 0);
});

test("landmarks rank above quarters with the same name", () => {
  assert.deepEqual(search(idx, "گلستان").map((p) => p.c), ["landmark", "quarter"]);
});

test("an empty query finds nothing", () => {
  assert.deepEqual(search(idx, "   "), []);
});
