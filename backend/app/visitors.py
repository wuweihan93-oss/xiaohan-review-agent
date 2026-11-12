"""用匿名 Cookie 识别浏览器；数据库只保存令牌摘要，不保存原始凭据。"""

import hashlib
import re
import secrets

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import Response

COOKIE_NAME = "xiaohan_visitor"
COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def memory_thread_id(visitor_id: str, conversation_id: str) -> str:
    """公开会话编号相同也不能共享模型记忆，且不接触旧版公共 checkpoint。"""
    digest = hashlib.sha256(f"{visitor_id}:{conversation_id}".encode()).hexdigest()
    return f"visitor:{digest}"


class VisitorMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        protected = path == "/api/visitor" or path.startswith(("/api/conversations", "/api/review"))
        if scope["type"] != "http" or not protected:
            return await self.app(scope, receive, send)

        request = Request(scope)
        token = request.cookies.get(COOKIE_NAME, "")
        new_cookie = re.fullmatch(r"[0-9a-f]{64}", token) is None
        if new_cookie:
            token = secrets.token_hex(32)
        scope.setdefault("state", {})["visitor_id"] = hashlib.sha256(token.encode()).hexdigest()

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                if new_cookie:
                    cookie = Response()
                    cookie.set_cookie(
                        COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, path="/api",
                        httponly=True, samesite="lax", secure=request.url.scheme == "https",
                    )
                    headers.append("set-cookie", cookie.headers["set-cookie"])
            await send(message)

        await self.app(scope, receive, send_with_cookie)
