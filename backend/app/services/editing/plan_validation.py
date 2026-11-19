"""Validate trusted planning output again before constructing FFmpeg arguments."""

import math

from app.services.editing.timing import validate_interval


def validate_plan(edit, start, end):
    fps = edit["fps"]
    if not math.isfinite(fps) or not 0 < fps <= 60:
        raise ValueError("Invalid planned frame rate")
    spans = edit["spans"]
    if not 1 <= len(spans) <= 4 or end - start > 180.000001:
        raise ValueError("Invalid edit plan")
    previous = start
    total = 0.0
    for left, right in spans:
        validate_interval(left, right, end, fps)
        if left < previous:
            raise ValueError("Unordered edit plan")
        previous = right
        total += right - left
    if (
        not math.isfinite(edit.get("duration", total))
        or abs(edit.get("duration", total) - total) > 1e-5
    ):
        raise ValueError("Inconsistent planned duration")
    previous = 0.0
    for cue in edit["cues"]:
        a, b = cue["start"], cue["end"]
        if not (previous <= a < b <= total + 1e-6) or not isinstance(cue["text"], str):
            raise ValueError("Invalid planned captions")
        previous = b
    previous = 0.0
    for frame in edit.get("framing", []):
        if (
            abs(frame["start"] - previous) > 1e-6
            or not (previous < frame["end"] <= total + 1e-6)
            or not 0 <= frame["position"] <= 1
        ):
            raise ValueError("Invalid planned framing")
        previous = frame["end"]
    if edit.get("framing") and abs(previous - total) > 1e-6:
        raise ValueError("Incomplete planned framing")
    if edit.get("audio", "balanced") not in {"normalized", "balanced", "absent"}:
        raise ValueError("Invalid audio preset")
