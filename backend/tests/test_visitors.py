"""验证匿名凭据、无数据库时的记忆隔离，不调用外部模型。"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.services import review as review_service
from app.services import conversations as conversation_service
from app import main
from app.agents import review as agent
from app.security.visitors import COOKIE_NAME, memory_thread_id


class VisitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_cookie_persists_identity_without_exposing_credential(self):
        response = await self.client.get("/api/visitor")
        token = self.client.cookies.get(COOKIE_NAME)
        self.assertEqual(len(token), 64)
        self.assertNotIn(token, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        header = response.headers["set-cookie"]
        for flag in ("HttpOnly", "SameSite=lax", "Path=/api", "Max-Age=31536000"):
            self.assertIn(flag, header)
        repeated = await self.client.get("/api/visitor")
        self.assertEqual(response.json(), repeated.json())
        self.assertNotIn("set-cookie", repeated.headers)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", cookies=self.client.cookies) as reopened:
            self.assertEqual((await reopened.get("/api/visitor")).json(), response.json())

    async def test_missing_malformed_cookie_and_https(self):
        initial = (await self.client.get("/api/visitor")).json()
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE_NAME, "invalid")
        changed = (await self.client.get("/api/visitor")).json()
        self.assertNotEqual(initial, changed)
        self.client.cookies.clear()
        third = (await self.client.get("/api/visitor")).json()
        self.assertNotEqual(changed, third)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test") as secure:
            response = await secure.get("/api/visitor")
            self.assertIn("Secure", response.headers["set-cookie"])
            self.assertEqual((await secure.get("/api/visitor")).json(), response.json())

    async def test_cache_scope_is_not_a_bearer_credential(self):
        scope = (await self.client.get("/api/visitor")).json()["cache_scope"]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", cookies={COOKIE_NAME: scope}) as forged:
            other = (await forged.get("/api/visitor")).json()["cache_scope"]
        self.assertNotEqual(scope, other)

    async def test_real_graph_memory_isolated_without_database_even_for_same_public_id(self):
        memory = InMemorySaver()
        client = Mock(get_tools=AsyncMock(return_value=[]))
        request = {"conversation_id": "same-public-id", "question": "访客甲的私有问题", "material_ids": ["资料"]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as other:
            with patch.object(review_service, "DB_URL", ""), patch.object(conversation_service, "DB_URL", ""), patch.object(review_service, "list_materials", return_value=[{"id": "资料"}]), patch.object(agent.memory, "_memory", memory), patch.object(agent, "_chat_model", return_value=FakeListChatModel(responses=["回答"])), patch.object(agent, "_mcp_client", return_value=client):
                first = await self.client.post("/api/review", json=request)
                second = await other.post("/api/review/stream", json={**request, "question": "访客乙的私有问题"})
                again = await self.client.post("/api/review", json={**request, "question": "甲继续"})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(again.status_code, 200)
            self.assertEqual(first.json()["conversation_id"], "same-public-id")
            self.assertIn("event: done", second.text)
            self.assertNotIn("visitor:", second.text)
            scope_a = (await self.client.get("/api/visitor")).json()["cache_scope"]
            scope_b = (await other.get("/api/visitor")).json()["cache_scope"]
        for scope, expected in ((scope_a, ["访客甲的私有问题", "甲继续"]), (scope_b, ["访客乙的私有问题"])):
            saved = await memory.aget_tuple({"configurable": {"thread_id": memory_thread_id(scope, "same-public-id")}})
            questions = [message.content for message in saved.checkpoint["channel_values"]["messages"] if isinstance(message, HumanMessage)]
            self.assertEqual(questions, expected)
