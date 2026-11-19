import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class StudioError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code


def install_error_handlers(app: FastAPI) -> None:
    def error(code: str, status: int, headers=None) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": code}}, status_code=status, headers=headers
        )

    @app.exception_handler(StudioError)
    async def studio_error(request: Request, exc: StudioError):
        return error(exc.code, exc.status, {"Cache-Control": "no-store"})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error(
            "not_found" if exc.status_code == 404 else "http_error",
            exc.status_code,
            exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return error("invalid_request", 422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        request_id = uuid4().hex
        logging.getLogger(__name__).exception(
            "Unhandled request failure", extra={"request_id": request_id}
        )
        return error("internal_error", 500, {"X-Request-ID": request_id})
