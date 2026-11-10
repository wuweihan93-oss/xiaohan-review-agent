"""工具异常与中断恢复，保证发送给模型的工具调用和结果成对出现。"""

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

logger = logging.getLogger(__name__)


def repair_tool_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """保留问答和成功的工具结果；为中断调用补错误结果，并移除游离或重复返回。"""
    repaired = []
    index = 0
    while index < len(messages):
        message = messages[index]
        index += 1
        if isinstance(message, ToolMessage):
            # 合法的工具返回会在下方随它所属的 AI 消息一起处理。
            continue
        repaired.append(message)
        if not isinstance(message, AIMessage) or not message.tool_calls:
            continue

        calls = {call["id"]: call for call in message.tool_calls}
        answered = set()
        while index < len(messages) and isinstance(messages[index], ToolMessage):
            result = messages[index]
            index += 1
            if result.tool_call_id in calls and result.tool_call_id not in answered:
                repaired.append(result)
                answered.add(result.tool_call_id)
        for call_id, call in calls.items():
            if call_id not in answered:
                repaired.append(ToolMessage(
                    content="上一次工具调用被中断，没有获得结果。请重新检索资料，不要把这条错误提示当作知识依据。",
                    tool_call_id=call_id,
                    name=call["name"],
                    status="error",
                ))
    return repaired


class ToolRecoveryMiddleware(AgentMiddleware):
    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:
        messages = state["messages"]
        repaired = repair_tool_messages(messages)
        if repaired == messages:
            return None
        # 原位追加无法修复已夹在历史问答中的缺失结果，必须按顺序更新消息状态。
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]}

    async def awrap_tool_call(self, request, handler):
        try:
            return await handler(request)
        except Exception as exc:
            logger.warning("学习资料工具执行失败（%s）：%s", request.tool_call["name"], exc)
            # 让失败也产生匹配的 ToolMessage，避免整个图停在悬空的 tool_calls 上。
            return ToolMessage(
                content=(
                    "学习资料工具执行失败，本次没有取得检索结果。"
                    "可以重试一次；若仍失败，请告诉用户资料服务暂时不可用，稍后再试。"
                    "不要编造检索内容。"
                ),
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
                status="error",
            )
