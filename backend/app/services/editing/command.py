from app.services.editing.aspect import video_filter
from app.services.editing.timing import duration_args, seek_args
from app.services.media import INPUT


def render_args(source, target, start, end, edit, *, has_audio=False):
    args = [
        "-nostdin",
        "-abort_on",
        "empty_output_stream",
        "-y",
        "-xerror",
    ]
    if edit and "spans" in edit:
        args.extend(plan_inputs(source, edit, start, end, has_audio))
    else:
        args.extend(
            [
                *INPUT,
                *seek_args(start),
                "-i",
                str(source),
                *duration_args(start, end),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                video_filter(edit),
            ]
        )
    return args + [
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-c:v",
        "libx264",
        "-threads",
        "1",
        "-preset",
        "veryfast",
        "-filter_threads",
        "1",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-f",
        "mp4",
        str(target),
    ]


def plan_inputs(source, edit, start, end, has_audio):
    from app.services.editing.plan_validation import validate_plan

    validate_plan(edit, start, end)
    spans = edit["spans"]
    args = []
    filters = []
    streams = []
    for i, (left, right) in enumerate(spans):
        length = right - left
        args.extend(
            [*INPUT, *seek_args(left), *duration_args(left, right), "-i", str(source)]
        )
        filters.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}]")
        streams.append(f"[v{i}]")
        if has_audio:
            chain = (
                f"[{i}:a]aresample=async=1:first_pts=0,apad,atrim=duration={length:.6f}"
            )
            if i:
                chain += ",afade=t=in:d=0.008"
            if i + 1 < len(spans):
                chain += f",afade=t=out:st={max(0, length - 0.008):.6f}:d=0.008"
            filters.append(chain + f"[a{i}]")
            streams.append(f"[a{i}]")
    filters.append(
        "".join(streams)
        + f"concat=n={len(spans)}:v=1:a={int(has_audio)}[v]"
        + ("[a]" if has_audio else "")
    )
    filters.append("[v]" + video_filter(edit) + "[outv]")
    if has_audio:
        audio_filter = (
            "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,asetpts=N/SR/TB"
            if edit.get("audio") == "normalized"
            else "acompressor=threshold=0.18:ratio=2:makeup=1.25"
        )
        filters.append(
            "[a]" + audio_filter + ",alimiter=limit=0.94:level=0:latency=1[outa]"
        )
    args += [
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[outv]",
    ]
    if has_audio:
        args += ["-map", "[outa]"]
    return args
