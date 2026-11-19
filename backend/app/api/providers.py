import re
import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from starlette.concurrency import run_in_threadpool

from app.api.media import DB, VideoInfo, require
from app.api.session import operator
from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.live import Association, Connection, Feed
from app.models.media import Video
from app.services.media import validate_source
from app.services.providers import http, oauth
from app.services.storage.local import LocalMedia
from app.services.storage.recordings import store_recording

router = APIRouter()
private = APIRouter(prefix="/api/providers", dependencies=[Depends(operator)])


def client(name, request):
    return oauth.adapter(name, request.app.state.dependencies.redis)


@private.get("")
def connections(request: Request, db: DB):
    rows = {r.provider: r for r in db.scalars(select(Connection))}
    return [
        {
            "provider": name,
            "configured": oauth.configured(client(name, request)),
            "account": rows[name].name if name in rows else None,
            "reconnect": name in rows and rows[name].checked < 0,
        }
        for name in oauth.ADAPTERS
    ]


@private.post("/{name}/connect")
def connect(name: oauth.Provider, request: Request, response: Response):
    return oauth.begin(client(name, request), request, response)


@router.get("/api/providers/{name}/callback")
def callback(
    name: oauth.Provider,
    request: Request,
    db: DB,
    state: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{43}$")],
    code: Annotated[str | None, Query(max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=512)] = None,
):
    if any(
        len(request.query_params.getlist(k)) > 1 for k in ("code", "state", "error")
    ):
        raise StudioError(403, "invalid_oauth_state")
    oauth.finish(client(name, request), request, db, state, code, error)
    response = RedirectResponse(
        get_settings().studio_origin + "/?live=1",
        303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    response.delete_cookie(
        "oauth_proof_" + name, path=f"/api/providers/{name}/callback"
    )
    return response


@private.delete("/{name}")
def disconnect(name: oauth.Provider, db: DB, request: Request):
    oauth.lock_provider(db, name)
    revoked = oauth.revoke(client(name, request), db)

    db.execute(delete(Connection).where(Connection.provider == name))
    db.execute(delete(Association).where(Association.provider == name))
    feeds = db.scalars(
        select(Feed).where(Feed.provider == name).with_for_update()
    ).all()
    for feed in feeds:
        if feed.state not in {"ended", "failed"}:
            feed.state = "ending"
        feed.secret_hash = ""
        feed.account = feed.broadcast = ""
        feed.title = "Disconnected feed"
    db.commit()
    return {"connected": False, "revoked": revoked}


def own_broadcast(db, request, name, broadcast):
    p = client(name, request)
    account, token = oauth.authorized(p, db)
    for item in p.broadcasts(token, account):
        if item["id"] == broadcast:
            # Disconnect must either see the new feed/link or precede this check.
            oauth.lock_provider(db, name)
            if (
                db.scalar(select(Connection.account).where(Connection.provider == name))
                != account
            ):
                raise StudioError(409, "provider_not_connected")
            return account, item
    raise StudioError(404, "broadcast_unavailable")


@private.get("/{name}/broadcasts")
def broadcasts(name: oauth.Provider, db: DB, request: Request):
    p = client(name, request)
    account, token = oauth.authorized(p, db)
    items = p.broadcasts(token, account)
    linked = {}
    for link in db.scalars(
        select(Association).where(
            Association.provider == name, Association.account == account
        )
    ):
        linked.setdefault(link.broadcast, []).append(str(link.video_id))
    return [{**item, "recordings": linked.get(item["id"], [])} for item in items]


class RecordingLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_id: UUID
    broadcast: str = Field(min_length=1, max_length=128)


@private.post("/{name}/recordings")
def associate(name: oauth.Provider, body: RecordingLink, db: DB, request: Request):
    account, _ = own_broadcast(db, request, name, body.broadcast)
    require(db, Video, body.video_id)
    db.merge(
        Association(
            video_id=body.video_id,
            provider=name,
            account=account,
            broadcast=body.broadcast,
        )
    )
    db.commit()
    return {"video_id": body.video_id}


@private.get("/twitch/clips")
def recent(db: DB, request: Request):
    p = client("twitch", request)
    account, token = oauth.authorized(p, db)
    return p.recent(token, account)


@private.post("/twitch/clips", status_code=202)
def create_clip(db: DB, request: Request):
    p = client("twitch", request)
    account, token = oauth.authorized(p, db)
    clip_id = p.create(token, account)
    return pending_clip(request, account, clip_id)


class VodSelection(BaseModel):
    vod_id: str = Field(pattern=r"^[0-9]{1,20}$")
    start: int = Field(ge=0, le=604800)
    end: int = Field(gt=0, le=604800)
    title: str = Field(min_length=1, max_length=100)


@private.post("/twitch/vod-clips", status_code=202)
def create_vod_clip(body: VodSelection, db: DB, request: Request):
    if not 5 <= body.end - body.start <= 60 or not body.title.strip():
        raise StudioError(422, "vod_range")
    p = client("twitch", request)
    account, token = oauth.authorized(p, db)
    clip_id = p.create_from_vod(
        token, account, body.vod_id, body.start, body.end, body.title
    )
    return pending_clip(request, account, clip_id)


def pending_clip(request, account, clip_id):
    request.app.state.dependencies.redis.set(
        f"native:{account}:{clip_id}", str(time.time()), ex=86400
    )
    return {"id": clip_id, "state": "pending"}


def twitch_clip(db, request, clip_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", clip_id):
        raise StudioError(422, "invalid_request")
    p = client("twitch", request)
    account, token = oauth.authorized(p, db)
    return p, account, token


@private.get("/twitch/clips/{clip_id}")
def clip_status(clip_id: str, db: DB, request: Request):
    p, account, token = twitch_clip(db, request, clip_id)
    ready = p.clip(token, account, clip_id)
    if ready:
        return ready
    stamp = request.app.state.dependencies.redis.get(f"native:{account}:{clip_id}")
    return {
        "id": clip_id,
        "state": "pending" if stamp and time.time() - float(stamp) < 60 else "failed",
    }


@private.post("/twitch/clips/{clip_id}/download")
def download(clip_id: str, db: DB, request: Request):
    p, account, token = twitch_clip(db, request, clip_id)
    return {"url": p.download(token, account, clip_id)}


@private.post("/twitch/clips/{clip_id}/import", response_model=VideoInfo)
async def import_clip(clip_id: str, db: DB, request: Request):
    slots = request.app.state.upload_slots
    if slots.locked():
        raise StudioError(429, "studio_busy")
    async with slots:
        return await run_in_threadpool(import_authorized_clip, clip_id, db, request)


def import_authorized_clip(clip_id: str, db: DB, request: Request):
    p, account, token = twitch_clip(db, request, clip_id)
    url = p.download(token, account, clip_id)
    cfg = get_settings()
    storage = LocalMedia(cfg.media_root)
    key = uuid4()
    with storage.stage(key) as temp:
        with http.open_response(url) as incoming, temp.open("xb") as out:
            deadline = time.monotonic() + 120
            while chunk := incoming.read(256 * 1024):
                if (
                    out.tell() + len(chunk) > cfg.max_upload_bytes
                    or time.monotonic() > deadline
                ):
                    raise StudioError(413, "upload_limit")
                out.write(chunk)
        video = Video(
            id=key, name=f"Twitch clip {clip_id[:100]}.mp4", **validate_source(temp)
        )
        return store_recording(db, storage, temp, video)


router.include_router(private)
