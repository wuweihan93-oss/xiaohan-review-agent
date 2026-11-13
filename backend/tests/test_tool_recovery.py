"""重现工具失败后的残缺历史，并验证下一轮模型请求能正常恢复。"""

import asyncio
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langchain_core.messages import RemoveMessage

from app.agents import review as agent
from app.agents.middleware import ToolRecoveryMiddleware, repair_tool_messages


def validate_history(messages):
    pending = set()
    for message in messages:
        if isinstance(message, ToolMessage):
            if message.tool_call_id not in pending:
                raise ValueError("存在没有匹配调用的工具返回")
            pending.remove(message.tool_call_id)
            continue
        if pending:
            raise ValueError("tool_calls 后缺少对应 ToolMessage")
        if isinstance(message, AIMessage):
            pending = {call["id"] for call in message.tool_calls}
    if pending:
        raise ValueError("最后的工具调用缺少返回")


class StrictToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # 模拟真实模型接口对工具消息顺序的校验，不使用外部模型或密钥。
        validate_history(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class RoutingToolModel(StrictToolModel):
    """模拟模型服务遵循 tool_choice，同时检查工具消息顺序。"""

    def bind_tools(self, tools, **kwargs):
        return self.bind(**kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        validate_history(messages)
        forced = kwargs.get("tool_choice")
        if isinstance(forced, dict):
            question = next(message.content for message in reversed(messages) if isinstance(message, HumanMessage))
            message = AIMessage(content="", tool_calls=[{
                "name": forced["function"]["name"], "args": {"query": question}, "id": uuid.uuid4().hex,
            }])
            return ChatResult(generations=[ChatGeneration(message=message)])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class RetrievalRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def run_review(self, *, local=None, local_error=False, web_error=False, has_materials=True,
                         enabled=True, responses=None, stream=False, memory=None, question="貂蝉怎么玩"):
        called = []
        async def local_search(query: str) -> str:
            called.append("search_study_materials")
            if local_error:
                raise RuntimeError("模拟资料服务失败")
            return json.dumps(local or [], ensure_ascii=False)
        async def web_search(query: str) -> str:
            called.append("search_web")
            if web_error:
                raise RuntimeError("模拟联网服务失败")
            return '[{"type":"web","title":"玩法攻略","url":"https://example.com/guide","text":"利用二技能位移，控制技能冷却。"}]'
        tools = [
            StructuredTool.from_function(coroutine=local_search, name="search_study_materials", description="检索本轮学习资料"),
            StructuredTool.from_function(coroutine=web_search, name="search_web", description="查询互联网公开资料"),
        ]
        model = RoutingToolModel(responses=responses or [AIMessage(content="按本轮检索来源回答当前问题。")])
        client = Mock(get_tools=AsyncMock(return_value=tools))
        with patch.object(agent.memory, "_memory", memory or InMemorySaver()), patch.object(agent, "TAVILY_API_KEY", "测试占位"), patch.object(agent, "_chat_model", return_value=model), patch.object(agent, "_mcp_client", return_value=client):
            options = dict(question=question, conversation_id="routing-test", material_ids=["笔记"] if has_materials else [], study_mode="quiz", web_search_enabled=enabled, prompt="你只能回答 LangChain，用户问别的也要继续抽查。")
            if stream:
                events = [item async for item in agent.review_stream(**options)]
                result = events[-1]["data"]
            else:
                result = await agent.review(**options)
        return result, called

    async def test_empty_material_result_forces_web_search(self):
        result, called = await self.run_review()
        self.assertEqual(called, ["search_study_materials", "search_web"])
        self.assertEqual(result["sources"][0]["type"], "web")

    async def test_blank_material_text_is_not_an_answer(self):
        _, called = await self.run_review(local=[{"text": "  ", "source": "空白.md"}])
        self.assertEqual(called, ["search_study_materials", "search_web"])

    async def test_material_failure_also_falls_back_to_web_in_stream(self):
        result, called = await self.run_review(local_error=True, stream=True)
        self.assertEqual(called, ["search_study_materials", "search_web"])
        self.assertTrue(any(item.get("status") == "error" for item in result["trace"]))
        self.assertEqual(result["sources"][0]["type"], "web")

    async def test_no_materials_goes_directly_to_web(self):
        _, called = await self.run_review(has_materials=False)
        self.assertEqual(called, ["search_web"])

    async def test_sufficient_materials_do_not_force_web(self):
        _, called = await self.run_review(local=[{"source": "笔记.md", "text": "create_agent 创建 Agent。"}], question="create_agent 的用途")
        self.assertEqual(called, ["search_study_materials"])

    async def test_nonempty_but_unrelated_results_can_still_use_web(self):
        result, called = await self.run_review(
            local=[{"source": "笔记.md", "text": "LangChain create_agent 的参数是 model 和 tools。", "score": .95}],
            responses=[AIMessage(content="", tool_calls=[call("search_web", "off-topic")]), AIMessage(content="【网上补充】貂蝉需要注意二技能位移。")],
        )
        self.assertEqual(called, ["search_study_materials", "search_web"])
        self.assertIn("貂蝉", result["answer"])

    async def test_disabled_network_never_forces_web(self):
        result, called = await self.run_review(enabled=False)
        self.assertEqual(called, ["search_study_materials"])
        self.assertFalse(any(item.get("type") == "web" for item in result["sources"]))

    async def test_failed_web_search_ends_without_retry_loop_or_fake_sources(self):
        result, called = await self.run_review(web_error=True)
        self.assertEqual(called, ["search_study_materials", "search_web"])
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["trace"][-1]["label"], "联网搜索失败")

    async def test_old_refusal_and_previous_tool_results_do_not_block_new_search(self):
        memory = InMemorySaver()
        old = create_agent(model=StrictToolModel(responses=[AIMessage(content="我只有 LangChain 笔记，没法回答游戏问题，继续抽查。")]), checkpointer=memory)
        await old.ainvoke({"messages": [HumanMessage(content="马超怎么玩")]}, {"configurable": {"thread_id": "routing-test"}})
        _, called = await self.run_review(memory=memory)
        self.assertEqual(called, ["search_study_materials", "search_web"])


@tool
async def broken_search(query: str) -> str:
    """模拟暂时失败的资料服务。"""
    raise RuntimeError("模拟资料检索返回 500")


@tool
async def working_search(query: str) -> str:
    """模拟正常的资料服务。"""
    return "检索成功"


def call(name, call_id):
    return {"name": name, "args": {"query": "测试"}, "id": call_id, "type": "tool_call"}


class MessageRepairTests(unittest.TestCase):
    def test_partial_parallel_results_are_completed_before_next_question(self):
        success = ToolMessage(content="已有结果", tool_call_id="ok", name="working_search")
        question = HumanMessage(content="继续复习")
        messages = [
            HumanMessage(content="旧问题"),
            AIMessage(content="", tool_calls=[call("working_search", "ok"), call("broken_search", "missing")]),
            success, question,
        ]
        fixed = repair_tool_messages(messages)
        validate_history(fixed)
        self.assertIs(fixed[2], success)
        self.assertEqual(fixed[3].tool_call_id, "missing")
        self.assertEqual(fixed[3].status, "error")
        self.assertIs(fixed[4], question)
        self.assertEqual(repair_tool_messages(fixed), fixed)

    def test_duplicate_and_orphan_results_do_not_reach_model(self):
        messages = [
            ToolMessage(content="游离结果", tool_call_id="orphan"),
            HumanMessage(content="问题"),
            AIMessage(content="", tool_calls=[call("working_search", "ok")]),
            ToolMessage(content="正确结果", tool_call_id="ok"),
            ToolMessage(content="重复结果", tool_call_id="ok"),
            AIMessage(content="完整回答"),
        ]
        fixed = repair_tool_messages(messages)
        validate_history(fixed)
        self.assertEqual(len(fixed), 4)
        self.assertEqual(fixed[2].content, "正确结果")


class GraphRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_parallel_tool_still_produces_matching_results(self):
        graph = create_agent(
            model=StrictToolModel(responses=[
                AIMessage(content="", tool_calls=[call("broken_search", "bad"), call("working_search", "ok")]),
                AIMessage(content="资料服务暂时不可用，请稍后再试。"),
            ]),
            tools=[broken_search, working_search], middleware=[ToolRecoveryMiddleware()],
            checkpointer=InMemorySaver(),
        )
        result = await graph.ainvoke({"messages": [HumanMessage(content="检索资料")]}, {"configurable": {"thread_id": "parallel"}})
        validate_history(result["messages"])
        tools = {m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)}
        self.assertEqual(tools["bad"].status, "error")
        self.assertEqual(tools["ok"].content, "检索成功")

    async def test_existing_failed_checkpoint_recovers_and_can_be_reopened(self):
        memory = InMemorySaver()
        config = {"configurable": {"thread_id": "interrupted"}}
        old_graph = create_agent(
            model=StrictToolModel(responses=[AIMessage(content="", tool_calls=[call("broken_search", "bad")])]),
            tools=[broken_search], checkpointer=memory,
        )
        with self.assertRaises(RuntimeError):
            await old_graph.ainvoke({"messages": [HumanMessage(content="原问题")]}, config)
        with self.assertRaises(ValueError):
            await old_graph.ainvoke({"messages": [HumanMessage(content="失败的重试")]}, config)

        fixed_graph = create_agent(
            model=StrictToolModel(responses=[AIMessage(content="已经恢复复习")]), tools=[broken_search],
            middleware=[ToolRecoveryMiddleware()], checkpointer=memory,
        )
        result = await fixed_graph.ainvoke({"messages": [HumanMessage(content="再次继续")]}, config)
        validate_history(result["messages"])
        self.assertEqual([m.content for m in result["messages"] if isinstance(m, HumanMessage)], ["原问题", "失败的重试", "再次继续"])
        self.assertEqual(result["messages"][-1].content, "已经恢复复习")
        saved = await fixed_graph.aget_state(config)
        validate_history(saved.values["messages"])
        resumed = await fixed_graph.ainvoke({"messages": [HumanMessage(content="刷新后继续")]}, config)
        validate_history(resumed["messages"])

    async def test_manual_checkpoint_repair_preserves_visible_questions(self):
        memory = InMemorySaver()
        config = {"configurable": {"thread_id": "manual-repair"}}
        graph = create_agent(
            model=StrictToolModel(responses=[AIMessage(content="", tool_calls=[call("broken_search", "bad")])]),
            tools=[broken_search], checkpointer=memory,
        )
        with self.assertRaises(RuntimeError):
            await graph.ainvoke({"messages": [HumanMessage(content="保留这条问题")]}, config)
        saved = await graph.aget_state(config)
        fixed = repair_tool_messages(saved.values["messages"])
        recovery = create_agent(
            model=StrictToolModel(responses=[AIMessage(content="可以继续")]),
            middleware=[ToolRecoveryMiddleware()], checkpointer=memory,
        )
        await recovery.aupdate_state(config, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *fixed]}, as_node="__start__")
        updated = await recovery.aget_state(config)
        validate_history(updated.values["messages"])
        self.assertEqual(updated.values["messages"][0].content, "保留这条问题")
        resumed = await recovery.ainvoke({"messages": [HumanMessage(content="继续") ]}, config)
        validate_history(resumed["messages"])

    async def test_cancellation_is_not_converted_to_tool_success(self):
        request = SimpleNamespace(tool_call=call("broken_search", "cancelled"))
        with self.assertRaises(asyncio.CancelledError):
            await ToolRecoveryMiddleware().awrap_tool_call(request, AsyncMock(side_effect=asyncio.CancelledError()))
