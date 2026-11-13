"""联网搜索的实际实现；MCP 工具负责把这项能力交给 Agent。"""

from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from app.core.config import (
    TAVILY_API_KEY,
    TAVILY_SEARCH_URL,
    WEB_SEARCH_CONNECT_TIMEOUT_SECONDS,
    WEB_SEARCH_DEPTH,
    WEB_SEARCH_MAX_RESULTS,
    WEB_SEARCH_QUERY_MAX_LENGTH,
    WEB_SEARCH_TEXT_MAX_LENGTH,
    WEB_SEARCH_TIMEOUT_SECONDS,
)


def safe_web_url(value: object) -> str:
    """只保留可作为网页引用打开的 HTTP / HTTPS 链接。"""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username or parsed.password:
            return ""
        _ = parsed.port
    except ValueError:
        return ""
    return value


async def search_web(query: str) -> list[dict]:
    """向 Tavily 查询公开网页，返回最多五条带标题、链接和摘要的结果。"""
    query = query.strip()
    if not query or len(query) > WEB_SEARCH_QUERY_MAX_LENGTH:
        raise ValueError(
            f"联网搜索关键词需要为 1 至 {WEB_SEARCH_QUERY_MAX_LENGTH} 个字符"
        )
    if not TAVILY_API_KEY:
        raise RuntimeError("联网搜索尚未配置，请在项目 .env 中填写 TAVILY_API_KEY")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                WEB_SEARCH_TIMEOUT_SECONDS, connect=WEB_SEARCH_CONNECT_TIMEOUT_SECONDS
            )
        ) as client:
            response = await client.post(
                TAVILY_SEARCH_URL,
                headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                json={
                    "query": query,
                    "search_depth": WEB_SEARCH_DEPTH,
                    "max_results": WEB_SEARCH_MAX_RESULTS,
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            "联网搜索超时，请稍后重试；已有学习资料仍可用于复习"
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError("暂时无法连接联网搜索服务，请检查网络后重试") from exc

    if response.status_code in {401, 403}:
        raise RuntimeError("联网搜索认证失败，请检查 TAVILY_API_KEY")
    if response.status_code in {429, 432, 433}:
        raise RuntimeError("联网搜索额度不足或请求过于频繁，请检查 Tavily 额度后重试")
    if response.is_error:
        raise RuntimeError(
            f"联网搜索服务暂时不可用（HTTP {response.status_code}），请稍后重试"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("联网搜索服务返回了无法解析的结果，请稍后重试") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise RuntimeError("联网搜索服务返回的数据格式不正确，请稍后重试")

    results = []
    seen = set()
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        url = safe_web_url(item.get("url"))
        text = item.get("content")
        if not url or url in seen or not isinstance(text, str) or not text.strip():
            continue
        seen.add(url)
        results.append(
            {
                "type": "web",
                "title": str(item.get("title") or url)[:200],
                "url": url,
                "source": url,
                "text": text.strip()[:WEB_SEARCH_TEXT_MAX_LENGTH],
                "retrieved_at": retrieved_at,
            }
        )
        if len(results) == WEB_SEARCH_MAX_RESULTS:
            break
    return results
