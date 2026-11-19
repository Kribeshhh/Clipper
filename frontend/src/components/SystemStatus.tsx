import { useEffect, useState } from "react";
import { getReadiness, type Checks } from "../services/health";
import { ServiceRow } from "../components/ServiceRow";

export function SystemStatus() {
  const [attempt, retry] = useState(0);
  const [result, setResult] = useState<{
    checks: Checks | null;
    at: Date;
  } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const signal = AbortSignal.any([
      controller.signal,
      AbortSignal.timeout(15000),
    ]);
    getReadiness(signal)
      .then((checks) => {
        if (!controller.signal.aborted) setResult({ checks, at: new Date() });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setResult({ checks: null, at: new Date() });
      });
    return () => controller.abort();
  }, [attempt]);
  const checks = result?.checks;
  const title = !result
    ? "Checking services…"
    : !checks
      ? "API unreachable"
      : checks.postgresql && checks.redis
        ? "All systems online"
        : "Service interruption";
  return (
    <>
      <main className="wrap py-12 sm:py-20">
        <h1>System status</h1>
        <section className="summary mt-7" aria-live="polite">
          <p>Workspace readiness</p>
          <h2>{title}</h2>
        </section>
        <section className="mt-9">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <h2 className="text-xl font-semibold">Services</h2>
            <button
              disabled={!result}
              onClick={() => {
                setResult(null);
                retry((n) => n + 1);
              }}
            >
              ↻ &nbsp; Refresh
            </button>
          </div>
          <ul className="mt-3 divide-y divide-stone-300 border-y border-stone-300">
            <ServiceRow
              name="API"
              role="FastAPI application"
              available={result ? Boolean(checks) : null}
            />
            <ServiceRow
              name="PostgreSQL"
              role="Application database"
              available={checks?.postgresql ?? null}
            />
            <ServiceRow
              name="Redis"
              role="Task broker"
              available={checks?.redis ?? null}
            />
          </ul>
          {result && (
            <p className="mt-4 text-sm text-stone-600">
              Last checked{" "}
              <time dateTime={result.at.toISOString()}>
                {result.at.toLocaleTimeString()}
              </time>
            </p>
          )}
        </section>
      </main>
    </>
  );
}
