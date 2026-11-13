"""配置迁移后仍定位已有数据，并在启动时拒绝不可用的切分参数。"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import config


class ConfigurationTests(unittest.TestCase):
    def load_config(self, **values):
        spec = importlib.util.spec_from_file_location(
            "isolated_config", config.__file__
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, values, clear=True), patch("dotenv.load_dotenv"):
            spec.loader.exec_module(module)
        return module

    def test_default_paths_still_point_to_existing_project_data(self):
        isolated = self.load_config()
        project = Path(__file__).resolve().parents[2]
        self.assertEqual(isolated.PROJECT_DIR, project)
        self.assertEqual(isolated.MILVUS_URI, str(project / "backend/data/xiaohan.db"))
        self.assertEqual(
            isolated.REGISTRY_PATH, project / "backend/data/materials.sqlite3"
        )
        self.assertEqual(isolated.UPLOAD_DIR, project / "backend/uploads")

    def test_relative_milvus_path_and_remote_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            isolated = self.load_config(XIAOHAN_MILVUS_URI=f"{directory}/study.db")
            self.assertEqual(
                isolated.REGISTRY_PATH, Path(directory).resolve() / "materials.sqlite3"
            )
        relative = self.load_config(XIAOHAN_MILVUS_URI="backend/data/xiaohan.db")
        self.assertEqual(relative.MILVUS_URI, str(relative.DATA_DIR / "xiaohan.db"))
        remote = self.load_config(XIAOHAN_MILVUS_URI="http://localhost:19530")
        self.assertEqual(remote.MILVUS_URI, "http://localhost:19530")
        self.assertEqual(remote.REGISTRY_PATH, remote.DATA_DIR / "materials.sqlite3")

    def test_rag_and_upload_settings_have_chinese_validation_errors(self):
        for values, message in (
            ({"RAG_CHUNK_SIZE": "x"}, "必须是整数"),
            ({"MAX_UPLOAD_MB": "0"}, "不能小于"),
            ({"RAG_CHUNK_SIZE": "100", "RAG_CHUNK_OVERLAP": "100"}, "必须小于"),
            ({"CHAT_TEMPERATURE": "nan"}, "必须在"),
        ):
            with (
                self.subTest(values=values),
                self.assertRaisesRegex(ValueError, message),
            ):
                self.load_config(**values)
