"""Health endpoints. ``/health`` = process is up. ``/ready`` = database and Redis answer."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.health import check_readiness

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    report = await check_readiness(request.app.state.engine, request.app.state.redis)
    body = {
        "status": "ok" if report.ok else "unavailable",
        "checks": {
            "database": "ok" if report.database else "fail",
            "redis": "ok" if report.redis else "fail",
        },
    }
    return JSONResponse(body, status_code=200 if report.ok else 503)
