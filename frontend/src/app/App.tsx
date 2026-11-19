import {
  lazy,
  Suspense,
  useState,
  useEffect,
  useCallback,
  useRef,
} from "react";
import { UrlImport } from "../components/UrlImport";
import { RecordingLinks } from "../components/RecordingLinks";
import { SystemStatus } from "../components/SystemStatus";
import {
  api,
  upload,
  timecode,
  ApiError,
  type Library,
  type Video,
} from "../services/studio";

const LiveStudio = lazy(async () => ({
  default: (await import("../components/LiveStudio")).LiveStudio,
}));
const Editor = lazy(async () => ({
  default: (await import("../components/Editor")).Editor,
}));

export function App() {
  const [live, setLive] = useState(
    new URLSearchParams(location.search).has("live"),
  );
  const [recordings, setRecordings] = useState(false);
  const [status, setStatus] = useState(false),
    [lib, setLib] = useState<Library | null>(null),
    [loading, setLoading] = useState(true);
  const [key, setKey] = useState(""),
    [err, setErr] = useState(""),
    [busy, setBusy] = useState(false),
    [pct, setPct] = useState<number | null>(null);
  const [importMode, setImportMode] = useState<"file" | "url">("file");
  const [selected, select] = useState<Video | null>(null),
    [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const report = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) {
      setLib(null);
      select(null);
    }
    setErr(e instanceof Error ? e.message : "Connection lost. Try again.");
  }, []);
  useEffect(() => {
    let mounted = true;
    api<Library>("/studio")
      .then((x) => {
        if (mounted) setLib(x);
      })
      .catch((e) => {
        if (mounted && !(e instanceof ApiError && e.status === 401)) report(e);
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [report]);
  const refreshLibrary = useCallback(() => {
    void api<Library>("/studio").then(setLib).catch(report);
  }, [report]);
  async function receive(file?: File) {
    if (!file || !lib || busy) return;
    setErr("");
    if (file.size > lib.max_bytes) {
      setErr(
        "File exceeds the " +
          Math.round(lib.max_bytes / 1024 ** 2) +
          " MiB upload limit.",
      );
      return;
    }
    setBusy(true);
    setPct(0);
    try {
      const v = await upload(file, setPct);
      setLib({ ...lib, videos: [v, ...lib.videos] });
      select(v);
    } catch (e) {
      report(e);
    } finally {
      setBusy(false);
      setPct(null);
      if (input.current) input.current.value = "";
    }
  }
  return (
    <>
      <header className="top">
        <div className="identity">
          <i aria-hidden="true">◩</i>Clipper <b>Studio</b>
        </div>
        <nav aria-label="Workspace">
          <button
            aria-current={
              !status && !live && !recordings && !selected ? "page" : undefined
            }
            onClick={() => {
              setStatus(false);
              setLive(false);
              select(null);
              setRecordings(false);
              setErr("");
            }}
          >
            Workspace
          </button>
          {lib && (
            <button
              aria-current={
                !status && !live && (recordings || !!selected)
                  ? "page"
                  : undefined
              }
              onClick={() => {
                setStatus(false);
                setLive(false);
                select(null);
                setRecordings(true);
                setErr("");
                refreshLibrary();
              }}
            >
              My Recordings
            </button>
          )}
          {lib && (
            <button
              aria-current={live && !status ? "page" : undefined}
              onClick={() => {
                setLive(true);
                setStatus(false);
                setErr("");
              }}
            >
              Live channels
            </button>
          )}
          <button
            aria-current={status ? "page" : undefined}
            onClick={() => setStatus(true)}
          >
            System status
          </button>
        </nav>
        {lib && (
          <button
            disabled={busy}
            onClick={async () => {
              try {
                await api("/session", "DELETE");
                setLib(null);
                select(null);
                setErr("");
              } catch (e) {
                report(e);
              }
            }}
          >
            Lock studio
          </button>
        )}
      </header>
      {status ? (
        <SystemStatus />
      ) : (
        <main className="shell">
          <div className="heading">
            <div>
              <p className="kicker">
                {lib && live ? "BROADCASTS" : "RECORDED GAMEPLAY"}
              </p>
              <h1>
                {lib && live
                  ? "Live channels"
                  : selected
                    ? "Clip editor"
                    : recordings && lib
                      ? "My Recordings"
                      : "Clipping workspace"}
              </h1>
            </div>
            {selected && !live && (
              <button
                onClick={() => {
                  select(null);
                  setRecordings(true);
                  setErr("");
                }}
              >
                ← Recordings
              </button>
            )}
          </div>
          {err && (
            <p className="notice" role="alert">
              {err}
            </p>
          )}
          <Suspense fallback={<p role="status">Loading workspace…</p>}>
            {lib && live ? (
              <LiveStudio
                videos={lib.videos}
                report={report}
                open={(v) => {
                  setLib({
                    ...lib,
                    videos: [v, ...lib.videos.filter((x) => x.id !== v.id)],
                  });
                  select(v);
                  setLive(false);
                  setErr("");
                }}
              />
            ) : selected && lib ? (
              <Editor key={selected.id} video={selected} onError={report} />
            ) : lib && !recordings ? (
              <section className="panel import">
                <h2>Clip a recording</h2>
                <UrlImport
                  automatic
                  open={select}
                  report={report}
                  refreshLibrary={refreshLibrary}
                />
              </section>
            ) : (
              <>
                <div className="workspace">
                  <section className="panel import">
                    <div className="panelTitle">
                      <h2>Import recording</h2>
                      <span>MP4</span>
                    </div>
                    {lib && (
                      <div className="liveActions">
                        <button
                          aria-pressed={importMode === "file"}
                          onClick={() => setImportMode("file")}
                        >
                          Upload File
                        </button>
                        <button
                          aria-pressed={importMode === "url"}
                          onClick={() => setImportMode("url")}
                        >
                          Import from URL
                        </button>
                      </div>
                    )}
                    {loading ? (
                      <p>Connecting to studio…</p>
                    ) : !lib ? (
                      <form
                        onSubmit={async (e) => {
                          e.preventDefault();
                          setBusy(true);
                          setErr("");
                          try {
                            await api("/session", "POST", { key });
                            setKey("");
                            setLib(await api<Library>("/studio"));
                          } catch (ex) {
                            report(ex);
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        <label>
                          Operator key
                          <input
                            autoComplete="current-password"
                            required
                            type="password"
                            value={key}
                            onChange={(e) => setKey(e.target.value)}
                            placeholder="Enter your studio key"
                          />
                        </label>
                        <p className="muted">
                          Unlock your private recordings and clip exports.
                        </p>
                        <button className="primary" disabled={busy}>
                          {busy ? "Unlocking…" : "Unlock studio →"}
                        </button>
                      </form>
                    ) : importMode === "url" ? (
                      <UrlImport
                        open={select}
                        report={report}
                        refreshLibrary={refreshLibrary}
                      />
                    ) : (
                      <div
                        className={"drop " + (drag ? "over" : "")}
                        onDragOver={(e) => {
                          e.preventDefault();
                          setDrag(true);
                        }}
                        onDragLeave={() => setDrag(false)}
                        onDrop={(e) => {
                          e.preventDefault();
                          setDrag(false);
                          void receive(e.dataTransfer.files[0]);
                        }}
                      >
                        <input
                          ref={input}
                          hidden
                          type="file"
                          accept="video/mp4,.mp4"
                          aria-label="Choose recording"
                          onChange={(e) => void receive(e.target.files?.[0])}
                        />
                        {pct === null ? (
                          <>
                            <strong>Drop your recording here</strong>
                            <p>
                              {Math.round(lib.max_bytes / 1024 ** 2)} MiB max ·
                              Up to {lib.max_seconds}s · H.264 / AAC
                            </p>
                            <button
                              className="primary"
                              onClick={() => input.current?.click()}
                            >
                              Choose MP4 ↑
                            </button>
                          </>
                        ) : (
                          <div role="status">
                            <strong>
                              {pct === 100
                                ? "Validating recording…"
                                : "Uploading " + pct + "%"}
                            </strong>
                            <progress max="100" value={pct} />
                          </div>
                        )}
                      </div>
                    )}
                  </section>
                  <aside className="monitor" aria-label="Source monitor">
                    <div className="monitorTop">
                      <span>▣ &nbsp; SOURCE MONITOR</span>
                      <span>STANDBY</span>
                    </div>
                    <div className="scope" aria-hidden="true">
                      <div className="ring"></div>
                      <div className="cross"></div>
                    </div>
                    <div className="monitorBottom">
                      <span>NO INPUT</span>
                      <span>00:00:00</span>
                    </div>
                  </aside>
                </div>
                {lib && (
                  <RecordingLinks
                    report={report}
                    open={(v) => {
                      setLib({
                        ...lib,
                        videos: [v, ...lib.videos.filter((x) => x.id !== v.id)],
                      });
                      select(v);
                      setErr("");
                    }}
                  />
                )}
                <section className="library">
                  <div className="panelTitle">
                    <h2>Recordings</h2>
                    {lib && <span>{lib.videos.length}</span>}
                  </div>
                  {lib?.videos.length ? (
                    <ul>
                      {lib.videos.map((v) => (
                        <li key={v.id}>
                          <button
                            onClick={() => {
                              select(v);
                              setErr("");
                            }}
                            disabled={busy}
                          >
                            <strong>{v.name}</strong>
                            <span>
                              {v.width} × {v.height} · {timecode(v.duration)} →
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted">
                      {lib
                        ? "Your uploaded recordings will appear here."
                        : "Unlock the studio to view your library."}
                    </p>
                  )}
                </section>
              </>
            )}
          </Suspense>
        </main>
      )}
    </>
  );
}
