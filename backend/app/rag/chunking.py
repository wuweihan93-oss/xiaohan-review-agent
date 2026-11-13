"""纯文本 → LangChain Document 切片；切分参数集中在 core/config.py。"""

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import RAG_CHUNK_OVERLAP, RAG_CHUNK_SIZE

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=RAG_CHUNK_SIZE,
    chunk_overlap=RAG_CHUNK_OVERLAP,
    separators=["\n## ", "\n### ", "\n\n", "\n", "。", "！", "？", " ", ""],
    add_start_index=True,
)


def prepare_chunks(text: str) -> list[Document]:
    if not text.strip():
        raise ValueError("文件中没有可读取的文字内容")
    return _splitter.create_documents([text])
