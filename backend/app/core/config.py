"""集中读取项目 .env，并提供按用途分组的配置。

部署时改 .env，业务代码只导入这里的值。没有填写的可选参数使用原来的默认值。
路径始终以项目目录为基准，不依赖启动命令所在的目录。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 一、路径与环境。迁移配置文件时不能改变已有 data / uploads 的位置。
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
UPLOAD_DIR = BACKEND_DIR / "uploads"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(PROJECT_DIR / ".env", override=True)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def _integer(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"配置 {name} 必须是整数") from exc
    if value < minimum:
        raise ValueError(f"配置 {name} 不能小于 {minimum}")
    return value


def _csv(name: str, default: str = "") -> list[str]:
    return [
        value.strip() for value in os.getenv(name, default).split(",") if value.strip()
    ]


TUTORIAL_DIR = Path(
    os.getenv("TUTORIAL_DIR", "/Users/williams/Conda/langchain_tutorial")
).expanduser()
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
CORS_ORIGINS = _csv("CORS_ORIGINS", "http://localhost:5174,http://127.0.0.1:5174")
MAX_UPLOAD_BYTES = _integer("MAX_UPLOAD_MB", 25) * 1024 * 1024

# 二、对话模型。
CHAT_MODEL = os.getenv("REVIEW_LLM_MODEL", "deepseek-chat")
CHAT_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
CHAT_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "")
try:
    CHAT_TEMPERATURE = float(os.getenv("CHAT_TEMPERATURE", "0.4"))
except ValueError as exc:
    raise ValueError("配置 CHAT_TEMPERATURE 必须是数字") from exc
if not 0 <= CHAT_TEMPERATURE <= 2:
    raise ValueError("配置 CHAT_TEMPERATURE 必须在 0 至 2 之间")

# 三、RAG：切分、Embedding 与向量存储。
RAG_CHUNK_SIZE = _integer("RAG_CHUNK_SIZE", 700)
RAG_CHUNK_OVERLAP = _integer("RAG_CHUNK_OVERLAP", 100, minimum=0)
if RAG_CHUNK_OVERLAP >= RAG_CHUNK_SIZE:
    raise ValueError("RAG_CHUNK_OVERLAP 必须小于 RAG_CHUNK_SIZE")
RETRIEVAL_DEFAULT_RESULTS = 5
RETRIEVAL_MAX_RESULTS = 8
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")
LOCAL_EMBEDDING_MODEL = os.getenv(
    "LOCAL_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBEDDING_BATCH_SIZE = 8
EMBEDDING_TIMEOUT_SECONDS = 90
_milvus_uri = os.getenv("XIAOHAN_MILVUS_URI", str(DATA_DIR / "xiaohan.db")).strip()
if "://" not in _milvus_uri:
    _milvus_path = Path(_milvus_uri).expanduser()
    if not _milvus_path.is_absolute():
        _milvus_path = PROJECT_DIR / _milvus_path
    _milvus_uri = str(_milvus_path.resolve())
MILVUS_URI = _milvus_uri
COLLECTION_NAME = "study_chunks"
# 本地 Milvus 沿用原来同目录的登记库；远程 Milvus 的登记库放在本项目 data 下。
REGISTRY_PATH = (
    DATA_DIR if "://" in MILVUS_URI else Path(MILVUS_URI).parent
) / "materials.sqlite3"
REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

# 四、页面历史与 LangGraph 记忆共同使用 PostgreSQL，但保存不同的数据。
DB_URL = os.getenv("DB_URL", "").strip()
DB_POOL_MAX_SIZE = 4
DB_CONNECT_TIMEOUT_SECONDS = 10
DB_POOL_TIMEOUT_SECONDS = 15
SSE_HEARTBEAT_SECONDS = 15
STREAM_CLEANUP_SECONDS = 15

# 五、联网搜索。额度、超时和摘要截断都集中在此处。
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
WEB_SEARCH_DEPTH = "basic"
WEB_SEARCH_MAX_RESULTS = 5
WEB_SEARCH_QUERY_MAX_LENGTH = 500
WEB_SEARCH_TEXT_MAX_LENGTH = 1800
WEB_SEARCH_TIMEOUT_SECONDS = 25
WEB_SEARCH_CONNECT_TIMEOUT_SECONDS = 8
MCP_RETRIEVAL_TIMEOUT_SECONDS = 60
MCP_LIST_TIMEOUT_SECONDS = 15

# 六、后台与反向代理。密钥类配置仍只放在 .env。
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
ADMIN_SESSION_SECONDS = 8 * 60 * 60
ADMIN_LOGIN_WINDOW_SECONDS = 60
ADMIN_LOGIN_ATTEMPTS_PER_IP = 5
ADMIN_LOGIN_ATTEMPTS_GLOBAL = 30
TRUSTED_PROXY_HOSTS = _csv("TRUSTED_PROXY_HOSTS")
TRUSTED_PROXY_CIDRS = _csv("TRUSTED_PROXY_CIDRS")
