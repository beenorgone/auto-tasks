import importlib.util
import json
import http.client
import os
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


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
        self.assertIn("<html lang='vi'>", markup)
        self.assertIn(">Xem trước</button>", markup)
        self.assertIn("Cập nhật phân loại", markup)
        self.assertNotIn("Open Kanban", markup)
        self.assertIn(">Áp dụng thay đổi</button>", markup)
        self.assertNotIn("confirm('Apply", markup)
        app_markup = APP.add_app_tabs(markup.encode(), "/history").decode()
        self.assertIn('href="/history" aria-current="page">Lịch sử hôm nay', app_markup)
        self.assertNotIn('href="/kanban"', app_markup)
        english = APP.page("unit-test-token", "en")
        self.assertIn("<html lang='en'>", english)
        self.assertIn(">Refresh classification</button>", english)
        self.assertIn(">Apply changes</button>", english)

    def test_http_rejects_foreign_host_and_origin(self):
        server = APP.ThreadingHTTPServer(("127.0.0.1", 0), APP.AppHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for headers, expected in [({}, 200), ({"Host": "attacker.example"}, 403), ({"Origin": "https://attacker.example"}, 403)]:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/api/status", headers=headers)
                response = connection.getresponse()
                self.assertEqual(expected, response.status)
                response.read()
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_history_accepts_only_known_commands_and_runs_without_project_sync(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(temporary)
            with patch.object(APP, "APP_STATE", state), patch.dict("os.environ", {"STATE_DIR": temporary}):
                with APP.ThreadingHTTPServer(("127.0.0.1", 0), APP.AppHandler) as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    try:
                        connection.request("GET", "/history")
                        response = connection.getresponse()
                        self.assertEqual(200, response.status)
                        markup = response.read().decode()
                        self.assertIn('id="classification-command"', markup)
                        self.assertIn(f'data-token="{state.csrf_token}"', markup)
                        self.assertIn('<option value="phân loại">', markup)
                        self.assertIn('/assets/history.js', markup)
                        for command, token, expected in (
                            ("phân loại", "invalid", 403),
                            ("docsync apply; echo unexpected", state.csrf_token, 400),
                            (["phân loại"], state.csrf_token, 400),
                            ("  XEM   TRƯỚC  ", state.csrf_token, 202),
                            ("docsync apply", state.csrf_token, 202),
                        ):
                            connection.request("POST", "/api/run", json.dumps({"command": command, "syncProject": True}),
                                               {"Content-Type": "application/json", "X-CSRF-Token": token})
                            response = connection.getresponse()
                            self.assertEqual(expected, response.status)
                            response.read()
                            if expected != 202:
                                self.assertEqual("idle", state.snapshot()["mode"])
                                continue
                            snapshot = self.wait_for_run(state)
                            self.assertEqual(0, snapshot["exitCode"])
                            mode = "apply" if command == "docsync apply" else "preview"
                            self.assertEqual(mode, snapshot["mode"])
                            self.assertIn("--no-sync", snapshot["logs"][0])
                            self.assertEqual(mode == "preview", "--dry-run" in snapshot["logs"][0])
                    finally:
                        connection.close()
                        server.shutdown()
                        thread.join()

    def test_folder_settings_take_effect_without_restarting_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            new_root = home / "WIP mới"
            accounting = home / "Đích kế toán"
            new_root.mkdir()
            accounting.mkdir()
            state = APP.RunState()
            with patch.object(APP, "APP_STATE", state), patch.dict(os.environ, {"HOME": temporary}, clear=True):
                with APP.ThreadingHTTPServer(("127.0.0.1", 0), APP.AppHandler) as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    try:
                        connection.request("GET", "/settings")
                        response = connection.getresponse()
                        self.assertEqual(200, response.status)
                        markup = response.read()
                        self.assertIn(b'id="folder-settings"', markup)
                        self.assertIn(b'id="system-rules"', markup)
                        paths = {"DOCSYNC_ROOT": str(new_root), "IVAR_DATA": str(accounting),
                                 "DOCSYNC_DOCUMENTS_SCAN": "", "DOCSYNC_DOWNLOADS_SCAN": ""}
                        body = json.dumps({"paths": paths})
                        connection.request("POST", "/api/settings", body)
                        response = connection.getresponse()
                        self.assertEqual(403, response.status)
                        response.read()
                        self.assertNotEqual(new_root, state.year_root)
                        connection.request("POST", "/api/settings", body, {"X-CSRF-Token": state.csrf_token})
                        response = connection.getresponse()
                        self.assertEqual(200, response.status)
                        settings = json.load(response)
                        self.assertEqual(paths, settings["paths"])
                        operations = {item["name"]: item for group in settings["system_routes"]
                                      for item in group["operations"]}
                        self.assertIn(str(accounting / "ngan hang/UNC/<năm>/<tháng>"),
                                      operations["Ủy nhiệm chi (UNC)"]["destinations"])
                        self.assertIn(str(accounting / "hoa don dau vao"),
                                      operations["Hóa đơn đầu vào PDF/XML"]["destinations"])
                        self.assertEqual([str(new_root / "Mua hàng - dịch vụ/PO")],
                                         operations["Tài liệu PO rời"]["destinations"])
                        self.assertEqual([str(new_root / "duplicates")], operations["UNC trùng"]["destinations"])
                        connection.request("GET", "/api/status")
                        self.assertEqual(str(new_root), json.load(connection.getresponse())["yearRoot"])
                    finally:
                        connection.close()
                        server.shutdown()
                        thread.join()


if __name__ == "__main__":
    unittest.main()
