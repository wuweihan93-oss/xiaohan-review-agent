"""会话业务：检查归属、查询历史、在会话锁内删除记忆与记录。"""

import asyncio
from typing import Any

from fastapi import HTTPException

from app.agents.memory import delete_memory
from app.core.config import DB_URL
from app.repositories.conversations import (
    ConversationNotFound,
    check_conversation_access,
    delete_conversation,
    get_conversation,
    list_conversations,
)
from app.security.visitors import memory_thread_id
from app.services.locks import conversation_locks


async def check_access(
    conversation_id: str, visitor_id: str, *, allow_missing: bool = False
) -> None:
    if not DB_URL:
        return
    try:
        await check_conversation_access(
            conversation_id, visitor_id, allow_missing=allow_missing
        )
    except ConversationNotFound:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="读取复习对话失败，请稍后重试。"
        ) from exc


async def conversation_list(
    visitor_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    try:
        return await list_conversations(visitor_id, limit, offset)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="读取历史复习失败，请稍后重试。"
        ) from exc


async def conversation_detail(conversation_id: str, visitor_id: str) -> dict[str, Any]:
    try:
        conversation = (
            await get_conversation(conversation_id, visitor_id) if DB_URL else None
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="读取复习对话失败，请稍后重试。"
        ) from exc
    if conversation is None:
        raise HTTPException(status_code=404, detail="找不到这段复习对话")
    return {"conversation": conversation}


async def remove_conversation(conversation_id: str, visitor_id: str) -> dict[str, bool]:
    await check_access(conversation_id, visitor_id)
    lock = conversation_locks.setdefault(
        memory_thread_id(visitor_id, conversation_id), asyncio.Lock()
    )
    if lock.locked():
        raise HTTPException(
            status_code=409, detail="这段对话正在生成回答，请停止生成后再删除。"
        )
    async with lock:
        try:
            deleted = (
                await delete_conversation(conversation_id, visitor_id, delete_memory)
                if DB_URL
                else False
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="删除会话失败，请检查数据库连接后重试。"
            ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="找不到这段复习对话")
    return {"ok": True}
