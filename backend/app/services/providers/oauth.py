import asyncio
import hashlib
import json
import logging
import secrets
import time
from typing import Literal
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.session import COOKIE, operator_binding, session_key
from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.live import Connection
from app.services.providers import http
from app.services.providers.twitch import Twitch
from app.services.providers.youtube import YouTube

Provider = Literal["youtube", "twitch"]
ADAPTERS = {"youtube": YouTube, "twitch": Twitch}


def cipher():
    key = get_settings().oauth_encryption_key
    try:
        return (
            Fernet(key.get_secret_value()) if key and key.get_secret_value() else None
        )
    except ValueError:
        raise StudioError(503, "provider_configuration") from None


def adapter(provider, redis):
    def call(url, **kwargs):
        bucket = "provider:budget:" + provider
        if redis.exists(bucket + ":wait"):
            raise StudioError(429, "provider_rate_limited")
        minute = bucket + ":" + str(int(time.time()) // 60)
        count = redis.incr(minute)
        redis.expire(minute, 120)
        if count > 80:
            raise StudioError(429, "provider_rate_limited")
        try:
            return http.request_json(url, **kwargs)
        except StudioError as e:
            if e.status == 429:
                redis.set(
                    bucket + ":wait", "1", ex=min(86400, getattr(e, "retry_after", 60))
                )
            raise

    return ADAPTERS[provider](get_settings(), call)


def configured(p):
    return bool(p.client_id and p.secret and p.secret.get_secret_value() and cipher())


def callback_url(p):
    return get_settings().studio_origin + "/api/providers/" + p.name + "/callback"


def begin(p, request, response):
    if not configured(p):
        raise StudioError(503, "provider_configuration")
    state = secrets.token_urlsafe(32)
    proof = secrets.token_urlsafe(32)
    payload = [
        p.name,
        session_key(request.cookies[COOKIE]),
        hashlib.sha256(proof.encode()).hexdigest(),
    ]
    redis = request.app.state.dependencies.redis
    redis.set("oauth:" + state, json.dumps(payload), ex=600)
    response.set_cookie(
        "oauth_proof_" + p.name,
        proof,
        httponly=True,
        samesite="lax",
        max_age=600,
        secure=get_settings().studio_origin.startswith("https:"),
        path="/api/providers/" + p.name + "/callback",
    )
    return {
        "url": p.authorize_url
        + "?"
        + urlencode(
            {
                "client_id": p.client_id,
                "redirect_uri": callback_url(p),
                "response_type": "code",
                "scope": " ".join(p.scopes),
                "state": state,
                **p.authorize_extra,
            }
        )
    }


def pack(p, reply, previous=None):
    access = http.field(reply, "access_token", 8192)
    refresh = reply.get("refresh_token") or (previous or {}).get("refresh")
    ttl = reply.get("expires_in")
    scopes = reply.get("scope", [])
    if isinstance(scopes, str):
        scopes = scopes.split()
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise StudioError(502, "invalid_provider_response")
    if scopes and not all(s in scopes for s in p.scopes):
        raise StudioError(403, "provider_denied")
    if (
        not isinstance(ttl, int)
        or ttl <= 0
        or not isinstance(refresh, str)
        or not 0 < len(refresh) <= 8192
    ):
        raise StudioError(502, "invalid_provider_response")
    return {"access": access, "refresh": refresh, "expires": time.time() + ttl}


def seal(data):
    return cipher().encrypt(json.dumps(data).encode()).decode()


def lock_provider(db, name):
    db.execute(
        text("SELECT pg_advisory_xact_lock(219074, :provider)"),
        {"provider": 1 if name == "youtube" else 2},
    )


def finish(p, request, db, state, code, error):
    if not configured(p):
        raise StudioError(503, "provider_configuration")
    redis = request.app.state.dependencies.redis
    saved = redis.getdel("oauth:" + state)
    if not saved:
        raise StudioError(403, "invalid_oauth_state")
    name, session, expected = json.loads(saved)
    proof = request.cookies.get("oauth_proof_" + p.name, "")
    binding = operator_binding()
    if (
        name != p.name
        or not secrets.compare_digest(
            expected, hashlib.sha256(proof.encode()).hexdigest()
        )
        or binding is None
        or redis.get(session) != binding
    ):
        raise StudioError(403, "invalid_oauth_state")
    if error or not code:
        raise StudioError(403, "provider_denied")
    reply = p.call(
        p.token_url,
        method="POST",
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": callback_url(p),
            "client_id": p.client_id,
            "client_secret": p.secret.get_secret_value(),
        },
    )
    data = pack(p, reply)
    account, title = p.identity(data["access"])
    lock_provider(db, p.name)
    existing = db.get(Connection, p.name, with_for_update=True)
    if existing and existing.account != account:
        raise StudioError(409, "disconnect_first")
    db.merge(
        Connection(
            provider=p.name,
            account=account,
            name=title,
            sealed=seal(data),
            checked=int(time.time()),
        )
    )
    db.commit()


def authorized(p, db):
    if not configured(p):
        raise StudioError(503, "provider_configuration")
    row = db.get(Connection, p.name, with_for_update=True)
    if not row:
        raise StudioError(409, "provider_not_connected")
    try:
        data = json.loads(cipher().decrypt(row.sealed.encode()))
    except InvalidToken:
        raise StudioError(503, "provider_configuration") from None
    refreshed = data["expires"] <= time.time() + 60
    if refreshed:
        reply = p.call(
            p.token_url,
            method="POST",
            form={
                "grant_type": "refresh_token",
                "refresh_token": data["refresh"],
                "client_id": p.client_id,
                "client_secret": p.secret.get_secret_value(),
            },
        )
        data = pack(p, reply, data)
        row.sealed = seal(data)
    if p.name == "twitch" and (refreshed or time.time() - row.checked >= 3600):
        if p.validate(data["access"]) != row.account:
            raise StudioError(403, "provider_denied")
        row.checked = int(time.time())
    account = row.account
    db.commit()
    return account, data["access"]


def revoke(p, db):
    row = db.get(Connection, p.name, with_for_update=True)
    if not row:
        return True
    try:
        data = json.loads(cipher().decrypt(row.sealed.encode()))
        if p.name == "youtube":
            url = "https://oauth2.googleapis.com/revoke"
            form = {"token": data["refresh"]}
        else:
            url = "https://id.twitch.tv/oauth2/revoke"
            form = {"token": data["access"], "client_id": p.client_id}
        with http.open_response(url, method="POST", form=form):
            return True
    except (InvalidToken, StudioError, AttributeError):
        return False


async def twitch_validation_loop(dependencies):
    def check():
        with Session(dependencies.db) as db:
            row = db.get(Connection, "twitch", with_for_update=True)
            if not row or row.checked < 0:
                return
            # Twitch requires validation on startup and hourly while connected.
            row.checked = 0
            try:
                authorized(adapter("twitch", dependencies.redis), db)
            except StudioError as exc:
                db.rollback()
                if exc.code in {"provider_reconnect", "provider_denied"}:
                    row.checked = -1
                    db.commit()
                raise

    while True:
        delay = 3500
        try:
            await asyncio.to_thread(check)
        except Exception:
            logging.getLogger(__name__).warning("Twitch validation deferred")
            delay = 60
        await asyncio.sleep(delay)
