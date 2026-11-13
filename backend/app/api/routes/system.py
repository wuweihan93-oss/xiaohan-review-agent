"""服务状态及前端匿名缓存标识；不返回密钥。"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies import current_visitor
from app.core.config import (
    CHAT_API_KEY,
    CHAT_MODEL,
    DB_URL,
    EMBEDDING_MODEL,
    TAVILY_API_KEY,
    TUTORIAL_DIR,
)
from app.services.materials import material_counts

router = APIRouter(prefix="/api", tags=["系统状态"])


@router.get("/visitor")
async def visitor_identity(
    visitor_id: str = Depends(current_visitor),
) -> dict[str, str]:
    # 摘要仅用于区分前端缓存；不能用它代替 Cookie 访问其他人的会话。
    return {"cache_scope": visitor_id}


@router.get("/health")
async def health() -> dict[str, Any]:
    counts = await asyncio.to_thread(material_counts)
    return {
        "ok": True,
        "model_configured": bool(CHAT_API_KEY),
        "model": CHAT_MODEL,
        "embedding": EMBEDDING_MODEL,
        "memory_backend": "PostgreSQL" if DB_URL else "InMemorySaver",
        "history_enabled": bool(DB_URL),
        "web_search_configured": bool(TAVILY_API_KEY),
        "tutorial_dir": str(TUTORIAL_DIR),
        "counts": counts,
    }
