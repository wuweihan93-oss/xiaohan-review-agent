"""LangGraph Checkpointer 保存模型上下文；页面历史由 repositories 单独保存。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import DB_URL

_memory: Any | None = None


@asynccontextmanager
async def checkpointer_context() -> AsyncIterator[Any]:
    if DB_URL:
        async with AsyncPostgresSaver.from_conn_string(DB_URL) as checkpointer:
            await checkpointer.setup()
            yield checkpointer
    else:
        yield InMemorySaver()


def set_checkpointer(checkpointer: Any | None) -> None:
    global _memory
    _memory = checkpointer


async def delete_memory(conversation_id: str) -> None:
    if _memory is None:
        raise RuntimeError("对话记忆服务尚未初始化")
    await _memory.adelete_thread(conversation_id)


def get_checkpointer() -> Any:
    """必须在应用启动完成后调用，避免悄悄创建另一份记忆。"""
    if _memory is None:
        raise RuntimeError("对话记忆服务尚未初始化")
    return _memory
