import os
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.settings import SignalWeights
from app.services.analysis import scoring

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")
signals = pytest.importorskip("app.services.analysis.signals")

WEIGHTS = SignalWeights().model_dump()


def write_audio(path, amplitudes):
    with wave.open(str(path), "wb") as f:
        f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        for amplitude in amplitudes:
            wave_data = amplitude * np.sin(2 * np.pi * 440 * np.arange(16000) / 16000)
            f.writeframes(wave_data.astype("<i2").tobytes())


def test_audio_changes_and_quiet_audio(tmp_path):
    wav = tmp_path / "audio.wav"
    write_audio(wav, [10] * 4 + [12000] * 10)
    levels = signals.audio_levels(wav)
    assert len(levels) == 14 and levels[4] > levels[3] + 50
    assert signals.audio_change(levels, 4) == 1
    assert signals.audio_change(levels, 1) == signals.audio_change(levels, 13) == 0


def test_silence_and_chunk_timestamps(tmp_path):
    wav = tmp_path / "speech.wav"
    write_audio(wav, [0] * 30 + [3000] * 2)

    class Model:
        def transcribe(self, audio, **kwargs):
            assert len(audio) == 32000 and kwargs["vad_filter"]
            return [
                SimpleNamespace(start=0.2, end=1.9, text=" wow ", no_speech_prob=0.01),
                SimpleNamespace(start=2.2, end=4, text="outside", no_speech_prob=0.01),
            ], None

    assert signals.transcribe(wav, 31.5, Model(), lambda _: None) == [
        {"start": 30.2, "end": 31.5, "text": "wow"}
    ]
    write_audio(wav, [0] * 3)
    assert signals.transcribe(wav, 3, None, lambda _: None) == []


def test_opencv_visual_samples(tmp_path):
    source = tmp_path / "cut.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=160x90:r=12:d=1[a];color=white:s=160x90:r=12:d=1[b];[a][b]concat=n=2",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
    )
    rows = signals.visual_samples(source, tmp_path / "sample.avi", 2)
    assert len(rows) == 4 and max(s for _, s in rows) > 0.9
    assert rows[1] == rows[3] == [0, 0]


def test_one_signal_or_no_observations_yields_no_highlights():
    assert scoring.rank(10, [], [], [], WEIGHTS) == []
    assert scoring.rank(3, [-70, -7, -9], [], [], WEIGHTS) == []
    assert scoring.rank(2, [], [[1, 1]] * 4, [], WEIGHTS) == []


def test_rank_evidence_weights_context_and_deduplication():
    levels = [-60] * 30
    for i in [1, 2, 25, 29]:
        levels[i] = -10
    samples = [[0, 0] for _ in range(60)]
    for i in [1, 2, 25, 29]:
        samples[i * 2] = [0.3, 0.1]
    transcript = [{"start": 25.1, "end": 26, "text": "wow"}]
    ranked = scoring.rank(29.7, levels, samples, transcript, WEIGHTS)
    assert len(ranked) == 2
    first, second = ranked
    assert first["event"] == 25 and first["speech"] == transcript[0]
    assert first["score"] > second["score"]
    assert first["end"] == 29.7 and second["start"] == 0
    assert first["start"] > second["end"] + 1
    assert "audio" in first["signals"] and first["dbfs"] == -10
    assert ranked == scoring.rank(29.7, levels, samples, transcript, WEIGHTS)
    assert (
        scoring.rank(29.7, levels, samples, transcript, dict.fromkeys(WEIGHTS, 0)) == []
    )
    muted = scoring.rank(29.7, levels, samples, transcript, {**WEIGHTS, "audio": 0})
    assert len(muted) == 1 and "audio" not in muted[0]["signals"]


def test_speechless_multisignal_visual_event():
    ranked = scoring.rank(2, [], [[0, 0], [0, 0], [1, 1], [0.35, 0.1]], [], WEIGHTS)
    assert ranked[0]["signals"].keys() == {"scene", "motion"}
    assert ranked[0]["speech"] is None and ranked[0]["dbfs"] is None


def test_local_whisper_rejects_non_speech(tmp_path):
    path = Path(os.environ.get("WHISPER_MODEL_DIR", "/opt/whisper-tiny"))
    if not (path / "model.bin").exists():
        pytest.skip("Requires the pinned local tiny model")
    wav = tmp_path / "tone.wav"
    write_audio(wav, [9000] * 4)
    assert signals.transcribe(wav, 4, signals.whisper(path), lambda _: None) == []
