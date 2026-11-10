"""验证部署归档不携带密钥、虚拟环境，以及文件路径迁移后的删除行为。"""

import importlib.util
import socket
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import materials

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("package_deploy", PROJECT / "scripts/package_deploy.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PackageTests(unittest.TestCase):
    def test_archive_contains_locked_code_but_no_local_environment_or_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "package.tar.gz"
            packager.create_package(output, with_data=False, tutorial_dir=None)
            with tarfile.open(output) as archive:
                names = archive.getnames()
                self.assertIn("xiaohan-review-agent/compose.yaml", names)
                self.assertIn("xiaohan-review-agent/backend/uv.lock", names)
                self.assertFalse(any(Path(name).name == ".env" for name in names))
                self.assertFalse(any(part in {".venv", "node_modules", "__pycache__"} for name in names for part in Path(name).parts))
                self.assertFalse(any(name.endswith("materials.sqlite3") for name in names))
            self.assertTrue(output.with_name(output.name + ".sha256").exists())

    def test_live_backend_refuses_data_snapshot(self):
        with socket.socket() as listener, tempfile.TemporaryDirectory() as directory:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaisesRegex(ValueError, "请先停止本机后端"):
                packager.create_package(Path(directory) / "package.tar.gz", with_data=True, tutorial_dir=None, backend_port=listener.getsockname()[1])

    def test_tutorial_copy_excludes_env_and_dependency_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            notes = root / "notes"
            notes.mkdir()
            (notes / "lesson.md").write_text("学习笔记", encoding="utf-8")
            (notes / ".env").write_text("测试密钥占位", encoding="utf-8")
            (notes / ".venv").mkdir()
            (notes / ".venv/irrelevant.py").write_text("", encoding="utf-8")
            output = root / "package.tar.gz"
            packager.create_package(output, with_data=False, tutorial_dir=notes)
            with tarfile.open(output) as archive:
                names = archive.getnames()
                self.assertIn("xiaohan-review-agent/runtime/tutorial/lesson.md", names)
                self.assertFalse(any("irrelevant.py" in name or Path(name).name == ".env" for name in names))


class UploadMigrationTests(unittest.TestCase):
    def test_delete_resolves_old_mac_path_under_current_upload_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uploads = root / "uploads"
            uploads.mkdir()
            filename = "test_migrated.md"
            target = uploads / filename
            target.write_text("测试资料", encoding="utf-8")
            registry_path = root / "materials.sqlite3"
            with patch.object(materials, "REGISTRY_PATH", registry_path), patch.object(materials, "UPLOAD_DIR", uploads), patch.object(materials, "_embeddings", return_value=Mock(embed_query=Mock(return_value=[0.0, 1.0]))), patch.object(materials, "_milvus", return_value=Mock()):
                connection = materials._registry()
                connection.execute("INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                    "migration-test", "测试资料", "上传/测试", "upload",
                    f"/Users/example/old-project/uploads/{filename}", "hash", 1, "2026-01-01",
                ))
                connection.commit()
                connection.close()
                self.assertTrue(materials.delete_material("migration-test"))
                self.assertFalse(target.exists())

    def test_new_upload_saves_filename_without_machine_specific_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            path.write_text("测试资料", encoding="utf-8")
            with patch.object(materials, "_ingest", return_value={}) as ingest:
                materials.ingest_file(path)
            self.assertEqual(ingest.call_args.kwargs["stored_path"], "sample.md")


if __name__ == "__main__":
    unittest.main()
