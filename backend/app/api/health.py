from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

router = APIRouter(prefix="/api/health")


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, bool]


@router.get("/live")
def live() -> Liveness:
    return Liveness()


@router.get("/ready", responses={503: {"model": Readiness}})
def ready(request: Request, response: Response) -> Readiness:
    checks = request.app.state.dependencies.check()
    healthy = all(checks.values())
    response.status_code = 200 if healthy else 503
    response.headers["Cache-Control"] = "no-store"
    return Readiness(status="ready" if healthy else "degraded", checks=checks)
