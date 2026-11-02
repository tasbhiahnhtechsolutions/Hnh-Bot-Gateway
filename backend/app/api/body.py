"""Read a request body with a hard size limit (plan section 6: 413 when too big)."""

from fastapi import Request

from app.core.errors import ApiError


def _too_large(limit: int) -> ApiError:
    return ApiError(
        413, "REQUEST_TOO_LARGE", f"request body is larger than {limit // (1024 * 1024)} MB"
    )


async def read_body_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _too_large(limit)  # chunked uploads have no Content-Length to check
        chunks.append(chunk)
    return b"".join(chunks)
