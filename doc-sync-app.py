#!/usr/bin/env python3
"""Local Material-style control panel for the IVAR document synchronization job."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
import webbrowser
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from docsync_core import board, health
from docsync_paths import save_workspace_paths, settings_payload, state_directory, work_root, workspace_paths
import docsync_daily as daily
import docsync_workflows as workflows
from docsync_history import decide as decide_history_file, render_today
import sqlite3


APP_ROOT = Path(__file__).resolve().parent
SYNC_SCRIPT = APP_ROOT / "ivar-documents-organizer.sh"
WIP_SCRIPT = APP_ROOT / "wip-organizer.py"
DOCUMENTS_ROOT = Path.home() / "Documents"
LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_LOG_LINES = 400
DAY_ACTIONS = {f"/api/day/{action}": action for action in ("credential", "types", "targets", "record", "attach")}
TABS = (("/", "Tổng quan"), ("/day", "File trong ngày"), ("/history", "Lịch sử hôm nay"),
        ("/workflows", "Workflows"), ("/settings", "Cài đặt"))
APP_ASSETS = {"/assets/app.css": ("docsync-app.css", "text/css"),
              "/assets/day.js": ("docsync-day.js", "text/javascript"),
              "/assets/history.js": ("docsync-history.js", "text/javascript"),
              "/assets/settings.js": ("docsync-settings.js", "text/javascript")}
TAB_TEMPLATES = {"/workflows": "docsync-workflows.html", "/day": "docsync-day.html", "/settings": "docsync-settings.html"}
HISTORY_COMMANDS = {"xem trước": "preview", "phân loại": "apply",
                    "docsync preview": "preview", "docsync apply": "apply"}


class RunState:
    """One-at-a-time subprocess runner with a bounded, browser-safe log feed."""

    def __init__(
        self,
        script: Path = SYNC_SCRIPT,
        wip_script: Path = WIP_SCRIPT,
        year_root: Path | None = None,
    ) -> None:
        self.script = script
        self.wip_script = wip_script
        self.fixed_root = year_root
        self.health_cache = {}
        self.health_cached_at = 0.0
        self.health_lock = threading.Lock()
        self.csrf_token = secrets.token_urlsafe(32)
        self.file_key = secrets.token_bytes(32)
        self.lock = threading.Lock()
        self.running = False
        self.mode = "idle"
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.exit_code: int | None = None
        self.logs: collections.deque[str] = collections.deque(maxlen=MAX_LOG_LINES)

    @property
    def year_root(self) -> Path:
        return self.fixed_root or work_root()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            root = self.year_root
            return {
                "running": self.running,
                "mode": self.mode,
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                "exitCode": self.exit_code,
                "logs": list(self.logs),
                "kanbanAvailable": (root / ".wip" / "dashboard.html").is_file(),
                "yearRoot": str(root),
            }

    def overview(self) -> dict[str, Any]:
        with self.health_lock:
            if time.monotonic() - self.health_cached_at >= 30:
                try:
                    self.health_cache = {**health(self.year_root), **board(self.year_root)}
                except (OSError, ValueError, sqlite3.Error) as error:
                    self.health_cache = {"error": str(error), "services": {}, "cards": [], "counts": {}}
                self.health_cached_at = time.monotonic()
            return self.health_cache

    def start(self, mode: str, sync_project: bool) -> tuple[bool, str]:
        if mode not in {"preview", "wip_scan", "apply"}:
            return False, "Unknown run mode."
        if mode in {"preview", "apply"} and not self.script.is_file():
            return False, f"Sync script is unavailable: {self.script}"
        if not self.wip_script.is_file():
            return False, f"WIP rule engine is unavailable: {self.wip_script}"
        with self.lock:
            if self.running:
                return False, "A document sync run is already in progress."
            paths = workspace_paths()
            root = self.fixed_root or Path(paths["DOCSYNC_ROOT"])
            if not root.is_dir():
                return False, f"WIP & Reference folder is unavailable: {root}"
            paths["DOCSYNC_ROOT"] = str(root)
            self.running = True
            self.mode = mode
            self.started_at = time.time()
            self.finished_at = None
            self.exit_code = None
            self.logs.clear()
            commands = self.commands_for(mode, sync_project, root)
        threading.Thread(target=self._run, args=(commands, paths), daemon=True).start()
        return True, "Run started."

    def commands_for(self, mode: str, sync_project: bool, root: Path) -> list[list[str]]:
        wip_base = [sys.executable, str(self.wip_script), "--year-root", str(root)]
        wip_commands = [
            [*wip_base, "scan"],
            [*wip_base, "dashboard", "--output", str(root / ".wip" / "dashboard.html")],
        ]
        if mode == "wip_scan":
            return wip_commands

        organizer = [
            "/bin/sh",
            str(self.script),
            "--plan-managed-rules" if mode == "preview" else "--apply-managed-rules",
        ]
        if mode == "preview":
            organizer.append("--dry-run")
        if mode == "preview" or not sync_project:
            organizer.append("--no-sync")
        return [organizer, *wip_commands]

    def _run(self, commands: list[list[str]], paths: dict[str, str]) -> None:
        exit_code = 1
        try:
            for args in commands:
                with self.lock:
                    self.logs.append("$ " + shlex.join(args))
                process = subprocess.Popen(
                    args,
                    cwd=self.script.parent,
                    env={**os.environ, **paths},
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                assert process.stdout is not None
                with process.stdout:
                    for line in process.stdout:
                        with self.lock:
                            self.logs.append(line.rstrip("\n"))
                exit_code = process.wait()
                if exit_code != 0:
                    break
        except OSError as error:
            with self.lock:
                self.logs.append(f"Unable to start document sync: {error}")
        finally:
            with self.lock:
                self.exit_code = exit_code
                self.finished_at = time.time()
                self.running = False
                self.logs.append(f"Run finished with exit code {exit_code}.")


APP_STATE = RunState()


def add_app_tabs(document: bytes, current: str) -> bytes:
    links = []
    for url, label in TABS:
        active = ' aria-current="page"' if url == current else ''
        links.append(f'<a href="{url}"{active}>{label}</a>')
    header = ('<link rel="stylesheet" href="/assets/app.css">'
              '<div class="ds-shell"><div class="ds-shell-inner"><a class="ds-brand" href="/">DocSync</a>'
              '<nav class="ds-tabs" aria-label="Các tab DocSync">' + ''.join(links) + '</nav></div></div>')
    before, main, after = document.partition(b"<main>")
    if not main:
        before, main, after = document, b"", b""
    return before + header.encode("utf-8") + main + after


def page(token: str) -> str:
    safe_token = json.dumps(token)
    return f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Document Sync</title><style>
:root{{--primary:#6750a4;--on-primary:#fff;--surface:#fffbfe;--surface-2:#f4eff7;--outline:#79747e;--text:#1d1b20;--muted:#5f5963;--good:#146c2e;--bad:#8c1d18}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--surface);color:var(--text);font:15px/1.45 Roboto,Arial,sans-serif}}main{{max-width:1240px;margin:auto;padding:32px 20px 48px}}h1{{margin:0;font-size:32px;letter-spacing:-.4px}}.sub{{color:var(--muted);margin:7px 0 26px}}.board{{display:grid;grid-template-columns:repeat(5,minmax(190px,1fr));gap:14px;overflow:auto;padding-bottom:8px}}.stage,.panel{{background:var(--surface-2);border-radius:18px;padding:18px;border:1px solid #e6e0e9}}.stage h2{{font-size:16px;margin:0 0 8px}}.stage p,.panel p{{margin:0;color:var(--muted)}}.stage.active{{border-color:var(--primary);box-shadow:0 1px 3px #0002}}.controls{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-top:24px}}.panel{{display:flex;flex-direction:column;align-items:flex-start}}.panel h2{{font-size:19px;margin:0 0 8px}}.actions{{display:flex;flex-wrap:wrap;gap:10px;margin-top:auto;padding-top:18px}}button,.button-link{{border:0;border-radius:999px;background:var(--primary);color:var(--on-primary);padding:11px 18px;font:600 14px Roboto,Arial;cursor:pointer;box-shadow:0 1px 2px #0003;text-decoration:none}}button.secondary,.button-link.secondary{{background:#e8def8;color:#21005d}}button.tonal{{background:#d8e2ff;color:#17315f}}button:disabled,.button-link.disabled{{opacity:.55;cursor:not-allowed;pointer-events:none}}label{{display:flex;gap:9px;align-items:flex-start;margin:16px 0 0;color:#403b43}}input{{margin-top:3px;accent-color:var(--primary)}}.confirm-row{{display:none;margin-top:14px;padding:12px;border-radius:12px;background:#fff4e7;color:#5d3a00}}.confirm-row.visible{{display:block}}.confirm-row p{{color:inherit;margin-bottom:10px}}.status{{margin-top:22px;padding:13px 15px;border-radius:12px;background:#e8def8;color:#21005d;font-weight:600}}.status.good{{background:#d8f6dd;color:var(--good)}}.status.bad{{background:#ffdad4;color:var(--bad)}}pre{{margin:18px 0 0;min-height:280px;max-height:460px;overflow:auto;background:#201a1b;color:#f5eff0;border-radius:14px;padding:16px;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;overflow-wrap:anywhere}}code{{font-size:.92em}}.meta{{color:var(--muted);font-size:13px;margin-top:8px}}@media(max-width:900px){{.controls{{grid-template-columns:1fr}}}}@media(max-width:760px){{main{{padding:24px 14px}}.board{{grid-template-columns:repeat(5,78vw)}}}}
</style></head><body><main>
<h1>Document Sync</h1><p class='sub'>Local-only control panel · WIP rules, managed routing, and daily classification history.</p>
<section class='board' aria-label='Sync workflow'><article class='stage'><h2>1 · Preview</h2><p>Preview managed routes and accounting actions without changing source documents.</p></article><article class='stage'><h2>2 · Scan</h2><p>Index root files and configured WIP folders with the canonical rules.</p></article><article class='stage'><h2>3 · Review</h2><p>Review files and resolve items that require a human decision.</p></article><article class='stage'><h2>4 · Apply</h2><p>Apply managed routes and accounting rules, then verify the results.</p></article><article class='stage'><h2>5 · Verify</h2><p>Check the outcome and today’s classification history.</p></article></section>
<section class='controls'>
<article class='panel'><h2>Safe preview</h2><p>Runs the organizer with <code>--dry-run --plan-managed-rules --no-sync</code>, then refreshes the WIP index. Source documents are not moved, renamed, copied, or deleted.</p><div class='actions'><button id='preview'>Preview routing</button></div></article>
<article class='panel'><h2>Phân loại file</h2><p>Classifies root files and configured WIP folders. This updates only the local index and dashboard; <code>review_only</code> rules never move documents.</p><div class='actions'><button id='wip-scan' class='tonal'>Cập nhật phân loại</button></div></article>
<article class='panel'><h2>Apply managed routes</h2><p>Applies managed routes and existing invoice/UNC rules, then refreshes the file index.</p><label><input id='sync-project' type='checkbox'>Also run the final rclone project sync (external change)</label><div class='actions'><button id='apply' class='secondary'>Apply changes</button></div><div id='apply-confirm' class='confirm-row' role='status'><p>Apply managed routes and accounting actions now?</p><div class='actions'><button id='confirm-apply'>Yes, apply</button><button id='cancel-apply' class='secondary'>Cancel</button></div></div></article>
</section>
<section class='panel' style='margin-top:24px'><h2>System health · Intake</h2><p id='health-summary'>Checking watcher and timer…</p><p id='queue-summary'></p><details style='width:100%;margin-top:12px'><summary>Files and next actions</summary><div id='queue-files'></div></details><p class='meta'>CLI: docsync board · docsync status · docsync preview</p></section>
<div id='status' class='status'>Checking local runner…</div><p class='meta'>Only one run can execute at a time. This app listens only on 127.0.0.1 and requires this browser session’s request token.</p><pre id='logs' aria-live='polite'>No run yet.</pre>
</main><script>
const token={safe_token}, status=document.querySelector('#status'), logs=document.querySelector('#logs'), preview=document.querySelector('#preview'), wipScan=document.querySelector('#wip-scan'), apply=document.querySelector('#apply'), confirmApply=document.querySelector('#confirm-apply'), cancelApply=document.querySelector('#cancel-apply'), applyConfirm=document.querySelector('#apply-confirm'), syncProject=document.querySelector('#sync-project');
function setState(data){{const running=data.running, failed=!running&&data.exitCode!==null&&data.exitCode!==0, modeLabel=data.mode==='wip_scan'?'WIP scan':data.mode;status.textContent=data.exitCode===75?'Another organizer run is active. Retry after it finishes.':running?`Running ${{modeLabel}}…`:failed?`Last run failed (exit ${{data.exitCode}}).`:data.exitCode===0?'Last run completed successfully.':'Ready for a preview.';status.className='status '+(running?'':failed?'bad':'good');[preview,wipScan,apply,confirmApply,cancelApply].forEach(el=>el.disabled=running);syncProject.disabled=running;logs.textContent=data.logs.length?data.logs.join('\\n'):'No run yet.';logs.scrollTop=logs.scrollHeight;}}
async function refresh(){{try{{setState(await (await fetch('/api/status',{{cache:'no-store'}})).json())}}catch(error){{status.textContent='Unable to reach the local runner.';status.className='status bad';}}}}
async function run(mode){{applyConfirm.classList.remove('visible');const response=await fetch('/api/run',{{method:'POST',headers:{{'Content-Type':'application/json','X-CSRF-Token':token}},body:JSON.stringify({{mode:mode,syncProject:mode==='apply'&&syncProject.checked}})}});const data=await response.json();if(!response.ok){{status.textContent=data.message||'Run could not start.';status.className='status bad';}}setState(data);}}
async function refreshOverview(){{
  try{{
    const data=await (await fetch('/api/overview',{{cache:'no-store'}})).json();
    const watcher=data.services['ivar-watcher.service']||{{}}, timer=data.services['ivar-documents-organizer.timer']||{{}};
    document.querySelector('#health-summary').textContent=data.error||`Theo dõi file: ${{watcher.ActiveState==='active'?'Đang chạy':'Cần kiểm tra'}} · Lịch tự động: ${{timer.ActiveState==='active'?'Đang bật':'Cần kiểm tra'}} · Xử lý: ${{data.organizerBusy?'Đang chạy':'Sẵn sàng'}} · Lần hẹn giờ gần nhất: ${{data.lastTimerRun||'Chưa rõ'}} · Lượt kế tiếp: ${{data.nextRun?new Date(data.nextRun).toLocaleString('vi-VN'):'Chưa rõ'}}`;
    const labels={{needs_review:'Chờ duyệt',in_progress:'Đang xử lý',waiting_external:'Chờ bên ngoài',ready:'Sẵn sàng chuyển',filed:'Đã lưu hồ sơ',archived:'Lưu trữ'}};
    document.querySelector('#queue-summary').textContent=Object.entries(data.counts).map(([key,count])=>`${{labels[key]||key}}: ${{count}}`).join(' · ');
    const container=document.querySelector('#queue-files');container.replaceChildren();
    for(const card of data.cards){{const row=document.createElement('p');row.style.margin='12px 0';row.textContent=`${{card.path}} — ${{labels[card.stage]||card.stage}} · ${{card.reason}} · Tiếp theo: ${{card.next}}`;container.appendChild(row);}}
  }}catch(error){{document.querySelector('#health-summary').textContent='Unable to load system health.';}}
}}
refreshOverview();setInterval(refreshOverview,30000);
preview.onclick=()=>run('preview');wipScan.onclick=()=>run('wip_scan');apply.onclick=()=>applyConfirm.classList.add('visible');confirmApply.onclick=()=>run('apply');cancelApply.onclick=()=>applyConfirm.classList.remove('visible');refresh();setInterval(refresh,1000);
</script></body></html>"""


class AppHandler(BaseHTTPRequestHandler):
    server_version = "DocumentSync/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_document(self, status: HTTPStatus, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_request(self) -> bool:
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in allowed_hosts or (origin and origin not in {f"http://{value}" for value in allowed_hosts}):
            self.send_json(HTTPStatus.FORBIDDEN, {"message": "Local origin required."})
            return False
        return True

    def do_GET(self) -> None:
        if not self.local_request():
            return
        if self.path == "/":
            body = page(APP_STATE.csrf_token).encode("utf-8")
            self.send_document(HTTPStatus.OK, add_app_tabs(body, self.path))
            return
        if self.path in TAB_TEMPLATES:
            filename = TAB_TEMPLATES[self.path]
            template = (APP_ROOT / filename).read_text(encoding="utf-8")
            body = template.replace("__DOCSYNC_TOKEN__", json.dumps(APP_STATE.csrf_token)).encode("utf-8")
            self.send_document(HTTPStatus.OK, add_app_tabs(body, self.path))
            return
        if self.path == "/api/settings":
            self.settings_reply()
            return
        if self.path in APP_ASSETS:
            filename, content_type = APP_ASSETS[self.path]
            self.send_document(HTTPStatus.OK, (APP_ROOT / filename).read_bytes(), content_type + "; charset=utf-8")
            return
        path, _, query = self.path.partition("?")
        if path == "/api/day":
            try:
                params = parse_qs(query, max_num_fields=4)
                result = daily.list_day(APP_STATE.year_root, APP_STATE.file_key, params.get("date", [""])[0],
                                        int(params.get("page", ["1"])[0]))
                self.send_json(HTTPStatus.OK, result)
            except (ValueError, OverflowError):
                self.send_json(HTTPStatus.BAD_REQUEST, {"message": "Chọn ngày hoặc trang hợp lệ."})
            except (OSError, sqlite3.Error):
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"message": "Không đọc được danh sách WIP trên máy."})
            return
        if self.path == "/api/workflows":
            self.workflow_reply()
            return
        if self.path == "/kanban":
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/history")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/history":
            try:
                template = (APP_ROOT / "docsync-history.html").read_text(encoding="utf-8")
                options = "".join(f'<option value="{escape(command)}"></option>' for command in HISTORY_COMMANDS)
                template = (template.replace("__DOCSYNC_TOKEN__", escape(APP_STATE.csrf_token))
                            .replace("__COMMAND_OPTIONS__", options))
                body = render_today(APP_STATE.year_root, template, key=APP_STATE.file_key).encode("utf-8")
            except OSError:
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"message": "Không đọc được nhật ký phân loại trên máy."})
                return
            self.send_document(HTTPStatus.OK, add_app_tabs(body, self.path))
            return
        if self.path == "/api/overview":
            self.send_json(HTTPStatus.OK, APP_STATE.overview())
            return
        if self.path == "/api/status":
            self.send_json(HTTPStatus.OK, APP_STATE.snapshot())
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"message": "Not found."})

    def do_POST(self) -> None:
        if not self.local_request():
            return
        action = DAY_ACTIONS.get(self.path)
        if action is None and self.path not in ("/api/run", "/api/settings", "/api/workflows",
                                               "/api/workflows/credential", "/api/history/classify"):
            self.send_json(HTTPStatus.NOT_FOUND, {"message": "Not found."})
            return
        if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), APP_STATE.csrf_token):
            self.send_json(HTTPStatus.FORBIDDEN, {"message": "Invalid local request token."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 32768 if self.path == "/api/settings" else 16384 if action or self.path == "/api/history/classify" else 2048
            if not 2 <= length <= limit:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
            if action:
                self.day_reply(action, payload)
                return
            if self.path == "/api/history/classify":
                self.history_reply(payload)
                return
            if self.path == "/api/settings":
                self.settings_reply(payload)
                return
            if self.path != "/api/run":
                self.workflow_reply(payload)
                return
            if "command" in payload:
                command = payload["command"]
                mode = HISTORY_COMMANDS.get(" ".join(command.casefold().split())) if isinstance(command, str) else None
                if mode is None:
                    self.send_json(HTTPStatus.BAD_REQUEST, {"message": "Chọn một lệnh trong danh sách gợi ý."})
                    return
                sync_project = False
            else:
                mode = payload.get("mode")
                sync_project = payload.get("syncProject", False)
            if not isinstance(mode, str) or not isinstance(sync_project, bool):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self.send_json(HTTPStatus.BAD_REQUEST, {"message": "Invalid run request."})
            return
        started, message = APP_STATE.start(mode, sync_project)
        self.send_json(HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT, {"message": message, **APP_STATE.snapshot()})

    def history_reply(self, payload: dict[str, Any]) -> None:
        try:
            with APP_STATE.lock:
                if APP_STATE.running:
                    raise BlockingIOError
                result = decide_history_file(APP_STATE.year_root, state_directory(), APP_STATE.file_key, payload)
            with APP_STATE.health_lock:
                APP_STATE.health_cached_at = 0
            self.send_json(HTTPStatus.OK, result)
        except BlockingIOError:
            self.send_json(HTTPStatus.CONFLICT, {"message": "DocSync đang xử lý file khác. Thử lại sau khi lượt hiện tại hoàn tất."})
        except FileExistsError:
            self.send_json(HTTPStatus.CONFLICT, {"message": "Đích đã có file cùng tên. File nguồn vẫn ở chỗ cũ."})
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"message": str(error)})
        except (OSError, RuntimeError, sqlite3.Error) as error:
            print(f"History classification failed: {error}", file=sys.stderr, flush=True)
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"message": "Chưa xác nhận được kết quả. Làm mới lịch sử trước khi thử lại."})

    def settings_reply(self, payload: dict[str, Any] | None = None) -> None:
        try:
            if payload is not None:
                with APP_STATE.lock:
                    if APP_STATE.running:
                        self.send_json(HTTPStatus.CONFLICT, {"message": "Đợi lượt phân loại hiện tại hoàn tất rồi lưu lại."})
                        return
                    save_workspace_paths(payload.get("paths"))
                with APP_STATE.health_lock:
                    APP_STATE.health_cached_at = 0
            self.send_json(HTTPStatus.OK, settings_payload())
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"message": str(error)})
        except OSError:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"message": "Không đọc hoặc lưu được cấu hình thư mục DocSync."})

    def day_reply(self, action: str, payload: dict[str, Any]) -> None:
        try:
            result = daily.dispatch(APP_STATE.year_root, APP_STATE.file_key, action, payload)
            self.send_json(HTTPStatus.OK, result)
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"message": str(error)})
        except (OSError, sqlite3.Error):
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"message": "Không đọc được file hoặc cấu hình trên máy."})

    def workflow_reply(self, payload: dict[str, Any] | None = None) -> None:
        try:
            if self.path == "/api/workflows/credential":
                workflows.save_credential(APP_STATE.year_root, payload.get("user"), payload.get("password"))
            elif payload is not None:
                key = payload.get("id")
                if not isinstance(key, str):
                    raise ValueError("Workflow không hợp lệ.")
                workflows.set_enabled(APP_STATE.year_root, key, payload.get("enabled"),
                                      draft_batches=payload.get("draft_batches"))
            result = workflows.overview(APP_STATE.year_root)
            self.send_json(HTTPStatus.OK, result)
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"message": str(error)})
        except (OSError, sqlite3.Error):
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"message": "Không đọc hoặc lưu được cấu hình Workflows trên máy."})


def serve(port: int, open_browser: bool) -> None:
    server = ThreadingHTTPServer((LOCAL_HOST, port), AppHandler)
    address = f"http://{LOCAL_HOST}:{port}/"
    print(f"Document Sync is running at {address}")
    if open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDocument Sync stopped.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true", help="open the local control panel in the default browser")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    serve(args.port, args.open)


if __name__ == "__main__":
    main()
