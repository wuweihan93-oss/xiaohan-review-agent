"""构建 LangChain 1.x Agent，提供完整回答与流式回答两种入口。"""

from collections.abc import AsyncIterator
from typing import Any

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.agents import memory
from app.agents.messages import message_text, review_result
from app.agents.middleware import RetrievalRoutingMiddleware, ToolRecoveryMiddleware
from app.agents.prompts import build_study_prompt
from app.core.config import (
    CHAT_API_KEY,
    CHAT_BASE_URL,
    CHAT_MODEL,
    CHAT_TEMPERATURE,
    TAVILY_API_KEY,
)
from app.integrations.mcp.client import create_mcp_client as _mcp_client

_model = None


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
            temperature=CHAT_TEMPERATURE,
        )
    return _model


async def _create_review_agent(
    *,
    question: str,
    conversation_id: str,
    material_ids: list[str],
    study_mode: str,
    prompt: str | None = None,
    web_search_enabled: bool = False,
):
    if not material_ids and not web_search_enabled:
        raise ValueError("请选择至少一份学习资料，或开启联网搜索")
    if web_search_enabled and not TAVILY_API_KEY:
        raise ValueError(
            "联网搜索尚未配置，请在项目 .env 中填写 TAVILY_API_KEY，或关闭联网后继续复习资料"
        )
    checkpointer = memory.get_checkpointer()

    # 1. 将页面选项转换成本轮 Prompt，检索规则始终覆盖过时的历史限制。
    system_prompt = build_study_prompt(
        material_ids=material_ids,
        study_mode=study_mode,
        web_search_enabled=web_search_enabled,
        prompt=prompt,
    )
    # 2. MCP Adapter 把外部工具描述转换为 LangChain 可用的工具。
    tools = await _mcp_client(
        material_ids, web_search_enabled=web_search_enabled
    ).get_tools()
    tools = [
        tool
        for tool in tools
        if (web_search_enabled or tool.name != "search_web")
        and (
            material_ids
            or tool.name not in {"search_study_materials", "list_selected_materials"}
        )
    ]
    # 3. 模型选择工具，Middleware 约束调用顺序，Checkpointer 保留上下文。
    return create_agent(
        model=_chat_model(),
        tools=tools,
        system_prompt=system_prompt,
        middleware=[
            ToolRecoveryMiddleware(),
            RetrievalRoutingMiddleware(
                has_materials=bool(material_ids), web_search_enabled=web_search_enabled
            ),
        ],
        checkpointer=checkpointer,
    )


async def review(**options) -> dict[str, Any]:
    agent = await _create_review_agent(**options)
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=options["question"])]},
        config={"configurable": {"thread_id": options["conversation_id"]}},
    )
    return review_result(
        result.get("messages", []),
        conversation_id=options["conversation_id"],
        study_mode=options["study_mode"],
        web_search_enabled=options.get("web_search_enabled", False),
    )


async def review_stream(**options) -> AsyncIterator[dict[str, Any]]:
    """模型片段实时发出，完整图状态用于整理本轮引用和最终答案。"""
    agent = await _create_review_agent(**options)
    result_options = {
        "conversation_id": options["conversation_id"],
        "study_mode": options["study_mode"],
        "web_search_enabled": options.get("web_search_enabled", False),
    }
    messages = []
    previous_state = None
    model_message_id = None
    draft = ""
    async for mode, data in agent.astream(
        {"messages": [HumanMessage(content=options["question"])]},
        config={"configurable": {"thread_id": options["conversation_id"]}},
        stream_mode=["messages", "values"],
    ):
        if mode == "messages":
            chunk, metadata = data
            if (
                not isinstance(chunk, (AIMessageChunk, AIMessage))
                or metadata.get("langgraph_node") != "model"
            ):
                continue
            # 一轮可能多次调用模型。检索前的过渡语不能拼接到最终答案里。
            if chunk.id and chunk.id != model_message_id:
                model_message_id = chunk.id
                if draft:
                    yield {"event": "reset", "data": {}}
                    draft = ""
            text = message_text(chunk)
            if text:
                draft += text
                yield {"event": "token", "data": {"text": text}}
        elif mode == "values":
            messages = data.get("messages", [])
            result = review_result(messages, **result_options)
            state = {"sources": result["sources"], "trace": result["trace"]}
            if state != previous_state:
                yield {"event": "state", "data": state}
                previous_state = state
            if (
                messages
                and isinstance(messages[-1], AIMessage)
                and messages[-1].tool_calls
                and draft
            ):
                yield {"event": "reset", "data": {}}
                draft = ""
    yield {"event": "result", "data": review_result(messages, **result_options)}
