"""API error type. Error body format is fixed by plan section 17.B.5."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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


def _api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers=exc.headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
