"""Resolve recording links to authorized media sources."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs
from uuid import UUID

from app.core.errors import StudioError
from app.services.url_ingestion import destination, parse_https


@dataclass(frozen=True)
class RemoteRecording:
    url: str = field(repr=False)
    identity: str = field(repr=False)


def direct_recording(url: str, identity: str | None = None) -> RemoteRecording:
    destination(url)
    return RemoteRecording(url, identity or url)


def resolve_recording(
    url: str,
    twitch_download: Callable[[str], str],
    saved_source: Callable[[str, str], UUID | None],
) -> RemoteRecording | UUID:
    parts, host = parse_https(url)
    path = parts.path.rstrip("/")
    kind = ""
    external_id = ""
    if host in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }:
        kind = "youtube"
        if host.endswith("youtu.be"):
            external_id = path.removeprefix("/")
        elif path == "/watch":
            ids = parse_qs(parts.query).get("v", [])
            external_id = ids[0] if len(ids) == 1 else ""
        elif match := re.fullmatch(r"/(?:embed|shorts|live)/([a-zA-Z0-9_-]{11})", path):
            external_id = match[1]
        if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", external_id):
            raise StudioError(422, "youtube_media_unavailable")
    elif host in {"clips.twitch.tv", "twitch.tv", "www.twitch.tv", "m.twitch.tv"}:
        pattern = (
            r"/([a-zA-Z0-9_-]{1,160})"
            if host == "clips.twitch.tv"
            else r"/[a-zA-Z0-9_]+/clip/([a-zA-Z0-9_-]{1,160})"
        )
        if match := re.fullmatch(pattern, path):
            return direct_recording(
                twitch_download(match[1]), "twitch-clip:" + match[1]
            )
        if host != "clips.twitch.tv" and (
            match := re.fullmatch(r"/videos/(\d{1,20})", path)
        ):
            kind, external_id = "twitch_vod", match[1]
        else:
            raise StudioError(422, "import_provider_page")
    else:
        return direct_recording(url)

    existing = saved_source(kind, external_id)
    if existing:
        return existing
    raise StudioError(
        422,
        "youtube_media_unavailable" if kind == "youtube" else "twitch_vod_unavailable",
    )
