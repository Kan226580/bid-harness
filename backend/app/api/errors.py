import logging
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from backend.app.core.errors import ApiError

logger = logging.getLogger("uvicorn.error")


def request_id_of(request: Request) -> str:
    """获取当前请求 id ，没有则临时生成一个"""
    value = getattr(request.state, "request_id", None)
    if not isinstance(value, str):
        value = str(uuid.uuid4())
        request.state.request_id = value
    return value


def ok(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    """包装成功响应"""
    return {"data": data, "request_id": request_id_of(request)}


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """包装错误响应"""
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = request_id_of(request)
    return JSONResponse(
        status_code=status_code,
        headers=response_headers,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "retryable": retryable,
            },
            "request_id": request_id_of(request),
        },
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_error request_id=%s method=%s path=%s",
        request_id_of(request),
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return error_response(request, 500, "INTERNAL_ERROR", "服务器内部错误")


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    全局异常处理器，
    作用是把业务异常 ApiError 和未知异常统一转换成规范的 JSON 错误响应，
    避免把堆栈泄露给用户
    """
    if not isinstance(exc, ApiError):
        # 兜底不给用户看堆栈
        return await unexpected_error_handler(request, exc)
    return error_response(
        request,
        exc.status_code,
        exc.code,
        exc.message,
        details=exc.details,
        retryable=exc.retryable,
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    请求参数校验异常处理器，
    专门处理 RequestValidationError，
    把 FastAPI/Pydantic 默认的 422 校验错误，
    转换成项目统一的错误响应格式
    """
    fields: list[dict[str, str]] = []
    if isinstance(exc, RequestValidationError):
        fields = [
            {
                "field": ".".join(str(part) for part in err["loc"] if part != "body"),
                "reason": err["msg"],
            }
            for err in exc.errors()
        ]
    return error_response(
        request, 422, "VALIDATION_ERROR", "请求字段不合法", details={"fields": fields}
    )


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, HTTPException):
        return await unexpected_error_handler(request, exc)
    codes = {404: "RESOURCE_NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
    messages = {404: "资源不存在", 405: "请求方法不支持"}
    return error_response(
        request,
        exc.status_code,
        codes.get(exc.status_code, "HTTP_ERROR"),
        messages.get(exc.status_code, "请求无法完成"),
        headers=exc.headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    """用于注册异常处理器"""
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
