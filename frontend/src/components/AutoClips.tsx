import { useEffect, useState } from "react";
import { api, timecode, ApiError } from "../services/studio";

type Clip = {
  id: string;
  description: string;
  state: string;
  duration: number;
  start: number;
  end: number;
  score: number;
  progress: number;
  signals: string[];
  error: string | null;
};
type Run = {
  state: string;
  stage?: string;
  progress?: number;
  warning?: string;
  clips: Clip[];
};
export function AutoClips({
  videoId,
  onError,
}: {
  videoId: string;
  onError: (e: unknown) => void;
}) {
  const [run, setRun] = useState<Run | null>(null),
    [reload, refresh] = useState(0),
    [busy, setBusy] = useState(false),
    [offline, setOffline] = useState(false);
  const url = "/videos/" + videoId + "/auto-clips";
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const r = await api<Run>(url);
        if (!active) return;
        setOffline(false);
        setRun(r);
        if (
          ["queued", "running", "pending"].includes(r.state) ||
          r.clips.some((c) => ["queued", "rendering"].includes(c.state))
        )
          timer = setTimeout(poll, 1500);
      } catch (e) {
        if (active) {
          setOffline(true);
          if (e instanceof ApiError && e.status === 401) onError(e);
          else timer = setTimeout(poll, 4000);
        }
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [url, reload, onError]);
  async function submit(path: string) {
    setBusy(true);
    try {
      await api(path, "POST");
      refresh((n) => n + 1);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel autoClips" aria-label="Automatic clips">
      <div className="panelTitle">
        <h2>Automatic clips</h2>
        {run && ["idle", "failed"].includes(run.state) && (
          <button
            className="primary"
            disabled={busy}
            onClick={() => void submit(url)}
          >
            {run.state === "failed"
              ? "Retry automatic clips"
              : "Generate AI Clips"}
          </button>
        )}
      </div>
      <div role="status">
        {offline ? (
          "Reconnecting to clip status…"
        ) : !run ? (
          "Loading…"
        ) : run.state === "failed" ? (
          "Automatic analysis or clip planning failed. You can retry."
        ) : run.state === "pending" ? (
          "Constructing edits from ranked moments…"
        ) : run.state === "idle" ? (
          "Analyze this recording and render ranked, captioned vertical clips."
        ) : run.state === "planned" ? (
          !run.clips.length ? (
            "No distinct moments had enough supporting evidence."
          ) : (
            ""
          )
        ) : (
          <>
            {run.stage} · {run.progress}%{" "}
            <progress max={100} value={run.progress} />
          </>
        )}
        {run?.warning && <p>{run.warning}</p>}
      </div>
      {run?.clips.map((c, i) => (
        <article key={c.id}>
          <h3>{c.description}</h3>
          <strong>
            #{i + 1} · {timecode(c.duration)}
          </strong>
          <p>
            Source {timecode(c.start)} – {timecode(c.end)} · Estimated
            engagement {c.score.toFixed(1)}
          </p>
          {c.state === "completed" ? (
            <>
              <video
                controls
                playsInline
                preload="none"
                aria-label={"Automatic clip " + (i + 1)}
                src={"/api/clips/" + c.id + "/preview"}
              />
              <a href={"/api/clips/" + c.id + "/download"} download>
                Download MP4
              </a>
            </>
          ) : c.state === "failed" ? (
            <p>
              Render failed · {c.error?.replaceAll("_", " ")}{" "}
              <button
                disabled={busy}
                onClick={() => void submit("/jobs/" + c.id + "/retry")}
              >
                Retry render
              </button>
            </p>
          ) : (
            <p role="status">
              {c.state} · {c.progress}%{" "}
              <progress value={c.progress} max={100} />
            </p>
          )}
        </article>
      ))}
    </section>
  );
}
