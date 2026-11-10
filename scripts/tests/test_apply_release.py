"""使用模拟 Docker 验证部署与回退，不修改本机容器或线上资料。"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
REVISION = "a" * 40
OLD_REVISION = "b" * 40
BACKEND_IMAGE = "registry.example.com/xiaohan/xiaohan-review-backend@sha256:" + "c" * 64
FRONTEND_IMAGE = "registry.example.com/xiaohan/xiaohan-review-frontend@sha256:" + "d" * 64
MARKER = "# 此文件由小晗 CI/CD 自动生成。"

FAKE_DOCKER = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'pull':
    stage = os.environ.get('FAIL_STAGE', '')
    if (stage == 'pull-backend' and 'xiaohan-review-backend@' in args[1]) or (stage == 'pull-frontend' and 'xiaohan-review-frontend@' in args[1]):
        sys.exit(1)
if args[0] == 'compose':
    override = Path(os.environ['FAKE_PROJECT']) / 'compose.override.yaml'
    is_new = override.exists() and os.environ['FAKE_BACKEND_IMAGE'] in override.read_text()
    if is_new and 'up' in args and os.environ.get('FAIL_STAGE') == 'up':
        sys.exit(1)
    if is_new and 'exec' in args and os.environ.get('FAIL_STAGE') == 'proxy':
        sys.exit(1)
"""


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.project = root / "server-project"
        self.release = root / "release"
        self.binary = root / "bin"
        for directory in (self.project, self.release, self.binary):
            directory.mkdir()
        self.original_compose = "name: xiaohan-review\nservices: {}\n"
        (self.project / "compose.yaml").write_text(self.original_compose)
        (self.project / ".env").write_text("DB_URL=测试占位\nXIAOHAN_HTTP_PORT=8888\n")
        (self.project / "runtime/data").mkdir(parents=True)
        (self.project / "runtime/data/keep.txt").write_text("已有学习资料")
        (self.release / "compose.yaml").write_text("name: xiaohan-review\nservices:\n  backend: {}\n")
        (self.release / "REVISION").write_text(REVISION + "\n")
        (self.release / "BACKEND_IMAGE").write_text(BACKEND_IMAGE + "\n")
        (self.release / "FRONTEND_IMAGE").write_text(FRONTEND_IMAGE + "\n")
        self.log = root / "docker.log"
        self.environment = {
            **os.environ,
            "PATH": str(self.binary) + os.pathsep + os.environ["PATH"],
            "FAKE_PROJECT": str(self.project),
            "FAKE_BACKEND_IMAGE": BACKEND_IMAGE,
            "FAKE_DOCKER_LOG": str(self.log),
            "FAIL_STAGE": "",
        }
        self.executable("docker", FAKE_DOCKER)
        self.executable("uname", '#!/bin/sh\nprintf "%s\\n" "${FAKE_MACHINE:-x86_64}"\n')
        # 锁与重试不需要在模拟容器测试中等待。
        self.executable("flock", "#!/bin/sh\nexit 0\n")
        self.executable("sleep", "#!/bin/sh\nexit 0\n")

    def executable(self, name, content):
        file = self.binary / name
        file.write_text(content)
        file.chmod(0o755)

    def apply(self, fail=""):
        result = subprocess.run(
            ["bash", str(PROJECT / "deploy/apply-release.sh"), str(self.project), str(self.release)],
            env={**self.environment, "FAIL_STAGE": fail}, capture_output=True, text=True,
        )
        self.assertEqual((self.project / "runtime/data/keep.txt").read_text(), "已有学习资料")
        self.assertEqual((self.project / ".env").read_text(), "DB_URL=测试占位\nXIAOHAN_HTTP_PORT=8888\n")
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_success_pins_both_images_and_keeps_settings_and_materials(self):
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        override = (self.project / "compose.override.yaml").read_text()
        self.assertIn(BACKEND_IMAGE, override)
        self.assertIn(FRONTEND_IMAGE, override)
        self.assertEqual((self.project / ".deploy/current-revision").read_text().strip(), REVISION)
        self.assertTrue(any("exec" in call and "frontend" in call for call in self.calls()))
        calls = self.calls()
        self.assertEqual(calls[:2], [["pull", BACKEND_IMAGE], ["pull", FRONTEND_IMAGE]])
        self.assertFalse(any(call[0] in {"load", "login"} for call in calls))

    def test_failed_first_release_restores_existing_manual_deployment(self):
        result = self.apply("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.project / "compose.yaml").read_text(), self.original_compose)
        self.assertFalse((self.project / "compose.override.yaml").exists())
        self.assertEqual(sum("up" in call for call in self.calls()), 2)
        self.assertIn("上一版容器已恢复", result.stderr)

    def test_failed_update_restores_previous_pinned_release(self):
        previous = f"{MARKER}\nservices:\n  backend:\n    image: xiaohan-review-backend:{OLD_REVISION}\n"
        (self.project / "compose.override.yaml").write_text(previous)
        state = self.project / ".deploy"
        state.mkdir()
        (state / "current-revision").write_text(OLD_REVISION)
        result = self.apply("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.project / "compose.override.yaml").read_text(), previous)
        self.assertEqual((state / "current-revision").read_text(), OLD_REVISION)

    def test_proxy_check_failure_also_rolls_back(self):
        result = self.apply("proxy")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.project / "compose.override.yaml").exists())
        self.assertEqual(sum("up" in call for call in self.calls()), 2)

    def test_either_image_pull_failure_keeps_current_configuration_and_containers(self):
        for stage in ("pull-backend", "pull-frontend"):
            with self.subTest(stage=stage):
                self.log.unlink(missing_ok=True)
                result = self.apply(stage)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.project / "compose.yaml").read_text(), self.original_compose)
                self.assertFalse((self.project / "compose.override.yaml").exists())
                self.assertFalse(any("up" in call for call in self.calls()))

    def test_pull_failure_preserves_previous_registry_release(self):
        previous = f"{MARKER}\nservices:\n  backend:\n    image: registry.example.com/xiaohan/backend@sha256:{'e' * 64}\n"
        (self.project / "compose.override.yaml").write_text(previous)
        result = self.apply("pull-frontend")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.project / "compose.override.yaml").read_text(), previous)
        self.assertFalse(any("up" in call for call in self.calls()))

    def test_mutable_tag_or_invalid_image_is_rejected_before_docker_calls(self):
        for image in ("registry.example.com/xiaohan/backend:latest", "https://registry.example.com/backend@sha256:" + "c" * 64, BACKEND_IMAGE + "\n恶意附加内容"):
            with self.subTest(image=image):
                (self.release / "BACKEND_IMAGE").write_text(image)
                result = self.apply()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("镜像地址格式不正确", result.stderr)
                self.assertEqual(self.calls(), [])

    def test_missing_frontend_image_is_rejected_before_docker_calls(self):
        (self.release / "FRONTEND_IMAGE").unlink()
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("部署包缺少 FRONTEND_IMAGE", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_custom_override_is_preserved_and_requires_manual_merge(self):
        override = self.project / "compose.override.yaml"
        override.write_text("services:\n  backend:\n    environment: {}\n")
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("已有自定义", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertTrue(override.read_text().startswith("services:"))

    def test_invalid_revision_does_not_change_current_configuration(self):
        (self.release / "REVISION").write_text("错误的版本号")
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual((self.project / "compose.yaml").read_text(), self.original_compose)

    def test_arm_server_is_rejected_before_pulling_amd64_images(self):
        self.environment["FAKE_MACHINE"] = "aarch64"
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("x86_64", result.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
