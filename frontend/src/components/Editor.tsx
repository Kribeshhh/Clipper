import { PortraitControls, useEditPreview } from "./PortraitControls";
import { AutoClips } from "./AutoClips";
import { Highlights } from "./Highlights";
import { useState, useEffect, useRef, useCallback } from "react";
import {
  api,
  timecode,
  validSelection,
  type Job,
  type Video,
} from "../services/studio";

type EditRequest = {
  request_id: string;
  start: number;
  end: number;
  portrait: { position: number; captions: boolean } | null;
};

export function Editor({
  video,
  onError,
}: {
  video: Video;
  onError: (e: unknown) => void;
}) {
  const [vertical, setVertical] = useState(false),
    [position, setPosition] = useState(0.5),
    [captions, setCaptions] = useState(true);
  const [clock, setClock] = useState(0),
    [revision, refreshPreview] = useState(0),
    [aspect, setAspect] = useState(video.width / video.height);
  const [batch, setBatch] = useState<EditRequest[]>([]);
  const onAnalyzed = useCallback(() => refreshPreview((n) => n + 1), []);
  const previewEnd = useRef<number | null>(null);
  const player = useRef<HTMLVideoElement>(null);
  const [start, setStart] = useState("0"),
    [end, setEnd] = useState(String(video.duration));
  const [jobs, setJobs] = useState<Job[]>([]),
    [active, select] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [watchError, setWatchError] = useState(false);
  const request = useRef({ selection: "", id: crypto.randomUUID() });
  const job = jobs.find((j) => j.id === active);
  const pending = jobs.some(
    (j) => j.state === "queued" || j.state === "rendering",
  );
  const valid = validSelection(start, end, video.duration, video.fps);
  const preview = useEditPreview(
    video,
    start,
    end,
    vertical && valid,
    revision,
  );
  const cue =
    vertical && captions
      ? preview?.cues.find(
          (c) => +start + c.start <= clock && clock < +start + c.end,
        )
      : null;
  const canRender =
    valid &&
    (!vertical || !captions || preview?.caption_ready === true) &&
    !busy &&
    !loading;
  useEffect(() => {
    let mounted = true;
    api<Job[]>("/videos/" + video.id + "/jobs")
      .then((list) => {
        if (mounted) {
          setJobs(list);
          select(list[0]?.id ?? null);
        }
      })
      .catch((e) => {
        if (mounted) onError(e);
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [video.id, onError]);
  useEffect(() => {
    if (!pending) return;
    let mounted = true,
      timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const fresh = await api<Job[]>("/videos/" + video.id + "/jobs");
        if (mounted) {
          setWatchError(false);
          setJobs(fresh);
        }
      } catch (e) {
        if (mounted) {
          setWatchError(true);
          if (e instanceof Error && "status" in e && e.status === 401)
            onError(e);
        }
      }
      if (mounted) timer = setTimeout(poll, 1500);
    }
    timer = setTimeout(poll, 700);
    return () => {
      mounted = false;
      clearTimeout(timer);
    };
  }, [video.id, pending, onError]);
  function playSelection(from: number, to: number) {
    const video = player.current;
    if (!video) return;
    previewEnd.current = to;
    video.scrollIntoView({ block: "center" });
    video.currentTime = from;
    void video.play().catch(() => {});
  }
  function currentEdit(): EditRequest {
    const portrait = vertical ? { position, captions } : null;
    const selection = JSON.stringify([start, end, portrait]);
    if (request.current.selection !== selection)
      request.current = { selection, id: crypto.randomUUID() };
    return {
      request_id: request.current.id,
      start: Number(start),
      end: Number(end),
      portrait,
    };
  }
  async function create(multiple = false) {
    setBusy(true);
    try {
      const url = "/videos/" + video.id + "/clips";
      const created = multiple
        ? await api<Job[]>(url + "/batch", "POST", { selections: batch })
        : [await api<Job>(url, "POST", currentEdit())];
      setJobs((list) => [
        ...created,
        ...list.filter((j) => !created.some((c) => c.id === j.id)),
      ]);
      select(created[0].id);
      if (multiple) setBatch([]);
      request.current.selection = "";
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  function approve() {
    const item = currentEdit();
    setBatch((list) =>
      list.some(
        (x) =>
          x.start === item.start &&
          x.end === item.end &&
          JSON.stringify(x.portrait) === JSON.stringify(item.portrait),
      )
        ? list
        : [...list, item],
    );
  }
  return (
    <div className="editorGrid">
      <details className="autoClips">
        <summary>Automatic clips for this recording</summary>
        <AutoClips videoId={video.id} onError={onError} />
      </details>
      <section className="panel">
        <div className="panelTitle">
          <h2>{video.name}</h2>
          <span>Source</span>
        </div>
        <div className="formatTabs" role="group" aria-label="Export format">
          <button aria-pressed={!vertical} onClick={() => setVertical(false)}>
            Original
          </button>
          <button aria-pressed={vertical} onClick={() => setVertical(true)}>
            Vertical 9:16
          </button>
        </div>
        <div className={vertical ? "portraitStage" : "sourceStage"}>
          <div className={vertical ? "portraitFrame" : ""}>
            <video
              style={
                vertical
                  ? {
                      objectPosition:
                        position * 100 + "% " + position * 100 + "%",
                    }
                  : undefined
              }
              onLoadedMetadata={(e) =>
                setAspect(
                  e.currentTarget.videoWidth / e.currentTarget.videoHeight,
                )
              }
              ref={player}
              onTimeUpdate={(e) => {
                setClock(e.currentTarget.currentTime);
                if (
                  previewEnd.current !== null &&
                  e.currentTarget.currentTime >= previewEnd.current
                ) {
                  e.currentTarget.pause();
                  previewEnd.current = null;
                }
              }}
              src={"/api/videos/" + video.id + "/preview"}
              controls
              playsInline
              preload="metadata"
              aria-label="Source recording"
            />
            {cue && <div className="subtitleOverlay">{cue.text}</div>}
          </div>
        </div>
        {vertical && (
          <PortraitControls
            aspect={aspect}
            position={position}
            setPosition={setPosition}
            captions={captions}
            setCaptions={setCaptions}
            preview={preview}
            valid={valid}
          />
        )}
        <p className="meta">
          {video.width} × {video.height} &nbsp; / &nbsp; {video.fps.toFixed(2)}{" "}
          FPS &nbsp; / &nbsp; {timecode(video.duration)}
        </p>
        <div className="cropLabels">
          <h3>Clip selection</h3>
          <button disabled={!valid} onClick={() => playSelection(+start, +end)}>
            Play selection
          </button>
        </div>
        <div className="timeFields">
          {(["Start", "End"] as const).map((label, i) => {
            const set = i ? setEnd : setStart;
            return (
              <div key={label}>
                <label>
                  {label} (seconds)
                  <input
                    type="number"
                    min="0"
                    max={video.duration}
                    step="any"
                    value={i ? end : start}
                    onChange={(e) => set(e.target.value)}
                  />
                </label>
                <button
                  className="link"
                  onClick={() =>
                    set(
                      String(
                        Math.min(
                          video.duration,
                          Number((player.current?.currentTime ?? 0).toFixed(3)),
                        ),
                      ),
                    )
                  }
                >
                  Use playhead
                </button>
              </div>
            );
          })}
        </div>
        {!valid && (
          <p className="notice">
            Choose at least one frame between 0 and {video.duration} seconds.
          </p>
        )}
        <button
          className="primary"
          onClick={() => void create()}
          disabled={!canRender}
        >
          {busy
            ? "Submitting…"
            : vertical
              ? "Render vertical clip"
              : "Generate clip"}
        </button>
        <button
          className="addBatch"
          disabled={!canRender || batch.length >= 12}
          onClick={approve}
        >
          Add to batch
        </button>
        {batch.length > 0 && (
          <section className="batch">
            <h3>Approved clips</h3>
            <ol>
              {batch.map((item, i) => (
                <li key={item.request_id}>
                  <span>
                    {item.start}s – {item.end}s ·{" "}
                    {item.portrait ? "9:16" : "Original"}
                  </span>
                  <button
                    disabled={busy}
                    aria-label={"Remove clip " + (i + 1)}
                    onClick={() =>
                      setBatch((list) => list.filter((x) => x !== item))
                    }
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ol>
            <button
              className="primary"
              disabled={busy}
              onClick={() => void create(true)}
            >
              Render batch ({batch.length})
            </button>
          </section>
        )}
      </section>
      <div className="resultsColumn">
        <Highlights
          videoId={video.id}
          onAnalyzed={onAnalyzed}
          onError={onError}
          preview={(a, b) => {
            setVertical(true);
            setPosition(0.5);
            refreshPreview((n) => n + 1);
            setStart(String(a));
            setEnd(String(b));
            playSelection(a, b);
          }}
        />
        <section className="panel">
          <div className="panelTitle">
            <h2>Clip exports</h2>
            <span>{jobs.length}</span>
          </div>
          {loading ? (
            <p>Loading exports…</p>
          ) : !jobs.length ? (
            <p className="muted">Clips from this recording will appear here.</p>
          ) : (
            <label>
              Export
              <select
                value={active ?? ""}
                onChange={(e) => select(e.target.value)}
              >
                {jobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.start}s – {j.end}s ·{" "}
                    {j.layout === "vertical" ? "9:16" : "Original"} · {j.state}
                  </option>
                ))}
              </select>
            </label>
          )}
          {job && (
            <div className="export" aria-live="polite">
              {job.state === "completed" ? (
                <>
                  <video
                    controls
                    playsInline
                    preload="metadata"
                    aria-label="Generated clip"
                    src={"/api/clips/" + job.id + "/preview"}
                  />
                  <a
                    className="primary"
                    href={"/api/clips/" + job.id + "/download"}
                    download
                  >
                    Download MP4 ↓
                  </a>
                </>
              ) : job.state === "failed" ? (
                <>
                  <p>
                    {job.error === "export_quota"
                      ? "Export storage limit reached. Increase the quota or free space before retrying."
                      : "Rendering failed. Check the source or retry the export."}
                  </p>
                  <button
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        const j = await api<Job>(
                          "/jobs/" + job.id + "/retry",
                          "POST",
                        );
                        setJobs((list) =>
                          list.map((x) => (x.id === j.id ? j : x)),
                        );
                      } catch (e) {
                        onError(e);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Retry render
                  </button>
                </>
              ) : (
                <>
                  <p>
                    {watchError
                      ? "Reconnecting to worker status…"
                      : job.state === "queued"
                        ? "Waiting for worker…"
                        : "Rendering clip…"}{" "}
                    {job.progress}%
                  </p>
                  <progress max="100" value={job.progress} />
                </>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
