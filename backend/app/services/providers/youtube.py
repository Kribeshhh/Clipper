from urllib.parse import urlencode

from app.core.errors import StudioError
from app.services.providers.http import field, rows


class YouTube:
    name = "youtube"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    scopes = ("https://www.googleapis.com/auth/youtube.readonly",)
    authorize_extra = {"access_type": "offline", "prompt": "consent"}

    def __init__(self, settings, call):
        self.client_id = settings.google_client_id
        self.secret = settings.google_client_secret
        self.call = call

    def get(self, resource, token, **params):
        return self.call(
            "https://www.googleapis.com/youtube/v3/"
            + resource
            + "?"
            + urlencode(params),
            headers={"Authorization": "Bearer " + token},
        )

    def identity(self, token):
        channels = rows(
            self.get("channels", token, part="id,snippet", mine="true"), "items"
        )
        if not channels:
            raise StudioError(403, "provider_channel_missing")
        return field(channels[0], "id"), field(channels[0].get("snippet"), "title")

    def broadcasts(self, token, account):
        result = []
        page = {}
        for _ in range(4):
            data = self.get(
                "liveBroadcasts",
                token,
                part="id,snippet,status",
                mine="true",
                broadcastType="all",
                maxResults=50,
                **page,
            )
            for item in rows(data, "items"):
                snippet = item.get("snippet")
                if field(snippet, "channelId") == account:
                    result.append(
                        {
                            "id": field(item, "id"),
                            "title": field(snippet, "title"),
                            "state": field(item.get("status"), "lifeCycleStatus"),
                        }
                    )
            if not data.get("nextPageToken"):
                break
            page = {"pageToken": field(data, "nextPageToken")}
        return result
