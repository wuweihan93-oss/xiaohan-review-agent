"""确保恢复旧会话后，每条回答只引用本轮工具结果。"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app import agent


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
        with patch.object(agent, "_memory", object()), patch.object(agent, "_mcp_client", return_value=client), patch.object(agent, "_chat_model"), patch.object(agent, "create_agent", return_value=graph):
            result = await agent.review(question="新问题", conversation_id="test", material_ids=["资料"], study_mode="coach")
        self.assertEqual(result["answer"], "新回答")
        self.assertEqual([item["source"] for item in result["sources"]], ["新资料"])
