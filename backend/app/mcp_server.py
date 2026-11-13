"""兼容原来的 python -m app.mcp_server 命令，工具定义已移到 integrations/mcp。"""

from app.integrations.mcp.server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
