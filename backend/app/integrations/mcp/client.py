"""MCP Adapter：每轮创建带有资料范围和联网开关的 stdio 工具客户端。"""

import json
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.core.config import API_BASE_URL, BACKEND_DIR, TAVILY_API_KEY


def create_mcp_client(
    material_ids: list[str], *, web_search_enabled: bool = False
) -> MultiServerMCPClient:
    child_env = {
        "PYTHONPATH": str(BACKEND_DIR),
        "API_BASE_URL": API_BASE_URL,
        "XIAOHAN_MATERIAL_IDS": json.dumps(material_ids),
        "XIAOHAN_WEB_SEARCH_ENABLED": "1" if web_search_enabled else "0",
    }
    # stdio 子进程不会默认继承模型和搜索密钥，按本轮开关显式传入所需配置。
    if web_search_enabled:
        child_env["TAVILY_API_KEY"] = TAVILY_API_KEY
    return MultiServerMCPClient(
        {
            "xiaohan-study-materials": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "app.integrations.mcp.server"],
                "env": child_env,
            }
        }
    )
