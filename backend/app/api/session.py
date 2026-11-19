import hashlib
import hmac
import secrets

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, SecretStr
from starlette.authentication import AuthCredentials, AuthenticationBackend, SimpleUser
from starlette.concurrency import run_in_threadpool

from app.core.errors import StudioError
from app.core.settings import get_settings

router = APIRouter(prefix="/api/session")
COOKIE = "clipper_session"
TTL = 8 * 3600


def mutation_guard(request: Request):
    if request.method in {"GET", "HEAD"}:
        return
    origin = request.headers.get("origin")
    if request.headers.get("x-studio") != "1" or (
        origin is not None and origin != get_settings().studio_origin
    ):
        raise StudioError(403, "origin_denied")


def session_key(token: str) -> str:
    return "studio:session:" + hashlib.sha256(token.encode()).hexdigest()


def operator_binding():
    key = get_settings().operator_key
    if key:
        return hmac.digest(
            key.get_secret_value().encode(), b"studio:operator:v1", "sha256"
        )


class StudioAuthentication(AuthenticationBackend):
    async def authenticate(self, conn):
        token = conn.cookies.get(COOKIE, "")
        binding = operator_binding()
        if binding and len(token) == 43:
            saved = await run_in_threadpool(
                conn.app.state.dependencies.redis.get, session_key(token)
            )
            if saved and secrets.compare_digest(saved, binding):
                return AuthCredentials(["operator"]), SimpleUser("operator")
        return None


def operator(request: Request, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    mutation_guard(request)
    if "operator" not in request.auth.scopes:
        raise StudioError(401, "sign_in_required")


class Login(BaseModel):
    key: SecretStr = Field(max_length=256)


@router.post("", dependencies=[Depends(mutation_guard)])
def login(body: Login, request: Request, response: Response):
    key = get_settings().operator_key
    if not key:
        raise StudioError(503, "operator_key_missing")
    redis = request.app.state.dependencies.redis
    budget = "studio:login-attempts"
    # One bounded bucket for the private studio, independent of forwarded IP headers.
    if (
        redis.eval(
            "local n=redis.call('incr',KEYS[1]); "
            "if n==1 then redis.call('expire',KEYS[1],60) end; return n",
            1,
            budget,
        )
        > 20
    ):
        raise StudioError(429, "login_rate_limited")
    if not secrets.compare_digest(
        body.key.get_secret_value().encode(), key.get_secret_value().encode()
    ):
        raise StudioError(401, "invalid_key")
    token = secrets.token_urlsafe(32)
    with redis.pipeline() as tx:
        old = request.cookies.get(COOKIE, "")
        if len(old) == 43:
            tx.delete(session_key(old))
        tx.delete(budget)
        tx.set(session_key(token), operator_binding(), ex=TTL)
        tx.execute()
    response.set_cookie(
        COOKIE,
        token,
        max_age=TTL,
        httponly=True,
        secure=get_settings().app_env == "production",
        samesite="strict",
        path="/api",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True}


@router.delete("", dependencies=[Depends(operator)])
def logout(request: Request, response: Response):
    request.app.state.dependencies.redis.delete(session_key(request.cookies[COOKIE]))
    response.delete_cookie(COOKIE, path="/api")
    return {"authenticated": False}
