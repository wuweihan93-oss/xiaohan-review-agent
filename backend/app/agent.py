from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import os
import sys
from typing import Any

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.config import (
    BACKEND_DIR,
    CHAT_API_KEY,
    CHAT_BASE_URL,
    CHAT_MODEL,
    DB_URL,
    DEFAULT_STUDY_PROMPT,
)
from app.middleware import ToolRecoveryMiddleware

_memory: Any | None = None
_model = None


@asynccontextmanager
async def checkpointer_context() -> AsyncIterator[Any]:
    if DB_URL:
        async with AsyncPostgresSaver.from_conn_string(DB_URL) as checkpointer:
            await checkpointer.setup()
            yield checkpointer
    else:
        yield InMemorySaver()


def set_checkpointer(checkpointer: Any | None) -> None:
    global _memory
    _memory = checkpointer


def _chat_model():
    global _model
    if not CHAT_API_KEY:
        raise RuntimeError("项目 .env 中没有配置 DEEPSEEK_API_KEY")
    if not CHAT_BASE_URL:
        raise RuntimeError("项目 .env 中没有配置 DEEPSEEK_BASE_URL")
    if _model is None:
        _model = init_chat_model(
            CHAT_MODEL,
            model_provider="deepseek",
            api_key=CHAT_API_KEY,
            base_url=CHAT_BASE_URL,
            temperature=0.4,
        )
    return _model


def _mcp_client(material_ids: list[str]) -> MultiServerMCPClient:
    child_env = {
        "PYTHONPATH": str(BACKEND_DIR),
        "API_BASE_URL": os.getenv("API_BASE_URL", "http://127.0.0.1:8010"),
        "XIAOHAN_MATERIAL_IDS": json.dumps(material_ids),
    }
    return MultiServerMCPClient(
        {
            "xiaohan-study-materials": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "app.mcp_server"],
                "env": child_env,
            }
        }
    )


def _tool_payload(message: ToolMessage) -> list[dict[str, Any]]:
    content = message.content
    candidates = content if isinstance(content, list) else [content]
    for candidate in candidates:
        text = candidate.get("text") if isinstance(candidate, dict) else candidate
        if not isinstance(text, str):
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict) and "text" in item]
    return []


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


async def review(
    *,
    question: str,
    conversation_id: str,
    material_ids: list[str],
    study_mode: str,
    prompt: str | None = None,
) -> dict[str, Any]:
    if not material_ids:
        raise ValueError("请先在学习资料中选择至少一份资料")
    if _memory is None:
        raise RuntimeError("对话记忆服务尚未初始化")

    mode_instruction = (
        "当前是抽查模式。先检索资料，每次只问一道问题。等待用户回答后再判断、解释和出下一题。"
        if study_mode == "quiz"
        else "当前是复盘模式。先检索资料，再用循序渐进的方式讲解；一轮只检查一个理解点。"
    )
    system_prompt = f"{prompt or DEFAULT_STUDY_PROMPT}\n\n{mode_instruction}"
    tools = await _mcp_client(material_ids).get_tools()
    agent = create_agent(
        model=_chat_model(),
        tools=tools,
        system_prompt=system_prompt,
        middleware=[ToolRecoveryMiddleware()],
        checkpointer=_memory,
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=question)]},
        config={"configurable": {"thread_id": conversation_id}},
    )

    messages = result.get("messages", [])
    # checkpoint 含整段历史；引用与工具过程只取本轮，避免旧引用重复出现。
    last_question = next(
        (index for index in range(len(messages) - 1, -1, -1) if isinstance(messages[index], HumanMessage)),
        -1,
    )
    current_messages = messages[last_question + 1 :]
    sources: list[dict[str, Any]] = []
    trace: list[dict[str, str]] = [
        {"label": "连接学习资料 MCP", "detail": "通过 stdio 发现资料检索工具"},
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
            trace.append({"label": "MCP 返回检索结果", "detail": message.name or "资料检索"})
            sources.extend(_tool_payload(message))
    final_message = next(
        (message for message in reversed(messages) if isinstance(message, AIMessage) and not getattr(message, "tool_calls", None)),
        None,
    )
    return {
        "answer": _message_text(final_message) if final_message else "小晗暂时没有生成回答，请再试一次。",
        "sources": sources,
        "trace": trace,
        "conversation_id": conversation_id,
        "study_mode": study_mode,
    }
