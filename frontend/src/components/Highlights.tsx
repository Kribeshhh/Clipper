import { useEffect, useState } from "react";
import { api, ApiError, timecode } from "../services/studio";

type Candidate = {
  start: number;
  end: number;
  event: number;
  score: number;
  signals: Record<string, number>;
  speech: { start: number; end: number; text: string } | null;
};
type Analysis = {
  state: "idle" | "queued" | "running" | "failed" | "completed";
  stage: string;
  progress: number;
  warning: string | null;
  candidates: Candidate[];
  outdated?: boolean;
};
const labels: Record<string, string> = {
  audio: "Audio rise",
  scene: "Scene change",
  motion: "On-screen motion",
  speech: "Speech activity",
  reaction: "Reaction phrase",
};

export function Highlights({
  videoId,
  preview,
  onAnalyzed,
  onError,
}: {
  videoId: string;
  preview: (a: number, b: number) => void;
  onError: (e: unknown) => void;
  onAnalyzed: () => void;
}) {
  const [result, setResult] = useState<Analysis | null>(null),
    [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false),
    [reload, setReload] = useState(0);
  const url = "/videos/" + videoId + "/analysis";
  useEffect(() => {
    let mounted = true,
      timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await api<Analysis>(url);
        if (!mounted) return;
        setResult(next);
        if (next.state === "completed") onAnalyzed();
        setOffline(false);
        if (next.state === "queued" || next.state === "running")
          timer = setTimeout(refresh, 1500);
      } catch (e) {
        if (mounted) {
          setOffline(true);
          if (e instanceof ApiError && e.status === 401) onError(e);
          else timer = setTimeout(refresh, 4000);
        }
      }
    }
    void refresh();
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  }, [url, onError, reload, onAnalyzed]);
  async function analyze() {
    setBusy(true);
    try {
      setResult(await api<Analysis>(url, "POST"));
      setReload((i) => i + 1);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  const running = result?.state === "queued" || result?.state === "running";
  return (
    <section className="panel highlights" aria-label="Highlight detection">
      <div className="panelTitle">
        <h2>Highlights</h2>
        {result &&
          !running &&
          (result.state !== "completed" ||
            result.warning ||
            result.outdated) && (
            <button onClick={analyze} disabled={busy}>
              {result.outdated
                ? "Update ranking"
                : result.warning || result.state === "failed"
                  ? "Retry analysis"
                  : "Analyze video"}
            </button>
          )}
      </div>
      <div role="status">
        {offline ? (
          "Reconnecting to analysis status…"
        ) : !result ? (
          "Loading analysis…"
        ) : running ? (
          <>
            <p>
              {result.stage} · {result.progress}%
            </p>
            <progress max="100" value={result.progress} />
          </>
        ) : result.state === "failed" ? (
          "Analysis was interrupted. You can retry or select a clip manually."
        ) : (
          result.warning
        )}
      </div>
      {result?.state === "idle" && (
        <p className="muted">
          Find moments to review using audio, speech and on-screen activity.
        </p>
      )}
      {result?.state === "completed" &&
        (result.candidates.length === 0 ? (
          <p className="muted">No moments met the highlight criteria.</p>
        ) : (
          <ol>
            {result.candidates.map((c, i) => (
              <li key={c.event}>
                <div className="candidateTitle">
                  <strong>
                    #{i + 1} &nbsp; {timecode(c.start)} – {timecode(c.end)}
                  </strong>
                  <span>Score {c.score.toFixed(1)}</span>
                </div>
                <p>
                  {Object.keys(c.signals)
                    .map((k) => labels[k])
                    .join(" · ")}
                </p>
                {c.speech && <blockquote>“{c.speech.text}”</blockquote>}
                <button onClick={() => preview(c.start, c.end)}>
                  Preview &amp; edit
                </button>
              </li>
            ))}
          </ol>
        ))}
    </section>
  );
}
