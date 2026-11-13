"""启动时连接历史库和 Checkpointer，退出时释放连接。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.memory import checkpointer_context, set_checkpointer
from app.repositories.conversations import backfill_conversations
from app.repositories.database import history_context


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with history_context(), checkpointer_context() as checkpointer:
        await backfill_conversations(checkpointer)
        set_checkpointer(checkpointer)
        try:
            yield
        finally:
            set_checkpointer(None)
