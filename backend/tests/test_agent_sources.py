"""确保恢复旧会话后，每条回答只引用本轮工具结果。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents import review as agent


class AgentSourcesTests(unittest.IsolatedAsyncioTestCase):
    async def test_sources_only_include_current_turn(self):
        graph = Mock()
        graph.ainvoke = AsyncMock(return_value={"messages": [
            HumanMessage(content="旧问题"),
            ToolMessage(content='[{"text":"旧片段", "source":"旧资料"}]', tool_call_id="old", name="search_study_materials"),
            AIMessage(content="旧回答"),
            HumanMessage(content="新问题"),
            ToolMessage(content='[{"text":"新片段", "source":"新资料"}]', tool_call_id="new", name="search_study_materials"),
            AIMessage(content="新回答"),
        ]})
        client = Mock()
        client.get_tools = AsyncMock(return_value=[])
        with patch.object(agent.memory, "_memory", object()), patch.object(agent, "_mcp_client", return_value=client), patch.object(agent, "_chat_model"), patch.object(agent, "create_agent", return_value=graph):
            result = await agent.review(question="新问题", conversation_id="test", material_ids=["资料"], study_mode="coach")
        self.assertEqual(result["answer"], "新回答")
        self.assertEqual([item["source"] for item in result["sources"]], ["新资料"])

    async def run_review(self, *, enabled=False, material_ids=None, tool_messages=None):
        graph = Mock()
        graph.ainvoke = AsyncMock(return_value={"messages": [HumanMessage(content="问题"), *(tool_messages or []), AIMessage(content="回答")]})
        client = Mock()
        client.get_tools = AsyncMock(return_value=[SimpleNamespace(name=name) for name in ("search_web", "search_study_materials", "list_selected_materials")])
        with patch.object(agent, "TAVILY_API_KEY", "测试密钥"), patch.object(agent.memory, "_memory", object()), patch.object(agent, "_mcp_client", return_value=client) as mcp, patch.object(agent, "_chat_model"), patch.object(agent, "create_agent", return_value=graph) as create:
            result = await agent.review(question="问题", conversation_id="测试", material_ids=["资料"] if material_ids is None else material_ids, study_mode="coach", web_search_enabled=enabled)
        return result, create.call_args.kwargs, mcp.call_args

    async def test_disabled_network_removes_search_tool_and_previous_web_sources(self):
        result, options, mcp_call = await self.run_review(tool_messages=[ToolMessage(
            content='[{"type":"web","url":"https://example.com","text":"网页"}]',
            tool_call_id="web", name="search_web",
        )])
        self.assertNotIn("search_web", [tool.name for tool in options["tools"]])
        self.assertEqual(result["sources"], [])
        self.assertFalse(mcp_call.kwargs["web_search_enabled"])
        self.assertIn("本轮已关闭联网搜索", options["system_prompt"])

    async def test_web_only_turn_exposes_no_material_tools_and_preserves_citations(self):
        result, options, _ = await self.run_review(enabled=True, material_ids=[], tool_messages=[ToolMessage(
            content='[{"type":"web","title":"官方文档","url":"https://example.com/docs","text":"网页摘要"}]',
            tool_call_id="web", name="search_web",
        )])
        self.assertEqual([tool.name for tool in options["tools"]], ["search_web"])
        self.assertEqual(result["sources"][0]["url"], "https://example.com/docs")
        self.assertTrue(result["web_search_enabled"])
        self.assertIn("没有选择学习资料", options["system_prompt"])
        self.assertTrue(any(item["label"] == "联网搜索返回结果" for item in result["trace"]))

    async def test_tool_error_is_recorded_without_becoming_a_reference(self):
        result, _, _ = await self.run_review(enabled=True, tool_messages=[ToolMessage(
            content='[{"type":"web","url":"https://example.com","text":"错误内容"}]',
            tool_call_id="bad", name="search_web", status="error",
        )])
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["trace"][-1]["status"], "error")
        self.assertEqual(result["trace"][-1]["label"], "联网搜索失败")

    async def test_enabled_network_rule_handles_unrelated_material_and_topic_switch(self):
        _, options, _ = await self.run_review(enabled=True)
        prompt = options["system_prompt"]
        self.assertIn("与问题无关/不足以回答时，必须调用 search_web", prompt)
        self.assertIn("选中资料只是优先参考来源，不限制用户能问的话题", prompt)
        self.assertIn("普通问答不强行出题", prompt)
        self.assertIn("覆盖上方自定义提示词及历史对话", prompt)

    async def test_empty_scope_or_unconfigured_search_is_rejected_before_agent_runs(self):
        with patch.object(agent, "create_agent") as create:
            with self.assertRaisesRegex(ValueError, "请选择"):
                await agent.review(question="问题", conversation_id="测试", material_ids=[], study_mode="coach")
            with patch.object(agent, "TAVILY_API_KEY", ""):
                with self.assertRaisesRegex(ValueError, "TAVILY_API_KEY"):
                    await agent.review(question="问题", conversation_id="测试", material_ids=[], study_mode="coach", web_search_enabled=True)
        create.assert_not_called()
