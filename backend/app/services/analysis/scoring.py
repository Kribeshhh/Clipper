import math
import re

from app.services.analysis.context import SpeechContext, qualities

REACTION = re.compile(
    r"\b(wow|whoa|no way|oh my god|unbelievable|clutch|hilarious|haha+)\b", re.I
)


def rank(duration, levels, samples, transcript, weights, *, limit=12):
    from app.services.analysis.signals import audio_change

    index = SpeechContext(transcript)
    events = []
    speech = {}
    for segment in transcript:
        for i in range(
            max(0, int(segment["start"])),
            min(math.ceil(duration), math.ceil(segment["end"])),
        ):
            if i not in speech or REACTION.search(segment["text"]):
                speech[i] = segment
    for i in range(math.ceil(duration)):
        pair = [s for s in samples[i * 2 : i * 2 + 2] if s is not None]
        cut = max((s for _, s in pair), default=0)
        # A large cut cannot simultaneously count as motion on the same frame.
        movement = max((m for m, s in pair if s < 0.42), default=0)
        segment = speech.get(i)
        raw = {
            "audio": audio_change(levels, i),
            "scene": min(1, max(0, (cut - 0.30) / 0.55)),
            "motion": min(1, max(0, (movement - 0.045) / 0.22)),
            "speech": float(bool(segment)) * 0.65,
            "reaction": float(bool(segment and REACTION.search(segment["text"]))),
        }
        signals = {
            k: round(v, 3) for k, v in raw.items() if v >= 0.25 and weights[k] > 0
        }
        quality, exchange = qualities(index, i, signals)
        if not (signals.keys() & {"audio", "motion", "scene"}) and not exchange:
            continue
        if len(signals) < 2 and not (exchange and signals):
            continue
        score = round(
            100
            * sum(weights[k] * v for k, v in signals.items())
            / sum(weights.values()),
            2,
        )
        score = round(
            0.65 * score
            + 0.10 * quality["hook"]
            + 0.08 * quality["context"]
            + 0.12 * quality["payoff"]
            + 0.05 * quality["pacing"],
            2,
        )
        if score < 18:
            continue
        start = max(0, i - 4)
        end = min(duration, i + 5)
        if segment:
            start = max(0, min(start, max(i - 10, segment["start"] - 1)))
            end = min(duration, max(end, min(i + 10, segment["end"] + 1)))
        events.append(
            {
                "event": i,
                "start": start,
                "end": end,
                "score": score,
                "signals": signals,
                "context": [
                    {"start": s["start"], "end": s["end"], "text": s["text"]}
                    for s in index.window(start - 15, end + 6)
                ],
                "quality": quality,
                "exchange": exchange,
                "speech": segment,
                "dbfs": levels[i] if i < len(levels) else None,
                "frame_change": movement,
                "histogram_distance": cut,
            }
        )
    selected = []
    for event in sorted(events, key=lambda e: (-e["score"], e["event"])):
        if any(
            (event["start"] < p["end"] + 1 and event["end"] + 1 > p["start"])
            or (event["speech"] is not None and event["speech"] == p["speech"])
            for p in selected
        ):
            continue
        selected.append(event)
        if len(selected) == limit:
            break
    return selected
