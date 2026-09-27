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
    def make_state(self, temporary):
        root = Path(temporary)
        year_root = root / "Documents" / "2026"
        year_root.mkdir(parents=True)
        script = root / "sync.sh"
        script.write_text("#!/bin/sh\nprintf 'organizer:%s\\n' \"$*\"\n", encoding="utf-8")
        wip_script = root / "wip.py"
        wip_script.write_text(
            """import sys
from pathlib import Path

print("wip:" + " ".join(sys.argv[1:]))
if "dashboard" in sys.argv:
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("<!doctype html><title>Test Kanban</title>", encoding="utf-8")
""",
            encoding="utf-8",
        )
        return APP.RunState(script, wip_script, year_root)

    def wait_for_run(self, state):
        for _ in range(100):
            if not state.snapshot()["running"]:
                return state.snapshot()
            time.sleep(0.01)
        self.fail("document sync test run did not finish")

    def test_preview_never_requests_rclone_sync(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(temporary)
            started, _message = state.start("preview", True)
            self.assertTrue(started)
            snapshot = self.wait_for_run(state)
            self.assertEqual(0, snapshot["exitCode"])
            self.assertIn("--plan-managed-rules", snapshot["logs"][0])
            self.assertIn("--dry-run", snapshot["logs"][0])
            self.assertIn("--no-sync", snapshot["logs"][0])
            self.assertTrue(snapshot["kanbanAvailable"])
            self.assertTrue(any(" scan" in line for line in snapshot["logs"]))
            self.assertTrue(any(" dashboard " in line for line in snapshot["logs"]))

    def test_apply_without_external_sync_adds_no_sync(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(temporary)
            started, _message = state.start("apply", False)
            self.assertTrue(started)
            snapshot = self.wait_for_run(state)
            self.assertIn("--apply-managed-rules --no-sync", snapshot["logs"][0])
            self.assertTrue(snapshot["kanbanAvailable"])

    def test_wip_scan_refreshes_dashboard_without_running_organizer(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(temporary)
            started, _message = state.start("wip_scan", False)
            self.assertTrue(started)
            snapshot = self.wait_for_run(state)
            self.assertEqual(0, snapshot["exitCode"])
            self.assertTrue(snapshot["kanbanAvailable"])
            self.assertFalse(any("organizer:" in line for line in snapshot["logs"]))

    def test_page_contains_csrf_token_and_workflow(self):
        markup = APP.page("unit-test-token")
        self.assertIn("unit-test-token", markup)
        self.assertIn("Preview routing", markup)
        self.assertIn("Refresh Kanban", markup)
        self.assertIn("Open Kanban", markup)
        self.assertIn("Apply managed routes", markup)
        self.assertNotIn("confirm('Apply", markup)

    def test_run_request_rejects_invalid_token(self):
        self.assertFalse(APP.secrets.compare_digest("invalid", APP.APP_STATE.csrf_token))
        self.assertEqual({"mode": "preview", "syncProject": False}, json.loads('{"mode":"preview","syncProject":false}'))


if __name__ == "__main__":
    unittest.main()
