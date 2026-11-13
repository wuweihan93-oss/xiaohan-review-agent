"""资料上传、导入、删除及 MCP 调用的检索接口。"""

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import MAX_UPLOAD_BYTES, UPLOAD_DIR
from app.core.errors import friendly_error
from app.schemas.review import SearchRequest
from app.services.materials import (
    delete_material,
    import_tutorial,
    ingest_file,
    list_materials,
    search_materials,
)

router = APIRouter(prefix="/api", tags=["学习资料"])


@router.get("/materials")
async def get_materials() -> dict[str, Any]:
    return {"materials": await asyncio.to_thread(list_materials)}


@router.post("/materials/import-tutorial")
async def import_tutorial_materials() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(import_tutorial)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=friendly_error("导入教程资料", exc)
        ) from exc


@router.post("/materials/upload")
async def upload_material(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = Path(file.filename or "学习资料").name
    safe_name = re.sub(r"[^\w.\-\u3400-\u9fff ]", "_", filename).strip()[:180]
    if not safe_name:
        raise HTTPException(status_code=400, detail="文件名无效")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"单个文件不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"
    target.write_bytes(data)
    try:
        material = await asyncio.to_thread(
            ingest_file, target, title=Path(safe_name).stem, source=f"上传/{safe_name}"
        )
        return {"material": material}
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500, detail=friendly_error("处理学习资料", exc)
        ) from exc
    finally:
        await file.close()


@router.delete("/materials/{material_id}")
async def remove_material(material_id: str) -> dict[str, bool]:
    try:
        deleted = await asyncio.to_thread(delete_material, material_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="找不到这份学习资料")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=friendly_error("删除学习资料", exc)
        ) from exc


@router.post("/retrieval/search")
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
        raise HTTPException(
            status_code=500, detail=friendly_error("检索学习资料", exc)
        ) from exc
