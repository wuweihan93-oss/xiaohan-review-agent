import asyncio
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from weakref import WeakValueDictionary

import anyio
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent import checkpointer_context, delete_memory, review, review_stream, set_checkpointer
from app.admin import AdminHeadersMiddleware, router as admin_router
from app.client_info import TrustedProxyMiddleware, client_ip
from app.config import CHAT_API_KEY, CHAT_MODEL, DB_URL, EMBEDDING_MODEL, TAVILY_API_KEY, TUTORIAL_DIR, UPLOAD_DIR
from app.conversations import (
    ConversationNotFound,
    backfill_conversations,
    begin_turn,
    check_conversation_access,
    delete_conversation,
    finish_turn,
    get_conversation,
    history_context,
    list_conversations,
)
from app.materials import (
    delete_material,
    import_tutorial,
    ingest_file,
    list_materials,
    material_counts,
    search_materials,
)
from app.visitors import VisitorMiddleware, memory_thread_id

_conversation_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with history_context(), checkpointer_context() as checkpointer:
        await backfill_conversations(checkpointer)
        set_checkpointer(checkpointer)
        try:
            yield
        finally:
            set_checkpointer(None)


app = FastAPI(title="小晗带你复习", version="0.1.0", lifespan=lifespan)
app.add_middleware(VisitorMiddleware)
app.add_middleware(AdminHeadersMiddleware)
app.add_middleware(TrustedProxyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(admin_router)


async def current_visitor(request: Request) -> str:
    return request.state.visitor_id


@app.get("/api/visitor")
async def visitor_identity(visitor_id: str = Depends(current_visitor)) -> dict[str, str]:
    # 摘要仅用于区分前端缓存；不能用它代替 Cookie 访问其他人的会话。
    return {"cache_scope": visitor_id}


@app.exception_handler(ConversationNotFound)
async def conversation_not_found(request, exc) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "找不到这段复习对话"})


async def _check_access(conversation_id: str, visitor_id: str, *, allow_missing: bool = False) -> None:
    if not DB_URL:
        return
    try:
        await check_conversation_access(conversation_id, visitor_id, allow_missing=allow_missing)
    except ConversationNotFound:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="读取复习对话失败，请稍后重试。") from exc


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": "请求内容有误，请检查输入后重试。"})


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1500)
    material_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=8)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=5000)
    conversation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    material_ids: list[str] = Field(default_factory=list)
    study_mode: Literal["coach", "quiz"] = "coach"
    prompt: str | None = None
    web_search_enabled: bool = False


def _friendly_error(action: str, exc: Exception) -> str:
    raw = str(exc).strip()
    lowered = raw.lower()
    if "tool_calls" in lowered and "tool" in lowered:
        return f"{action}失败：对话中的工具记录不完整，请重试当前问题；系统会自动修复中断的工具记录。"
    if "api key" in lowered or "authentication" in lowered or "unauthorized" in lowered:
        reason = "模型认证失败，请检查项目 .env 中的 DEEPSEEK_API_KEY。"
    elif "milvus" in lowered or "collection" in lowered:
        reason = "向量数据库操作失败，请确认 Milvus Lite 索引状态。"
    elif "timeout" in lowered or "timed out" in lowered:
        reason = "模型请求超时，请检查网络后重试。"
    elif "429" in lowered or "rate limit" in lowered:
        reason = "模型服务请求过于频繁，请稍后重试。"
    elif isinstance(exc, ValueError):
        return raw
    else:
        reason = "请检查文件格式和模型配置后重试。"
    return f"{action}失败：{reason} 技术详情：{raw[:300]}".strip()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    counts = await asyncio.to_thread(material_counts)
    return {
        "ok": True,
        "model_configured": bool(CHAT_API_KEY),
        "model": CHAT_MODEL,
        "embedding": EMBEDDING_MODEL,
        "memory_backend": "PostgreSQL" if DB_URL else "InMemorySaver",
        "history_enabled": bool(DB_URL),
        "web_search_configured": bool(TAVILY_API_KEY),
        "tutorial_dir": str(TUTORIAL_DIR),
        "counts": counts,
    }


@app.get("/api/conversations")
async def conversation_list(
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
    visitor_id: str = Depends(current_visitor),
) -> dict[str, Any]:
    try:
        return await list_conversations(visitor_id, limit, offset)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="读取历史复习失败，请稍后重试。") from exc


@app.get("/api/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str, visitor_id: str = Depends(current_visitor)) -> dict[str, Any]:
    try:
        conversation = await get_conversation(conversation_id, visitor_id) if DB_URL else None
    except Exception as exc:
        raise HTTPException(status_code=503, detail="读取复习对话失败，请稍后重试。") from exc
    if conversation is None:
        raise HTTPException(status_code=404, detail="找不到这段复习对话")
    return {"conversation": conversation}


@app.delete("/api/conversations/{conversation_id}")
async def remove_conversation(conversation_id: str, visitor_id: str = Depends(current_visitor)) -> dict[str, bool]:
    await _check_access(conversation_id, visitor_id)
    lock = _conversation_locks.setdefault(memory_thread_id(visitor_id, conversation_id), asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="这段对话正在生成回答，请停止生成后再删除。")
    async with lock:
        try:
            deleted = await delete_conversation(conversation_id, visitor_id, delete_memory) if DB_URL else False
        except Exception as exc:
            raise HTTPException(status_code=503, detail="删除会话失败，请检查数据库连接后重试。") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="找不到这段复习对话")
    return {"ok": True}


@app.get("/api/materials")
async def get_materials() -> dict[str, Any]:
    return {"materials": await asyncio.to_thread(list_materials)}


@app.post("/api/materials/import-tutorial")
async def import_tutorial_materials() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(import_tutorial)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error("导入教程资料", exc)) from exc


@app.post("/api/materials/upload")
async def upload_material(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = Path(file.filename or "学习资料").name
    safe_name = re.sub(r"[^\w.\-\u3400-\u9fff ]", "_", filename).strip()[:180]
    if not safe_name:
        raise HTTPException(status_code=400, detail="文件名无效")
    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="单个文件不能超过 25 MB")
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"
    target.write_bytes(data)
    try:
        material = await asyncio.to_thread(ingest_file, target, title=Path(safe_name).stem, source=f"上传/{safe_name}")
        return {"material": material}
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=_friendly_error("处理学习资料", exc)) from exc
    finally:
        await file.close()


@app.delete("/api/materials/{material_id}")
async def remove_material(material_id: str) -> dict[str, bool]:
    try:
        deleted = await asyncio.to_thread(delete_material, material_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="找不到这份学习资料")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error("删除学习资料", exc)) from exc


@app.post("/api/retrieval/search")
async def retrieval_search(request: SearchRequest) -> dict[str, Any]:
    try:
        results = await asyncio.to_thread(
            search_materials,
            request.query,
            request.material_ids,
            request.limit,
        )
        return {"results": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_friendly_error("检索学习资料", exc)) from exc


async def _validate_chat(request: ChatRequest) -> None:
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="请输入复习问题")
    if not request.material_ids and not request.web_search_enabled:
        raise HTTPException(status_code=400, detail="请选择至少一份学习资料，或开启联网搜索")
    if request.web_search_enabled and not TAVILY_API_KEY:
        raise HTTPException(status_code=400, detail="联网搜索尚未配置，请在项目 .env 中填写 TAVILY_API_KEY，或关闭联网后继续复习资料")
    known_ids = {material["id"] for material in await asyncio.to_thread(list_materials)}
    if any(material_id not in known_ids for material_id in request.material_ids):
        raise HTTPException(status_code=400, detail="所选资料已不存在，请重新选择")


def _review_options(request: ChatRequest, visitor_id: str) -> dict[str, Any]:
    return {
        **request.model_dump(), "question": request.question.strip(),
        "conversation_id": memory_thread_id(visitor_id, request.conversation_id),
    }


@app.post("/api/review")
async def chat(request: ChatRequest, visitor_id: str = Depends(current_visitor), source_ip: str | None = Depends(client_ip)) -> dict[str, Any]:
    await _validate_chat(request)
    await _check_access(request.conversation_id, visitor_id, allow_missing=True)
    # 同一段对话按顺序处理，避免两轮问答交错写入 checkpoint 和历史记录。
    lock = _conversation_locks.setdefault(memory_thread_id(visitor_id, request.conversation_id), asyncio.Lock())
    async with lock:
        user_message = None
        if DB_URL:
            try:
                user_message = await begin_turn(
                    request.conversation_id, request.question.strip(), request.material_ids,
                    request.study_mode, request.prompt,
                    web_search_enabled=request.web_search_enabled, visitor_id=visitor_id, client_ip=source_ip,
                )
            except ConversationNotFound:
                raise
            except Exception as exc:
                raise HTTPException(status_code=503, detail="保存复习问题失败，请检查数据库连接后重试。") from exc
        try:
            result = await review(**_review_options(request, visitor_id))
            result["conversation_id"] = request.conversation_id
        except Exception as exc:
            detail = _friendly_error("小晗复习", exc)
            if DB_URL:
                try:
                    await finish_turn(request.conversation_id, f"这次复习没有完成：{detail}", [], [], visitor_id=visitor_id, failed=True)
                except Exception as save_error:
                    raise HTTPException(status_code=503, detail="复习服务暂时失败，且无法保存错误记录，请检查数据库连接。") from save_error
            raise HTTPException(status_code=502, detail=detail) from exc
        if DB_URL:
            try:
                assistant_message = await finish_turn(
                    request.conversation_id, result["answer"], result["sources"], result["trace"], visitor_id=visitor_id
                )
            except Exception as exc:
                raise HTTPException(status_code=503, detail="回答已生成，但保存历史记录失败，请稍后重试。") from exc
            result.update(user_message=user_message, assistant_message=assistant_message)
        return result


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _produce_review(request: ChatRequest, queue: asyncio.Queue, lock: asyncio.Lock, visitor_id: str, source_ip: str | None = None) -> None:
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
                    request.conversation_id, request.question.strip(), request.material_ids,
                    request.study_mode, request.prompt, web_search_enabled=request.web_search_enabled, visitor_id=visitor_id, client_ip=source_ip,
                )
            queue.put_nowait(("start", {"conversation_id": request.conversation_id, "user_message": user_message}))
            async for item in review_stream(**_review_options(request, visitor_id)):
                event, data = item["event"], item["data"]
                if event == "token":
                    answer += data["text"]
                elif event == "reset":
                    answer = ""
                elif event == "state":
                    sources, trace = data["sources"], data["trace"]
                elif event == "result":
                    answer, sources, trace = data["answer"], data["sources"], data["trace"]
                    completed = True
                    # 保存放到流结束后处理，确保每一轮只写入一条完整回答。
                    continue
                queue.put_nowait((event, data))
            if not completed:
                raise RuntimeError("回答流意外结束，请重试当前问题。")
            assistant_message = None
            if DB_URL:
                final_save = asyncio.create_task(finish_turn(request.conversation_id, answer, sources, trace, visitor_id=visitor_id))
                assistant_message = await asyncio.shield(final_save)
            saved = True
            queue.put_nowait(("done", {
                "answer": answer, "sources": sources, "trace": trace,
                "conversation_id": request.conversation_id, "user_message": user_message,
                "assistant_message": assistant_message,
            }))
        except (Exception, asyncio.CancelledError) as exc:
            interrupted = isinstance(exc, asyncio.CancelledError)
            if interrupted and final_save is not None:
                # 回答生成完后即使浏览器离开，也要把这一次保存完成，避免写入两条回答。
                try:
                    await final_save
                    return
                except Exception:
                    logger.exception("保存已完成的流式回答失败")
            detail = "已停止生成" if interrupted else _friendly_error("小晗复习", exc)
            if interrupted:
                content = f"{answer}\n\n[已停止生成]" if answer else "已停止生成。"
            else:
                content = f"{answer}\n\n" if answer else ""
                content += f"这次复习没有完成：{detail}"
            assistant_message = None
            if not saved and DB_URL and user_message:
                try:
                    assistant_message = await finish_turn(request.conversation_id, content, sources, trace, visitor_id=visitor_id, failed=True)
                except Exception:
                    logger.exception("保存流式回答的中断记录失败")
                    detail = "回答已中断，且保存历史记录失败，请检查数据库连接。"
            elif DB_URL and not user_message:
                detail = "保存复习问题失败，请检查数据库连接后重试。"
            queue.put_nowait(("error", {
                "detail": detail, "content": content, "sources": sources, "trace": trace,
                "assistant_message": assistant_message,
            }))
        finally:
            queue.put_nowait(None)


@app.post("/api/review/stream")
async def chat_stream(request: ChatRequest, visitor_id: str = Depends(current_visitor), source_ip: str | None = Depends(client_ip)) -> StreamingResponse:
    await _validate_chat(request)
    await _check_access(request.conversation_id, visitor_id, allow_missing=True)
    lock = _conversation_locks.setdefault(memory_thread_id(visitor_id, request.conversation_id), asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="这段对话正在生成回答，请稍后再试。")

    async def events():
        queue = asyncio.Queue()
        producer = asyncio.create_task(_produce_review(request, queue, lock, visitor_id, source_ip))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
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
                    await asyncio.wait_for(producer, timeout=15)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    logger.warning("等待流式回答清理超时或被取消")

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
