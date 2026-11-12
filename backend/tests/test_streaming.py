"""验证真实 LangGraph 流、SSE 协议、中断保存及删除并发保护，不使用外部模型。"""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from app import agent, main
from test_tool_recovery import StrictToolModel, call


class StreamingToolModel(StrictToolModel):
    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages).generations[0].message
        for char in message.content:
            yield ChatGenerationChunk(message=AIMessageChunk(content=char))
        if message.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_calls=message.tool_calls))


@tool
async def search_study_materials(query: str) -> str:
    """返回隔离的测试资料。"""
    return '[{"source":"新资料","text":"切分后向量化"}]'


class AgentStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_graph_streams_tokens_and_resets_tool_preface(self):
        memory = InMemorySaver()
        model = StreamingToolModel(responses=[
            AIMessage(content="先查资料", tool_calls=[call("search_study_materials", "lookup")]),
            AIMessage(content="逐步回答"),
        ])
        client = Mock(get_tools=AsyncMock(return_value=[search_study_materials]))
        with patch.object(agent, "_memory", memory), patch.object(agent, "_chat_model", return_value=model), patch.object(agent, "_mcp_client", return_value=client):
            events = [item async for item in agent.review_stream(question="检索", conversation_id="stream-test", material_ids=["资料"], study_mode="coach")]
        draft = ""
        tokens = []
        for item in events:
            if item["event"] == "token":
                tokens.append(item["data"]["text"])
                draft += item["data"]["text"]
            elif item["event"] == "reset":
                draft = ""
        self.assertGreater(len(tokens), 2)
        self.assertEqual(draft, "逐步回答")
        self.assertTrue(any(item["event"] == "reset" for item in events))
        result = events[-1]["data"]
        self.assertEqual(events[-1]["event"], "result")
        self.assertEqual(result["answer"], "逐步回答")
        self.assertEqual(result["sources"][0]["source"], "新资料")
        self.assertTrue(any(step["label"] == "MCP 返回资料结果" for step in result["trace"]))

    async def test_first_token_arrives_before_model_finishes(self):
        client = Mock(get_tools=AsyncMock(return_value=[]))
        with patch.object(agent, "_memory", InMemorySaver()), patch.object(agent, "_chat_model", return_value=FakeListChatModel(responses=["分段回答"], sleep=0.01)), patch.object(agent, "_mcp_client", return_value=client):
            stream = agent.review_stream(question="问题", conversation_id="first-token", material_ids=["资料"], study_mode="coach")
            async for item in stream:
                if item["event"] == "token":
                    self.assertEqual(item["data"]["text"], "分")
                    break
            remaining = [item async for item in stream]
        self.assertEqual(remaining[-1]["data"]["answer"], "分段回答")

    def test_reasoning_and_tool_arguments_are_not_visible_answer_text(self):
        message = AIMessageChunk(content=[{"type": "reasoning", "text": "隐藏推理"}, {"type": "text", "text": "可见回答"}])
        self.assertEqual(agent._message_text(message), "可见回答")


class StreamingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.request = {"question": "问题", "conversation_id": "stream-api", "material_ids": ["资料"]}

    async def test_endpoint_returns_sse_and_complete_result(self):
        async def stream(**options):
            yield {"event": "token", "data": {"text": "你好"}}
            yield {"event": "result", "data": {"answer": "你好", "sources": [], "trace": []}}
        with patch.object(main, "DB_URL", ""), patch.object(main, "list_materials", return_value=[{"id": "资料"}]), patch.object(main, "review_stream", stream):
            response = await self.client.post("/api/review/stream", json=self.request)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertIn('event: token\ndata: {"text": "你好"}', response.text)
        self.assertIn('event: done', response.text)
        self.assertNotIn('event: error', response.text)

    async def test_invalid_scope_is_normal_http_error_before_stream(self):
        with patch.object(main, "begin_turn", new=AsyncMock()) as begin:
            response = await self.client.post("/api/review/stream", json={"question": "问题"})
        self.assertEqual(response.status_code, 400)
        begin.assert_not_awaited()

    async def run_interrupted_producer(self, *, cancel=False):
        waiting = asyncio.Event()
        async def stream(**options):
            yield {"event": "state", "data": {"sources": [{"text": "引用"}], "trace": []}}
            yield {"event": "token", "data": {"text": "已生成的文字"}}
            if cancel:
                await waiting.wait()
            else:
                raise RuntimeError("模拟断流")
        queue, lock = asyncio.Queue(), asyncio.Lock()
        finish = AsyncMock(return_value={"id": "saved", "role": "assistant"})
        with patch.object(main, "DB_URL", "隔离测试"), patch.object(main, "begin_turn", new=AsyncMock(return_value={"id": "user"})), patch.object(main, "finish_turn", finish), patch.object(main, "review_stream", stream):
            task = asyncio.create_task(main._produce_review(main.ChatRequest(**self.request), queue, lock, "test-visitor"))
            while (await queue.get())[0] != "token":
                pass
            if cancel:
                self.assertFalse(task.done())
                self.assertTrue(lock.locked())
                task.cancel()
            await task
        finish.assert_awaited_once()
        self.assertTrue(finish.call_args.kwargs["failed"])
        self.assertIn("已生成的文字", finish.call_args.args[1])
        self.assertFalse(lock.locked())
        return finish.call_args.args[1]

    async def test_partial_error_is_saved_once(self):
        self.assertIn("这次复习没有完成", await self.run_interrupted_producer())

    async def test_stop_generation_saves_partial_answer_and_releases_lock(self):
        self.assertIn("已停止生成", await self.run_interrupted_producer(cancel=True))

    async def test_disconnect_during_final_save_does_not_insert_duplicate_answer(self):
        saving, release = asyncio.Event(), asyncio.Event()
        async def stream(**options):
            yield {"event": "result", "data": {"answer": "完整回答", "sources": [], "trace": []}}
        async def finish(*args, **kwargs):
            saving.set()
            await release.wait()
            return {"id": "saved"}
        with patch.object(main, "DB_URL", "隔离测试"), patch.object(main, "begin_turn", new=AsyncMock(return_value={"id": "user"})), patch.object(main, "finish_turn", new=AsyncMock(side_effect=finish)) as saved, patch.object(main, "review_stream", stream):
            task = asyncio.create_task(main._produce_review(main.ChatRequest(**self.request), asyncio.Queue(), asyncio.Lock(), "test-visitor"))
            await saving.wait()
            task.cancel()
            release.set()
            await task
        saved.assert_awaited_once()
        self.assertNotIn("failed", saved.call_args.kwargs)

    async def test_deletion_rejects_busy_conversation_and_protects_unknown_ids(self):
        lock = asyncio.Lock()
        await lock.acquire()
        from app.visitors import memory_thread_id
        visitor = (await self.client.get("/api/visitor")).json()["cache_scope"]
        main._conversation_locks[memory_thread_id(visitor, "busy-test")] = lock
        with patch.object(main, "DB_URL", "隔离测试"), patch.object(main, "check_conversation_access", new=AsyncMock()), patch.object(main, "delete_conversation", new=AsyncMock(return_value=False)) as delete:
            busy = await self.client.delete("/api/conversations/busy-test")
            delete.assert_not_awaited()
            missing = await self.client.delete("/api/conversations/missing-test")
        lock.release()
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(missing.status_code, 404)
