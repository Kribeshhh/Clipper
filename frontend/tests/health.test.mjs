import test from "node:test";
import assert from "node:assert/strict";
import { getReadiness } from "../src/services/health.ts";
for (const [name, status, body, valid] of [
  [
    "healthy",
    200,
    { status: "ready", checks: { postgresql: true, redis: true } },
    true,
  ],
  [
    "degraded",
    503,
    { status: "degraded", checks: { postgresql: false, redis: true } },
    true,
  ],
  ["invalid payload", 200, {}, false],
  ["proxy failure", 502, {}, false],
  [
    "inconsistent status",
    200,
    { status: "ready", checks: { postgresql: false, redis: true } },
    false,
  ],
]) {
  test(name, async (t) => {
    t.mock.method(globalThis, "fetch", async () =>
      Response.json(body, { status }),
    );
    const request = getReadiness(new AbortController().signal);
    if (valid) assert.deepEqual(await request, body.checks);
    else await assert.rejects(request);
  });
}
