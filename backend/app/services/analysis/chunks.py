"""One five-minute window per task, checkpointed before yielding the analysis slot."""

import math

from app.services.analysis import signals
from app.services.analysis.context import WINDOW_SECONDS


def advance(db, job, video, source, scratch, settings):
    saved = job.checkpoint or {}
    start = saved.get("next", 0)
    length = min(WINDOW_SECONDS, video.duration - start)
    levels = []
    speech = []
    missing = saved.get("missing", False)
    if video.has_audio:
        wav = scratch / "window.wav"
        signals.extract_audio(source, wav, length, start=start)
        levels = signals.audio_levels(wav)
        if max(levels, default=-100) >= -60:
            try:
                speech = signals.transcribe(
                    wav,
                    length,
                    signals.whisper(settings.whisper_model_dir),
                    lambda _: None,
                )
            except (OSError, ValueError, RuntimeError, ImportError):
                missing = True
        wav.unlink()
    for s in speech:
        for item in [s, *s.get("words", [])]:
            item["start"] += start
            item["end"] += start
    focus = []
    samples = signals.visual_samples(
        source, scratch / "window.avi", length, focus, start=start
    )

    expected = math.ceil(length * 2)
    samples = (samples + [None] * max(0, expected - len(samples)))[:expected]
    if video.has_audio:
        count = math.ceil(length)
        levels = (levels + [None] * max(0, count - len(levels)))[:count]
    job.levels = (job.levels or []) + levels if start else levels
    job.samples = (job.samples or []) + samples if start else samples
    shifted = [[t + start, x, y] for t, x, y in focus]
    job.focus_points = (job.focus_points or []) + shifted if start else shifted
    transcript = saved.get("transcript", []) + speech
    until = start + length
    done = until >= video.duration
    job.checkpoint = (
        None if done else {"next": until, "transcript": transcript, "missing": missing}
    )
    job.transcript = transcript if done else None
    if missing:
        job.warning = "Speech unavailable for one or more windows."
    job.stage = "Analyzing recording"
    job.progress = 2 + int(88 * until / video.duration)
    if not done:
        job.attempts = 0
    db.commit()
    return done
