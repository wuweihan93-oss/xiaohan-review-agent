"""验证页面联网开关到 Agent 的参数传递，不连接线上数据库与模型。"""

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import main


class ReviewApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_web_only_request_reaches_agent_with_switch_enabled(self):
        answer = {"answer": "网上资料", "sources": [{"type": "web", "url": "https://example.com", "text": "摘要"}], "trace": []}
        with patch.object(main, "DB_URL", ""), patch.object(main, "TAVILY_API_KEY", "测试占位"), patch.object(main, "list_materials", return_value=[]), patch.object(main, "review", new=AsyncMock(return_value=answer)) as review:
            response = await self.client.post("/api/review", json={"question": "查询最新官方文档", "material_ids": [], "web_search_enabled": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(review.call_args.kwargs["web_search_enabled"])
        self.assertEqual(review.call_args.kwargs["material_ids"], [])
        self.assertEqual(response.json()["sources"][0]["type"], "web")

    async def test_missing_key_or_empty_scope_does_not_create_history_or_run_agent(self):
        with patch.object(main, "TAVILY_API_KEY", ""), patch.object(main, "begin_turn", new=AsyncMock()) as begin, patch.object(main, "review", new=AsyncMock()) as review:
            missing_key = await self.client.post("/api/review", json={"question": "测试", "web_search_enabled": True})
            empty_scope = await self.client.post("/api/review", json={"question": "测试"})
        self.assertEqual(missing_key.status_code, 400)
        self.assertIn("TAVILY_API_KEY", missing_key.json()["detail"])
        self.assertEqual(empty_scope.status_code, 400)
        begin.assert_not_awaited()
        review.assert_not_awaited()

    async def test_health_only_exposes_search_availability(self):
        with patch.object(main, "TAVILY_API_KEY", "不得出现在接口里的密钥"), patch.object(main, "material_counts", return_value={}):
            response = await self.client.get("/api/health")
        self.assertTrue(response.json()["web_search_configured"])
        self.assertNotIn("不得出现在接口里的密钥", response.text)
