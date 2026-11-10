import json
import os

import httpx
from fastmcp import FastMCP

from app.config import API_BASE_URL

_selected_ids = json.loads(os.getenv("XIAOHAN_MATERIAL_IDS", "[]"))
mcp = FastMCP("小晗学习资料")


@mcp.tool
async def search_study_materials(query: str) -> str:
    """在用户本轮选定的学习资料中搜索相关内容，回答时引用命中的文件。"""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{API_BASE_URL}/api/retrieval/search",
            json={"query": query, "material_ids": _selected_ids, "limit": 5},
        )
        if response.is_error:
            try:
                detail = response.json().get("detail", "资料服务暂时不可用")
            except ValueError:
                detail = "资料服务暂时不可用"
            raise RuntimeError(f"学习资料检索失败（HTTP {response.status_code}）：{detail}")
        response.raise_for_status()
        return json.dumps(response.json()["results"], ensure_ascii=False)


@mcp.tool
async def list_selected_materials() -> str:
    """列出本轮复习选中的资料名称。"""
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{API_BASE_URL}/api/materials")
        response.raise_for_status()
        selected = set(_selected_ids)
        materials = [item for item in response.json()["materials"] if item["id"] in selected]
        return json.dumps(materials, ensure_ascii=False)


@mcp.prompt
def review_with_xiaohan(topic: str) -> str:
    """生成一段按学习资料开展的复习提示词。"""
    return (
        "你是小晗，先调用 search_study_materials 查找用户选定的学习资料。"
        f"用简单中文带用户复习主题“{topic}”，解释后只提出一道检查理解的问题。"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
