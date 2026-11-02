"""Write one request_logs row. A failing log write must never break the request."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.request_log import RequestKind, RequestLog, RequestStatus
from app.repositories import request_logs

log = get_logger(__name__)


async def record_request(
    db: AsyncSession,
    *,
    vendor_id: uuid.UUID,
    request_id: str,
    kind: RequestKind,
    mode: str | None,
    latency_ms: int,
    error_code: str | None,
) -> None:
    try:
        await request_logs.add(
            db,
            RequestLog(
                vendor_id=vendor_id,
                request_id=request_id[:64],
                kind=kind.value,
                status=(RequestStatus.ERROR if error_code else RequestStatus.OK).value,
                mode=mode,
                latency_ms=latency_ms,
                error_code=error_code[:40] if error_code else None,
            ),
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        log.warning("request_log_failed", error_type=type(exc).__name__)
