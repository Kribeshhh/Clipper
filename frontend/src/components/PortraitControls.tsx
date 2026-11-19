import { useEffect, useState } from "react";
import { api, type Video } from "../services/studio";

export type Cue = { start: number; end: number; text: string };
type Preview = {
  caption_ready: boolean;
  cues: Cue[];
  focus: { x: number; y: number } | null;
};

export function useEditPreview(
  video: Video,
  start: string,
  end: string,
  enabled: boolean,
  revision: number,
) {
  const [saved, setSaved] = useState<{
    key: string;
    data: Preview | null;
  } | null>(null);
  const key = video.id + "/" + start + "/" + end + "/" + revision;
  useEffect(() => {
    if (!enabled) return;
    let current = true;
    const timer = setTimeout(() => {
      api<Preview>(
        "/videos/" +
          video.id +
          "/edit-preview?" +
          new URLSearchParams({ start, end }),
      )
        .then((data) => {
          if (current) setSaved({ key, data });
        })
        .catch(() => {
          if (current) setSaved({ key, data: null });
        });
    }, 150);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [enabled, video.id, start, end, key]);
  return saved?.key === key && enabled ? saved.data : undefined;
}

export function PortraitControls({
  aspect,
  position,
  setPosition,
  captions,
  setCaptions,
  preview,
  valid,
}: {
  aspect: number;
  position: number;
  setPosition: (p: number) => void;
  captions: boolean;
  setCaptions: (b: boolean) => void;
  preview: Preview | null | undefined;
  valid: boolean;
}) {
  const horizontal = aspect >= 9 / 16;
  const fraction = horizontal ? 9 / 16 / aspect : aspect / (9 / 16);
  const focus = preview?.focus;
  const suggested =
    focus && fraction < 1
      ? Math.max(
          0,
          Math.min(
            1,
            ((horizontal ? focus.x : focus.y) - fraction / 2) / (1 - fraction),
          ),
        )
      : 0.5;
  return (
    <div className="portraitControls">
      <div className="cropLabels">
        <label htmlFor="crop-offset">
          {horizontal ? "Horizontal" : "Vertical"} crop
        </label>
        <output>{Math.round(position * 100)}%</output>
      </div>
      <input
        id="crop-offset"
        type="range"
        min="0"
        max="1"
        step=".005"
        value={position}
        onChange={(e) => setPosition(Number(e.target.value))}
      />
      <div className="cropLabels">
        <small>{horizontal ? "Left" : "Top"}</small>
        <small>{horizontal ? "Right" : "Bottom"}</small>
      </div>
      <div className="cropActions">
        <button onClick={() => setPosition(0.5)}>Center crop</button>
        {focus && (
          <button onClick={() => setPosition(suggested)}>
            Use motion suggestion
          </button>
        )}
      </div>
      <label className="captionToggle">
        <input
          type="checkbox"
          checked={captions}
          onChange={(e) => setCaptions(e.target.checked)}
        />{" "}
        Burn in subtitles
      </label>
      {captions && valid && (
        <p className="muted" role="status">
          {preview === undefined
            ? "Loading subtitle preview…"
            : preview === null
              ? "Subtitle preview unavailable."
              : !preview.caption_ready
                ? "Analyze the video for subtitles, or turn subtitles off."
                : !preview.cues.length
                  ? "No speech in this selection. The export will have no subtitles."
                  : preview.cues.length +
                    " timed subtitle" +
                    (preview.cues.length === 1 ? "" : "s") +
                    " in this selection."}
        </p>
      )}
    </div>
  );
}
