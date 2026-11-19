import assert from "node:assert/strict";
import test from "node:test";
import {
  parseRecordingLink,
  playback,
  seconds,
} from "../src/services/recordingLinks.ts";

test("YouTube share and watch links preserve hour-long offsets and discard tracking", () => {
  const r = parseRecordingLink(
    "https://youtu.be/dQw4w9WgXcQ?t=2h58m20s&si=private",
  );
  assert.equal(r.start, 10700);
  const urls = playback(r, "localhost");
  assert.match(urls.embed, /start=10700&end=10730$/);
  assert.deepEqual(parseRecordingLink(urls.watch), r);
  assert.equal(seconds("2:58:20"), 10700);
  assert.ok(Number.isNaN(seconds("2:68:20")));
});

test("Twitch clip and VOD links open the correct official players", () => {
  const vod = parseRecordingLink("https://www.twitch.tv/videos/123?t=2h10m");
  assert.equal(vod.start, 7800);
  assert.match(
    playback(vod, "localhost").embed,
    /parent=localhost&time=2h10m0s/,
  );
  const short = parseRecordingLink("https://clips.twitch.tv/Example-123");
  assert.deepEqual(
    short,
    parseRecordingLink("https://www.twitch.tv/creator/clip/Example-123"),
  );
});

test("unrecognized hosts, credentials and malformed selections are rejected", () => {
  for (const url of [
    "https://youtube.com.fake/watch?v=dQw4w9WgXcQ",
    "https://user@youtu.be/dQw4w9WgXcQ",
    "https://localhost/videos/123",
    "https://www.twitch.tv/channel",
    "https://youtu.be/dQw4w9WgXcQ?t=bad",
  ])
    assert.throws(() => parseRecordingLink(url));
});
