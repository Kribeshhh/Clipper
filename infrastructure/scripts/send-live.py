#!/usr/bin/env python3
"""Publish the operator's own MPEG-TS encoder output from stdin, one MP4 at a time."""

import argparse
import csv
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("studio_origin")
    parser.add_argument("feed_id", type=UUID)
    args = parser.parse_args()
    origin = urlsplit(args.studio_origin)
    if (
        not origin.hostname
        or origin.path
        or origin.query
        or origin.fragment
        or origin.username
        or origin.password
        or (
            origin.scheme != "https"
            and not (
                origin.scheme == "http"
                and origin.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        parser.error("Provide an HTTPS studio origin or loopback HTTP origin")
    key = os.environ.get("CLIPPER_INGEST_KEY", "")
    if len(key) != 43:
        parser.error("Set CLIPPER_INGEST_KEY to this feed's private ingest key")
    base = f"{args.studio_origin}/api/live/{args.feed_id}"
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(path, data=None):
        req = Request(
            base + path,
            data=data,
            method="GET" if data is None else "PUT",
            headers={"Authorization": "Bearer " + key, "Content-Type": "video/mp4"},
        )
        with opener.open(req, timeout=90) as response:
            return json.loads(response.read(4096))

    cursor = request("/cursor")
    if cursor["state"] not in {"waiting", "receiving", "interrupted"}:
        parser.error("This feed has closed")
    seq = cursor["sequence"] + 1
    offset = cursor["end"] + (max(0, time.time() - cursor["last_seen"]) if seq else 0)
    with tempfile.TemporaryDirectory(prefix="clipper-publisher-") as temp:
        root = Path(temp)
        # Finalized CSV rows provide the encoder's presentation timestamps.
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            parser.error("FFmpeg is required on the encoder host")
        process = subprocess.Popen(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "pipe",
                "-f",
                "mpegts",
                "-i",
                "pipe:0",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-force_key_frames",
                "expr:gte(t,n_forced*10)",
                "-c:a",
                "aac",
                "-f",
                "segment",
                "-segment_time",
                "10",
                "-segment_list",
                "pipe:1",
                "-segment_list_type",
                "csv",
                "-reset_timestamps",
                "1",
                "%08d.mp4",
            ],
            cwd=temp,
            stdout=subprocess.PIPE,
            text=True,
            env={"PATH": os.environ["PATH"]},
        )
        stop = threading.Event()

        def bound_spool():
            while not stop.wait(0.25):
                files = list(root.glob("*.mp4"))
                try:
                    if (
                        len(files) > 8
                        or sum(f.stat().st_size for f in files) > 128 * 1024**2
                    ):
                        process.kill()
                        return
                except FileNotFoundError:
                    continue

        guard = threading.Thread(target=bound_spool, daemon=True)
        guard.start()
        try:
            for number, (name, start, _) in enumerate(csv.reader(process.stdout)):
                if name != f"{number:08d}.mp4":
                    raise RuntimeError("Unexpected segment manifest")
                path = root / name
                if path.stat().st_size > 32 * 1024**2:
                    raise RuntimeError("Segment too large; lower encoder bitrate")
                data = path.read_bytes()
                deadline = time.monotonic() + 120
                while True:
                    try:
                        request(
                            f"/segments/{seq}?start={offset + float(start):.6f}", data
                        )
                        break
                    except HTTPError as exc:
                        if exc.code not in {429, 502, 503, 504}:
                            raise SystemExit(
                                f"Studio rejected segment {seq} (HTTP {exc.code})"
                            ) from None
                    except (URLError, TimeoutError):
                        pass
                    if time.monotonic() > deadline:
                        raise SystemExit(
                            "Feed interrupted; restart with the same feed and fresh encoder input"
                        )
                    time.sleep(2)
                path.unlink()
                print(f"Accepted segment {seq}", flush=True)
                seq += 1
            if process.wait() != 0:
                raise SystemExit(
                    "Encoder stopped or the bounded spool filled; restart with fresh input"
                )
            request("/end", b"")
        finally:
            stop.set()
            if process.poll() is None:
                process.kill()
                process.wait()
            guard.join()


if __name__ == "__main__":
    main()
