"""管理员鉴权、同源校验与登录限流；访客聊天不依赖管理员身份。"""

import hashlib
import re
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import (
    ADMIN_LOGIN_ATTEMPTS_GLOBAL,
    ADMIN_LOGIN_ATTEMPTS_PER_IP,
    ADMIN_LOGIN_WINDOW_SECONDS,
    ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME,
    DB_URL,
)
from app.repositories import admin as admin_store

COOKIE_NAME = "xiaohan_admin"
_attempts = defaultdict(deque)
_global_attempts = deque()


def ensure_configured():
    if not ADMIN_PASSWORD_HASH:
        raise HTTPException(
            503,
            "后台尚未设置密码，请在项目目录运行 python3 scripts/setup_admin.py 后重启后端。",
        )
    if not DB_URL:
        raise HTTPException(503, "后台需要 PostgreSQL，请配置 DB_URL 后重启后端。")


def credential_version() -> str:
    return hashlib.sha256(
        f"{ADMIN_USERNAME}\0{ADMIN_PASSWORD_HASH}".encode()
    ).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_same_origin(request: Request):
    # 不相信外部任意传入的转发协议；由服务器配置可信反向代理。
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and origin != expected
    ):
        raise HTTPException(403, "后台请求来源不正确，请从本站后台页面操作。")


def limit_login(request: Request):
    now = time.monotonic()
    for key in list(_attempts):
        while _attempts[key] and _attempts[key][0] <= now - ADMIN_LOGIN_WINDOW_SECONDS:
            _attempts[key].popleft()
        if not _attempts[key]:
            del _attempts[key]
    while _global_attempts and _global_attempts[0] <= now - ADMIN_LOGIN_WINDOW_SECONDS:
        _global_attempts.popleft()
    address = getattr(request.state, "source_ip", None) or (
        request.client.host if request.client else "unknown"
    )
    attempts = _attempts[address]
    if (
        len(attempts) >= ADMIN_LOGIN_ATTEMPTS_PER_IP
        or len(_global_attempts) >= ADMIN_LOGIN_ATTEMPTS_GLOBAL
    ):
        raise HTTPException(
            429, "登录尝试过于频繁，请一分钟后重试。", headers={"Retry-After": "60"}
        )
    attempts.append(now)
    _global_attempts.append(now)


async def require_admin(request: Request) -> dict:
    ensure_configured()
    token = request.cookies.get(COOKIE_NAME, "")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise HTTPException(401, "请先登录管理员后台。")
    try:
        session = await admin_store.read_session(
            token_hash(token), credential_version()
        )
    except Exception as exc:
        raise HTTPException(503, "后台暂时无法连接数据库，请稍后重试。") from exc
    if session is None:
        raise HTTPException(401, "管理员登录已过期，请重新登录。")
    return session
