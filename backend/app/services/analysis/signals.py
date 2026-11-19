"""Bounded signal extraction. All observations use seconds on the source timeline."""

import math
import wave
from functools import lru_cache
from statistics import median

import cv2
import numpy as np

from app.services.media import INPUT, run_media

RATE = 16000


def extract_audio(source, wav, duration, start=0):
    run_media(
        "ffmpeg",
        [
            "-nostdin",
            "-y",
            *INPUT,
            "-ss",
            str(start),
            "-i",
            str(source),
            "-t",
            str(duration),
            "-map",
            "0:a:0",
            "-af",
            "aresample=16000:async=1:first_pts=0",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(wav),
        ],
    )


def audio_levels(wav):
    levels = []
    with wave.open(str(wav), "rb") as audio:
        while data := audio.readframes(RATE):
            pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
            rms = float(np.sqrt(np.mean(pcm * pcm)))
            levels.append(round(20 * math.log10(max(rms, 1e-5)), 2))
    return levels


@lru_cache(maxsize=1)
def whisper(path):
    from faster_whisper import WhisperModel

    return WhisperModel(
        str(path),
        device="cpu",
        compute_type="int8",
        cpu_threads=1,
        num_workers=1,
        local_files_only=True,
    )


def transcribe(wav, duration, model, progress):
    segments = []
    with wave.open(str(wav), "rb") as audio:
        while audio.tell() < audio.getnframes():
            offset = audio.tell() / RATE
            data = audio.readframes(30 * RATE)
            pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
            if np.max(np.abs(pcm)) > 0.001:
                results, _ = model.transcribe(
                    pcm,
                    word_timestamps=True,
                    beam_size=1,
                    vad_filter=True,
                    condition_on_previous_text=False,
                    temperature=0,
                )
                for item in results:
                    if not all(math.isfinite(x) for x in (item.start, item.end)):
                        continue
                    start = max(offset, offset + item.start)
                    end = min(duration, offset + len(pcm) / RATE, offset + item.end)
                    text = item.text.strip()
                    if start < end and text and item.no_speech_prob < 0.65:
                        row = {"start": start, "end": end, "text": text}
                        words = []
                        for w in getattr(item, "words", None) or []:
                            if math.isfinite(w.start) and math.isfinite(w.end):
                                a = max(start, offset + w.start)
                                b = min(end, offset + w.end)
                                if a < b and w.word.strip():
                                    words.append(
                                        {
                                            "start": a,
                                            "end": b,
                                            "text": w.word.strip(),
                                            "probability": float(w.probability),
                                        }
                                    )
                        if words:
                            row["words"] = words
                        segments.append(row)
            progress(min(1, audio.tell() / audio.getnframes()))
    return segments


def visual_samples(source, proxy, duration, focus_points=None, start=0):
    # Decode reference frames, then reduce to two small intra frames per second.
    # OpenCV only sees the locally generated proxy, never an uploaded container.
    run_media(
        "ffmpeg",
        [
            "-nostdin",
            "-y",
            *INPUT,
            "-skip_frame",
            "bidir",
            "-ss",
            str(start),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-t",
            str(duration),
            "-vf",
            "fps=2:start_time=0,scale=160:90",
            "-c:v",
            "mjpeg",
            "-q:v",
            "5",
            "-threads",
            "1",
            str(proxy),
        ],
    )
    cv2.setNumThreads(1)
    capture = cv2.VideoCapture(str(proxy), cv2.CAP_FFMPEG)
    previous = None
    previous_hist = None
    rows = []
    try:
        if not capture.isOpened():
            if duration < 0.5:
                return []
            raise RuntimeError("Visual sampling failed")
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
            cv2.normalize(hist, hist, alpha=1, norm_type=cv2.NORM_L1)
            motion = scene = 0.0
            if previous is not None:
                delta = cv2.absdiff(gray, previous)
                motion = cv2.mean(delta)[0] / 255
                scene = cv2.compareHist(hist, previous_hist, cv2.HISTCMP_BHATTACHARYYA)
                if focus_points is not None and scene < 0.25:
                    ys, xs = np.where(delta > 32)
                    if (
                        0.015 < len(xs) / gray.size < 0.30
                        and xs.std() < 24
                        and ys.std() < 17
                    ):
                        focus_points.append(
                            [
                                len(rows) / 2,
                                round(float(xs.mean() / 160), 3),
                                round(float(ys.mean() / 90), 3),
                            ]
                        )
            rows.append([round(motion, 4), round(scene, 4)])
            previous, previous_hist = gray, hist
    finally:
        capture.release()
    return rows


def audio_change(levels, index):
    if not 0 < index < len(levels):
        return 0.0
    previous = [v for v in levels[max(0, index - 6) : index] if v is not None]
    if not previous or levels[index] is None:
        return 0.0
    gain = levels[index] - median(previous)
    # Ignore fluctuations near the noise floor and constant loud passages.
    return max(0, min(1, (gain - 3) / 15, (levels[index] + 48) / 24))
