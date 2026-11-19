import json
import subprocess

import pytest

from app.services.editing.caption_render import write_captions
from app.services.editing.crop import crop_filter, suggested_focus
from app.services.editing.render import render
from app.services.editing.subtitles import prepare_cues


def test_relative_cues_and_plain_text(tmp_path):
    segments = [
        {"start": 1, "end": 2.5, "text": "talk"},
        {"start": 3, "end": 5, "text": "{\\pos(0,0)}\nDialogue: 7"},
    ]
    cues = prepare_cues(segments, 2, 4)
    assert [(c["start"], c["end"]) for c in cues] == [(0, 0.5), (1, 2)]
    write_captions(tmp_path, cues)
    data = (tmp_path / "captions.ass").read_text()
    assert data.count("\nDialogue:") == 2 and "{\\pos" not in data
    assert prepare_cues([], 0, 2) == prepare_cues(segments, 5, 7) == []
    pages = prepare_cues([{"start": 0, "end": 12, "text": "long phrase " * 40}], 0, 12)
    assert len(pages) > 1 and pages[0]["start"] == 0 and pages[-1]["end"] == 12
    assert all(a["end"] == b["start"] for a, b in zip(pages, pages[1:], strict=False))


def test_crop_fallback_and_stable_motion():
    assert suggested_focus(None, 0, 5) is None
    points = [[t / 2, 0.85, 0.64] for t in range(10)]
    assert suggested_focus(points, 0, 5) == {"x": 0.85, "y": 0.64}
    assert suggested_focus(points, 7, 12) is None
    for t in range(5):
        points[t][1] = 0.1
    assert suggested_focus(points, 0, 5) is None
    for bad in [-0.1, 1.1, float("nan")]:
        with pytest.raises(ValueError):
            crop_filter(bad)


def ffmpeg(*args):
    return subprocess.check_output(
        ["ffmpeg", "-v", "error", "-nostdin", *map(str, args)]
    )


@pytest.mark.parametrize("position,caption", [(0, True), (1, False)])
def test_encoded_crop_audio_and_caption_frames(tmp_path, position, caption):
    source = tmp_path / "video.mp4"
    # A split-color image and a tone beginning one second into the original.
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=red:size=640x360:rate=30:d=3,drawbox=x=320:y=0:w=320:h=360:color=blue:t=fill",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=600:duration=2,adelay=1000:all=1",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        source,
    )
    target = tmp_path / "clip.partial"
    edit = {
        "position": position,
        "cues": [{"start": 0.5, "end": 1, "text": "CAPTION TEST"}] if caption else [],
    }
    result = render(source, target, 0.5, 2, None, edit=edit)
    assert result["fps"] == 30 and (result["width"], result["height"]) == (720, 1280)
    assert result["has_audio"] and result["duration"] == pytest.approx(1.5, abs=0.035)
    streams = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(target)]
        )
    )["streams"]
    assert streams[0]["sample_aspect_ratio"] == "1:1"
    assert all(
        float(s["start_time"]) == pytest.approx(0, abs=0.035)
        and float(s["duration"]) == pytest.approx(1.5, abs=0.04)
        for s in streams
    )

    # Inspect burned pixels before, during and after the cue.
    def frame(t):
        return ffmpeg(
            "-ss",
            t,
            "-i",
            target,
            "-frames:v",
            "1",
            "-vf",
            "scale=180:320",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        )

    def white(data):
        return sum(
            min(rgb) > 160
            for rgb in zip(data[::3], data[1::3], data[2::3], strict=True)
        )

    before, during, after = map(frame, [0.2, 0.7, 1.2])
    assert white(before) == white(after) == 0
    assert (white(during) > 10) == caption
    assert before[0 if position == 0 else 2] > 200
    import array

    pcm = array.array("h")
    pcm.frombytes(
        ffmpeg("-i", target, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1")
    )
    onset = next(i for i, v in enumerate(pcm) if abs(v) > 200) / 8000
    assert onset == pytest.approx(0.5, abs=0.04)


@pytest.mark.parametrize(
    "shape,sar,box", [("144x180", "2/1", "12:24"), ("108x240", "1/1", "24:24")]
)
def test_portrait_geometry_fractional_fps_and_no_audio(tmp_path, shape, sar, box):
    source = tmp_path / "silent.mp4"
    w, h = box.split(":")
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"color=black:s={shape}:r=30000/1001:d=1,"
        f"drawbox=x=(iw-{w})/2:y=(ih-{h})/2:w={w}:h={h}:color=white:t=fill,setsar={sar}",
        "-c:v",
        "libx264",
        source,
    )
    target = tmp_path / "clip.partial"
    result = render(
        source,
        target,
        0.1,
        0.8,
        None,
        edit={"position": 0.5, "cues": []},
        expect_audio=False,
    )
    assert not result["has_audio"] and result["fps"] == pytest.approx(30000 / 1001)
    pixels = ffmpeg(
        "-i",
        target,
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
    points = [(i % 180, i // 180) for i, v in enumerate(pixels) if v > 160]
    xs, ys = zip(*points, strict=True)
    assert abs((max(xs) - min(xs)) - (max(ys) - min(ys))) <= 2
