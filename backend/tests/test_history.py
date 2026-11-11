"""历史记录的数据库集成测试：设置 XIAOHAN_POSTGRES_TESTS=1 后运行。"""

import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx
from psycopg import AsyncConnection

from app import main
from app.config import DB_URL


@unittest.skipUnless(DB_URL and os.getenv("XIAOHAN_POSTGRES_TESTS") == "1", "需要显式启用 PostgreSQL 集成测试")
class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        self.material_id = str(uuid.uuid4())
        self.lifespan = main.app.router.lifespan_context(main.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
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
                await graph.ainvoke({"messages": [HumanMessage(content="检索资料")]}, {"configurable": {"thread_id": conversation_id}})
        config = {"configurable": {"thread_id": self.ids[0]}}
        self.assertIsNotNone(await agent._memory.aget_tuple(config))
        response = await self.client.delete(f"/api/conversations/{self.ids[0]}")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(await agent._memory.aget_tuple(config))
        self.assertEqual(await backfill_conversations(agent._memory), 0)
        async with await AsyncConnection.connect(DB_URL, autocommit=True) as connection:
            for table in ("xiaohan_messages", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                column = "conversation_id" if table == "xiaohan_messages" else "thread_id"
                cursor = await connection.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (self.ids[0],))
                self.assertEqual((await cursor.fetchone())[0], 0)
        self.assertIsNotNone(await agent._memory.aget_tuple({"configurable": {"thread_id": self.ids[1]}}))
        remaining = await self.client.get(f"/api/conversations/{self.ids[1]}")
        self.assertEqual(len(remaining.json()["conversation"]["messages"]), 2)
        await self.lifespan.__aexit__(None, None, None)
        self.lifespan = main.app.router.lifespan_context(main.app)
        await self.lifespan.__aenter__()
        missing = await self.client.get(f"/api/conversations/{self.ids[0]}")
        self.assertEqual(missing.status_code, 404)
        repeated = await self.client.delete(f"/api/conversations/{self.ids[0]}")
        self.assertEqual(repeated.status_code, 404)
        await agent.delete_memory(self.ids[1])

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
