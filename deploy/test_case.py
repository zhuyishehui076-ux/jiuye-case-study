"""Safety-boundary tests for the deployment helper; no Docker engine is contacted."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("case_runner", Path(__file__).with_name("case.py"))
CASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CASE)


class ConfigurationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jiuye-case-boundary-")
        self.root = Path(self.temp.name)
        self.state = self.root / ".case-state"
        (self.state / "test" / "reports").mkdir(parents=True)
        self.root_patch = patch.object(CASE, "ROOT", self.root)
        self.state_patch = patch.object(CASE, "STATE", self.state)
        self.root_patch.start()
        self.state_patch.start()
        project = "jiuye_case_test_" + secrets.token_hex(8)
        password = secrets.token_urlsafe(40)
        self.data = {"CASE_PROJECT": project, "CASE_DATABASE": project, "CASE_DB_PASSWORD": password,
                     "DATABASE_URL": f"postgresql+psycopg://case_user:{password}@postgres:5432/{project}",
                     "JWT_SECRET": secrets.token_urlsafe(48), "FIELD_ENCRYPTION_KEY": secrets.token_urlsafe(48),
                     "INSTALLATION_ID": "jiuye-case-test-" + secrets.token_hex(16),
                     "CASE_REPORTS": (self.state / "test" / "reports").as_posix()}

    def tearDown(self):
        self.state_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def load(self):
        (self.state / "test" / "config.json").write_text(json.dumps(self.data), encoding="utf-8")
        return CASE.load("test")

    def test_generated_configuration_is_accepted(self):
        self.assertEqual(self.load()["CASE_PROJECT"], self.data["CASE_PROJECT"])

    def test_extra_environment_key_is_rejected(self):
        self.data["DOCKER_HOST"] = "tcp://example.invalid:2375"
        with self.assertRaisesRegex(RuntimeError, "configuration keys"):
            self.load()

    def test_database_target_override_is_rejected(self):
        self.data["DATABASE_URL"] += "?host=example.invalid"
        with self.assertRaisesRegex(RuntimeError, "target mismatch"):
            self.load()

    def test_reused_secrets_are_rejected(self):
        self.data["FIELD_ENCRYPTION_KEY"] = self.data["JWT_SECRET"]
        with self.assertRaisesRegex(RuntimeError, "reused"):
            self.load()

    def test_foreign_reports_path_is_rejected(self):
        self.data["CASE_REPORTS"] = (self.root / "unrelated").as_posix()
        with self.assertRaisesRegex(RuntimeError, "reports mount"):
            self.load()

    def test_foreign_installation_is_rejected(self):
        self.data["INSTALLATION_ID"] = "development"
        with self.assertRaisesRegex(RuntimeError, "installation identity"):
            self.load()

    def test_foreign_database_name_is_rejected(self):
        self.data["CASE_DATABASE"] = "unrelated"
        with self.assertRaisesRegex(RuntimeError, "database identity"):
            self.load()

    def test_remote_docker_context_is_rejected(self):
        outputs = [subprocess.CompletedProcess([], 0, "remote\n", ""),
                   subprocess.CompletedProcess([], 0, json.dumps([
                       {"Endpoints": {"docker": {"Host": "ssh://example.invalid"}}}]), "")]
        with patch.object(CASE, "invoke", side_effect=outputs):
            with self.assertRaisesRegex(RuntimeError, "local engine"):
                CASE.local_context(self.data)

    def test_existing_project_resource_is_rejected(self):
        output = subprocess.CompletedProcess([], 0, self.data["CASE_PROJECT"] + "_pgdata\n", "")
        with patch.object(CASE, "invoke", return_value=output):
            with self.assertRaisesRegex(RuntimeError, "already exist"):
                CASE.require_fresh(self.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
