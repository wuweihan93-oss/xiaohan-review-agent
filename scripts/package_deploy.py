"""生成不包含密钥与本机虚拟环境的 Ubuntu 部署包。"""

import argparse
import hashlib
import shutil
import socket
import tarfile
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
FILES = ["README.md", "pyproject.toml", "uv.lock", ".python-version", ".env.example", ".gitignore", ".dockerignore", "compose.yaml"]
DIRECTORIES = ["backend/app", "backend/tests", "frontend/src", "deploy", "scripts"]
EXTRA_FILES = [
    "backend/pyproject.toml", "backend/uv.lock", "frontend/package.json",
    "frontend/package-lock.json", "frontend/index.html", "frontend/vite.config.js",
]
IGNORED = {".venv", "__pycache__", "node_modules", ".git", ".idea", ".ipynb_checkpoints", "assets", "asset", "todo_workspace"}


def backend_is_running(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def create_package(output: Path, *, with_data: bool, tutorial_dir: Path | None, backend_port: int = 8010) -> Path:
    if with_data and backend_is_running(backend_port):
        raise ValueError(f"请先停止本机后端（端口 {backend_port}），再打包资料库，避免复制正在写入的 Milvus 文件。")
    if tutorial_dir is not None and not tutorial_dir.is_dir():
        raise ValueError("指定的教程目录不存在")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xiaohan-deploy-") as temporary:
        stage = Path(temporary) / PROJECT.name
        stage.mkdir()
        for name in FILES + EXTRA_FILES:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT / name, target)
        for name in DIRECTORIES:
            shutil.copytree(PROJECT / name, stage / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        for name in ("data", "uploads", "tutorial"):
            (stage / "runtime" / name).mkdir(parents=True)
        if with_data:
            for name in ("data", "uploads"):
                shutil.copytree(PROJECT / "backend" / name, stage / "runtime" / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".DS_Store"))
        if tutorial_dir is not None:
            for source in tutorial_dir.rglob("*"):
                relative = source.relative_to(tutorial_dir)
                if not source.is_file() or source.is_symlink() or source.suffix.lower() not in {".ipynb", ".md", ".txt", ".py"}:
                    continue
                if any(part.startswith(".") or part in IGNORED for part in relative.parts):
                    continue
                destination = stage / "runtime/tutorial" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        # 不跟随符号链接，也不把 .env、虚拟环境和 node_modules 带进归档。
        for path in stage.rglob("*"):
            if path.is_symlink() or path.name == ".env" or path.name in {".venv", "node_modules"}:
                raise ValueError("打包目录中存在不应归档的密钥文件、环境目录或符号链接")
        with tarfile.open(output, "w:gz") as archive:
            archive.add(stage, arcname=PROJECT.name)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + ".sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="部署包输出位置（.tar.gz）")
    parser.add_argument("--with-data", action="store_true", help="附带当前资料库与上传文件，必须先停止后端")
    parser.add_argument("--tutorial-dir", type=Path, help="同时打包教程笔记，自动排除 .env、隐藏目录及依赖目录")
    parser.add_argument("--backend-port", type=int, default=8010, help="本机后端端口，用于检查是否已停止")
    args = parser.parse_args()
    try:
        output = create_package(args.output, with_data=args.with_data, tutorial_dir=args.tutorial_dir, backend_port=args.backend_port)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"打包失败：{exc}\n")
    print(f"部署包已生成：{output}")
    print(f"大小：{output.stat().st_size / 1024 / 1024:.1f} MB；校验文件：{output.name}.sha256")
    print("项目 .env 未放进部署包，请通过 SSH 单独上传。服务器使用 Docker 按锁文件安装依赖。")


if __name__ == "__main__":
    main()
