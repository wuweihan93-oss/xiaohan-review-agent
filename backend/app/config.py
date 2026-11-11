import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
UPLOAD_DIR = BACKEND_DIR / "uploads"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_DIR / ".env", override=True)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

TUTORIAL_DIR = Path(
    os.getenv("TUTORIAL_DIR", "/Users/williams/Conda/langchain_tutorial")
).expanduser()
_milvus_uri = os.getenv("XIAOHAN_MILVUS_URI", str(DATA_DIR / "xiaohan.db")).strip()
if "://" not in _milvus_uri:
    _milvus_path = Path(_milvus_uri).expanduser()
    if not _milvus_path.is_absolute():
        _milvus_path = PROJECT_DIR / _milvus_path
    _milvus_uri = str(_milvus_path.resolve())
MILVUS_URI = _milvus_uri
COLLECTION_NAME = "study_chunks"
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")
LOCAL_EMBEDDING_MODEL = os.getenv(
    "LOCAL_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
CHAT_MODEL = os.getenv("REVIEW_LLM_MODEL", "deepseek-chat")
CHAT_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
CHAT_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "")
DB_URL = os.getenv("DB_URL", "").strip()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

DEFAULT_STUDY_PROMPT = """你叫小晗，是用户的学习搭子。你要帮助用户复习已经学过的 LangChain、Prompt、Agent、RAG、Milvus 和 MCP。
默认正常聊天；涉及学习资料时检索笔记，需要最新信息时联网；只有选择抽查模式才逐题提问。有选定学习资料时，回答前先调用 search_study_materials；是否可以补充互联网资料，以本轮检索规则为准。
带我复盘时：先用通俗中文说明概念，再连到笔记中的代码流程，最后提出一个简短问题检查理解；一次只问一个问题。
抽查我时：每次只出一道题，等待用户回答；先判断并解释，再根据表现继续下一题，不要一次公布全部答案。
引用资料时说出文件名。笔记没有涉及的内容要明确说明，不要假装是用户学过的内容。"""
