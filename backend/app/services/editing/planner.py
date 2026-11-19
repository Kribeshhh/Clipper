"""Build a chronological edit decision list using only measured source evidence."""

import math
import re

from app.services.analysis.context import SETUP, SpeechContext
from app.services.analysis.scoring import REACTION
from app.services.editing.crop import framing_plan, suggested_focus
from app.services.editing.subtitles import prepare_cues
from app.services.editing.timing import validate_interval


def nearby_cut(edge, samples, ctx, low, high):
    # Snap only to an observed discontinuity outside speech near the desired edge.
    cuts = [
        i / 2
        for i in range(
            max(0, math.ceil(2 * max(low, edge - 1.5))),
            min(len(samples), 1 + math.floor(2 * min(high, edge + 1.5))),
        )
        if samples[i] is not None
        and samples[i][1] >= 0.42
        and not ctx.window(i / 2 - 0.12, i / 2 + 0.12)
    ]
    return min(cuts, key=lambda t: abs(t - edge), default=edge)


def boundaries(c, ctx, duration, minimum, maximum, samples):
    event = c["event"]
    turns = ctx.window(event - maximum * 0.65, event + maximum * 0.5)
    prior = [s for s in turns if s["start"] < event and s["end"] >= event - minimum]
    opening = next((s for s in reversed(prior) if SETUP.search(s["text"])), None)
    start = (
        opening["start"] if opening else max(0, min(c["start"], event - minimum * 0.62))
    )
    end = min(duration, max(c["end"], event + minimum * 0.38))
    # Include contiguous replies around the payoff, then cut at a speech boundary.
    for s in turns:
        if s["end"] <= start or s["start"] >= end + 1.2:
            continue
        if s["start"] < start < s["end"]:
            start = s["start"]
        if (
            s["start"] <= end + 1.2
            and s["end"] > end
            and s["start"] <= event + maximum * 0.35
        ):
            end = s["end"] + 0.25
    # A later reaction can contain its own imperative ("watch this"), while the
    # actual setup is earlier in the same speech run. Walk back across connected
    # utterances, bounded by the configured narrative window.
    for turn in reversed(ctx.window(start - maximum * 0.65, start)):
        if turn["end"] < start - 2 or end - turn["start"] > maximum:
            break
        start = min(start, turn["start"])
    first = next(iter(ctx.window(start, end)), None)
    if first and re.match(
        r"^(because|and then|that's why|which means)\b", first["text"], re.I
    ):
        earlier = ctx.window(first["start"] - 4, first["start"])
        if earlier and end - earlier[-1]["start"] <= maximum:
            start = min(start, earlier[-1]["start"])
    # Follow sustained visible action a little beyond the spoken reaction.
    for i in range(
        math.ceil(end * 2),
        min(len(samples), math.ceil(2 * min(end + 5, start + maximum))),
    ):
        row = samples[i]
        if row is None or row[0] < 0.045 or row[1] >= 0.42:
            break
        end = (i + 1) / 2
    # Finish the utterance even if it extends beyond the preferred target.
    for turn in ctx.window(end - 0.01, end + 0.01):
        if turn["start"] < end < turn["end"]:
            end = turn["end"] + 0.15
    start = nearby_cut(max(0, start - 0.15), samples, ctx, 0, event - 0.5)
    end = nearby_cut(end, samples, ctx, event + 0.5, duration)
    end = min(duration, end)
    if end - start > 180:
        return None
    return start, end


def keep_ranges(a, b, event, ctx, levels, samples):
    # Only close a sustained quiet gap if visual activity is low and speech has
    # actually been measured. Keep handles on both sides of every join.
    gaps = []
    run = None
    for i in range(math.ceil(a), math.floor(b) + 1):
        quiet = (
            i + 1 <= b
            and i < len(levels)
            and levels[i] is not None
            and levels[i] < -48
            and abs(i - event) > 2
            and not ctx.window(i - 0.25, i + 1.25)
            and max(
                (max(row) for row in samples[i * 2 : i * 2 + 2] if row is not None),
                default=1,
            )
            < 0.12
        )
        if quiet and run is None:
            run = i
        if not quiet and run is not None:
            if i - run >= 3:
                gaps.append((run + 0.35, i - 0.35))
            run = None
    # Trim quiet leaders and tails to the measured activity. Retain short handles.
    if gaps and gaps[0][0] - a < 1.36:
        a = gaps.pop(0)[1]
    if gaps and b - gaps[-1][1] < 1.36:
        b = gaps.pop()[0]
    kept = []
    cursor = a
    for left, right in gaps[:3]:
        if left - cursor >= 1 and b - right >= 1:
            kept.append([cursor, left])
            cursor = right
    kept.append([cursor, b])
    return kept


def aligned_cues(rows, a, b):
    cues = []
    for s in rows:
        words = s.get("words") or []
        if not words:
            cues.extend(prepare_cues([s], a, b))
            continue
        for i in range(0, len(words), 5):
            group = [w for w in words[i : i + 5] if w["end"] > a and w["start"] < b]
            if not group:
                continue
            text = " ".join(w["text"] for w in group)
            left = max(a, group[0]["start"])
            right = min(b, group[-1]["end"])
            for w in group:
                if w["probability"] < 0.70 or not REACTION.search(w["text"]):
                    continue
                word_start = max(left, w["start"])
                word_end = min(right, w["end"])
                if word_end <= word_start:
                    continue
                if left < word_start:
                    cues.append(
                        {"start": left - a, "end": word_start - a, "text": text}
                    )
                cues.append(
                    {
                        "start": word_start - a,
                        "end": word_end - a,
                        "text": text,
                        "emphasis": w["text"],
                    }
                )
                left = word_end
            if left < right:
                cues.append({"start": left - a, "end": right - a, "text": text})
    return cues


def plans(video, analysis, candidates, minimum, maximum):
    ctx = SpeechContext(analysis.transcript or [])
    selected = []
    signatures = []
    for c in candidates:
        edges = boundaries(
            c, ctx, video.duration, minimum, maximum, analysis.samples or []
        )
        if not edges:
            continue
        a, b = edges
        validate_interval(a, b, video.duration, video.fps)
        if any(a < p["end"] + 1 and b > p["start"] - 1 for p in selected):
            continue
        rows = ctx.window(a, b)
        signature = set(" ".join(s["text"].lower() for s in rows).split())
        if len(signature) >= 8 and any(
            len(signature & old) / len(signature | old) > 0.82 for old in signatures
        ):
            continue
        spans = (
            keep_ranges(
                a, b, c["event"], ctx, analysis.levels or [], analysis.samples or []
            )
            if analysis.transcript is not None
            else [[a, b]]
        )
        a, b = spans[0][0], spans[-1][1]
        focus = suggested_focus(analysis.focus_points, a, b)
        cues = []
        elapsed = 0
        accent = None
        for left, right in spans:
            validate_interval(left, right, video.duration, video.fps)
            for cue in aligned_cues(ctx.window(left, right), left, right):
                cues.append(
                    {
                        **cue,
                        "start": cue["start"] + elapsed,
                        "end": cue["end"] + elapsed,
                    }
                )
            if left <= c["event"] < right:
                accent = elapsed + c["event"] - left
            elapsed += right - left
        selected.append(
            {
                "start": a,
                "end": b,
                "description": next(
                    (
                        s["text"][:160]
                        for s in rows
                        if s["start"] <= c["event"] < s["end"]
                    ),
                    rows[0]["text"][:160] if rows else "Audio and visual activity",
                ),
                "score": c["score"],
                "quality": c["quality"],
                "event": c["event"],
                "signals": c["signals"],
                "speech": [
                    {"text": s["text"], "start": s["start"], "end": s["end"]}
                    for s in rows
                ],
                "edit": {
                    "position": focus["x"]
                    if focus and video.width >= video.height
                    else focus["y"]
                    if focus
                    else 0.5,
                    "captions": True,
                    "cues": cues,
                    "spans": spans,
                    "framing": framing_plan(
                        analysis.focus_points,
                        analysis.samples or [],
                        spans,
                        video.width,
                        video.height,
                    ),
                    "audio": "normalized"
                    if getattr(video, "has_audio", True)
                    else "absent",
                    "accent": accent if c["quality"]["payoff"] >= 40 else None,
                    "fps": min(60, video.fps),
                    "duration": elapsed,
                },
            }
        )
        signatures.append(signature)
        if len(selected) >= 10:
            break
    return selected
