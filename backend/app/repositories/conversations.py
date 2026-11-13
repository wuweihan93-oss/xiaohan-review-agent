"""保存供页面查看的复习记录；LangGraph checkpoint 继续负责模型上下文。"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from app.core.config import DB_URL
from app.repositories.database import get_pool
from app.security.visitors import memory_thread_id


class ConversationNotFound(Exception):
    """编号不存在或不属于当前访客，对外统一显示找不到会话。"""


async def list_conversations(
    visitor_id: str, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    if not DB_URL:
        return {"conversations": [], "total": 0, "enabled": False}
    async with get_pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT count(*) AS total FROM xiaohan_conversations WHERE visitor_id = %s",
            (visitor_id,),
        )
        total = (await cursor.fetchone())["total"]
        cursor = await connection.execute(
            """
            SELECT c.id, c.title, c.material_ids, c.study_mode, c.prompt, c.web_search_enabled, c.created_at, c.updated_at,
                (SELECT count(*) FROM xiaohan_messages m WHERE m.conversation_id = c.id) AS message_count,
                (SELECT left(content, 80) FROM xiaohan_messages m
                 WHERE m.conversation_id = c.id ORDER BY position DESC LIMIT 1) AS preview
            FROM xiaohan_conversations c WHERE c.visitor_id = %s
            ORDER BY c.updated_at DESC, c.id
            LIMIT %s OFFSET %s
        """,
            (visitor_id, limit, offset),
        )
        return {
            "conversations": await cursor.fetchall(),
            "total": total,
            "enabled": True,
        }


async def get_conversation(
    conversation_id: str, visitor_id: str
) -> dict[str, Any] | None:
    async with get_pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT * FROM xiaohan_conversations WHERE id = %s AND visitor_id = %s",
            (conversation_id, visitor_id),
        )
        conversation = await cursor.fetchone()
        if conversation is None:
            return None
        # 消息查询也校验归属，防止两次查询之间旧会话被删除、编号被他人重新使用。
        cursor = await connection.execute(
            """
            SELECT m.id, m.role, m.content, m.sources, m.trace, m.failed, m.created_at
            FROM xiaohan_messages m JOIN xiaohan_conversations c ON c.id = m.conversation_id
            WHERE m.conversation_id = %s AND c.visitor_id = %s ORDER BY m.position
        """,
            (conversation_id, visitor_id),
        )
        conversation.pop("visitor_id", None)
        conversation["messages"] = await cursor.fetchall()
        return conversation


async def check_conversation_access(
    conversation_id: str, visitor_id: str, *, allow_missing: bool = False
) -> None:
    """在开始生成或返回流式响应前检查归属；写入时还会再次原子校验。"""
    async with get_pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT visitor_id FROM xiaohan_conversations WHERE id = %s",
            (conversation_id,),
        )
        row = await cursor.fetchone()
    if row is None and allow_missing:
        return
    if row is None or row["visitor_id"] != visitor_id:
        raise ConversationNotFound()


async def delete_conversation(
    conversation_id: str,
    visitor_id: str,
    delete_memory: Callable[[str], Awaitable[None]],
) -> bool:
    """只删除本项目的会话；先清理记忆，防止启动时从 checkpoint 恢复已删记录。"""
    async with get_pool().connection() as connection:
        async with connection.transaction():
            cursor = await connection.execute(
                "SELECT id FROM xiaohan_conversations WHERE id = %s AND visitor_id = %s FOR UPDATE",
                (conversation_id, visitor_id),
            )
            if await cursor.fetchone() is None:
                return False
            await delete_memory(memory_thread_id(visitor_id, conversation_id))
            # 外键级联删除该会话的所有消息，不影响上传资料或其他会话。
            await connection.execute(
                "DELETE FROM xiaohan_conversations WHERE id = %s", (conversation_id,)
            )
    return True


async def begin_turn(
    conversation_id: str,
    question: str,
    material_ids: list[str],
    study_mode: str,
    prompt: str | None,
    *,
    visitor_id: str,
    web_search_enabled: bool = False,
    client_ip: str | None = None,
) -> dict[str, Any]:
    message = {"id": str(uuid.uuid4()), "role": "user", "content": question}
    async with get_pool().connection() as connection:
        async with connection.transaction():
            cursor = await connection.execute(
                """
                INSERT INTO xiaohan_conversations (id, title, material_ids, study_mode, prompt, web_search_enabled, visitor_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET material_ids = EXCLUDED.material_ids,
                    study_mode = EXCLUDED.study_mode, prompt = EXCLUDED.prompt,
                    web_search_enabled = EXCLUDED.web_search_enabled, updated_at = now()
                WHERE xiaohan_conversations.visitor_id = EXCLUDED.visitor_id
                RETURNING id
            """,
                (
                    conversation_id,
                    " ".join(question.split())[:40],
                    Jsonb(material_ids),
                    study_mode,
                    prompt,
                    web_search_enabled,
                    visitor_id,
                ),
            )
            if not await cursor.fetchone():
                raise ConversationNotFound()
            await connection.execute(
                """
                INSERT INTO xiaohan_messages (id, conversation_id, role, content, client_ip)
                VALUES (%s, %s, 'user', %s, %s)
            """,
                (message["id"], conversation_id, question, client_ip),
            )
    return message


async def finish_turn(
    conversation_id: str,
    content: str,
    sources: list,
    trace: list,
    *,
    visitor_id: str,
    failed: bool = False,
) -> dict[str, Any]:
    message = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "content": content,
        "sources": sources,
        "trace": trace,
        "failed": failed,
    }
    async with get_pool().connection() as connection:
        async with connection.transaction():
            cursor = await connection.execute(
                "SELECT id FROM xiaohan_conversations WHERE id = %s AND visitor_id = %s FOR UPDATE",
                (conversation_id, visitor_id),
            )
            if not await cursor.fetchone():
                raise ConversationNotFound()
            await connection.execute(
                """
                INSERT INTO xiaohan_messages (id, conversation_id, role, content, sources, trace, failed)
                VALUES (%s, %s, 'assistant', %s, %s, %s, %s)
            """,
                (
                    message["id"],
                    conversation_id,
                    content,
                    Jsonb(sources),
                    Jsonb(trace),
                    failed,
                ),
            )
            await connection.execute(
                "UPDATE xiaohan_conversations SET updated_at = now() WHERE id = %s",
                (conversation_id,),
            )
    return message


async def backfill_conversations(checkpointer: Any) -> int:
    """只恢复能从专用工具调用确认属于小晗的旧 checkpoint，不导入教程中的其他 Agent。"""
    if not DB_URL:
        return 0
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from app.agents.messages import message_text, tool_sources

    async with get_pool().connection() as connection:
        cursor = await connection.execute("""
            SELECT DISTINCT thread_id FROM checkpoints
            WHERE checkpoint_ns = '' AND thread_id NOT LIKE 'visitor:%' AND thread_id NOT IN (SELECT id FROM xiaohan_conversations)
        """)
        candidates = await cursor.fetchall()
    imported = 0
    for candidate in candidates:
        thread_id = candidate["thread_id"]
        saved = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": thread_id}}
        )
        if not saved:
            continue
        messages = saved.checkpoint.get("channel_values", {}).get("messages", [])
        if not any(
            isinstance(m, ToolMessage) and m.name == "search_study_materials"
            for m in messages
        ):
            continue
        question = next(
            (message_text(m) for m in messages if isinstance(m, HumanMessage)), ""
        )
        if not question:
            continue
        visible = []
        material_ids = set()
        sources = []
        for message in messages:
            if isinstance(message, HumanMessage):
                sources = []
                visible.append(("user", message_text(message), []))
            elif isinstance(message, ToolMessage):
                found = tool_sources(message)
                sources.extend(found)
                material_ids.update(
                    item["material_id"] for item in found if item.get("material_id")
                )
            elif isinstance(message, AIMessage) and not message.tool_calls:
                visible.append(("assistant", message_text(message), sources.copy()))
        timestamp = saved.checkpoint.get("ts") or datetime.now(timezone.utc).isoformat()
        async with get_pool().connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    INSERT INTO xiaohan_conversations (id, title, material_ids, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id
                """,
                    (
                        thread_id,
                        " ".join(question.split())[:40],
                        Jsonb(sorted(material_ids)),
                        timestamp,
                        timestamp,
                    ),
                )
                if not await cursor.fetchone():
                    continue
                for role, content, citations in visible:
                    await connection.execute(
                        """
                        INSERT INTO xiaohan_messages (id, conversation_id, role, content, sources, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                        (
                            str(uuid.uuid4()),
                            thread_id,
                            role,
                            content,
                            Jsonb(citations),
                            timestamp,
                        ),
                    )
                imported += 1
    return imported
