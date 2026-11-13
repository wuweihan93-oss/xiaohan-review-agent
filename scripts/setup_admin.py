"""本机后台配置入口；容器与本机共用同一份账号校验和 .env 写入逻辑。"""

from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "backend"))
from app.admin_setup import main, save_credentials as save_credentials

if __name__ == "__main__":
    main()
