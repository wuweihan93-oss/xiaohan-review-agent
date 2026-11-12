"""独立管理员登录与只读会话后台，不改变访客之间的访问权限。"""

import asyncio
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders

from app import admin_store
from app.admin_password import verify_password
from app.config import ADMIN_PASSWORD_HASH, ADMIN_USERNAME, DB_URL

router = APIRouter(prefix="/api/admin", tags=["管理员后台"])
COOKIE_NAME = "xiaohan_admin"
SESSION_SECONDS = 8 * 60 * 60
_attempts = defaultdict(deque)
_global_attempts = deque()


class AdminHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/admin"):
            return await self.app(scope, receive, send)

        async def no_store(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        await self.app(scope, receive, no_store)


def _configured():
    if not ADMIN_PASSWORD_HASH:
        raise HTTPException(503, "后台尚未设置密码，请在项目目录运行 python3 scripts/setup_admin.py 后重启后端。")
    if not DB_URL:
        raise HTTPException(503, "后台需要 PostgreSQL，请配置 DB_URL 后重启后端。")


def _version() -> str:
    return hashlib.sha256(f"{ADMIN_USERNAME}\0{ADMIN_PASSWORD_HASH}".encode()).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _same_origin(request: Request):
    # 不相信外部任意传入的转发协议；由服务器配置可信反向代理。
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != expected):
        raise HTTPException(403, "后台请求来源不正确，请从本站后台页面操作。")


def _limit_login(request: Request):
    now = time.monotonic()
    for key in list(_attempts):
        while _attempts[key] and _attempts[key][0] <= now - 60:
            _attempts[key].popleft()
        if not _attempts[key]:
            del _attempts[key]
    while _global_attempts and _global_attempts[0] <= now - 60:
        _global_attempts.popleft()
    address = getattr(request.state, "source_ip", None) or (request.client.host if request.client else "unknown")
    attempts = _attempts[address]
    if len(attempts) >= 5 or len(_global_attempts) >= 30:
        raise HTTPException(429, "登录尝试过于频繁，请一分钟后重试。", headers={"Retry-After": "60"})
    attempts.append(now)
    _global_attempts.append(now)


async def require_admin(request: Request) -> dict:
    _configured()
    token = request.cookies.get(COOKIE_NAME, "")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise HTTPException(401, "请先登录管理员后台。")
    try:
        session = await admin_store.read_session(_token_hash(token), _version())
    except Exception as exc:
        raise HTTPException(503, "后台暂时无法连接数据库，请稍后重试。") from exc
    if session is None:
        raise HTTPException(401, "管理员登录已过期，请重新登录。")
    return session


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    _configured()
    _same_origin(request)
    # 自定义请求头让跨站表单无法直接触发登录；CORS 不允许第三方站点读取后台。
    if request.headers.get("x-admin-request") != "1":
        raise HTTPException(403, "请从后台登录页面提交。")
    _limit_login(request)
    valid = await asyncio.to_thread(verify_password, body.password, ADMIN_PASSWORD_HASH)
    if not valid or not hmac.compare_digest(body.username.encode(), ADMIN_USERNAME.encode()):
        raise HTTPException(401, "管理员账号或密码不正确。")
    token, csrf_token = secrets.token_hex(32), secrets.token_hex(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)
    try:
        await admin_store.create_session(_token_hash(token), csrf_token, _version(), expires_at)
    except Exception as exc:
        raise HTTPException(503, "保存管理员登录状态失败，请检查数据库连接。") from exc
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, path="/api/admin", httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return {"username": ADMIN_USERNAME, "csrf_token": csrf_token, "expires_at": expires_at}


@router.get("/session")
async def session_info(session: dict = Depends(require_admin)) -> dict:
    return {"username": ADMIN_USERNAME, **session}


@router.post("/logout")
async def logout(request: Request, response: Response, session: dict = Depends(require_admin)) -> dict:
    _same_origin(request)
    if not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), session["csrf_token"].encode()):
        raise HTTPException(403, "后台操作校验失败，请刷新页面后重试。")
    try:
        await admin_store.remove_session(_token_hash(request.cookies[COOKIE_NAME]))
    except Exception as exc:
        raise HTTPException(503, "退出登录失败，请稍后重试。") from exc
    response.delete_cookie(COOKIE_NAME, path="/api/admin", httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return {"ok": True}


@router.get("/overview", dependencies=[Depends(require_admin)])
async def overview() -> dict:
    try:
        return await admin_store.overview()
    except Exception as exc:
        raise HTTPException(503, "读取后台概览失败，请稍后重试。") from exc


@router.get("/conversations", dependencies=[Depends(require_admin)])
async def conversations(
    q: str = Query(default="", max_length=200), visitor_id: str | None = Query(default=None, max_length=64),
    legacy: bool = False, limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0),
) -> dict:
    try:
        return await admin_store.conversation_list(q.strip(), visitor_id, legacy, limit, offset)
    except Exception as exc:
        raise HTTPException(503, "读取后台会话列表失败，请稍后重试。") from exc


@router.get("/conversations/{conversation_id}", dependencies=[Depends(require_admin)])
async def conversation(conversation_id: str) -> dict:
    try:
        result = await admin_store.conversation_detail(conversation_id)
    except Exception as exc:
        raise HTTPException(503, "读取后台会话详情失败，请稍后重试。") from exc
    if result is None:
        raise HTTPException(404, "这段会话已被删除或不存在。")
    return {"conversation": result}


@router.get("/turns", dependencies=[Depends(require_admin)])
async def turns(
    q: str = Query(default="", max_length=200), visitor_id: str | None = Query(default=None, max_length=64),
    legacy: bool = False, limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0),
) -> dict:
    try:
        return await admin_store.turn_list(q.strip(), visitor_id, legacy, limit, offset)
    except Exception as exc:
        raise HTTPException(503, "读取问答表格失败，请稍后重试。") from exc
