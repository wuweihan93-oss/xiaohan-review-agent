"""历史记录的数据库集成测试：设置 XIAOHAN_POSTGRES_TESTS=1 后运行。"""

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx
from psycopg import AsyncConnection

from app import main
from app.config import DB_URL
from app.visitors import memory_thread_id


@unittest.skipUnless(DB_URL and os.getenv("XIAOHAN_POSTGRES_TESTS") == "1", "需要显式启用 PostgreSQL 集成测试")
class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        self.material_id = str(uuid.uuid4())
        self.lifespan = main.app.router.lifespan_context(main.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
        self.visitor = (await self.client.get("/api/visitor")).json()["cache_scope"]
        self.threads = [memory_thread_id(self.visitor, item) for item in self.ids]
        self.materials = patch("app.main.list_materials", return_value=[{"id": self.material_id}])
        self.materials.start()

    async def asyncTearDown(self):
        self.materials.stop()
        await self.client.aclose()
        async with await AsyncConnection.connect(DB_URL, autocommit=True, connect_timeout=10) as connection:
            for conversation_id in self.ids:
                await connection.execute("DELETE FROM xiaohan_conversations WHERE id = %s", (conversation_id,))
        await self.lifespan.__aexit__(None, None, None)

    async def test_delete_removes_messages_and_memory_without_resurrection(self):
        from langchain.agents import create_agent
        from langchain_core.messages import AIMessage, HumanMessage
        from app import agent
        from app.conversations import backfill_conversations
        from test_streaming import search_study_materials
        from test_tool_recovery import StrictToolModel, call

        graph = create_agent(
            model=StrictToolModel(responses=[
                AIMessage(content="", tool_calls=[call("search_study_materials", "lookup")]),
                AIMessage(content="已复习"),
            ]), tools=[search_study_materials], checkpointer=agent._memory,
        )
        with patch("app.main.review", new=AsyncMock(return_value=self.answer("资料"))):
            for index, conversation_id in enumerate(self.ids):
                await self.client.post("/api/review", json=self.request(index))
                await graph.ainvoke({"messages": [HumanMessage(content="检索资料")]}, {"configurable": {"thread_id": self.threads[index]}})
        config = {"configurable": {"thread_id": self.threads[0]}}
        self.assertIsNotNone(await agent._memory.aget_tuple(config))
        response = await self.client.delete(f"/api/conversations/{self.ids[0]}")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(await agent._memory.aget_tuple(config))
        self.assertEqual(await backfill_conversations(agent._memory), 0)
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            for table in ("xiaohan_messages", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                column = "conversation_id" if table == "xiaohan_messages" else "thread_id"
                cursor = await connection.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (self.ids[0] if table == "xiaohan_messages" else self.threads[0],))
                self.assertEqual((await cursor.fetchone())[0], 0)
        self.assertIsNotNone(await agent._memory.aget_tuple({"configurable": {"thread_id": self.threads[1]}}))
        remaining = await self.client.get(f"/api/conversations/{self.ids[1]}")
        self.assertEqual(len(remaining.json()["conversation"]["messages"]), 2)
        await self.lifespan.__aexit__(None, None, None)
        self.lifespan = main.app.router.lifespan_context(main.app)
        await self.lifespan.__aenter__()
        missing = await self.client.get(f"/api/conversations/{self.ids[0]}")
        self.assertEqual(missing.status_code, 404)
        repeated = await self.client.delete(f"/api/conversations/{self.ids[0]}")
        self.assertEqual(repeated.status_code, 404)
        await agent.delete_memory(self.threads[1])

    async def test_memory_cleanup_failure_preserves_history_for_retry(self):
        with patch("app.main.review", new=AsyncMock(return_value=self.answer("资料"))):
            await self.client.post("/api/review", json=self.request())
        with patch("app.main.delete_memory", new=AsyncMock(side_effect=RuntimeError("模拟记忆清理失败"))):
            response = await self.client.delete(f"/api/conversations/{self.ids[0]}")
        self.assertEqual(response.status_code, 503)
        restored = await self.client.get(f"/api/conversations/{self.ids[0]}")
        self.assertEqual(len(restored.json()["conversation"]["messages"]), 2)

    async def test_stream_round_persists_one_answer_and_partial_failure(self):
        async def success(**options):
            yield {"event": "token", "data": {"text": "逐步"}}
            yield {"event": "token", "data": {"text": "回答"}}
            yield {"event": "result", "data": {"answer": "逐步回答", "sources": [], "trace": []}}
        async def failed(**options):
            yield {"event": "token", "data": {"text": "部分内容"}}
            raise RuntimeError("模拟断流")
        with patch("app.main.review_stream", success):
            first = await self.client.post("/api/review/stream", json=self.request())
        with patch("app.main.review_stream", failed):
            second = await self.client.post("/api/review/stream", json=self.request(question="继续"))
        self.assertIn("event: done", first.text)
        self.assertIn("event: error", second.text)
        saved = (await self.client.get(f"/api/conversations/{self.ids[0]}")).json()["conversation"]
        self.assertEqual([message["role"] for message in saved["messages"]], ["user", "assistant", "user", "assistant"])
        self.assertEqual(saved["messages"][1]["content"], "逐步回答")
        self.assertFalse(saved["messages"][1]["failed"])
        self.assertIn("部分内容", saved["messages"][3]["content"])
        self.assertTrue(saved["messages"][3]["failed"])

    def request(self, index=0, **changes):
        return {
            "conversation_id": self.ids[index], "question": "测试：复习文档切分",
            "material_ids": [self.material_id], "study_mode": "coach", **changes,
        }

    def answer(self, source):
        return {
            "answer": "复习回答", "sources": [{"source": source, "text": source}],
            "trace": [{"label": "测试工具", "detail": source}],
        }

    async def test_visitors_cannot_list_read_continue_or_delete_each_others_conversations(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as other:
            with patch("app.main.review", new=AsyncMock(return_value=self.answer("资料"))) as review:
                await self.client.post("/api/review", json=self.request())
                await other.post("/api/review", json=self.request(1, question="访客乙的对话"))
                review.reset_mock()
                listing = (await other.get("/api/conversations")).json()
                self.assertEqual(listing["total"], 1)
                self.assertEqual([item["id"] for item in listing["conversations"]], [self.ids[1]])
                self.assertNotIn("visitor_id", listing["conversations"][0])
                denied = [
                    await other.get(f"/api/conversations/{self.ids[0]}"),
                    await other.delete(f"/api/conversations/{self.ids[0]}"),
                    await other.post("/api/review", json=self.request()),
                    await other.post("/api/review/stream", json=self.request()),
                ]
                self.assertTrue(all(response.status_code == 404 for response in denied))
                self.assertTrue(all("text/event-stream" not in response.headers.get("content-type", "") for response in denied))
                review.assert_not_awaited()
            original = (await self.client.get(f"/api/conversations/{self.ids[0]}")).json()["conversation"]
            self.assertEqual(len(original["messages"]), 2)
            self.client.cookies.clear()
            self.assertEqual((await self.client.get("/api/conversations")).json()["total"], 0)
            self.assertEqual((await self.client.get(f"/api/conversations/{self.ids[0]}")).status_code, 404)

    async def test_unowned_legacy_rows_are_retained_but_cannot_be_claimed(self):
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            await connection.execute(
                "INSERT INTO xiaohan_conversations (id, title) VALUES (%s, %s)",
                (self.ids[0], "升级前的公共记录"),
            )
        self.assertEqual((await self.client.get("/api/conversations")).json()["total"], 0)
        self.assertEqual((await self.client.get(f"/api/conversations/{self.ids[0]}")).status_code, 404)
        with patch("app.main.review", new=AsyncMock()) as review:
            response = await self.client.post("/api/review", json=self.request())
        self.assertEqual(response.status_code, 404)
        review.assert_not_awaited()
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            cursor = await connection.execute("SELECT visitor_id, title FROM xiaohan_conversations WHERE id = %s", (self.ids[0],))
            self.assertEqual(await cursor.fetchone(), (None, "升级前的公共记录"))

    async def test_atomic_creation_collision_and_write_guards(self):
        from app.conversations import ConversationNotFound, begin_turn, delete_conversation, finish_turn
        owners = [self.visitor, "another-isolated-visitor"]
        results = await asyncio.gather(*[
            begin_turn(self.ids[0], f"问题{index}", [], "coach", None, visitor_id=owner)
            for index, owner in enumerate(owners)
        ], return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ConversationNotFound) for result in results), 1)
        winner = next(index for index, result in enumerate(results) if not isinstance(result, Exception))
        loser = owners[1 - winner]
        with self.assertRaises(ConversationNotFound):
            await finish_turn(self.ids[0], "越权回答", [], [], visitor_id=loser)
        cleanup = AsyncMock()
        self.assertFalse(await delete_conversation(self.ids[0], loser, cleanup))
        cleanup.assert_not_awaited()
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            cursor = await connection.execute("SELECT content FROM xiaohan_messages WHERE conversation_id = %s", (self.ids[0],))
            self.assertEqual(await cursor.fetchall(), [(f"问题{winner}",)])

    async def test_restore_after_reconnecting_and_keep_round_citations(self):
        with patch("app.main.review", new=AsyncMock(side_effect=[self.answer("第一轮"), self.answer("第二轮")])):
            first = await self.client.post("/api/review", json=self.request())
            second = await self.client.post("/api/review", json=self.request(question="继续抽查", study_mode="quiz", prompt="一次一题"))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        # 关闭并重新建立数据库连接，验证记录来自数据库而非 Python 变量。
        await self.lifespan.__aexit__(None, None, None)
        self.lifespan = main.app.router.lifespan_context(main.app)
        await self.lifespan.__aenter__()
        saved = (await self.client.get(f"/api/conversations/{self.ids[0]}")).json()["conversation"]
        self.assertEqual([m["role"] for m in saved["messages"]], ["user", "assistant", "user", "assistant"])
        self.assertEqual(saved["messages"][1]["sources"][0]["source"], "第一轮")
        self.assertEqual(saved["messages"][3]["sources"][0]["source"], "第二轮")
        self.assertEqual(saved["study_mode"], "quiz")
        self.assertEqual(saved["material_ids"], [self.material_id])
        self.assertEqual(saved["prompt"], "一次一题")

    async def test_new_conversation_keeps_previous_record_and_orders_list(self):
        with patch("app.main.review", new=AsyncMock(return_value=self.answer("资料"))):
            await self.client.post("/api/review", json=self.request())
            await self.client.post("/api/review", json=self.request(1, question="测试：另一段对话"))
        listing = (await self.client.get("/api/conversations?limit=1")).json()
        self.assertEqual(listing["conversations"][0]["id"], self.ids[1])
        self.assertEqual(listing["conversations"][0]["message_count"], 2)
        next_page = (await self.client.get("/api/conversations?limit=1&offset=1")).json()
        self.assertEqual(next_page["conversations"][0]["id"], self.ids[0])
        previous = (await self.client.get(f"/api/conversations/{self.ids[0]}")).json()["conversation"]
        self.assertEqual(len(previous["messages"]), 2)
        self.assertEqual(previous["messages"][0]["content"], self.request()["question"])

    async def test_failed_generation_remains_visible(self):
        with patch("app.main.review", new=AsyncMock(side_effect=RuntimeError("模拟生成失败"))):
            response = await self.client.post("/api/review", json=self.request())
        self.assertEqual(response.status_code, 502)
        saved = (await self.client.get(f"/api/conversations/{self.ids[0]}")).json()["conversation"]
        self.assertEqual(len(saved["messages"]), 2)
        self.assertTrue(saved["messages"][1]["failed"])
        self.assertIn("这次复习没有完成", saved["messages"][1]["content"])

    async def test_invalid_material_does_not_create_history(self):
        response = await self.client.post("/api/review", json=self.request(material_ids=[str(uuid.uuid4())]))
        self.assertEqual(response.status_code, 400)
        missing = await self.client.get(f"/api/conversations/{self.ids[0]}")
        self.assertEqual(missing.status_code, 404)
        invalid_page = await self.client.get("/api/conversations?limit=0")
        self.assertEqual(invalid_page.status_code, 422)

    async def test_web_only_conversation_restores_switch_and_citable_urls(self):
        source = {"type": "web", "title": "官方文档", "url": "https://docs.langchain.com/", "source": "https://docs.langchain.com/", "text": "联网摘要"}
        answer = {"answer": "网上补充", "sources": [source], "trace": [{"label": "联网搜索返回结果", "detail": "search_web", "status": "success"}]}
        with patch("app.main.TAVILY_API_KEY", "测试占位"), patch("app.main.review", new=AsyncMock(return_value=answer)):
            response = await self.client.post("/api/review", json=self.request(material_ids=[], web_search_enabled=True))
        self.assertEqual(response.status_code, 200)
        saved = (await self.client.get(f"/api/conversations/{self.ids[0]}")).json()["conversation"]
        self.assertTrue(saved["web_search_enabled"])
        self.assertEqual(saved["material_ids"], [])
        self.assertEqual(saved["messages"][1]["sources"], [source])
        # 新连接仍能读取联网开关；关闭后的下一轮也要更新会话设置。
        await self.lifespan.__aexit__(None, None, None)
        self.lifespan = main.app.router.lifespan_context(main.app)
        await self.lifespan.__aenter__()
        restored = (await self.client.get(f"/api/conversations/{self.ids[0]}")).json()["conversation"]
        self.assertTrue(restored["web_search_enabled"])
        with patch("app.main.review", new=AsyncMock(return_value=self.answer("学习资料"))):
            await self.client.post("/api/review", json=self.request(web_search_enabled=False))
        updated = (await self.client.get(f"/api/conversations/{self.ids[0]}")).json()["conversation"]
        self.assertFalse(updated["web_search_enabled"])


if __name__ == "__main__":
    unittest.main()
