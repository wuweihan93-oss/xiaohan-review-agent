import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import (
    COLLECTION_NAME,
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL,
    LOCAL_EMBEDDING_MODEL,
    MILVUS_URI,
    TUTORIAL_DIR,
    UPLOAD_DIR,
)

from fastembed import TextEmbedding
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pymilvus import DataType, MilvusClient
from pypdf import PdfReader
from docx import Document as WordDocument


class LocalEmbeddings(Embeddings):
    """通过本地多语言模型生成向量，不向第三方发送学习资料。"""

    def __init__(self) -> None:
        self._model = TextEmbedding(model_name=LOCAL_EMBEDDING_MODEL)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(self._model.query_embed([text])).tolist()


class DashScopeEmbeddings(Embeddings):
    """通过百炼多模态 Embedding API 为文本生成向量。"""

    def __init__(self) -> None:
        self._url = (
            EMBEDDING_BASE_URL.rstrip("/")
            + "/services/embeddings/multimodal-embedding/multimodal-embedding"
        )
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {EMBEDDING_API_KEY}"},
            timeout=90,
        )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), 8):
            batch = texts[offset : offset + 8]
            response = self._client.post(
                self._url,
                json={
                    "model": EMBEDDING_MODEL,
                    "input": {"contents": [{"text": text} for text in batch]},
                },
            )
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if response.is_error:
                error = payload.get("error", {}) if isinstance(payload, dict) else {}
                detail = error.get("message") or payload.get("message") or response.reason_phrase
                raise RuntimeError(f"百炼 Embedding 请求失败（HTTP {response.status_code}）：{detail}")

            items = payload.get("output", {}).get("embeddings", [])
            indexed: list[list[float] | None] = [None] * len(batch)
            for fallback_index, item in enumerate(items):
                index = int(item.get("index", fallback_index))
                if 0 <= index < len(indexed):
                    indexed[index] = item.get("embedding")
            if any(vector is None for vector in indexed):
                raise RuntimeError("百炼 Embedding 返回的向量数量与请求文本数量不一致")
            vectors.extend(indexed)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


_client: MilvusClient | None = None
_embedder: Embeddings | None = None
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=700,
    chunk_overlap=100,
    separators=["\n## ", "\n### ", "\n\n", "\n", "。", "！", "？", " ", ""],
    add_start_index=True,
)
_database = Path(MILVUS_URI).expanduser()
if not _database.is_absolute():
    _database = Path(__file__).resolve().parents[1].parent / _database
REGISTRY_PATH = _database.parent / "materials.sqlite3"
REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)


def _registry() -> sqlite3.Connection:
    connection = sqlite3.connect(REGISTRY_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS materials (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT NOT NULL UNIQUE,
            origin TEXT NOT NULL,
            stored_path TEXT,
            content_hash TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    connection.commit()
    return connection


def _milvus(dimension: int) -> MilvusClient:
    global _client
    if _client is None:
        _client = MilvusClient(uri=MILVUS_URI)
        if _client.has_collection(collection_name=COLLECTION_NAME):
            description = _client.describe_collection(collection_name=COLLECTION_NAME)
            vector_field = next(
                (field for field in description.get("fields", []) if field["name"] == "vector"),
                None,
            )
            existing_dimension = (
                int(vector_field.get("params", {}).get("dim", 0)) if vector_field else 0
            )
            if existing_dimension != dimension:
                stats = _client.get_collection_stats(collection_name=COLLECTION_NAME)
                if int(stats.get("row_count", 0)):
                    raise ValueError(
                        "Embedding 模型的向量维度与现有资料库不一致，请清空旧向量库后重新导入资料"
                    )
                _client.drop_collection(collection_name=COLLECTION_NAME)
        if not _client.has_collection(collection_name=COLLECTION_NAME):
            schema = _client.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
            schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension)
            schema.add_field(field_name="material_id", datatype=DataType.VARCHAR, max_length=64)
            schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=12000)
            schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=2048)
            schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=512)
            index_params = _client.prepare_index_params()
            index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
            _client.create_collection(
                collection_name=COLLECTION_NAME,
                schema=schema,
                index_params=index_params,
            )
        _client.load_collection(collection_name=COLLECTION_NAME)
    return _client


def _embeddings() -> Embeddings:
    global _embedder
    if _embedder is None:
        configured = [EMBEDDING_MODEL, EMBEDDING_API_KEY, EMBEDDING_BASE_URL]
        if all(configured):
            _embedder = DashScopeEmbeddings()
        elif any(configured):
            raise ValueError("Embedding API 配置不完整，请同时填写模型名、API Key 和 Base URL")
        else:
            _embedder = LocalEmbeddings()
    return _embedder


def _decode_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or raw.count(b"\x00") > len(raw) // 10:
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _notebook_text(path: Path) -> str:
    notebook = json.loads(_decode_text(path))
    sections = []
    for number, cell in enumerate(notebook.get("cells", []), start=1):
        if cell.get("cell_type") not in {"markdown", "code"}:
            continue
        source = cell.get("source", "")
        text = "".join(source) if isinstance(source, list) else str(source)
        if not text.strip():
            continue
        kind = "代码单元" if cell.get("cell_type") == "code" else "笔记"
        sections.append(f"### {kind} {number}\n{text.strip()}")
    return "\n\n".join(sections)


def read_material(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".py", ".json", ".csv"}:
        return _decode_text(path)
    if suffix == ".ipynb":
        return _notebook_text(path)
    if suffix == ".pdf":
        reader = PdfReader(path)
        return "\n\n".join(
            f"### 第 {index} 页\n{page.extract_text() or ''}"
            for index, page in enumerate(reader.pages, start=1)
        )
    if suffix == ".docx":
        document = WordDocument(path)
        paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            paragraphs.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows)
        return "\n".join(paragraphs)
    raise ValueError("暂不支持此文件类型。请上传 PDF、DOCX、Markdown、TXT、Python、JSON、CSV 或 notebook 文件。")


def _prepare_chunks(text: str) -> list[Document]:
    if not text.strip():
        raise ValueError("文件中没有可读取的文字内容")
    return _splitter.create_documents([text])


def _ingest(
    *,
    title: str,
    source: str,
    origin: str,
    text: str,
    stored_path: str | None = None,
) -> dict[str, Any]:
    chunks = _prepare_chunks(text)
    vectors = _embeddings().embed_documents([chunk.page_content for chunk in chunks])
    dimension = len(vectors[0])

    registry = _registry()
    existing = registry.execute("SELECT id FROM materials WHERE source = ?", (source,)).fetchone()
    material_id = str(uuid.uuid4())
    client = _milvus(dimension)
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
        client.insert(collection_name=COLLECTION_NAME, data=rows)
        if existing:
            client.delete(
                collection_name=COLLECTION_NAME,
                filter=f'material_id == "{existing["id"]}"',
            )
        registry.execute(
            """INSERT INTO materials (id, title, source, origin, stored_path, content_hash, chunk_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
                 id=excluded.id, title=excluded.title, origin=excluded.origin,
                 stored_path=excluded.stored_path, content_hash=excluded.content_hash,
                 chunk_count=excluded.chunk_count, created_at=excluded.created_at""",
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
            client.delete(collection_name=COLLECTION_NAME, filter=f'material_id == "{material_id}"')
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


def ingest_file(path: Path, *, title: str | None = None, source: str | None = None, origin: str = "upload") -> dict[str, Any]:
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
    ignored = {".git", ".venv", "__pycache__", ".ipynb_checkpoints", ".idea", "asset", "assets", "todo_workspace"}
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


def list_materials() -> list[dict[str, Any]]:
    registry = _registry()
    try:
        rows = registry.execute(
            "SELECT id, title, source, origin, chunk_count, created_at FROM materials ORDER BY origin, title COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        registry.close()


def search_materials(query: str, material_ids: list[str], limit: int = 5) -> list[dict[str, Any]]:
    if not material_ids:
        return []
    safe_ids = [item for item in material_ids if re.fullmatch(r"[0-9a-f-]{36}", item)]
    if not safe_ids:
        return []
    quoted_ids = ", ".join(f'"{item}"' for item in safe_ids)
    query_vector = _embeddings().embed_query(query)
    result = _milvus(len(query_vector)).search(
        collection_name=COLLECTION_NAME,
        data=[query_vector],
        filter=f"material_id in [{quoted_ids}]",
        limit=max(1, min(limit, 8)),
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
    registry = _registry()
    try:
        row = registry.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
        if not row:
            return False
        dimension = len(_embeddings().embed_query("删除资料"))
        _milvus(dimension).delete(
            collection_name=COLLECTION_NAME,
            filter=f'material_id == "{material_id}"',
        )
        registry.execute("DELETE FROM materials WHERE id = ?", (material_id,))
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


def material_counts() -> dict[str, int]:
    registry = _registry()
    try:
        rows = registry.execute("SELECT origin, COUNT(*) AS count FROM materials GROUP BY origin").fetchall()
        result = {"tutorial": 0, "upload": 0}
        for row in rows:
            if row["origin"] in result:
                result[row["origin"]] = int(row["count"])
        return result
    finally:
        registry.close()
