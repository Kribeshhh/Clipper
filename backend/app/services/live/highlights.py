import os
from pathlib import Path
from shutil import disk_usage
from tempfile import TemporaryDirectory
from uuid import uuid5

from sqlalchemy import func, select

from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.live import Moment
from app.models.media import Analysis, Job, Video
from app.services.media import probe, run_media


def contiguous(left, right):
    a, v, _ = left
    b, w, _ = right
    return (
        a.sequence + 1 == b.sequence
        and abs(a.start + v.duration - b.start) <= 0.25
        and (v.width, v.height, v.fps, v.has_audio)
        == (w.width, w.height, w.fps, w.has_audio)
    )


def ranges(left, middle, right, last_end):
    segment, video, analysis = middle
    start = segment.start
    lo = left[0].start if left else start
    hi = right[0].start + right[1].duration if right else start + video.duration
    selected = []
    for c in sorted(analysis.candidates, key=lambda c: c["event"]):
        anchor = start + c["event"]
        a = max(lo, min(start + c["start"], anchor - 4))
        b = min(hi, max(start + c["end"], anchor + 5))
        if a < last_end or b - a < 1 / video.fps:
            continue
        selected.append((a, b, c))
        last_end = b + 1
    return selected


def publish(db, feed, left, middle, right, storage):
    events = ranges(left, middle, right, feed.last_highlight_end)
    if not events:
        return
    window = [r for r in (left, middle, right) if r]
    origin = window[0][0].start
    vid = uuid5(feed.id, "window:" + str(middle[0].sequence))
    used = db.scalar(
        select(func.coalesce(func.sum(Video.size), 0)).where(
            Video.id.in_(select(Job.video_id).join(Moment, Moment.job_id == Job.id))
        )
    )
    budget = min(
        get_settings().export_quota_bytes - used,
        get_settings().max_upload_bytes,
        disk_usage(storage.root).free - 16 * 1024**2,
    )
    if (
        budget < 65536
        or db.scalar(
            select(func.count()).select_from(Moment).where(Moment.feed_id == feed.id)
        )
        >= 300
    ):
        raise StudioError(507, "live_highlight_quota")
    transcript = []
    with (
        TemporaryDirectory(prefix="clip-render-", dir=storage.root) as folder,
        storage.stage(vid) as tmp,
    ):
        directory = Path(folder)
        manifest = []
        for i, (s, v, an) in enumerate(window):
            name = f"{i}.mp4"
            os.link(storage.path(v.id), directory / name)
            manifest.extend([f"file {name}", f"duration {v.duration:.6f}"])
            delta = s.start - origin
            transcript.extend(
                [
                    {
                        **c,
                        "start": c["start"] + delta,
                        "end": c["end"] + delta,
                        **(
                            {
                                "words": [
                                    {
                                        **w,
                                        "start": w["start"] + delta,
                                        "end": w["end"] + delta,
                                    }
                                    for w in c["words"]
                                ]
                            }
                            if c.get("words")
                            else {}
                        ),
                    }
                    for c in an.transcript or []
                ]
            )
        (directory / "media.ffconcat").write_text("\n".join(manifest) + "\n")
        run_media(
            "ffmpeg",
            [
                "-nostdin",
                "-y",
                "-protocol_whitelist",
                "file",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                "media.ffconcat",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c",
                "copy",
                "-map_metadata",
                "-1",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(tmp),
            ],
            cwd=folder,
            byte_limit=budget,
        )
        meta = probe(tmp)
        db.add(
            Video(id=vid, name=f"Live highlight · {middle[0].start:.1f}s.mp4", **meta)
        )
        db.flush()
        db.add(
            Analysis(
                video_id=vid,
                state="completed",
                stage="Live window",
                progress=100,
                weights=middle[2].weights,
                transcript=transcript,
                candidates=[
                    {
                        **c,
                        "start": a - origin,
                        "end": min(meta["duration"], b - origin),
                        "event": middle[0].start + c["event"] - origin,
                        "speech": (
                            {
                                **c["speech"],
                                "start": c["speech"]["start"]
                                + middle[0].start
                                - origin,
                                "end": c["speech"]["end"] + middle[0].start - origin,
                            }
                            if c.get("speech")
                            else None
                        ),
                    }
                    for a, b, c in events
                ],
            )
        )
        for a, b, c in events:
            job = Job(
                id=uuid5(vid, str(c["event"])),
                video_id=vid,
                start=a - origin,
                end=min(meta["duration"], b - origin),
            )
            db.add(job)
            db.flush()
            db.add(
                Moment(
                    job_id=job.id,
                    feed_id=feed.id,
                    start=a,
                    end=origin + job.end,
                    evidence=c,
                )
            )
            feed.last_highlight_end = b + 1
        tmp.replace(storage.path(vid))
