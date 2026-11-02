"""API error type. Error body format is fixed by plan section 17.B.5."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.status_code = status_code
        self.code = code
        self.message = message or code
        self.headers = headers


def error_response(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status_code, exc.code, exc.message, exc.headers)


def _validation_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # FastAPI's default answer echoes the input back. We only say where and why.
    problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]
    return error_response(400, "INVALID_REQUEST", "; ".join(problems))


def http_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
    return error_response(exc.status_code, code, str(exc.detail), dict(exc.headers or {}))


def _unhandled_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return error_response(500, "INTERNAL_ERROR", "unexpected error")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
