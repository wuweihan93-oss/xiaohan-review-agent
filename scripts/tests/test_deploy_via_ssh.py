"""验证 SSH 传输参数和临时密钥清理，所有网络命令均使用模拟程序。"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
REVISION = "a" * 40

FAKE_NETWORK = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

kind = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['FAKE_NETWORK_LOG'], 'a') as stream:
    stream.write(json.dumps([kind, *args]) + '\\n')
if kind == 'ssh':
    if args[-1].startswith('mktemp -d '):
        print('/tmp/xiaohan-release.' + os.environ['REVISION'] + '.ABC123')
    elif 'apply-release.sh' in args[-1] and os.environ.get('FAIL_REMOTE') == '1':
        sys.exit(1)
"""


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.binary = root / "bin"
        self.binary.mkdir()
        self.key = root / "test_key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True)
        public_key = self.key.with_suffix(".pub").read_text().strip()
        self.archive = root / "xiaohan-release.tar.gz"
        self.archive.touch()
        self.archive.with_name(self.archive.name + ".sha256").touch()
        self.log = root / "network.log"
        self.environment = {
            **os.environ,
            "PATH": str(self.binary) + os.pathsep + os.environ["PATH"],
            "SSH_HOST": "43.139.216.149", "SSH_USER": "ubuntu", "SSH_PORT": "22",
            "SSH_PRIVATE_KEY": self.key.read_text(),
            "SSH_KNOWN_HOSTS": "43.139.216.149 " + public_key,
            "REVISION": REVISION,
            "DEPLOY_PATH": "/home/ubuntu/apps/project with 'quotes' and $dollars",
            "FAKE_NETWORK_LOG": str(self.log), "FAIL_REMOTE": "0",
        }
        for name in ("ssh", "scp"):
            file = self.binary / name
            file.write_text(FAKE_NETWORK)
            file.chmod(0o755)

    def run_transfer(self, **changes):
        return subprocess.run(
            ["bash", str(PROJECT / "scripts/deploy_via_ssh.sh"), str(self.archive)],
            env={**self.environment, **changes}, text=True, capture_output=True,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_transfer_checks_host_and_preserves_quoted_project_path(self):
        result = self.run_transfer()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual([call[0] for call in calls], ["ssh", "scp", "ssh", "ssh"])
        for call in calls:
            self.assertIn("StrictHostKeyChecking=yes", call)
            key_path = Path(call[call.index("-i") + 1])
            self.assertFalse(key_path.exists(), "任务结束后应删除私钥临时副本")
        remote_command = calls[2][-1]
        self.assertIn("sha256sum -c", remote_command)
        # 实际解析远程命令，确认空格、引号和美元符号仍属于同一个路径参数。
        stubs = '''cd() { :; }
sha256sum() { :; }
tar() { :; }
cat() { printf '%s' "$REVISION"; }
bash() { printf '%s\\0' "$@"; }
'''
        parsed = subprocess.run(
            ["bash", "-c", stubs + remote_command], env={**os.environ, "REVISION": REVISION},
            check=True, capture_output=True,
        )
        arguments = parsed.stdout.decode().split("\0")
        self.assertEqual(arguments[1], self.environment["DEPLOY_PATH"])
        self.assertNotIn(self.environment["SSH_PRIVATE_KEY"], result.stdout + result.stderr)

    def test_missing_trusted_host_stops_before_network_connection(self):
        public_key = self.key.with_suffix(".pub").read_text().strip()
        result = self.run_transfer(SSH_KNOWN_HOSTS="other.example " + public_key)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SSH_KNOWN_HOSTS", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_remote_failure_preserves_release_for_troubleshooting_and_cleans_key(self):
        result = self.run_transfer(FAIL_REMOTE="1")
        self.assertNotEqual(result.returncode, 0)
        calls = self.calls()
        self.assertEqual([call[0] for call in calls], ["ssh", "scp", "ssh"])
        self.assertFalse(Path(calls[0][calls[0].index("-i") + 1]).exists())


if __name__ == "__main__":
    unittest.main()
