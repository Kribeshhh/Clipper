"""Observable proxies for narrative structure. Scores are editorial ranking points."""

import re
from bisect import bisect_left

WINDOW_SECONDS = 300

SETUP = re.compile(
    r"\?|\b(what if|watch this|last chance|one more|"
    r"only one|wait for|how did|can we)\b",
    re.I,
)
PAYOFF = re.compile(
    r"\b(actually|turns out|made it|we won|got it|did it|just won|no way|"
    r"somehow|instead|last second|can't believe|clutch|haha+|wow)\b",
    re.I,
)


class SpeechContext:
    def __init__(self, transcript):
        self.rows = sorted(transcript, key=lambda s: s["start"])
        self.starts = [s["start"] for s in self.rows]

    def window(self, a, b):
        i = max(0, bisect_left(self.starts, a) - 1)
        return [s for s in self.rows[i : bisect_left(self.starts, b)] if s["end"] > a]


def qualities(index, event, signals):
    rows = index.window(event - 15, event + 6)
    before = [s for s in rows if s["end"] <= event + 0.25]
    at = [s for s in rows if s["start"] <= event + 1 and s["end"] > event]
    setup = next((s for s in reversed(before) if SETUP.search(s["text"])), None)
    reply = next((s for s in at if PAYOFF.search(s["text"]) or "!" in s["text"]), None)
    exchange = bool(
        setup and reply and setup is not reply and reply["start"] >= setup["end"] - 0.1
    )
    activity = max(signals.get("motion", 0), signals.get("scene", 0))
    rise = signals.get("audio", 0)
    hook = max(0.85 if setup else 0, 0.6 if reply else 0, activity, rise * 0.7)
    context = (
        min(1, 0.22 + 0.30 * bool(before) + 0.28 * bool(at) + 0.2 * exchange)
        if rows
        else min(1, 0.35 + activity * 0.45)
    )
    payoff = min(
        1, 0.35 * bool(reply) + 0.35 * exchange + 0.25 * rise + 0.35 * activity
    )
    pace = min(1, 0.25 + min(3, len(rows)) * 0.12 + activity * 0.28 + rise * 0.15)
    return {
        k: round(v * 100, 1)
        for k, v in (
            ("hook", hook),
            ("context", context),
            ("payoff", payoff),
            ("pacing", pace),
        )
    }, exchange
