"""LangChain Embeddings 接口：远程三项配置齐全时用百炼，否则使用本地模型。"""

import httpx
from fastembed import TextEmbedding
from langchain_core.embeddings import Embeddings

from app.core.config import (
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_MODEL,
    EMBEDDING_TIMEOUT_SECONDS,
    LOCAL_EMBEDDING_MODEL,
)


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
            timeout=EMBEDDING_TIMEOUT_SECONDS,
        )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[offset : offset + EMBEDDING_BATCH_SIZE]
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
                detail = (
                    error.get("message")
                    or payload.get("message")
                    or response.reason_phrase
                )
                raise RuntimeError(
                    f"百炼 Embedding 请求失败（HTTP {response.status_code}）：{detail}"
                )

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


_embedder: Embeddings | None = None


def get_embeddings() -> Embeddings:
    global _embedder
    if _embedder is None:
        configured = [EMBEDDING_MODEL, EMBEDDING_API_KEY, EMBEDDING_BASE_URL]
        if all(configured):
            _embedder = DashScopeEmbeddings()
        elif any(configured):
            raise ValueError(
                "Embedding API 配置不完整，请同时填写模型名、API Key 和 Base URL"
            )
        else:
            _embedder = LocalEmbeddings()
    return _embedder
