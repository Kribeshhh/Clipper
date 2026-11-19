import json
import time
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from app.core.errors import StudioError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


@contextmanager
def open_response(url, *, method="GET", headers=None, form=None):
    request = Request(
        url,
        data=urlencode(form).encode() if form is not None else None,
        headers=headers or {},
        method=method,
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            request, timeout=10
        ) as response:
            yield response
    except HTTPError as exc:
        with exc:
            body = exc.read(4096)
        if exc.code == 429 or (
            exc.code == 403
            and any(
                code in body
                for code in (
                    b"quotaExceeded",
                    b"dailyLimitExceeded",
                    b"rateLimitExceeded",
                )
            )
        ):
            error = StudioError(429, "provider_rate_limited")

            headers = exc.headers

            def number(header, default):
                value = headers.get(header, "")
                return int(value) if value.isdigit() and len(value) < 12 else default

            error.retry_after = max(
                1,
                number("Retry-After", 60),
                number("Ratelimit-Reset", 0) - int(time.time()),
            )
            raise error from None
        if exc.code in (400, 401) and (
            exc.code == 401 or b"invalid_grant" in body or b"refresh token" in body
        ):
            raise StudioError(409, "provider_reconnect") from None
        if exc.code == 403:
            raise StudioError(403, "provider_denied") from None
        if exc.code == 404:
            raise StudioError(404, "provider_offline_or_missing") from None
        raise StudioError(502, "provider_request_failed") from None
    except (URLError, TimeoutError, OSError):
        raise StudioError(502, "provider_unavailable") from None


def request_json(url, **kwargs):
    with open_response(url, **kwargs) as response:
        data = response.read(1024 * 1024 + 1)
    try:
        if len(data) > 1024 * 1024:
            raise ValueError
        obj = json.loads(data)
        if not isinstance(obj, dict):
            raise ValueError
        return obj
    except (ValueError, UnicodeError):
        raise StudioError(502, "invalid_provider_response") from None


def field(obj, key, limit=512):
    value = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(value, str) or not 0 < len(value) <= limit:
        raise StudioError(502, "invalid_provider_response")
    return value


def rows(obj, key):
    value = obj.get(key)
    if (
        not isinstance(value, list)
        or len(value) > 100
        or any(not isinstance(x, dict) for x in value)
    ):
        raise StudioError(502, "invalid_provider_response")
    return value
