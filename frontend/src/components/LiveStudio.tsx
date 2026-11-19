import { useCallback, useEffect, useState } from "react";
import { api, timecode, type Video } from "../services/studio";

type Provider = "youtube" | "twitch";
type Account = {
  provider: Provider;
  configured: boolean;
  reconnect: boolean;
  account: string | null;
};
type Stream = {
  id: string;
  title: string;
  state: string;
  recordings?: string[];
};
type Clip = { id: string; title?: string; state: string };
type Feed = {
  id: string;
  provider: Provider;
  title: string;
  state: string;
  latest: number;
  end: number;
  error: string | null;
  stage: string | null;
  progress: number | null;
  moments: {
    job: string;
    video: string;
    state: string;
    progress: number;
    start: number;
    end: number;
    score: number;
    signals: string[];
  }[];
};
type Props = {
  videos: Video[];
  open: (v: Video) => void;
  report: (e: unknown) => void;
};
type Secret = { id: string; ingest_key: string };

function Channel({
  account,
  videos,
  open,
  report,
  reload,
  started,
}: Props & {
  account: Account;
  reload: () => void;
  started: (s: Secret) => void;
}) {
  const [recent, setRecent] = useState<Clip[] | null>(null);
  const name = account.provider === "youtube" ? "YouTube" : "Twitch";
  const base = "/providers/" + account.provider;
  const [streams, setStreams] = useState<Stream[] | null>(null),
    [selected, select] = useState(""),
    [recording, choose] = useState("");
  const [clip, setClip] = useState<Clip | null>(null),
    [busy, setBusy] = useState(false);
  const stream = streams?.find((s) => s.id === selected);
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      report(e);
    } finally {
      setBusy(false);
    }
  }
  const refresh = useCallback(async () => {
    const rows = await api<Stream[]>(base + "/broadcasts");
    setStreams(rows);
    select((old) => rows.find((r) => r.id === old)?.id ?? rows[0]?.id ?? "");
  }, [base]);
  useEffect(() => {
    if (account.account) void refresh().catch(report);
  }, [account.account, refresh, report]);
  useEffect(() => {
    if (clip?.state !== "pending") return;
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    async function check() {
      try {
        const result = await api<Clip>(
          base + "/clips/" + encodeURIComponent(clip!.id),
        );
        if (current) {
          setClip(result);
          if (result.state === "pending") timer = setTimeout(check, 6000);
        }
      } catch (e) {
        if (current) {
          report(e);
          setClip(null);
        }
      }
    }
    timer = setTimeout(check, 3000);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [clip?.id, clip?.state, base, report]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section className="panel channel">
      <div className="panelTitle">
        <h2>{name}</h2>
        <span>
          {account.reconnect
            ? "Reconnect required"
            : account.account
              ? "Connected"
              : "Disconnected"}
        </span>
      </div>
      {!account.account ? (
        <>
          <p className="muted">
            {account.configured
              ? "Authorize your channel to view broadcasts."
              : "Configure the OAuth app in the studio environment to connect."}
          </p>
          <button
            disabled={!account.configured || busy}
            onClick={() =>
              act(async () => {
                location.assign(
                  (await api<{ url: string }>(base + "/connect", "POST")).url,
                );
              })
            }
          >
            Connect {name}
          </button>
        </>
      ) : (
        <>
          <p>{account.account}</p>
          {account.configured && (
            <button
              disabled={busy}
              onClick={() =>
                act(async () =>
                  location.assign(
                    (await api<{ url: string }>(base + "/connect", "POST")).url,
                  ),
                )
              }
            >
              Reconnect {name}
            </button>
          )}
          {account.provider === "twitch" && (
            <>
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => setRecent(await api<Clip[]>(base + "/clips")))
                }
              >
                Browse channel clips
              </button>
              {recent?.length ? (
                <label>
                  Channel clips
                  <select
                    value={clip?.id ?? ""}
                    onChange={(e) =>
                      setClip(
                        recent.find((c) => c.id === e.target.value) ?? null,
                      )
                    }
                  >
                    <option value="">Choose a clip</option>
                    {recent.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.title}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                recent && <p>No clips returned for this channel.</p>
              )}
            </>
          )}
          <div className="liveActions">
            <button disabled={busy} onClick={() => act(refresh)}>
              Refresh broadcasts
            </button>
            <button
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const res = await api<{ revoked: boolean }>(base, "DELETE");
                  if (!res.revoked)
                    report(
                      new Error(
                        "Disconnected locally. Revoke the app in your provider account settings to finish revocation.",
                      ),
                    );
                  reload();
                })
              }
            >
              Disconnect
            </button>
          </div>
          {streams?.length ? (
            <label>
              Authorized broadcast
              <select value={selected} onChange={(e) => select(e.target.value)}>
                {streams.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title} · {s.state}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p className="muted">
              {streams ? "No broadcasts available." : "Retrieving broadcasts…"}
            </p>
          )}
          <p className="muted">
            {name === "YouTube"
              ? "YouTube supplies broadcast metadata. For AI clipping, provide your own encoder feed or an authorized recording."
              : "Twitch supplies stream metadata and native clips. Continuous AI clipping uses your own encoder feed."}
          </p>
          {stream && (
            <>
              <div className="liveActions">
                {stream.state === "live" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(async () =>
                        started(
                          await api<Secret>("/live", "POST", {
                            provider: account.provider,
                            broadcast: stream.id,
                          }),
                        ),
                      )
                    }
                  >
                    Start encoder feed
                  </button>
                )}
                {name === "Twitch" && stream.state === "live" && (
                  <button
                    disabled={busy || clip?.state === "pending"}
                    onClick={() =>
                      act(async () =>
                        setClip(await api<Clip>(base + "/clips", "POST")),
                      )
                    }
                  >
                    Create clip on Twitch
                  </button>
                )}
              </div>
              {name === "Twitch" && (
                <p className="muted">
                  Creating a native clip publishes it on Twitch.
                </p>
              )}
              <label>
                Independently supplied recording
                <select
                  value={recording}
                  onChange={(e) => choose(e.target.value)}
                >
                  <option value="">Choose from your uploads</option>
                  {videos.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                disabled={busy || !recording}
                onClick={() =>
                  act(async () => {
                    await api(base + "/recordings", "POST", {
                      broadcast: stream.id,
                      video_id: recording,
                    });
                    open(videos.find((v) => v.id === recording)!);
                  })
                }
              >
                Associate and open editor
              </button>
              {stream.recordings?.map((id) => (
                <button
                  key={id}
                  onClick={() =>
                    act(async () => open(await api<Video>("/videos/" + id)))
                  }
                >
                  Open associated recording
                </button>
              ))}
            </>
          )}
          {clip && (
            <div role="status">
              <p>Twitch clip · {clip.title ?? clip.state}</p>
              {clip.state === "ready" && (
                <div className="liveActions">
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(async () => {
                        const { url } = await api<{ url: string }>(
                          base +
                            "/clips/" +
                            encodeURIComponent(clip.id) +
                            "/download",
                          "POST",
                        );
                        location.assign(url);
                      })
                    }
                  >
                    Download from Twitch
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(async () =>
                        open(
                          await api<Video>(
                            base +
                              "/clips/" +
                              encodeURIComponent(clip.id) +
                              "/import",
                            "POST",
                          ),
                        ),
                      )
                    }
                  >
                    Import for analysis
                  </button>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function LiveStudio(props: Props) {
  const { report } = props;
  const [accounts, setAccounts] = useState<Account[]>([]),
    [feeds, setFeeds] = useState<Feed[]>([]),
    [secret, show] = useState<Secret | null>(null);
  const refresh = useCallback(() => {
    void api<Account[]>("/providers").then(setAccounts).catch(report);
  }, [report]);
  useEffect(() => {
    refresh();
  }, [refresh]);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const r = await api<Feed[]>("/live");
        if (active) setFeeds(r);
      } catch (e) {
        if (active) report(e);
      }
      if (active) timer = setTimeout(update, 4000);
    }
    void update();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [report]);
  return (
    <>
      <div className="workspace">
        {accounts.map((a) => (
          <Channel
            key={a.provider + (a.account ?? "")}
            account={a}
            {...props}
            reload={refresh}
            started={show}
          />
        ))}
      </div>
      {secret && (
        <section className="panel livePanel">
          <h2>Connect your encoder</h2>
          <p>
            Set CLIPPER_INGEST_KEY on the encoder host to this one-time key.
          </p>
          <input
            aria-label="Private ingest key"
            readOnly
            value={secret.ingest_key}
            onFocus={(e) => e.target.select()}
          />
          <p>Pipe your own MPEG-TS output into</p>
          <code>
            python3 infrastructure/scripts/send-live.py {location.origin}{" "}
            {secret.id}
          </code>
          <div>
            <button onClick={() => show(null)}>Hide key</button>
          </div>
        </section>
      )}
      <section className="panel livePanel">
        <h2>Live processing</h2>
        {feeds.length === 0 ? (
          <p className="muted">
            Start an encoder feed from a live broadcast to detect highlights.
          </p>
        ) : (
          feeds.map((f) => (
            <article key={f.id}>
              <strong>{f.title}</strong>
              <p>
                {f.state} · {f.latest + 1} sequence positions ·{" "}
                {timecode(f.end)}
              </p>
              {f.stage && (
                <p>
                  {f.stage} · {f.progress}%
                </p>
              )}
              {f.error && <p role="alert">{f.error.replaceAll("_", " ")}</p>}
              {["waiting", "receiving", "interrupted"].includes(f.state) && (
                <button
                  onClick={() =>
                    api("/live/" + f.id + "/stop", "POST").catch(report)
                  }
                >
                  Finish feed
                </button>
              )}
              <ul>
                {f.moments.map((m) => (
                  <li key={m.job}>
                    <strong>
                      {timecode(m.start)} – {timecode(m.end)}
                    </strong>{" "}
                    · {m.state} · {m.progress}%
                    <p>
                      {m.signals.join(" · ")} · Score {m.score}
                    </p>
                    <button
                      onClick={() =>
                        api<Video>("/videos/" + m.video)
                          .then(props.open)
                          .catch(report)
                      }
                    >
                      Review highlight
                    </button>
                    {m.state === "completed" && (
                      <a
                        className="primary"
                        download
                        href={"/api/clips/" + m.job + "/download"}
                      >
                        Download MP4
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            </article>
          ))
        )}
      </section>
    </>
  );
}
