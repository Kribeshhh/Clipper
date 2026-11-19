import test from "node:test";
import assert from "node:assert/strict";
import { validSelection, timecode } from "../src/services/studio.ts";

test("selection accepts frame-aligned and fractional boundaries", () => {
  assert.equal(validSelection("0", "0.041666667", 2, 24), true);
  assert.equal(validSelection("0.7", "2", 2, 24), true);
});
test("selection rejects empty, reversed, out-of-range and partial-frame intervals", () => {
  for (const [from, to] of [
    ["", "1"],
    ["0", "Infinity"],
    ["0", ""],
    ["2", "1"],
    ["-1", "2"],
    ["0", "3"],
    ["0", "0.001"],
  ])
    assert.equal(validSelection(from, to, 2, 24), false);
});
test("playback timecode carries minutes and hours", () =>
  assert.equal(timecode(3700.5), "01:01:40"));
