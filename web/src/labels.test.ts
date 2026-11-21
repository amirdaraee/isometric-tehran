// node --test src/labels.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { boxFor, candidates, declutter, modeAt, rank, stepZoom, zoomStep, ZOOM, type Item, type LabelPlace } from "./labels.ts";

const featured: LabelPlace = { c: "landmark", x: 0, y: 0, f: 1, s: "golestan-palace" };
const quarter: LabelPlace = { c: "quarter", x: 0, y: 0, q: 1 };
const landmark: LabelPlace = { c: "museum", x: 0, y: 0, s: "malek-museum" };
const square: LabelPlace = { c: "square", x: 0, y: 0 };
const minor: LabelPlace = { c: "hotel", x: 0, y: 0 };
const street: LabelPlace = { c: "street", x: 0, y: 0, s: "lalehzar-street" };

test("ranks: featured > districts > landmarks > major places > the rest; streets never", () => {
  assert.deepEqual([featured, quarter, landmark, square, minor, street].map(rank), [0, 1, 2, 3, 4, -1]);
});

test("candidates are sorted by rank, keep their order within a rank and leave streets out", () => {
  const a = { ...minor, x: 1 }, b = { ...minor, x: 2 };
  assert.deepEqual(candidates([a, street, square, b, featured]), [featured, square, a, b]);
});

test("far out only featured landmarks and districts; more come in as the map zooms in", () => {
  const show = (z: number) => [featured, quarter, landmark, square, minor, street].map((p) => modeAt(p, z));
  assert.deepEqual(show(0.05), [null, "quarter", null, null, null, null]);
  assert.deepEqual(show(0.12), ["pin", "quarter", null, null, null, null]);
  assert.deepEqual(show(0.2), ["pin", "quarter", null, null, null, null]);
  assert.deepEqual(show(0.25), ["pin", null, "pin", null, null, null]);
  assert.deepEqual(show(0.5), ["pin", null, "pin", "dot", null, null]);
  assert.deepEqual(show(0.8), ["pin", null, "pin", "dot", "dot", null]);
  assert.deepEqual(show(1), ["pin", null, "pin", "pin", "dot", null]);
  assert.deepEqual(show(1.2), ["pin", null, "pin", "pin", "pin", null]);
});

test("a place without a name in the interface language stays a dot, and a district without one is left out", () => {
  assert.equal(modeAt(square, 1.2, { named: false }), "dot");
  assert.equal(modeAt(featured, 0.3, { named: false }), "dot");
  assert.equal(modeAt(quarter, 0.1, { named: false }), null);
});

test("the picked place always shows as a pin, even a street or far out", () => {
  assert.equal(modeAt(street, 0.05, { active: true }), "pin");
  assert.equal(modeAt(minor, 0.05, { active: true }), "pin");
  assert.equal(modeAt(quarter, 0.9, { active: true }), "pin");
});

test("zoom steps are an eighth of a doubling and round down", () => {
  assert.equal(zoomStep(1), 0);
  assert.equal(zoomStep(2), 8);
  assert.equal(zoomStep(0.5), -8);
  assert.ok(stepZoom(zoomStep(0.3)) <= 0.3);
  assert.ok(stepZoom(zoomStep(0.3) + 1) > 0.3);
  assert.equal(zoomStep(ZOOM.majorDot), zoomStep(ZOOM.majorDot * 1.0001));
});

const pin = (x: number, y: number, w = 100): Item => ({ x, y, box: boxFor("pin", w) });

test("an overlapping lower-priority label is hidden, a distant one is kept", () => {
  // at z = 1: second label 50 px to the right overlaps, third is 400 px away
  assert.deepEqual(declutter([pin(0, 0), pin(50, 0), pin(400, 0)], 1), [true, false, true]);
  // zoomed in four times, the first two no longer overlap
  assert.deepEqual(declutter([pin(0, 0), pin(50, 0), pin(400, 0)], 4), [true, true, true]);
});

test("the more important label wins whatever its position", () => {
  assert.deepEqual(declutter([pin(50, 0), pin(0, 0)], 1), [true, false]);
});

test("a label hidden by one that is itself hidden can show", () => {
  // b overlaps a, c overlaps b but not a: c shows
  assert.deepEqual(declutter([pin(0, 0), pin(100, 0), pin(200, 0)], 1), [true, false, true]);
});

test("the result does not depend on where the map is (panning never changes it)", () => {
  const items = Array.from({ length: 200 }, (_, i) => pin((i * 7919) % 3000, (i * 104729) % 2000, 40 + (i % 5) * 30));
  const moved = items.map((it) => ({ ...it, x: it.x + 12345.678, y: it.y - 9876.5 }));
  for (const z of [0.1, 0.3, 0.75]) assert.deepEqual(declutter(moved, z), declutter(items, z));
});

test("shown labels never overlap", () => {
  const items = Array.from({ length: 300 }, (_, i) => pin((i * 7919) % 2500, (i * 104729) % 1500, 40 + (i % 7) * 25));
  const z = 0.5, keep = declutter(items, z, 0);
  const boxes = items.filter((_, i) => keep[i]).map((it) => [it.x * z + it.box[0], it.y * z + it.box[1], it.x * z + it.box[2], it.y * z + it.box[3]]);
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const [a, b] = [boxes[i], boxes[j]];
      assert.ok(!(a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]), `labels ${i} and ${j} overlap`);
    }
  }
  assert.ok(keep.some((k) => !k) && keep.filter(Boolean).length > 10);
});

test("long names are capped in width; dots and districts have their own boxes", () => {
  assert.deepEqual(boxFor("pin", 1000), boxFor("pin", 180));
  assert.deepEqual(boxFor("dot", 50), [-6, -6, 6, 6]);
  assert.equal(boxFor("quarter", 100)[2], 54);
});
