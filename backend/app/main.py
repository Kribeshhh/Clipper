import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.authentication import AuthenticationMiddleware

from app.api import analysis, imports, live, media, providers, recording_links, session
from app.api.health import router
from app.core.dependencies import Dependencies
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.services.providers.oauth import twitch_validation_loop


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.upload_slots = asyncio.Semaphore(2)
        configure_logging(settings.log_level)
        app.state.dependencies = Dependencies(settings)
        validation = (
            asyncio.create_task(twitch_validation_loop(app.state.dependencies))
            if settings.twitch_client_id
            else None
        )
        try:
            yield
        finally:
            if validation:
                validation.cancel()
                with suppress(asyncio.CancelledError):
                    await validation
            app.state.dependencies.close()

    app = FastAPI(
        title="Gaming Clipper",
        lifespan=lifespan,
        openapi_url="/openapi.json" if settings.app_env == "development" else None,
    )
    app.add_middleware(AuthenticationMiddleware, backend=session.StudioAuthentication())
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET"],
            allow_headers=["Accept"],
        )

    @app.middleware("http")
    async def bound_control_requests(request: Request, call_next):
        # Browser JSON requests have a known length. Only video uploads may stream.
        if request.method == "POST" and request.url.path.rstrip("/") != "/api/videos":
            length = request.headers.get("content-length")
            if not length or not length.isascii() or not length.isdigit():
                return JSONResponse(
                    {"error": {"code": "length_required"}}, status_code=411
                )
            if len(length) > 5 or int(length) > 8192:
                return JSONResponse(
                    {"error": {"code": "request_too_large"}}, status_code=413
                )
        return await call_next(request)

    install_error_handlers(app)
    app.include_router(router)
    app.include_router(session.router)
    app.include_router(providers.router)
    app.include_router(live.router)
    app.include_router(recording_links.router)
    app.include_router(imports.router)
    app.include_router(media.router)
    app.include_router(analysis.router)
    return app
