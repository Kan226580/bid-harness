import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from backend.app.api.errors import error_response, unexpected_error_handler
from backend.app.core.settings import settings

logger = logging.getLogger("uvicorn.error")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class RequestIdMiddleware(BaseHTTPMiddleware):
    """给每个请求一个 request_id ，成功和错误响应都用它，方便查日志"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = str(uuid.uuid4())
        started = time.perf_counter()
        logger.info(
            "request_started request_id=%s method=%s path=%s",
            request.state.request_id,
            request.method,
            request.url.path,
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            # 未知异常可能穿过用户中间件，到外层 ServerErrorMiddleware。
            # 在这里兜底，保证普通 JSON 接口的 500 也有本次编号和响应头。
            response = await unexpected_error_handler(request, exc)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            "request_finished request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
            request.state.request_id,
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """浏览器发写请求会自动带 Origin ,命令行/测试工具不带则放行"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and origin != settings.security.public_origin:
                return error_response(request, 403, "CSRF_INVALID", "请求来源不被信任")
        return await call_next(request)
