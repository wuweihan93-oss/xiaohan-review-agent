"""把 LangChain 消息解析成文本、引用和工具过程，不负责模型调用。"""

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.core.config import DB_URL
from app.integrations.web_search import safe_web_url


def tool_sources(message: ToolMessage) -> list[dict[str, Any]]:
    if message.status == "error":
        return []
    content = message.content
    candidates = content if isinstance(content, list) else [content]
    sources = []
    for candidate in candidates:
        text = candidate.get("text") if isinstance(candidate, dict) else candidate
        if not isinstance(text, str):
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                    continue
                if message.name == "search_web" or item.get("type") == "web":
                    url = safe_web_url(item.get("url"))
                    if not url:
                        continue
                    sources.append({**item, "type": "web", "source": url, "url": url})
                else:
                    sources.append({**item, "type": "material"})
    return sources


def message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") if isinstance(part, dict) else part
            for part in content
            if isinstance(part, str)
            or (isinstance(part, dict) and part.get("type") in {"text", "text_delta"})
        )
    return str(content)


def review_result(
    messages: list, *, conversation_id: str, study_mode: str, web_search_enabled: bool
) -> dict[str, Any]:
    # checkpoint 含整段历史；引用与工具过程只取本轮，避免旧引用重复出现。
    last_question = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], HumanMessage)
        ),
        -1,
    )
    current_messages = messages[last_question + 1 :]
    sources: list[dict[str, Any]] = []
    trace: list[dict[str, str]] = [
        {
            "label": "连接复习工具 MCP",
            "detail": "资料检索与联网搜索" if web_search_enabled else "仅学习资料检索",
        },
        {
            "label": "LangChain Agent",
            "detail": f"create_agent + {'PostgreSQL' if DB_URL else 'InMemorySaver'} Checkpointer",
        },
    ]
    for message in current_messages:
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            for call in message.tool_calls:
                trace.append(
                    {
                        "label": "Agent 选择工具",
                        "detail": call.get("name", "学习资料工具"),
                    }
                )
        elif isinstance(message, ToolMessage):
            found = tool_sources(message)
            # 防止复用 checkpoint 时将已关闭的工具来源带到新一轮引用中。
            found = [
                item for item in found if web_search_enabled or item["type"] != "web"
            ]
            failed = message.status == "error"
            label = "联网搜索失败" if message.name == "search_web" else "工具执行失败"
            if not failed:
                label = (
                    "联网搜索返回结果"
                    if message.name == "search_web"
                    else "MCP 返回资料结果"
                )
            trace.append(
                {
                    "label": label,
                    "detail": f"{message.name or '资料检索'} · {len(found)} 条引用"
                    if not failed
                    else "暂未取得结果，可稍后重试",
                    "status": "error" if failed else "success",
                }
            )
            for source in found:
                if source not in sources:
                    sources.append(source)
    final_message = next(
        (
            message
            for message in reversed(current_messages)
            if isinstance(message, AIMessage)
            and not getattr(message, "tool_calls", None)
        ),
        None,
    )
    return {
        "answer": message_text(final_message)
        if final_message
        else "小晗暂时没有生成回答，请再试一次。",
        "sources": sources,
        "trace": trace,
        "conversation_id": conversation_id,
        "study_mode": study_mode,
        "web_search_enabled": web_search_enabled,
    }
