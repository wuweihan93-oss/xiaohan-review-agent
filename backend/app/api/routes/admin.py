"""管理员登录与只读问答表格接口；鉴权策略在 security/admin.py。"""

import asyncio
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.core.config import ADMIN_SESSION_SECONDS
from app.repositories import admin as admin_store
from app.schemas.admin import LoginRequest
from app.security import admin as auth
from app.security.admin import require_admin
from app.security.passwords import verify_password

router = APIRouter(prefix="/api/admin", tags=["管理员后台"])


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    auth.ensure_configured()
    auth.require_same_origin(request)
    # 自定义请求头让跨站表单无法直接触发登录；CORS 不允许第三方站点读取后台。
    if request.headers.get("x-admin-request") != "1":
        raise HTTPException(403, "请从后台登录页面提交。")
    auth.limit_login(request)
    valid = await asyncio.to_thread(
        verify_password, body.password, auth.ADMIN_PASSWORD_HASH
    )
    if not valid or not hmac.compare_digest(
        body.username.encode(), auth.ADMIN_USERNAME.encode()
    ):
        raise HTTPException(401, "管理员账号或密码不正确。")
    token, csrf_token = secrets.token_hex(32), secrets.token_hex(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ADMIN_SESSION_SECONDS)
    try:
        await admin_store.create_session(
            auth.token_hash(token), csrf_token, auth.credential_version(), expires_at
        )
    except Exception as exc:
        raise HTTPException(503, "保存管理员登录状态失败，请检查数据库连接。") from exc
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        max_age=ADMIN_SESSION_SECONDS,
        path="/api/admin",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return {
        "username": auth.ADMIN_USERNAME,
        "csrf_token": csrf_token,
        "expires_at": expires_at,
    }


@router.get("/session")
async def session_info(session: dict = Depends(require_admin)) -> dict:
    return {"username": auth.ADMIN_USERNAME, **session}


@router.post("/logout")
async def logout(
    request: Request, response: Response, session: dict = Depends(require_admin)
) -> dict:
    auth.require_same_origin(request)
    if not hmac.compare_digest(
        request.headers.get("x-csrf-token", "").encode(), session["csrf_token"].encode()
    ):
        raise HTTPException(403, "后台操作校验失败，请刷新页面后重试。")
    try:
        await admin_store.remove_session(
            auth.token_hash(request.cookies[auth.COOKIE_NAME])
        )
    except Exception as exc:
        raise HTTPException(503, "退出登录失败，请稍后重试。") from exc
    response.delete_cookie(
        auth.COOKIE_NAME,
        path="/api/admin",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return {"ok": True}


@router.get("/overview", dependencies=[Depends(require_admin)])
async def overview() -> dict:
    try:
        return await admin_store.overview()
    except Exception as exc:
        raise HTTPException(503, "读取后台概览失败，请稍后重试。") from exc


@router.get("/conversations", dependencies=[Depends(require_admin)])
async def conversations(
    q: str = Query(default="", max_length=200),
    visitor_id: str | None = Query(default=None, max_length=64),
    legacy: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    try:
        return await admin_store.conversation_list(
            q.strip(), visitor_id, legacy, limit, offset
        )
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
    q: str = Query(default="", max_length=200),
    visitor_id: str | None = Query(default=None, max_length=64),
    legacy: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    try:
        return await admin_store.turn_list(q.strip(), visitor_id, legacy, limit, offset)
    except Exception as exc:
        raise HTTPException(503, "读取问答表格失败，请稍后重试。") from exc
