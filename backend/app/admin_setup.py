"""交互设置管理员账号和密码，仅使用 Python 标准库，不输出明文密码。"""

import argparse
import getpass
import os
from pathlib import Path
import re
import tempfile

from app.admin_password import hash_password

PROJECT = Path(__file__).resolve().parents[2]


def save_credentials(path: Path, username: str, password: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
        raise ValueError("账号只支持 1 至 64 位英文字母、数字、下划线、点或短横线。")
    if not 12 <= len(password) <= 256:
        raise ValueError("管理员密码需要 12 至 256 个字符。")
    path = path.expanduser().resolve()
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    values = {"ADMIN_USERNAME": username, "ADMIN_PASSWORD_HASH": hash_password(password)}
    lines = [line for line in original.splitlines() if not re.match(r"^\s*(?:export\s+)?ADMIN_(?:USERNAME|PASSWORD_HASH)\s*=", line)]
    # 单引号防止 Docker Compose 把摘要里的 $ 解释成环境变量。
    updated = "\n".join([*lines, "", *(f"{key}='{value}'" for key, value in values.items()), ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(updated)
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=PROJECT / ".env", help="要写入的项目 .env 路径")
    args = parser.parse_args()
    try:
        username = input("管理员账号（直接回车使用 admin）：").strip() or "admin"
        password = getpass.getpass("管理员密码（至少 12 个字符，输入时不会显示）：")
        confirmation = getpass.getpass("请再次输入密码：")
        if password != confirmation:
            raise ValueError("两次密码不一致，配置未修改。")
        save_credentials(args.env_file, username, password)
    except (OSError, ValueError, EOFError) as exc:
        parser.exit(1, f"后台配置失败：{exc}\n")
    except KeyboardInterrupt:
        parser.exit(1, "\n已取消，配置未修改。\n")
    print("已保存管理员账号和密码摘要，请重启后端，再打开 /admin 登录。")
    print("修改密码并重启后端后，原来的管理员登录会自动失效。")


if __name__ == "__main__":
    main()
