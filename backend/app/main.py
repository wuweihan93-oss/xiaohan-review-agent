import asyncio
import json
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from weakref import WeakValueDictionary

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agent import checkpointer_context, review, set_checkpointer
from app.config import CHAT_API_KEY, CHAT_MODEL, DB_URL, EMBEDDING_MODEL, TUTORIAL_DIR, UPLOAD_DIR
from app.conversations import (
    backfill_conversations,
    begin_turn,
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

_conversation_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    material_ids: list[str] = Field(min_length=1)
    study_mode: Literal["coach", "quiz"] = "coach"
    prompt: str | None = None


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
        "tutorial_dir": str(TUTORIAL_DIR),
        "counts": counts,
    }


@app.get("/api/conversations")
async def conversation_list(
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> dict[str, Any]:
    try:
        return await list_conversations(limit, offset)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="读取历史复习失败，请稍后重试。") from exc


@app.get("/api/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str) -> dict[str, Any]:
    try:
        conversation = await get_conversation(conversation_id) if DB_URL else None
    except Exception as exc:
        raise HTTPException(status_code=503, detail="读取复习对话失败，请稍后重试。") from exc
    if conversation is None:
        raise HTTPException(status_code=404, detail="找不到这段复习对话")
    return {"conversation": conversation}


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


@app.post("/api/review")
async def chat(request: ChatRequest) -> dict[str, Any]:
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="请输入复习问题")
    known_ids = {material["id"] for material in await asyncio.to_thread(list_materials)}
    if any(material_id not in known_ids for material_id in request.material_ids):
        raise HTTPException(status_code=400, detail="所选资料已不存在，请重新选择")
    # 同一段对话按顺序处理，避免两轮问答交错写入 checkpoint 和历史记录。
    lock = _conversation_locks.setdefault(request.conversation_id, asyncio.Lock())
    async with lock:
        user_message = None
        if DB_URL:
            try:
                user_message = await begin_turn(
                    request.conversation_id, request.question.strip(), request.material_ids,
                    request.study_mode, request.prompt,
                )
            except Exception as exc:
                raise HTTPException(status_code=503, detail="保存复习问题失败，请检查数据库连接后重试。") from exc
        try:
            result = await review(
                question=request.question.strip(),
                conversation_id=request.conversation_id,
                material_ids=request.material_ids,
                study_mode=request.study_mode,
                prompt=request.prompt,
            )
        except Exception as exc:
            detail = _friendly_error("小晗复习", exc)
            if DB_URL:
                try:
                    await finish_turn(request.conversation_id, f"这次复习没有完成：{detail}", [], [], failed=True)
                except Exception as save_error:
                    raise HTTPException(status_code=503, detail="复习服务暂时失败，且无法保存错误记录，请检查数据库连接。") from save_error
            raise HTTPException(status_code=502, detail=detail) from exc
        if DB_URL:
            try:
                assistant_message = await finish_turn(
                    request.conversation_id, result["answer"], result["sources"], result["trace"]
                )
            except Exception as exc:
                raise HTTPException(status_code=503, detail="回答已生成，但保存历史记录失败，请稍后重试。") from exc
            result.update(user_message=user_message, assistant_message=assistant_message)
        return result
