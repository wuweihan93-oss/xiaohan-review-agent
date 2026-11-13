"""一轮复习的业务流程：校验资料 → 调用 Agent → 保存历史 → 发出流事件。"""

import asyncio
import json
import logging
from typing import Any

import anyio
from fastapi import HTTPException

from app.agents.review import review, review_stream
from app.core.config import (
    DB_URL,
    SSE_HEARTBEAT_SECONDS,
    STREAM_CLEANUP_SECONDS,
    TAVILY_API_KEY,
)
from app.core.errors import friendly_error
from app.repositories.conversations import ConversationNotFound, begin_turn, finish_turn
from app.schemas.review import ChatRequest
from app.security.visitors import memory_thread_id
from app.services.conversations import check_access
from app.services.locks import conversation_locks
from app.services.materials import list_materials

logger = logging.getLogger(__name__)


async def _validate_chat(request: ChatRequest) -> None:
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="请输入复习问题")
    if not request.material_ids and not request.web_search_enabled:
        raise HTTPException(
            status_code=400, detail="请选择至少一份学习资料，或开启联网搜索"
        )
    if request.web_search_enabled and not TAVILY_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="联网搜索尚未配置，请在项目 .env 中填写 TAVILY_API_KEY，或关闭联网后继续复习资料",
        )
    known_ids = {material["id"] for material in await asyncio.to_thread(list_materials)}
    if any(material_id not in known_ids for material_id in request.material_ids):
        raise HTTPException(status_code=400, detail="所选资料已不存在，请重新选择")


def _review_options(request: ChatRequest, visitor_id: str) -> dict[str, Any]:
    return {
        **request.model_dump(),
        "question": request.question.strip(),
        "conversation_id": memory_thread_id(visitor_id, request.conversation_id),
    }


async def chat(
    request: ChatRequest, visitor_id: str, source_ip: str | None = None
) -> dict[str, Any]:
    await _validate_chat(request)
    await check_access(request.conversation_id, visitor_id, allow_missing=True)
    # 同一段对话按顺序处理，避免两轮问答交错写入 checkpoint 和历史记录。
    lock = conversation_locks.setdefault(
        memory_thread_id(visitor_id, request.conversation_id), asyncio.Lock()
    )
    async with lock:
        user_message = None
        if DB_URL:
            try:
                user_message = await begin_turn(
                    request.conversation_id,
                    request.question.strip(),
                    request.material_ids,
                    request.study_mode,
                    request.prompt,
                    web_search_enabled=request.web_search_enabled,
                    visitor_id=visitor_id,
                    client_ip=source_ip,
                )
            except ConversationNotFound:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail="保存复习问题失败，请检查数据库连接后重试。"
                ) from exc
        try:
            result = await review(**_review_options(request, visitor_id))
            result["conversation_id"] = request.conversation_id
        except Exception as exc:
            detail = friendly_error("小晗复习", exc)
            if DB_URL:
                try:
                    await finish_turn(
                        request.conversation_id,
                        f"这次复习没有完成：{detail}",
                        [],
                        [],
                        visitor_id=visitor_id,
                        failed=True,
                    )
                except Exception as save_error:
                    raise HTTPException(
                        status_code=503,
                        detail="复习服务暂时失败，且无法保存错误记录，请检查数据库连接。",
                    ) from save_error
            raise HTTPException(status_code=502, detail=detail) from exc
        if DB_URL:
            try:
                assistant_message = await finish_turn(
                    request.conversation_id,
                    result["answer"],
                    result["sources"],
                    result["trace"],
                    visitor_id=visitor_id,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="回答已生成，但保存历史记录失败，请稍后重试。",
                ) from exc
            result.update(
                user_message=user_message, assistant_message=assistant_message
            )
        return result


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _produce_review(
    request: ChatRequest,
    queue: asyncio.Queue,
    lock: asyncio.Lock,
    visitor_id: str,
    source_ip: str | None = None,
) -> None:
    """生成与落库共用会话锁；浏览器断开时保留已经生成的文字。"""
    async with lock:
        user_message = None
        answer = ""
        sources, trace = [], []
        saved = False
        final_save = None
        completed = False
        try:
            if DB_URL:
                user_message = await begin_turn(
                    request.conversation_id,
                    request.question.strip(),
                    request.material_ids,
                    request.study_mode,
                    request.prompt,
                    web_search_enabled=request.web_search_enabled,
                    visitor_id=visitor_id,
                    client_ip=source_ip,
                )
            queue.put_nowait(
                (
                    "start",
                    {
                        "conversation_id": request.conversation_id,
                        "user_message": user_message,
                    },
                )
            )
            async for item in review_stream(**_review_options(request, visitor_id)):
                event, data = item["event"], item["data"]
                if event == "token":
                    answer += data["text"]
                elif event == "reset":
                    answer = ""
                elif event == "state":
                    sources, trace = data["sources"], data["trace"]
                elif event == "result":
                    answer, sources, trace = (
                        data["answer"],
                        data["sources"],
                        data["trace"],
                    )
                    completed = True
                    # 保存放到流结束后处理，确保每一轮只写入一条完整回答。
                    continue
                queue.put_nowait((event, data))
            if not completed:
                raise RuntimeError("回答流意外结束，请重试当前问题。")
            assistant_message = None
            if DB_URL:
                final_save = asyncio.create_task(
                    finish_turn(
                        request.conversation_id,
                        answer,
                        sources,
                        trace,
                        visitor_id=visitor_id,
                    )
                )
                assistant_message = await asyncio.shield(final_save)
            saved = True
            queue.put_nowait(
                (
                    "done",
                    {
                        "answer": answer,
                        "sources": sources,
                        "trace": trace,
                        "conversation_id": request.conversation_id,
                        "user_message": user_message,
                        "assistant_message": assistant_message,
                    },
                )
            )
        except (Exception, asyncio.CancelledError) as exc:
            interrupted = isinstance(exc, asyncio.CancelledError)
            if interrupted and final_save is not None:
                # 回答生成完后即使浏览器离开，也要把这一次保存完成，避免写入两条回答。
                try:
                    await final_save
                    return
                except Exception:
                    logger.exception("保存已完成的流式回答失败")
            detail = "已停止生成" if interrupted else friendly_error("小晗复习", exc)
            if interrupted:
                content = f"{answer}\n\n[已停止生成]" if answer else "已停止生成。"
            else:
                content = f"{answer}\n\n" if answer else ""
                content += f"这次复习没有完成：{detail}"
            assistant_message = None
            if not saved and DB_URL and user_message:
                try:
                    assistant_message = await finish_turn(
                        request.conversation_id,
                        content,
                        sources,
                        trace,
                        visitor_id=visitor_id,
                        failed=True,
                    )
                except Exception:
                    logger.exception("保存流式回答的中断记录失败")
                    detail = "回答已中断，且保存历史记录失败，请检查数据库连接。"
            elif DB_URL and not user_message:
                detail = "保存复习问题失败，请检查数据库连接后重试。"
            queue.put_nowait(
                (
                    "error",
                    {
                        "detail": detail,
                        "content": content,
                        "sources": sources,
                        "trace": trace,
                        "assistant_message": assistant_message,
                    },
                )
            )
        finally:
            queue.put_nowait(None)


async def stream_events(
    request: ChatRequest, visitor_id: str, source_ip: str | None = None
):
    """先完成校验再返回迭代器，使错误仍可作为普通 HTTP 响应返回。"""
    await _validate_chat(request)
    await check_access(request.conversation_id, visitor_id, allow_missing=True)
    lock = conversation_locks.setdefault(
        memory_thread_id(visitor_id, request.conversation_id), asyncio.Lock()
    )
    if lock.locked():
        raise HTTPException(
            status_code=409, detail="这段对话正在生成回答，请稍后再试。"
        )

    async def events():
        queue = asyncio.Queue()
        producer = asyncio.create_task(
            _produce_review(request, queue, lock, visitor_id, source_ip)
        )
        try:
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=SSE_HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield ": 心跳\n\n"
                    continue
                if item is None:
                    break
                yield _sse(*item)
        finally:
            if not producer.done():
                producer.cancel()
            # StreamingResponse 在断连时取消生成器，屏蔽该取消以完成中断记录的落库。
            with anyio.CancelScope(shield=True):
                try:
                    await asyncio.wait_for(producer, timeout=STREAM_CLEANUP_SECONDS)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    logger.warning("等待流式回答清理超时或被取消")

    return events()
