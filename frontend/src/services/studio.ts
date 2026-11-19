export type Video = {
  id: string;
  name: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  size: number;
  has_audio: boolean;
};
export type Job = {
  id: string;
  video_id: string;
  layout: "source" | "vertical";
  error: string | null;
  state: "queued" | "rendering" | "completed" | "failed";
  progress: number;
  start: number;
  end: number;
};
export type Library = {
  videos: Video[];
  max_bytes: number;
  max_seconds: number;
};
const messages: Record<string, string> = {
  recording_mismatch:
    "This link already belongs to a different recording. Paste the link again to open that recording.",
  vod_not_owned:
    "This VOD belongs to another channel. Open it on Twitch to see its clipping options.",
  vod_range: "Choose 5–60 seconds within the Twitch recording.",
  import_url:
    "Use a direct HTTPS download URL without login credentials or a fragment.",
  import_dns: "The source hostname could not be resolved.",
  import_destination: "The source must be a publicly accessible HTTPS server.",
  youtube_media_unavailable:
    "This YouTube link provides playback access only. No authorized recording is linked in this studio. Paste a direct HTTPS MP4 link supplied by the owner.",
  twitch_vod_unavailable:
    "This Twitch VOD page has no authorized full-recording download. Paste an authorized MP4 link or a downloadable Twitch clip.",
  import_provider_page:
    "YouTube watch pages and Twitch VODs provide playback access only. For automatic clipping, supply an authorized direct MP4 URL or an eligible Twitch clip link.",
  import_redirect:
    "The source returned an invalid or excessive redirect chain.",
  import_access:
    "Access was denied or the recording is unavailable. A signed link may have expired.",
  import_connection:
    "Could not establish a verified HTTPS connection to the source.",
  import_response: "The source did not return a complete downloadable file.",
  import_transfer:
    "The download was interrupted. Submit a fresh link to retry.",
  import_incomplete:
    "The file ended early. Request a fresh direct download link.",
  import_timeout: "The source exceeded the download time limit.",
  import_size: "The recording exceeds the URL import size limit.",
  import_space: "Insufficient free media storage for this import.",
  import_interrupted:
    "The import worker was interrupted. Submit the link again.",
  import_busy: "Four imports are already pending. Wait or cancel one.",
  provider_configuration:
    "Configure the provider app credentials, callback origin and encryption key.",
  provider_reconnect:
    "The provider credential has expired. Reconnect the channel.",
  provider_denied:
    "Permission was denied. Check channel permissions and clipping settings.",
  provider_not_connected: "Connect the channel first.",
  provider_rate_limited:
    "Provider request limit reached. Wait before refreshing.",
  provider_unavailable: "The provider is temporarily unavailable.",
  provider_request_failed: "The provider could not complete this request.",
  provider_offline_or_missing: "The stream is offline or unavailable.",
  stream_offline: "The broadcast is offline.",
  clip_download_unavailable:
    "Twitch did not supply an authorized download for this clip.",
  independent_media_required:
    "Supply your own encoder feed or an authorized recording for analysis.",
  feed_busy: "Finish the existing encoder feed before starting another.",
  sign_in_required: "Unlock the studio to access your recordings.",
  login_rate_limited: "Too many unlock attempts. Try again in one minute.",
  recording_quota: "The private recording storage limit has been reached.",
  invalid_key: "That operator key was not accepted.",
  operator_key_missing: "Configure the operator key on the server.",
  invalid_media: "This recording is damaged or unreadable.",
  unsupported_media: "Choose an H.264 MP4 with optional AAC audio.",
  duration_limit: "This recording exceeds the studio duration limit.",
  upload_limit: "The file exceeds the studio size limit.",
  processing_timeout:
    "Validation exceeded the processing limit. Try a shorter recording.",
  studio_busy: "Both upload slots are busy. Try again shortly.",
  transcript_unavailable:
    "Analyze the video before rendering subtitles, or turn them off.",
  invalid_range: "Select at least one frame within the recording.",
  request_conflict: "The selection changed. Submit it again.",
  upload_timeout: "Upload timed out. Try again.",
};
export class ApiError extends Error {
  code: string;
  status: number;
  constructor(status: number, code: string) {
    super(
      messages[code] ?? "The studio could not complete the request. Try again.",
    );
    this.status = status;
    this.code = code;
  }
}
export async function api<T>(
  url: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch("/api" + url, {
    method,
    cache: "no-store",
    headers: {
      "X-Studio": "1",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data?.error?.code);
  return data as T;
}
export function upload(
  file: File,
  progress: (percent: number) => void,
): Promise<Video> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/videos");
    xhr.responseType = "json";
    xhr.setRequestHeader("Content-Type", "video/mp4");
    xhr.setRequestHeader("X-Studio", "1");
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) progress(Math.round((100 * e.loaded) / e.total));
    };
    xhr.onload = () =>
      xhr.status === 201
        ? resolve(xhr.response)
        : reject(new ApiError(xhr.status, xhr.response?.error?.code));
    xhr.onerror = () =>
      reject(
        new Error(
          "Upload connection lost. Refresh the library before trying again.",
        ),
      );
    xhr.send(file);
  });
}
export const timecode = (n: number) =>
  [Math.floor(n / 3600), Math.floor(n / 60) % 60, Math.floor(n) % 60]
    .map((v) => String(v).padStart(2, "0"))
    .join(":");

export function validSelection(
  start: string,
  end: string,
  duration: number,
  fps: number,
) {
  const from = Number(start);
  const to = Number(end);
  return (
    start.trim() !== "" &&
    end.trim() !== "" &&
    Number.isFinite(from) &&
    Number.isFinite(to) &&
    from >= 0 &&
    to <= duration &&
    from < to &&
    to - from >= Math.min(1 / fps, duration) - 1e-8
  );
}
