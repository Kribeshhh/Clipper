"""Self-authored synthetic dialogue and gameplay patterns, with real local inference."""

import json
import os
import subprocess
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.orm import Session
from test_url_import import PUBLIC, URL, fake_server

from app.models.media import Analysis, Job, VideoImport
from app.services import url_ingestion
from app.services.storage.local import LocalMedia
from app.workers import analysis, imports, rendering


def make_recording(path):
    fixtures = Path(__file__).parent / "fixtures"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=s=160x90:r=12:d=330",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=160x90:r=12:d=330",
            "-i",
            str(fixtures / "first.flac"),
            "-i",
            str(fixtures / "second.flac"),
            "-filter_complex",
            "[0:v][1:v]overlay=enable='between(t,8,19)+between(t,307,320)'[v];"
            "[2:a]adelay=8000:all=1[a];[3:a]adelay=307000:all=1[b];"
            "[a][b]amix,apad,atrim=duration=330[s]",
            "-map",
            "[v]",
            "-map",
            "[s]",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1", reason="requires local model and studio"
)
def test_import_to_automatic_download(studio, tmp_path, monkeypatch):
    client, root = studio
    source = tmp_path / "synthetic.mp4"
    make_recording(source)
    monkeypatch.setattr(url_ingestion, "resolve", lambda _: [PUBLIC])
    fake_server(monkeypatch, source.read_bytes())
    body = {"url": URL, "generate_clips": True}
    submitted = client.post("/api/imports", json=body)
    assert submitted.status_code == 202
    key = UUID(submitted.json()["id"])
    assert client.post("/api/imports", json=body).json()["id"] == str(key)
    assert str(key) in client.get("/api/imports?automatic=true").text
    endpoint = f"/api/videos/{key}/auto-clips"
    with Session(client.app.state.dependencies.db, expire_on_commit=False) as db:
        imported = db.get(VideoImport, key)
        try:
            imports.process(db, imported, LocalMedia(root))
            assert imported.state == "completed"
            assert client.get(endpoint).json()["state"] == "queued"
            analysis.analyze_next.run()
            row = db.get(Analysis, key)
            assert row.checkpoint["next"] == 300
            analysis.analyze_next.run()
            db.refresh(row)
            assert row.state == "completed" and row.warning is None
            assert len(row.samples) == 660
            assert any(w["start"] > 307 for s in row.transcript for w in s["words"])
            results = client.get(endpoint).json()["clips"]
            assert len(results) == 2, results
            ranges = sorted((c["start"], c["end"]) for c in results)
            assert ranges[0][0] < 12 < ranges[0][1] < ranges[1][0] < 313 < ranges[1][1]
            for _ in results:
                rendering.render_next.run()
            assert client.post(endpoint).is_success
            final = client.get(endpoint).json()["clips"]
            assert [c["id"] for c in final] == [c["id"] for c in results]
            for clip in final:
                assert clip["state"] == "completed", clip
                key_id = UUID(clip["id"])
                edit = db.get(Job, key_id).edit
                assert edit["cues"] and all(
                    0 <= cue["start"] < cue["end"] <= edit["duration"] + 1e-6
                    for cue in edit["cues"]
                )
                assert set(clip["quality"]) == {"hook", "context", "pacing", "payoff"}
                download = client.get(f"/api/clips/{key_id}/download")
                assert b"ftyp" in download.content[:32]
                assert (
                    download.content
                    == client.get(f"/api/clips/{key_id}/preview").content
                )
                output = LocalMedia(root).path(key_id, clip=True)
                streams = json.loads(
                    subprocess.check_output(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_streams",
                            "-of",
                            "json",
                            str(output),
                        ]
                    )
                )["streams"]
                video, audio = streams
                assert (video["width"], video["height"]) == (720, 1280)
                assert abs(float(video["duration"]) - float(audio["duration"])) <= 0.125
                assert (
                    abs(float(video["start_time"]) - float(audio["start_time"]))
                    <= 0.084
                )
                assert clip["duration"] == pytest.approx(edit["duration"], abs=0.17)
        finally:
            db.delete(imported)
            db.commit()
    client.delete("/api/session")
    assert client.get(endpoint).status_code == client.post(endpoint).status_code == 401


def test_planner_remaps_spoken_payoff_after_quiet_gap():
    from types import SimpleNamespace

    from app.services.editing.planner import plans

    transcript = [
        {"start": 1, "end": 4, "text": "Can we make the jump?"},
        {
            "start": 14,
            "end": 17,
            "text": "Wow!",
            "words": [
                {"text": "Wow!", "start": 14.25, "end": 14.8, "probability": 0.91}
            ],
        },
    ]
    levels = [-25] * 25
    levels[5:13] = [-80] * 8
    analysis_row = SimpleNamespace(
        transcript=transcript, levels=levels, samples=[[0, 0]] * 50, focus_points=[]
    )
    video = SimpleNamespace(duration=25, fps=30, width=320, height=180)
    event = dict(
        start=10,
        end=20,
        event=15,
        quality={"payoff": 80},
        score=90,
        signals={"audio": 0.8, "speech": 0.65},
    )
    (plan,) = plans(video, analysis_row, [event, event], 20, 60)
    first, second = plan["edit"]["spans"]
    assert first[0] <= 1 < 4 <= first[1] < second[0] < 14
    cue = next(c for c in plan["edit"]["cues"] if c.get("emphasis"))
    assert cue["start"] == pytest.approx(first[1] - first[0] + 14.25 - second[0])
    assert cue["end"] - cue["start"] == pytest.approx(0.55)


def test_scene_boundaries_require_silence():
    from app.services.analysis.context import SpeechContext
    from app.services.editing.planner import nearby_cut

    samples = [[0, 0]] * 30
    samples[8] = [0, 0.8]
    assert nearby_cut(4.7, samples, SpeechContext([]), 0, 10) == 4
    spoken = SpeechContext([{"start": 3.5, "end": 5, "text": "Still speaking."}])
    assert nearby_cut(4.7, samples, spoken, 0, 10) == 4.7


@pytest.mark.parametrize("audio", ["balanced", "normalized"])
def test_cut_retimes_encoded_audio_and_captions_together(tmp_path, audio):
    import numpy as np
    from test_editing import ffmpeg

    from app.services.editing.render import render

    source = tmp_path / "source.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=160x90:r=25:d=6",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=600:duration=1,adelay=4250:all=1,apad",
        "-t",
        "6",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        source,
    )
    output = tmp_path / "edit.partial"
    edit = {
        "audio": audio,
        "position": 0.5,
        "spans": [[0, 1], [4, 6]],
        "fps": 25,
        "accent": 1.3,
        "cues": [{"start": 1.25, "end": 2.25, "text": "Wow!", "emphasis": "Wow!"}],
    }
    result = render(source, output, 0, 6, None, edit=edit, expect_audio=True)
    assert result["duration"] == pytest.approx(3, abs=0.04)
    pcm = np.frombuffer(
        ffmpeg("-i", output, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"),
        dtype="<i2",
    )
    assert np.flatnonzero(abs(pcm) > 200)[0] / 8000 == pytest.approx(1.25, abs=0.04)
    for t, visible in [(1.12, False), (1.44, True), (2.4, False)]:
        frame = ffmpeg(
            "-ss",
            str(t),
            "-i",
            output,
            "-frames:v",
            "1",
            "-vf",
            "scale=180:320",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "pipe:1",
        )
        assert (max(frame) > 100) == visible


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="requires studio")
def test_failed_plan_retries_without_repeating_inference(
    studio, recording, monkeypatch
):
    from test_media import upload

    from app.workers import autoclips

    client, _ = studio
    video_id = UUID(upload(client, recording).json()["id"])
    endpoint = f"/api/videos/{video_id}/auto-clips"
    assert client.post(endpoint).is_success

    def fail(*args):
        raise RuntimeError("Interrupted planning")

    with monkeypatch.context() as patch:
        patch.setattr(autoclips, "schedule", fail)
        analysis.analyze_next.run()
    assert client.get(endpoint).json()["state"] == "failed"
    monkeypatch.setattr(analysis, "analyze", fail)
    assert client.post(endpoint).is_success
    analysis.analyze_next.run()
    assert client.get(endpoint).json()["state"] == "planned"


def test_auto_planner_can_select_ten_independent_events():
    from types import SimpleNamespace

    from app.services.editing.planner import plans

    video = SimpleNamespace(duration=600, fps=30, width=160, height=90)
    observed = SimpleNamespace(samples=[], transcript=[], levels=[], focus_points=[])
    events = [
        {
            "event": i,
            "start": i - 4,
            "end": i + 5,
            "score": 80,
            "quality": {"payoff": 50},
            "signals": {"motion": 0.8, "scene": 0.6},
        }
        for i in range(20, 590, 45)
    ]
    assert len(plans(video, observed, events, 20, 60)) == 10


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1", reason="requires private studio"
)
def test_link_first_rejects_pages_and_private_sources(studio):
    client, _ = studio
    for url, code in [
        ("https://www.youtube.com/watch?v=abcdefghijk", "youtube_media_unavailable"),
        ("https://www.twitch.tv/videos/1234", "twitch_vod_unavailable"),
        ("https://localhost/private.mp4", "import_destination"),
        ("http://clips.twitch.tv/TestClip", "import_url"),
    ]:
        reply = client.post("/api/imports", json={"url": url, "generate_clips": True})
        assert reply.status_code == 422 and reply.json()["error"]["code"] == code
    client.delete("/api/session")
    assert (
        client.post(
            "/api/imports", json={"url": URL, "generate_clips": True}
        ).status_code
        == 401
    )
    assert client.get("/api/imports?automatic=true").status_code == 401


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1" or os.getenv("RUN_PUBLIC_MEDIA") != "1",
    reason="requires the public Sintel trailer and a local studio",
)
def test_public_link_to_download(studio):
    client, root = studio
    submitted = client.post(
        "/api/imports",
        json={
            "url": "https://media.w3.org/2010/05/sintel/trailer.mp4",
            "generate_clips": True,
        },
    )
    assert submitted.status_code == 202
    key = UUID(submitted.json()["id"])
    endpoint = f"/api/videos/{key}/auto-clips"
    with Session(client.app.state.dependencies.db, expire_on_commit=False) as db:
        job = db.get(VideoImport, key)
        try:
            imports.process(db, job, LocalMedia(root))
            assert job.state == "completed", job.error
            analysis.analyze_next.run()
            selected = client.get(endpoint).json()["clips"]
            assert 2 <= len(selected) <= 10
            for _ in selected:
                rendering.render_next.run()
            for clip in client.get(endpoint).json()["clips"]:
                assert clip["state"] == "completed"
                media = "/api/clips/" + clip["id"]
                preview = client.get(media + "/preview")
                assert preview.status_code == 200 and b"ftyp" in preview.content[:32]
                assert preview.content == client.get(media + "/download").content
        finally:
            db.delete(job)
            db.commit()


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="requires studio")
def test_playback_link_uses_operator_selected_recording(studio, recording):
    from uuid import uuid4

    from test_media import upload

    from app.models.media import RecordingLink

    client, _ = studio
    link = {
        "kind": "youtube",
        "external_id": uuid4().hex[:11],
        "start": 3974,
        "end": 4004,
        "title": "Podcast",
    }
    endpoint = "/api/recording-links/generate"
    assert (
        client.post(endpoint, json=link).json()["error"]["code"] == "recording_required"
    )
    assert (
        client.post(endpoint, json={**link, "video_id": str(uuid4())}).status_code
        == 404
    )
    key = upload(client, recording).json()["id"]
    try:
        assert client.post(endpoint, json={**link, "video_id": key}).json() == {
            "video_id": key
        }
        assert client.post(endpoint, json={**link, "start": 0, "end": 30}).json() == {
            "video_id": key
        }
        existing = client.post(
            "/api/imports",
            json={
                "url": "https://youtu.be/" + link["external_id"] + "?t=125",
                "generate_clips": True,
            },
        )
        assert existing.status_code == 202 and existing.json()["id"] == key
        assert existing.json()["state"] == "linked"
        other = upload(client, recording).json()["id"]
        assert (
            client.post(endpoint, json={**link, "video_id": other}).json()["error"][
                "code"
            ]
            == "recording_mismatch"
        )
        assert key in client.get("/api/imports?automatic=true").text
        assert client.get(f"/api/videos/{key}/auto-clips").json()["state"] == "queued"
    finally:
        with Session(client.app.state.dependencies.db) as db:
            row = db.get(RecordingLink, ("youtube", link["external_id"]))
            if row:
                db.delete(row)
                db.commit()
    client.delete("/api/session")
    assert client.post(endpoint, json=link).status_code == 401
