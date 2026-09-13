import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import desktop_gui
import security_audit


class SecurityAuditTests(unittest.TestCase):
    def test_private_key_boundary_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.txt"
            path.write_text(
                "-----BEGIN OPENSSH PRIVATE KEY-----\nredacted\n",
                encoding="utf-8",
            )
            failures = security_audit.audit_file(path)
        self.assertTrue(any("私钥正文" in item for item in failures))

    def test_normal_source_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.py"
            path.write_text("print('safe')\n", encoding="utf-8")
            failures = security_audit.audit_file(path)
        self.assertEqual([], failures)


class ConfigMigrationTests(unittest.TestCase):
    def test_legacy_identity_fields_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = Path(directory)
            config_dir = appdata / desktop_gui.APP_NAME
            config_dir.mkdir()
            config_path = config_dir / "config.json"
            config_path.write_text(json.dumps({
                "version": 1,
                "accounts": [],
                "hosts": [{
                    "id": "host-1",
                    "name": "测试服务器",
                    "target": "test-host",
                    "identity_file": r"C:\Users\demo\.ssh\id_ed25519",
                    "private_key": "must-not-survive",
                }],
            }), encoding="utf-8")

            with mock.patch.dict("os.environ", {"APPDATA": str(appdata)}):
                store = desktop_gui.ConfigStore()

            host = next(item for item in store.data["hosts"] if item["id"] == "host-1")
            local = next(item for item in store.data["hosts"] if item["id"] == desktop_gui.LOCAL_HOST_ID)
            self.assertEqual(4, store.data["version"])
            self.assertNotIn("identity_file", host)
            self.assertNotIn("private_key", host)
            self.assertEqual("ssh", host["connection_type"])
            self.assertFalse(host["heartbeat_enabled"])
            self.assertEqual(60, host["heartbeat_interval_seconds"])
            self.assertEqual(2, host["heartbeat_failure_threshold"])
            self.assertEqual("local", local["connection_type"])
            self.assertEqual("本机 Windows", local["name"])
            persisted = config_path.read_text(encoding="utf-8")
            self.assertNotIn("id_ed25519", persisted)
            self.assertNotIn("must-not-survive", persisted)


if __name__ == "__main__":
    unittest.main()
