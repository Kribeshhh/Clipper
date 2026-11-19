import re
from urllib.parse import urlencode, urlsplit

from app.core.errors import StudioError
from app.services.providers.http import field, rows


class Twitch:
    name = "twitch"
    authorize_url = "https://id.twitch.tv/oauth2/authorize"
    token_url = "https://id.twitch.tv/oauth2/token"
    scopes = ("clips:edit", "channel:manage:clips")
    authorize_extra = {}

    def __init__(self, settings, call):
        self.client_id = settings.twitch_client_id
        self.secret = settings.twitch_client_secret
        self.call = call

    def helix(self, path, token, method="GET", **params):
        result = self.call(
            "https://api.twitch.tv/helix/" + path + "?" + urlencode(params),
            method=method,
            headers={"Client-Id": self.client_id, "Authorization": "Bearer " + token},
        )
        return rows(result, "data")

    def validate(self, token):
        data = self.call(
            "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": "OAuth " + token},
        )
        if (
            field(data, "client_id") != self.client_id
            or not isinstance(data.get("scopes"), list)
            or not all(s in data["scopes"] for s in self.scopes)
        ):
            raise StudioError(403, "provider_denied")
        return field(data, "user_id")

    def identity(self, token):
        account = self.validate(token)
        users = self.helix("users", token)
        if len(users) != 1 or field(users[0], "id") != account:
            raise StudioError(403, "provider_denied")
        return account, field(users[0], "display_name")

    def broadcasts(self, token, account):
        return [
            {"id": field(item, "id"), "title": field(item, "title"), "state": "live"}
            for item in self.helix("streams", token, user_id=account)
            if field(item, "user_id") == account
        ]

    def create(self, token, account):
        if not self.broadcasts(token, account):
            raise StudioError(409, "stream_offline")
        result = self.helix("clips", token, "POST", broadcaster_id=account)
        if len(result) != 1:
            raise StudioError(502, "clip_creation_failed")
        return field(result[0], "id")

    def create_from_vod(self, token, account, vod_id, start, end, title):
        videos = self.helix("videos", token, id=vod_id)
        if len(videos) != 1 or field(videos[0], "id") != vod_id:
            raise StudioError(404, "provider_offline_or_missing")
        if field(videos[0], "user_id") != account:
            raise StudioError(403, "vod_not_owned")
        duration = re.fullmatch(
            r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", field(videos[0], "duration", 24)
        )
        if not duration:
            raise StudioError(502, "invalid_provider_response")
        hours, minutes, seconds = (int(x or 0) for x in duration.groups())
        if end > hours * 3600 + minutes * 60 + seconds:
            raise StudioError(422, "vod_range")
        result = self.helix(
            "videos/clips",
            token,
            "POST",
            broadcaster_id=account,
            editor_id=account,
            vod_id=vod_id,
            vod_offset=end,
            duration=end - start,
            title=title,
        )
        if len(result) != 1:
            raise StudioError(502, "clip_creation_failed")
        return field(result[0], "id", 160)

    def recent(self, token, account):
        return [
            {"id": field(row, "id"), "title": field(row, "title"), "state": "ready"}
            for row in self.helix("clips", token, broadcaster_id=account, first=20)
            if field(row, "broadcaster_id") == account
        ]

    def clip(self, token, account, clip_id):
        clips = self.helix("clips", token, id=clip_id)
        for clip in clips:
            if (
                field(clip, "id") == clip_id
                and field(clip, "broadcaster_id") == account
            ):
                return {"id": clip_id, "title": field(clip, "title"), "state": "ready"}
        return None

    def download(self, token, account, clip_id):
        for clip in self.helix(
            "clips/downloads",
            token,
            broadcaster_id=account,
            editor_id=account,
            clip_id=clip_id,
        ):
            if field(clip, "clip_id") != clip_id:
                continue
            url = clip.get("landscape_download_url") or clip.get(
                "portrait_download_url"
            )
            if isinstance(url, str) and len(url) < 8192:
                parts = urlsplit(url)
                if (
                    parts.scheme == "https"
                    and parts.hostname
                    and parts.hostname.endswith(".twitchcdn.net")
                    and parts.netloc == parts.hostname
                    and not parts.fragment
                    and not any(c in url for c in "\r\n\\")
                ):
                    return url
        raise StudioError(403, "clip_download_unavailable")
