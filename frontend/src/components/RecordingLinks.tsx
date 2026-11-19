import { useState, useEffect } from "react";
import { api, type Video } from "../services/studio";
import {
  parseRecordingLink,
  playback,
  seconds,
  type RecordingLink,
} from "../services/recordingLinks";

type Props = { open: (video: Video) => void; report: (error: unknown) => void };
type Clip = { id: string; state: string };
const linksPath = "/recording-links";
const clipPath = "/providers/twitch/clips/";
const key = (r: RecordingLink) => r.kind + "/" + r.external_id;
export function RecordingLinks({ open, report }: Props) {
  const [links, setLinks] = useState<RecordingLink[]>([]),
    [active, setActive] = useState<RecordingLink | null>(null);
  const [url, setUrl] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [title, setTitle] = useState("");
  const [clip, setClip] = useState<Clip | null>(null),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  useEffect(() => {
    let current = true;
    api<RecordingLink[]>(linksPath)
      .then((list) => {
        if (current) setLinks(list);
      })
      .catch((e) => {
        if (current) report(e);
      });
    return () => {
      current = false;
    };
  }, [report]);
  useEffect(() => {
    if (!clip || clip.state !== "pending") return;
    let current = true;
    const timer = setTimeout(() => {
      api<Clip>(clipPath + encodeURIComponent(clip.id))
        .then((result) => {
          if (current) setClip(result);
        })
        .catch((e) => {
          if (current) {
            setClip({ ...clip, state: "unknown" });
            report(e);
          }
        });
    }, 3000);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [clip, report]);
  function choose(r: RecordingLink) {
    setActive(r);
    setStart(String(r.start));
    setEnd(String(r.end));
    setTitle(r.title);
    setClip(null);
    setMessage("");
  }
  async function save(r: RecordingLink) {
    const saved = await api<RecordingLink>(linksPath, "POST", r);
    setLinks((list) => [saved, ...list.filter((x) => key(x) !== key(saved))]);
    choose(saved);
  }
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await fn();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Link operation failed.");
      if (!(e instanceof Error) || ("status" in e && e.status === 401))
        report(e);
    } finally {
      setBusy(false);
    }
  }
  const pending = clip?.state === "pending";
  const src = active && playback(active, location.hostname);
  function selection() {
    const a = seconds(start),
      b = seconds(end);
    if (
      !active ||
      !title.trim() ||
      !Number.isSafeInteger(a) ||
      !Number.isSafeInteger(b) ||
      b <= a ||
      b > 604800
    )
      throw new Error(
        "Enter a title and a valid start and end in seconds or H:MM:SS.",
      );
    return { ...active, start: a, end: b, title: title.trim() };
  }
  const readyId =
    active?.kind === "twitch_clip"
      ? active.external_id
      : clip?.state === "ready"
        ? clip.id
        : null;
  return (
    <section className="panel linked">
      <div className="panelTitle">
        <h2>Import recording link</h2>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            const parsed = parseRecordingLink(url);
            const existing = links.find((x) => key(x) === key(parsed));
            if (existing && parsed.start === 0) choose(existing);
            else
              await save({ ...parsed, title: existing?.title ?? parsed.title });
            setUrl("");
          });
        }}
      >
        <label>
          YouTube or Twitch link
          <input
            type="url"
            required
            maxLength={2048}
            placeholder="https://…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </label>
        <button disabled={busy || pending}>Open link</button>
      </form>
      {links.length > 0 && (
        <label>
          Saved recordings
          <select
            disabled={busy || pending}
            value={active ? key(active) : ""}
            onChange={(e) => {
              const r = links.find((x) => key(x) === e.target.value);
              if (r) choose(r);
            }}
          >
            <option value="">Choose a recording</option>
            {links.map((r) => (
              <option key={key(r)} value={key(r)}>
                {r.title}
              </option>
            ))}
          </select>
        </label>
      )}
      {active && src && (
        <>
          <iframe
            className={
              active.kind === "youtube" ? "youtubePlayer" : "twitchPlayer"
            }
            title="Linked recording preview"
            src={src.embed}
            allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
            allow="encrypted-media; fullscreen"
          />
          <a href={src.watch} target="_blank" rel="noopener noreferrer">
            Open on {active.kind === "youtube" ? "YouTube" : "Twitch"} ↗
          </a>
          {active.kind !== "twitch_clip" && (
            <>
              <label>
                Clip title
                <input
                  value={title}
                  maxLength={100}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <div className="liveActions">
                <label>
                  Start (H:MM:SS or seconds)
                  <input
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label>
                  End
                  <input value={end} onChange={(e) => setEnd(e.target.value)} />
                </label>
              </div>
              <button
                disabled={busy || pending}
                onClick={() =>
                  void run(async () => {
                    await save(selection());
                    setMessage("Selection saved.");
                  })
                }
              >
                Save and preview selection
              </button>
              {active.kind === "youtube" ? (
                <>
                  <p>
                    The player previews your saved range. A YouTube timestamp
                    link opens at its start. AI analysis and MP4 export require
                    an authorized media file.
                  </p>
                  <button
                    onClick={() =>
                      void run(async () => {
                        await navigator.clipboard.writeText(src.watch);
                        setMessage("YouTube timestamp link copied.");
                      })
                    }
                  >
                    Copy YouTube timestamp link
                  </button>
                </>
              ) : (
                <>
                  <p>
                    Preview starts at the saved time. Publishing creates a
                    public 5–60 second clip on your connected Twitch channel,
                    which you can then import for editing.
                  </p>
                  <button
                    disabled={busy || pending}
                    onClick={() =>
                      void run(async () => {
                        const r = selection();
                        if (r.end - r.start < 5 || r.end - r.start > 60)
                          throw new Error(
                            "Choose 5–60 seconds for a Twitch clip.",
                          );
                        await save(r);
                        setClip(
                          await api<Clip>(
                            "/providers/twitch/vod-clips",
                            "POST",
                            {
                              vod_id: r.external_id,
                              start: r.start,
                              end: r.end,
                              title: r.title,
                            },
                          ),
                        );
                      })
                    }
                  >
                    Publish selection on Twitch
                  </button>
                </>
              )}
            </>
          )}
          {clip && <p role="status">Twitch clip · {clip.state}</p>}
          {clip?.state === "unknown" && (
            <button
              disabled={busy}
              onClick={() =>
                void run(async () =>
                  setClip(
                    await api<Clip>(clipPath + encodeURIComponent(clip.id)),
                  ),
                )
              }
            >
              Check clip status
            </button>
          )}
          {readyId && (
            <button
              disabled={busy}
              onClick={() =>
                void run(async () =>
                  open(
                    await api<Video>(
                      clipPath + encodeURIComponent(readyId) + "/import",
                      "POST",
                    ),
                  ),
                )
              }
            >
              Import clip into editor
            </button>
          )}
        </>
      )}
      {(message || busy) && <p role="status">{busy ? "Working…" : message}</p>}
    </section>
  );
}
