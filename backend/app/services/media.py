import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from app.core.errors import StudioError
from app.core.settings import get_settings

# Only the MOV demuxer is used. Reject external track references and network protocols.
INPUT = [
    "-protocol_whitelist",
    "file",
    "-f",
    "mov",
    "-enable_drefs",
    "0",
    "-use_absolute_path",
    "0",
    "-threads",
    "1",
]
MP4_BRANDS = {
    "isom",
    "iso2",
    "iso3",
    "iso4",
    "iso5",
    "iso6",
    "mp41",
    "mp42",
    "avc1",
    "M4V ",
    "dash",
}


def run_media(
    tool, args, progress=None, *, cwd="/", byte_limit=None, timeout=None, check=None
):
    cfg = get_settings()
    timeout = timeout or cfg.media_timeout_seconds
    binary = shutil.which(tool)
    if binary is None:
        raise StudioError(503, "media_tool_unavailable")
    with tempfile.TemporaryFile() as output:
        command = [
            sys.executable,
            str(Path(__file__).with_name("media_exec.py")),
            str(timeout),
            str(
                byte_limit
                if byte_limit is not None
                else max(cfg.max_upload_bytes, 65536)
            ),
            binary,
            "-v",
            "error",
            "-max_alloc",
            "134217728",
            *args,
        ]
        with subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.DEVNULL,
            env={"PATH": os.defpath},
            cwd=cwd,
        ) as child:
            deadline = time.monotonic() + timeout
            offset = 0
            try:
                while child.poll() is None:
                    if time.monotonic() >= deadline:
                        raise StudioError(422, "processing_timeout")
                    if check:
                        check()
                    if progress:
                        data = os.pread(output.fileno(), 65536, offset)
                        for line in data.splitlines(keepends=True):
                            if not line.endswith(b"\n"):
                                break
                            offset += len(line)
                            if line.startswith(b"out_time_us="):
                                value = line.split(b"=", 1)[1].strip()
                                if value.isdigit():
                                    progress(int(value) / 1_000_000)
                    try:
                        child.wait(timeout=0.25)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
            if child.returncode:
                raise StudioError(422, "invalid_media")
        output.seek(0)
        return output.read(65536)


def probe(
    path: Path, max_duration: float | None = None, *, duration_limit=None, check=None
):
    data = run_media(
        "ffprobe",
        [
            *INPUT,
            "-show_entries",
            "format=duration:format_tags=major_brand:stream=codec_type,codec_name,width,height,avg_frame_rate,duration,pix_fmt:stream_disposition=attached_pic",
            "-of",
            "json",
            str(path),
        ],
        check=check,
    )
    try:
        info = json.loads(data)
        if info["format"]["tags"]["major_brand"] not in MP4_BRANDS:
            raise ValueError()
        streams = info["streams"]
        (video,) = [s for s in streams if s["codec_type"] == "video"]
        audio = [s for s in streams if s["codec_type"] == "audio"]
        if (
            len(streams) != 1 + len(audio)
            or len(audio) > 1
            or video["codec_name"] != "h264"
            or video["pix_fmt"] not in {"yuv420p", "yuvj420p"}
            or video["disposition"]["attached_pic"]
            or any(s["codec_name"] != "aac" for s in audio)
        ):
            raise StudioError(415, "unsupported_media")
        duration = float(video.get("duration", info["format"]["duration"]))
        longest = float(info["format"]["duration"])
        fps = float(Fraction(video["avg_frame_rate"]))
        width, height = video["width"], video["height"]
        if not (
            0 < fps <= 120
            and 0 < width <= 4096
            and 0 < height <= 4096
            and width * height <= 4096 * 2160
            and math.isfinite(duration)
            and duration > 0
            and math.isfinite(longest)
            and longest > 0
        ):
            raise ValueError()
        if max(duration, longest) > (
            duration_limit or get_settings().max_duration_seconds
        ):
            raise StudioError(422, "duration_limit")
        if max_duration is not None and max(duration, longest) > max_duration:
            raise StudioError(422, "segment_duration")
        return dict(
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            codec="h264",
            has_audio=bool(audio),
            size=path.stat().st_size,
        )
    except (KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise StudioError(422, "invalid_media") from exc


def validate_source(
    path: Path,
    max_duration: float | None = None,
    *,
    duration_limit=None,
    timeout=None,
    check=None,
):
    metadata = probe(path, max_duration, duration_limit=duration_limit, check=check)
    run_media(
        "ffmpeg",
        [
            "-nostdin",
            "-abort_on",
            "empty_output_stream",
            "-xerror",
            "-err_detect",
            "explode",
            *INPUT,
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-threads",
            "1",
            "-f",
            "null",
            "-",
        ],
        timeout=timeout,
        check=check,
    )
    return metadata
