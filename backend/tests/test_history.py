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


if __name__ == "__main__":
    unittest.main()
