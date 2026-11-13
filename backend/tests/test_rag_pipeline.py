"""使用临时 SQLite 和模拟向量服务验证拆分后的 RAG 流程，不下载模型。"""

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from app.rag.chunking import prepare_chunks
from app.rag.loaders import read_material
from app.services import materials


class RagPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.vector_client = Mock()
        self.embedder = Mock()
        self.embedder.embed_documents.side_effect = lambda texts: [
            [0.1, 0.2] for _ in texts
        ]
        self.embedder.embed_query.return_value = [0.1, 0.2]
        for patcher in (
            patch.object(
                materials.registry_store,
                "REGISTRY_PATH",
                self.root / "registry.sqlite3",
            ),
            patch.object(
                materials, "get_vector_client", return_value=self.vector_client
            ),
            patch.object(materials, "get_embeddings", return_value=self.embedder),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_upload_and_reimport_keep_one_registry_entry_with_matching_vectors(self):
        file = self.root / "笔记.md"
        file.write_text("RAG 先加载资料，再切分、向量化，最后检索。" * 100)
        first = materials.ingest_file(file)
        rows = self.vector_client.insert.call_args.kwargs["data"]
        self.assertGreater(len(rows), 1)
        self.assertEqual(len(rows), first["chunk_count"])
        self.assertTrue(all(row["material_id"] == first["id"] for row in rows))
        self.assertTrue(all(row["vector"] == [0.1, 0.2] for row in rows))
        self.assertEqual(materials.material_counts()["upload"], 1)
        second = materials.ingest_file(file)
        self.assertNotEqual(first["id"], second["id"])
        listing = materials.list_materials()
        self.assertEqual([row["id"] for row in listing], [second["id"]])
        self.assertIn(first["id"], self.vector_client.delete.call_args.kwargs["filter"])

    def test_vector_insert_failure_leaves_no_registry_or_partial_vectors(self):
        self.vector_client.insert.side_effect = RuntimeError("模拟向量写入失败")
        file = self.root / "失败.md"
        file.write_text("这份资料不应该出现在登记库。")
        with self.assertRaisesRegex(RuntimeError, "模拟向量写入失败"):
            materials.ingest_file(file)
        self.assertEqual(materials.list_materials(), [])
        inserted_id = self.vector_client.insert.call_args.kwargs["data"][0][
            "material_id"
        ]
        self.assertIn(inserted_id, self.vector_client.delete.call_args.kwargs["filter"])

    def test_search_keeps_selected_scope_and_ignores_invalid_ids(self):
        selected = str(uuid.uuid4())
        self.vector_client.search.return_value = [
            [
                {
                    "entity": {
                        "material_id": selected,
                        "text": "检索片段",
                        "source": "笔记.md",
                        "title": "笔记",
                    },
                    "distance": 0.87654,
                }
            ]
        ]
        results = materials.search_materials(
            "RAG 是什么", [selected, "不合法的编号"], limit=100
        )
        options = self.vector_client.search.call_args.kwargs
        self.assertEqual(options["filter"], f'material_id in ["{selected}"]')
        self.assertEqual(options["limit"], 8)
        self.assertEqual(results[0]["score"], 0.8765)
        self.assertEqual(materials.search_materials("问题", []), [])
        self.assertEqual(materials.search_materials("问题", ["不合法"]), [])
        self.assertEqual(self.embedder.embed_query.call_count, 1)

    def test_notebook_keeps_code_and_markdown_without_cell_output(self):
        file = self.root / "lesson.ipynb"
        file.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "markdown", "source": ["# Agent\n", "学习说明"]},
                        {
                            "cell_type": "code",
                            "source": ['print("示例")'],
                            "outputs": [{"text": "不导入运行结果"}],
                        },
                    ]
                }
            )
        )
        text = read_material(file)
        self.assertIn("学习说明", text)
        self.assertIn('print("示例")', text)
        self.assertNotIn("不导入运行结果", text)
        with self.assertRaisesRegex(ValueError, "没有可读取"):
            prepare_chunks("  \n ")


class MaterialApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_list_retrieve_and_delete_share_the_same_material(self):
        """从真实 HTTP 上传入口走到文件解析与临时登记库，仅替换外部向量服务。"""
        import httpx
        from app.api.routes import materials as routes
        from app.main import create_app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            embedder = Mock()
            embedder.embed_documents.side_effect = lambda texts: [
                [0.1, 0.2] for _ in texts
            ]
            embedder.embed_query.return_value = [0.1, 0.2]
            vector_client = Mock()
            with (
                patch.object(
                    materials.registry_store,
                    "REGISTRY_PATH",
                    root / "materials.sqlite3",
                ),
                patch.object(routes, "UPLOAD_DIR", root),
                patch.object(materials, "UPLOAD_DIR", root),
                patch.object(materials, "get_embeddings", return_value=embedder),
                patch.object(
                    materials, "get_vector_client", return_value=vector_client
                ),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app()),
                    base_url="http://test",
                ) as client:
                    uploaded = await client.post(
                        "/api/materials/upload",
                        files={
                            "file": (
                                "lesson.md",
                                "RAG 先切分再向量化。".encode(),
                                "text/markdown",
                            )
                        },
                    )
                    self.assertEqual(uploaded.status_code, 200)
                    material = uploaded.json()["material"]
                    listing = (await client.get("/api/materials")).json()["materials"]
                    self.assertEqual([row["id"] for row in listing], [material["id"]])
                    vector_client.search.return_value = [
                        [
                            {
                                "entity": {
                                    "material_id": material["id"],
                                    "text": "RAG 先切分再向量化。",
                                    "source": "上传/lesson.md",
                                },
                                "distance": 0.9,
                            }
                        ]
                    ]
                    retrieved = await client.post(
                        "/api/retrieval/search",
                        json={"query": "RAG 的步骤", "material_ids": [material["id"]]},
                    )
                    self.assertEqual(retrieved.status_code, 200)
                    self.assertEqual(
                        retrieved.json()["results"][0]["material_id"], material["id"]
                    )
                    deleted = await client.delete(f"/api/materials/{material['id']}")
                    self.assertEqual(deleted.status_code, 200)
                    self.assertEqual(
                        (await client.get("/api/materials")).json()["materials"], []
                    )
                    self.assertEqual(list(root.glob("*.md")), [])
