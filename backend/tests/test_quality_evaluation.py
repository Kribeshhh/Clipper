"""Six self-authored stories with independently fixed source intervals.

The network fixture serves a real TLS file. Only test DNS and the connection
address/CA are replaced so the HTTPS importer can reach an isolated local server.
Inference, decoding, planning, database jobs, encoding, previews and downloads run.
"""

import functools
import http.client
import json
import os
import ssl
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.orm import Session
from test_url_import import PUBLIC

from app.models.media import Analysis, Job, VideoImport
from app.services import url_ingestion
from app.services.storage.local import LocalMedia
from app.workers import analysis, imports, rendering

FIXTURES = Path(__file__).parent / "fixtures"
REFERENCES = json.loads((FIXTURES / "evaluation.json").read_text())


def evaluation_video(target):
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=s=160x90:r=12:d=420",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=s=160x90:r=12:d=420",
    ]
    enabled = "+".join(f"between(t,{r['start']},{r['end']})" for r in REFERENCES)
    filters = [f"[0:v][1:v]overlay=enable='{enabled}'[v]"]
    for i, ref in enumerate(REFERENCES):
        args += ["-i", str(FIXTURES / ref["file"])]
        filters.append(f"[{i + 2}:a]adelay={ref['start'] * 1000}:all=1[a{i}]")
    filters.append(
        "".join(f"[a{i}]" for i in range(6))
        + "amix=inputs=6:normalize=0,apad,atrim=duration=420[a]"
    )
    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(target),
    ]
    subprocess.run(args, check=True)


@pytest.fixture
def recording_server(tmp_path, monkeypatch):
    target = tmp_path / "evaluation.mp4"
    evaluation_video(target)
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def connect(host, address, timeout):
        assert host == "evaluation.example" and address == PUBLIC
        return http.client.HTTPSConnection(
            "localhost",
            server.server_port,
            timeout=timeout,
            context=ssl.create_default_context(cafile=str(cert)),
        )

    monkeypatch.setattr(url_ingestion, "resolve", lambda _: [PUBLIC])
    monkeypatch.setattr(url_ingestion, "PinnedHTTPS", connect)
    try:
        yield "https://evaluation.example/evaluation.mp4"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="requires private studio and local Whisper",
)
def test_six_reference_stories_to_real_exports(studio, recording_server, tmp_path):
    client, root = studio
    submitted = client.post(
        "/api/imports", json={"url": recording_server, "generate_clips": True}
    )
    assert submitted.status_code == 202
    key = UUID(submitted.json()["id"])
    endpoint = f"/api/videos/{key}/auto-clips"
    storage = LocalMedia(root)
    with Session(client.app.state.dependencies.db, expire_on_commit=False) as db:
        imported = db.get(VideoImport, key)
        try:
            imports.process(db, imported, storage)
            assert imported.state == "completed", imported.error
            analysis.analyze_next.run()
            analysis.analyze_next.run()
            row = db.get(Analysis, key)
            assert (
                row.state == "completed"
                and row.checkpoint is None
                and row.warning is None
            )
            selected = client.get(endpoint).json()["clips"]
            report = {"selected": selected, "transcript": row.transcript}
            (tmp_path / "evaluation-results.json").write_text(
                json.dumps(report, indent=2)
            )
            assert 5 <= len(selected) <= 10, selected
            matches = []
            for ref in REFERENCES:
                match = max(
                    selected,
                    key=lambda p: max(
                        0, min(p["end"], ref["end"]) - max(p["start"], ref["start"])
                    ),
                )
                coverage = max(
                    0, min(match["end"], ref["end"]) - max(match["start"], ref["start"])
                ) / (ref["end"] - ref["start"])
                matches.append(
                    {
                        "name": ref["name"],
                        "coverage": coverage,
                        "hook": match["start"] <= ref["start"] + 0.75,
                        "payoff": match["end"] >= ref["end"] - 0.75,
                    }
                )
            report["references"] = matches
            (tmp_path / "evaluation-results.json").write_text(
                json.dumps(report, indent=2)
            )
            assert all(
                r["coverage"] >= 0.9 and r["hook"] and r["payoff"] for r in matches
            ), matches
            chronological = sorted(selected, key=lambda c: c["start"])
            assert all(
                a["end"] <= b["start"]
                for a, b in zip(chronological, chronological[1:], strict=False)
            )
            for _ in selected:
                rendering.render_next.run()
            outputs = client.get(endpoint).json()["clips"]
            for clip in outputs:
                assert clip["state"] == "completed", clip
                job = db.get(Job, UUID(clip["id"]))
                assert job.edit["cues"] and job.edit["framing"]
                assert clip["description"] and len(clip["description"]) <= 160
                assert all(
                    0 <= c["start"] < c["end"] <= job.edit["duration"] + 1e-6
                    for c in job.edit["cues"]
                )
                path = storage.path(job.id, clip=True)
                probe = json.loads(
                    subprocess.check_output(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_streams",
                            "-of",
                            "json",
                            str(path),
                        ]
                    )
                )
                video, audio = probe["streams"]
                assert (video["width"], video["height"]) == (720, 1280)
                assert abs(float(video["duration"]) - float(audio["duration"])) <= 0.17
                assert (
                    abs(float(video["start_time"]) - float(audio["start_time"]))
                    <= 0.084
                )
                download = client.get(f"/api/clips/{job.id}/download")
                assert b"ftyp" in download.content[:32]
                assert (
                    download.content
                    == client.get(f"/api/clips/{job.id}/preview").content
                )
            report["renders"] = len(outputs)
            (tmp_path / "evaluation-results.json").write_text(
                json.dumps(report, indent=2)
            )
        finally:
            db.delete(imported)
            db.commit()


def test_scene_framing_uses_stable_motion_and_center_fallback():
    from app.services.editing.crop import framing_plan

    points = [[i / 2, 0.1, 0.5] for i in range(8)] + [
        [i / 2, 0.9, 0.5] for i in range(8, 16)
    ]
    samples = [[0.1, 0] for _ in range(16)]
    samples[8] = [0.5, 0.9]
    framed = framing_plan(points, samples, [[0, 8]], 640, 360)
    assert [f["position"] for f in framed] == [0.1, 0.9]
    assert framed[0]["end"] == framed[1]["start"] == 4
    fallback = framing_plan([], samples, [[0, 8]], 640, 360)
    assert all(f["position"] == 0.5 and f["basis"] == "center" for f in fallback)


@pytest.mark.parametrize("invalid", ["caption", "frame", "duration", "audio", "order"])
def test_edit_plan_rejects_invalid_timing_and_presets(invalid):
    from app.services.editing.plan_validation import validate_plan

    edit = {"spans": [[1, 3]], "fps": 30, "cues": [], "duration": 2}
    if invalid == "caption":
        edit["cues"] = [{"start": 1, "end": 3, "text": "bad"}]
    elif invalid == "frame":
        edit["framing"] = [{"start": 0, "end": 2, "position": 1.1}]
    elif invalid == "duration":
        edit["duration"] = float("nan")
    elif invalid == "audio":
        edit["audio"] = "volume=100"
    else:
        edit["spans"] = [[2, 3], [1, 2]]
    with pytest.raises(ValueError):
        validate_plan(edit, 1, 3)


def test_encoded_scene_crop_changes_only_at_planned_cut(tmp_path):
    from test_editing import ffmpeg

    from app.services.editing.render import render

    source = tmp_path / "source.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=red:s=640x360:r=25:d=4,drawbox=x=320:y=0:w=320:h=360:color=blue:t=fill",
        "-c:v",
        "libx264",
        source,
    )
    edit = {
        "position": 0.5,
        "fps": 25,
        "spans": [[0, 4]],
        "duration": 4,
        "cues": [],
        "framing": [
            {"start": 0, "end": 2, "position": 0},
            {"start": 2, "end": 4, "position": 1},
        ],
        "audio": "absent",
    }
    output = tmp_path / "render.partial"
    result = render(source, output, 0, 4, None, edit=edit, expect_audio=False)
    assert (result["width"], result["height"]) == (720, 1280)
    for t, channel in [(1, 0), (3, 2)]:
        frame = ffmpeg(
            "-ss",
            str(t),
            "-i",
            output,
            "-frames:v",
            "1",
            "-vf",
            "scale=1:1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        )
        assert frame[channel] > 200
