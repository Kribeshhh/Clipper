export type Checks = { postgresql: boolean; redis: boolean };

export async function getReadiness(signal: AbortSignal): Promise<Checks> {
  const response = await fetch("/api/health/ready", {
    signal,
    cache: "no-store",
  });
  if (response.status !== 200 && response.status !== 503)
    throw new Error("API unavailable");
  const body = await response.json();
  const checks = body?.checks;
  if (
    typeof checks?.postgresql !== "boolean" ||
    typeof checks?.redis !== "boolean"
  )
    throw new Error("Invalid readiness response");
  const ready = checks.postgresql && checks.redis;
  if (body.status !== (ready ? "ready" : "degraded") || response.ok !== ready)
    throw new Error("Inconsistent readiness response");
  return checks;
}
