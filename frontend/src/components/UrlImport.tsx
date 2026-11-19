import { useEffect, useState } from "react";
import { api, ApiError, type Video } from "../services/studio";

import { AutoClips } from "./AutoClips";

type Job = {
  id: string;
  state: string;
  received: number;
  total: number | null;
  error: string | null;
};
const active = (j: Job) =>
  ["queued", "downloading", "validating", "cancelling"].includes(j.state);
export function UrlImport({
  automatic = false,
  open,
  report,
  refreshLibrary,
}: {
  automatic?: boolean;
  open: (v: Video) => void;
  report: (e: unknown) => void;
  refreshLibrary: () => void;
}) {
  const [url, setUrl] = useState(""),
    [selected, select] = useState(""),
    [error, setError] = useState(""),
    [jobs, setJobs] = useState<Job[]>([]),
    [busy, setBusy] = useState(false),
    [revision, refresh] = useState(0);
  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const rows = await api<Job[]>(
          automatic ? "/imports?automatic=true" : "/imports",
        );
        if (automatic && selected && !rows.some((j) => j.id === selected))
          rows.unshift(await api<Job>("/imports/" + selected));
        if (current) {
          setJobs(rows);
          if (rows.some(active)) timer = setTimeout(poll, 1500);
          else if (rows.some((j) => j.state === "completed")) refreshLibrary();
        }
      } catch (e) {
        if (current) {
          report(e);
          if (!(e instanceof ApiError && e.status === 401))
            timer = setTimeout(poll, 5000);
        }
      }
    }
    void poll();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [automatic, selected, revision, report, refreshLibrary]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      report(e);
    } finally {
      setBusy(false);
    }
  }
  const current = jobs.find((j) => j.id === selected) ?? jobs[0];
  return (
    <div>
      <form
        className={automatic ? "link-submit" : undefined}
        onSubmit={(e) => {
          e.preventDefault();
          void action(async () => {
            setError("");
            let job: Job;
            try {
              job = await api<Job>("/imports", "POST", {
                url: url.trim(),
                generate_clips: automatic,
              });
            } catch (e) {
              if (e instanceof ApiError && e.status !== 401) {
                setError(e.message);
                return;
              }
              throw e;
            }
            select(job.id);
            setUrl("");
            setJobs((list) => [job, ...list.filter((j) => j.id !== job.id)]);
            refresh((i) => i + 1);
          });
        }}
      >
        <label>
          {automatic ? "Video URL" : "Direct HTTPS recording URL"}
          <input
            type="url"
            required
            maxLength={4096}
            autoComplete="off"
            disabled={busy}
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setError("");
            }}
            placeholder="https://example.com/recording.mp4"
          />
        </label>
        <button className="primary" disabled={busy}>
          {automatic ? "Generate AI Clips" : "Import recording"}
        </button>
      </form>
      <p>
        {automatic
          ? "Use an authorized direct MP4 link or an eligible clip from your connected Twitch channel."
          : "Use a direct HTTPS link to an MP4 you have permission to import."}
      </p>
      {error && (
        <p className="notice" role="alert">
          {error}
        </p>
      )}
      {automatic && jobs.length > 1 && (
        <label>
          Recent runs
          <select value={current.id} onChange={(e) => select(e.target.value)}>
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                Recording {j.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
      )}
      {automatic && !current && (
        <p role="status">Generated clips will appear here.</p>
      )}
      {(automatic ? (current ? [current] : []) : jobs).map((j) => (
        <div className="importJob" key={j.id}>
          <strong>Import {j.id.slice(0, 8)}</strong>
          <p role="status">
            {j.error
              ? new ApiError(422, j.error).message
              : j.state === "linked"
                ? "Using local recording"
                : j.state === "completed"
                  ? "Recording imported"
                  : j.state}{" "}
            {j.state === "downloading" &&
              (j.total
                ? Math.floor((j.received * 100) / j.total) + "%"
                : (j.received / 1048576).toFixed(1) + " MiB received")}
          </p>
          {j.state === "downloading" && (
            <progress
              aria-label="Download progress"
              value={j.total ? j.received : undefined}
              max={j.total ?? 1}
            />
          )}
          {active(j) && (
            <button
              disabled={busy || j.state === "cancelling"}
              onClick={() =>
                void action(async () => {
                  await api("/imports/" + j.id, "DELETE");
                  refresh((i) => i + 1);
                })
              }
            >
              Cancel import
            </button>
          )}
          {automatic && ["completed", "linked"].includes(j.state) && (
            <AutoClips key={j.id} videoId={j.id} onError={report} />
          )}
          {!automatic && j.state === "completed" && (
            <button
              disabled={busy}
              onClick={() =>
                void action(async () =>
                  open(await api<Video>("/videos/" + j.id)),
                )
              }
            >
              Preview recording
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
