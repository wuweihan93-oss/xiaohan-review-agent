"""资料流程：加载 → 切分 → Embedding → Milvus → SQLite 登记。"""

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import (
    COLLECTION_NAME,
    RETRIEVAL_DEFAULT_RESULTS,
    RETRIEVAL_MAX_RESULTS,
    TUTORIAL_DIR,
    UPLOAD_DIR,
)
from app.rag.chunking import prepare_chunks
from app.rag.embeddings import get_embeddings
from app.rag.loaders import read_material
from app.rag.vector_store import get_vector_client
from app.repositories import materials as registry_store
from app.repositories.materials import (
    list_materials as list_materials,
)
from app.repositories.materials import (
    material_counts as material_counts,
)
from app.repositories.materials import (
    open_registry,
)


def _ingest(
    *,
    title: str,
    source: str,
    origin: str,
    text: str,
    stored_path: str | None = None,
) -> dict[str, Any]:
    # 1. 把长文切成 Document；Embedding 模型只接收切片文字。
    chunks = prepare_chunks(text)
    vectors = get_embeddings().embed_documents([chunk.page_content for chunk in chunks])
    dimension = len(vectors[0])

    # 2. 同一来源重新导入时替换登记记录，不向资料列表反复追加同一文件。
    registry = open_registry()
    existing = registry_store.find_by_source(registry, source)
    material_id = str(uuid.uuid4())
    client = get_vector_client(dimension)
    rows = [
        {
            "vector": vector,
            "material_id": material_id,
            "text": chunk.page_content[:11500],
            "source": source[:2000],
            "title": title[:500],
        }
        for chunk, vector in zip(chunks, vectors)
    ]
    try:
        # 3. 先写向量，再提交文件登记；失败时清理本次新向量并回滚登记。
        client.insert(collection_name=COLLECTION_NAME, data=rows)
        if existing:
            client.delete(
                collection_name=COLLECTION_NAME,
                filter=f'material_id == "{existing["id"]}"',
            )
        registry_store.save_material(
            registry,
            (
                material_id,
                title,
                source,
                origin,
                stored_path,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                len(chunks),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        registry.commit()
    except Exception:
        if client.has_collection(collection_name=COLLECTION_NAME):
            client.delete(
                collection_name=COLLECTION_NAME,
                filter=f'material_id == "{material_id}"',
            )
        registry.rollback()
        raise
    finally:
        registry.close()
    return {
        "id": material_id,
        "title": title,
        "source": source,
        "origin": origin,
        "chunk_count": len(chunks),
    }


def ingest_file(
    path: Path,
    *,
    title: str | None = None,
    source: str | None = None,
    origin: str = "upload",
) -> dict[str, Any]:
    return _ingest(
        title=title or path.stem,
        source=source or path.name,
        origin=origin,
        text=read_material(path),
        stored_path=path.name if origin == "upload" else None,
    )


def import_tutorial() -> dict[str, Any]:
    if not TUTORIAL_DIR.is_dir():
        raise ValueError(f"找不到教程目录：{TUTORIAL_DIR}")
    supported = {".ipynb", ".md", ".txt", ".py"}
    ignored = {
        ".git",
        ".venv",
        "__pycache__",
        ".ipynb_checkpoints",
        ".idea",
        "asset",
        "assets",
        "todo_workspace",
    }
    files = []
    for path in sorted(TUTORIAL_DIR.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in supported:
            continue
        parts = path.relative_to(TUTORIAL_DIR).parts
        if any(part.startswith(".") or part in ignored for part in parts):
            continue
        files.append(path)

    results = []
    for path in files:
        relative = path.relative_to(TUTORIAL_DIR).as_posix()
        try:
            results.append(
                ingest_file(
                    path,
                    title=path.stem,
                    source=f"教程/{relative}",
                    origin="tutorial",
                )
            )
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
    return {
        "imported": len(results),
        "chunks": sum(item["chunk_count"] for item in results),
        "materials": results,
    }


def search_materials(
    query: str, material_ids: list[str], limit: int = RETRIEVAL_DEFAULT_RESULTS
) -> list[dict[str, Any]]:
    if not material_ids:
        return []
    safe_ids = [item for item in material_ids if re.fullmatch(r"[0-9a-f-]{36}", item)]
    if not safe_ids:
        return []
    quoted_ids = ", ".join(f'"{item}"' for item in safe_ids)
    query_vector = get_embeddings().embed_query(query)
    result = get_vector_client(len(query_vector)).search(
        collection_name=COLLECTION_NAME,
        data=[query_vector],
        filter=f"material_id in [{quoted_ids}]",
        limit=max(1, min(limit, RETRIEVAL_MAX_RESULTS)),
        output_fields=["material_id", "text", "source", "title"],
        search_params={"metric_type": "COSINE", "params": {}},
    )
    return [
        {
            "material_id": hit.get("entity", {}).get("material_id", ""),
            "text": hit.get("entity", {}).get("text", ""),
            "source": hit.get("entity", {}).get("source", ""),
            "title": hit.get("entity", {}).get("title", ""),
            "score": round(float(hit.get("distance", 0)), 4),
        }
        for hit in (result[0] if result else [])
    ]


def delete_material(material_id: str) -> bool:
    registry = open_registry()
    try:
        row = registry_store.find_by_id(registry, material_id)
        if not row:
            return False
        dimension = len(get_embeddings().embed_query("删除资料"))
        get_vector_client(dimension).delete(
            collection_name=COLLECTION_NAME,
            filter=f'material_id == "{material_id}"',
        )
        registry_store.remove_material(registry, material_id)
        registry.commit()
        stored_path = row["stored_path"]
    finally:
        registry.close()
    if stored_path:
        # 兼容旧数据库中的 Mac 绝对路径，迁移后始终在当前上传目录中定位文件。
        path = (UPLOAD_DIR / Path(stored_path).name).resolve()
        if path.parent == UPLOAD_DIR.resolve() and path.exists():
            path.unlink()
    return True
