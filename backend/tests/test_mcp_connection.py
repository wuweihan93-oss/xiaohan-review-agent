"""真实启动 stdio 子进程，确认迁移后的 MCP 入口可发现工具；不执行联网或向量查询。"""

import asyncio
import unittest
from app.integrations.mcp.client import create_mcp_client


class McpConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_server_exposes_material_tools_without_web_search(self):
        client = create_mcp_client([], web_search_enabled=False)
        tools = await asyncio.wait_for(client.get_tools(), timeout=30)
        names = {tool.name for tool in tools}
        self.assertIn("search_study_materials", names)
        self.assertIn("list_selected_materials", names)
        self.assertNotIn("search_web", names)
