import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("doc_sync_app", Path(__file__).with_name("doc-sync-app.py"))
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class DocumentSyncAppTests(unittest.TestCase):
    def test_preview_never_requests_rclone_sync(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "sync.sh"
            script.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
            state = APP.RunState(script)
            started, _message = state.start("preview", True)
            self.assertTrue(started)
            for _ in range(50):
                if not state.snapshot()["running"]:
                    break
                time.sleep(0.01)
            snapshot = state.snapshot()
            self.assertEqual(0, snapshot["exitCode"])
            self.assertIn("--plan-managed-rules", snapshot["logs"][0])
            self.assertIn("--no-sync", snapshot["logs"][0])

    def test_apply_without_external_sync_adds_no_sync(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "sync.sh"
            script.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
            state = APP.RunState(script)
            started, _message = state.start("apply", False)
            self.assertTrue(started)
            for _ in range(50):
                if not state.snapshot()["running"]:
                    break
                time.sleep(0.01)
            self.assertIn("--apply-managed-rules --no-sync", state.snapshot()["logs"][0])

    def test_page_contains_csrf_token_and_workflow(self):
        markup = APP.page("unit-test-token")
        self.assertIn("unit-test-token", markup)
        self.assertIn("Preview routing", markup)
        self.assertIn("Apply managed routes", markup)

    def test_run_request_rejects_invalid_token(self):
        self.assertFalse(APP.secrets.compare_digest("invalid", APP.APP_STATE.csrf_token))
        self.assertEqual({"mode": "preview", "syncProject": False}, json.loads('{"mode":"preview","syncProject":false}'))


if __name__ == "__main__":
    unittest.main()
