export type RecordingLink = {
  kind: "youtube" | "twitch_vod" | "twitch_clip";
  external_id: string;
  start: number;
  end: number;
  title: string;
};

export function seconds(text: string): number {
  if (!/^(?:\d+:)?(?:[0-5]?\d:)?\d+$/.test(text)) return NaN;
  const parts = text.split(":").map(Number);
  if (parts.slice(1).some((n) => n >= 60)) return NaN;
  return parts.reduce((v, n) => v * 60 + n, 0);
}

export function parseRecordingLink(input: string): RecordingLink {
  const invalid = () =>
    new Error("Paste a YouTube video, Twitch VOD, or Twitch clip link.");
  let u: URL;
  try {
    u = new URL(input.trim());
  } catch {
    throw invalid();
  }
  if (
    !["https:", "http:"].includes(u.protocol) ||
    u.username ||
    u.password ||
    u.port
  )
    throw invalid();
  const host = u.hostname.replace(/^(www\.|m\.)/, "");
  const path = u.pathname.replace(/\/$/, "");
  let kind: RecordingLink["kind"], id: string | undefined;
  if (host === "youtube.com" || host === "youtu.be") {
    kind = "youtube";
    id =
      host === "youtu.be"
        ? path.slice(1)
        : path === "/watch"
          ? (u.searchParams.get("v") ?? "")
          : /^\/(?:live|shorts|embed)\/([^/]+)$/.exec(path)?.[1];
    if (!id || !/^[\w-]{11}$/.test(id)) throw invalid();
  } else if (host === "twitch.tv" && /^\/videos\/\d{1,20}$/.test(path)) {
    kind = "twitch_vod";
    id = path.split("/")[2];
  } else {
    kind = "twitch_clip";
    id =
      host === "clips.twitch.tv"
        ? path.slice(1)
        : host === "twitch.tv"
          ? /^\/\w+\/clip\/([\w-]+)$/.exec(path)?.[1]
          : undefined;
    if (!id || !/^[\w-]{1,160}$/.test(id)) throw invalid();
  }
  const raw =
    u.searchParams.get("t") ??
    u.searchParams.get("start") ??
    new URLSearchParams(u.hash.slice(1)).get("t") ??
    "0";
  const match = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/.exec(raw);
  const start =
    kind === "twitch_clip"
      ? 0
      : match && raw
        ? Number(match[1] || 0) * 3600 +
          Number(match[2] || 0) * 60 +
          Number(match[3] || 0)
        : seconds(raw);
  const end = u.searchParams.has("end")
    ? seconds(u.searchParams.get("end")!)
    : start + 30;
  if (
    !Number.isSafeInteger(start) ||
    !Number.isSafeInteger(end) ||
    end <= start ||
    end > 604800
  )
    throw new Error("The link contains an invalid timestamp.");
  const provider = kind === "youtube" ? "YouTube" : "Twitch";
  return {
    kind,
    external_id: id,
    start,
    end,
    title: (provider + " · " + id).slice(0, 100),
  };
}

export function playback(link: RecordingLink, parent: string) {
  const id = encodeURIComponent(link.external_id);
  if (link.kind === "youtube")
    return {
      embed: `https://www.youtube-nocookie.com/embed/${id}?start=${link.start}&end=${link.end}`,
      watch: `https://www.youtube.com/watch?v=${id}&t=${link.start}s`,
    };
  if (link.kind === "twitch_clip")
    return {
      embed: `https://clips.twitch.tv/embed?clip=${id}&parent=${encodeURIComponent(parent)}&autoplay=false`,
      watch: `https://clips.twitch.tv/${id}`,
    };
  const time = `${Math.floor(link.start / 3600)}h${Math.floor(link.start / 60) % 60}m${link.start % 60}s`;
  return {
    embed: `https://player.twitch.tv/?video=v${id}&parent=${encodeURIComponent(parent)}&time=${time}&autoplay=false`,
    watch: `https://www.twitch.tv/videos/${id}?t=${time}`,
  };
}
