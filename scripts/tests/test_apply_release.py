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
MARKER = "# 此文件由小晗 CI/CD 自动生成。"

FAKE_DOCKER = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'load' and os.environ.get('FAIL_STAGE') == 'load':
    sys.exit(1)
if args[0] == 'compose':
    override = Path(os.environ['FAKE_PROJECT']) / 'compose.override.yaml'
    is_new = override.exists() and os.environ['FAKE_REVISION'] in override.read_text()
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
        (self.release / "images.tar").touch()
        self.log = root / "docker.log"
        self.environment = {
            **os.environ,
            "PATH": str(self.binary) + os.pathsep + os.environ["PATH"],
            "FAKE_PROJECT": str(self.project),
            "FAKE_REVISION": REVISION,
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
        self.assertIn(f"xiaohan-review-backend:{REVISION}", override)
        self.assertIn(f"xiaohan-review-frontend:{REVISION}", override)
        self.assertEqual((self.project / ".deploy/current-revision").read_text().strip(), REVISION)
        self.assertTrue(any("exec" in call and "frontend" in call for call in self.calls()))

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

    def test_image_import_failure_keeps_current_compose_and_containers(self):
        result = self.apply("load")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.project / "compose.yaml").read_text(), self.original_compose)
        self.assertFalse(any("up" in call for call in self.calls()))

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

    def test_arm_server_is_rejected_before_loading_amd64_images(self):
        self.environment["FAKE_MACHINE"] = "aarch64"
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("x86_64", result.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
