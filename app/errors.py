"""业务异常与统一错误响应。"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, detail: dict | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


def not_found(what: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{what} 不存在")


def conflict(code: str, message: str, detail: dict | None = None) -> ApiError:
    return ApiError(409, code, message, detail)


def forbidden(code: str, message: str, detail: dict | None = None) -> ApiError:
    return ApiError(403, code, message, detail)


def bad_request(message: str, detail: dict | None = None) -> ApiError:
    return ApiError(400, "BAD_REQUEST", message, detail)


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    body = {"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}}
    return JSONResponse(status_code=exc.status_code, content=body)
