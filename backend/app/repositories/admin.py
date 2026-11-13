"""后台只读查询及管理员登录会话，与匿名访客接口分别鉴权。"""

from datetime import datetime

from app.repositories.database import get_pool


async def create_session(
    token_hash: str, csrf_token: str, credential_version: str, expires_at: datetime
) -> None:
    async with get_pool().connection() as connection:
        async with connection.transaction():
            # 登录时顺便清理已过期的管理员会话，不创建定时任务，也不删除访客记录。
            await connection.execute(
                "DELETE FROM xiaohan_admin_sessions WHERE expires_at <= now()"
            )
            await connection.execute(
                """
                INSERT INTO xiaohan_admin_sessions (token_hash, csrf_token, credential_version, expires_at)
                VALUES (%s, %s, %s, %s)
            """,
                (token_hash, csrf_token, credential_version, expires_at),
            )


async def read_session(token_hash: str, credential_version: str) -> dict | None:
    async with get_pool().connection() as connection:
        cursor = await connection.execute(
            """
            SELECT csrf_token, expires_at FROM xiaohan_admin_sessions
            WHERE token_hash = %s AND credential_version = %s AND expires_at > now()
        """,
            (token_hash, credential_version),
        )
        return await cursor.fetchone()


async def remove_session(token_hash: str) -> None:
    async with get_pool().connection() as connection:
        await connection.execute(
            "DELETE FROM xiaohan_admin_sessions WHERE token_hash = %s", (token_hash,)
        )


async def overview() -> dict:
    async with get_pool().connection() as connection:
        cursor = await connection.execute("""
            SELECT count(*) AS conversations, count(DISTINCT visitor_id) AS visitors,
                count(*) FILTER (WHERE visitor_id IS NULL) AS legacy_conversations,
                (SELECT count(*) FROM xiaohan_messages) AS messages
            FROM xiaohan_conversations
        """)
        return await cursor.fetchone()


async def conversation_list(
    query: str, visitor_id: str | None, legacy: bool, limit: int, offset: int
) -> dict:
    # 搜索标题、会话编号或消息原文，所有输入均通过参数传递。
    conditions, values = [], []
    if query:
        conditions.append(
            "(strpos(lower(c.title), lower(%s)) > 0 OR strpos(c.id, %s) > 0 OR EXISTS (SELECT 1 FROM xiaohan_messages m WHERE m.conversation_id = c.id AND strpos(lower(m.content), lower(%s)) > 0))"
        )
        values.extend([query, query, query])
    if visitor_id:
        conditions.append("c.visitor_id = %s")
        values.append(visitor_id)
    if legacy:
        conditions.append("c.visitor_id IS NULL")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    async with get_pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT count(*) AS total FROM xiaohan_conversations c" + where, values
        )
        total = (await cursor.fetchone())["total"]
        cursor = await connection.execute(
            """
            SELECT c.*,
                (SELECT count(*) FROM xiaohan_messages m WHERE m.conversation_id = c.id) AS message_count,
                (SELECT left(content, 100) FROM xiaohan_messages m WHERE m.conversation_id = c.id ORDER BY position DESC LIMIT 1) AS preview
            FROM xiaohan_conversations c
        """
            + where
            + " ORDER BY c.updated_at DESC, c.id LIMIT %s OFFSET %s",
            [*values, limit, offset],
        )
        return {"conversations": await cursor.fetchall(), "total": total}


async def conversation_detail(conversation_id: str) -> dict | None:
    async with get_pool().connection() as connection:
        async with connection.transaction():
            # 一次读取期间阻止删除，避免详情中混入被重新创建的同名会话。
            cursor = await connection.execute(
                "SELECT * FROM xiaohan_conversations WHERE id = %s FOR SHARE",
                (conversation_id,),
            )
            conversation = await cursor.fetchone()
            if conversation is None:
                return None
            cursor = await connection.execute(
                """
                SELECT id, role, content, sources, trace, failed, created_at, client_ip
                FROM xiaohan_messages WHERE conversation_id = %s ORDER BY position
            """,
                (conversation_id,),
            )
            conversation["messages"] = await cursor.fetchall()
            return conversation


async def turn_list(
    query: str, visitor_id: str | None, legacy: bool, limit: int, offset: int
) -> dict:
    """以访客问题为一行，只配对紧接其后的助手消息，未保存的回答显示为空。"""
    conditions, values = ["u.role = 'user'"], []
    if query:
        conditions.append(
            "(strpos(lower(u.content), lower(%s)) > 0 OR strpos(lower(coalesce(a.content, '')), lower(%s)) > 0 OR strpos(coalesce(c.visitor_id, ''), %s) > 0 OR strpos(coalesce(u.client_ip, ''), %s) > 0 OR strpos(lower(c.title), lower(%s)) > 0)"
        )
        values.extend([query] * 5)
    if visitor_id:
        conditions.append("c.visitor_id = %s")
        values.append(visitor_id)
    if legacy:
        conditions.append("c.visitor_id IS NULL")
    # 下一条若是新的用户问题，不能错把后面的回答配到本行。
    relation = """
        FROM xiaohan_messages u
        JOIN xiaohan_conversations c ON c.id = u.conversation_id
        LEFT JOIN LATERAL (
            SELECT m.id, m.role, m.content, m.failed, m.created_at
            FROM xiaohan_messages m
            WHERE m.conversation_id = u.conversation_id AND m.position > u.position
            ORDER BY m.position LIMIT 1
        ) a ON a.role = 'assistant'
        WHERE """ + " AND ".join(conditions)
    async with get_pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT count(*) AS total " + relation, values
        )
        total = (await cursor.fetchone())["total"]
        cursor = await connection.execute(
            """
            SELECT u.id AS question_id, c.id AS conversation_id, c.title AS conversation_title,
                c.visitor_id, u.created_at AS asked_at, u.content AS question, u.client_ip,
                a.content AS answer, a.created_at AS answered_at, coalesce(a.failed, FALSE) AS failed
        """
            + relation
            + " ORDER BY u.created_at DESC, u.position DESC LIMIT %s OFFSET %s",
            [*values, limit, offset],
        )
        return {"rows": await cursor.fetchall(), "total": total}
