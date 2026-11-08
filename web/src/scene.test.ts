// node --test src/scene.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { cityHour, mix, seasonOf, sunTimes, type Variant } from "./scene.ts";

const none = new Set<Variant>();
const sun = sunTimes(new Date(2026, 5, 21));

test("midday is plain day, no grade", () => {
  const m = mix({ hour: 13, season: "summer" }, none, sun);
  assert.equal(m.night, 0);
  assert.equal(m.grade, "");
  assert.deepEqual(m.layers, [{ v: "day", a: 1 }]);
});

test("midnight without a painted night version is a dark grade over the day map", () => {
  const m = mix({ hour: 0.5, season: "summer" }, none, sun);
  assert.equal(m.night, 1);
  assert.match(m.grade, /brightness\(0\.38/);
  assert.deepEqual(m.layers.map((l) => l.v), ["day"]);
});

test("a painted night version is layered in instead of the grade", () => {
  const m = mix({ hour: 23, season: "summer" }, new Set<Variant>(["night"]), sun);
  assert.deepEqual(m.layers.map((l) => l.v), ["day", "night"]);
  assert.doesNotMatch(m.grade, /brightness/);
});

test("golden hour before sunset", () => {
  const m = mix({ hour: sun[1] - 0.6, season: "summer" }, none, sun);
  assert.equal(m.night, 0);
  assert.match(m.grade, /sepia/);
});

test("Tehran time is UTC+3:30", () => {
  assert.equal(cityHour(new Date(Date.UTC(2026, 0, 1, 20, 30)), 3.5), 0);
  assert.equal(cityHour(new Date(Date.UTC(2026, 6, 1, 8, 0)), 3.5), 11.5);
});

test("seasons by month", () => {
  assert.equal(seasonOf(new Date(2026, 2, 21)), "spring"); // Nowruz
  assert.equal(seasonOf(new Date(2026, 11, 21)), "winter"); // Yalda
});
