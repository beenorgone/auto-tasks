#!/usr/bin/env python3
"""Local Material-style control panel for the IVAR document synchronization job."""

from __future__ import annotations

import argparse
import collections
import json
import secrets
import subprocess
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parent
SYNC_SCRIPT = APP_ROOT / "ivar-documents-organizer.sh"
LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_LOG_LINES = 400


class RunState:
    """One-at-a-time subprocess runner with a bounded, browser-safe log feed."""

    def __init__(self, script: Path = SYNC_SCRIPT) -> None:
        self.script = script
        self.csrf_token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.running = False
        self.mode = "idle"
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.exit_code: int | None = None
        self.logs: collections.deque[str] = collections.deque(maxlen=MAX_LOG_LINES)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "mode": self.mode,
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                "exitCode": self.exit_code,
                "logs": list(self.logs),
            }

    def start(self, mode: str, sync_project: bool) -> tuple[bool, str]:
        if mode not in {"preview", "apply"}:
            return False, "Unknown run mode."
        if not self.script.is_file():
            return False, f"Sync script is unavailable: {self.script}"
        with self.lock:
            if self.running:
                return False, "A document sync run is already in progress."
            self.running = True
            self.mode = mode
            self.started_at = time.time()
            self.finished_at = None
            self.exit_code = None
            self.logs.clear()
            args = ["/bin/sh", str(self.script), "--plan-managed-rules" if mode == "preview" else "--apply-managed-rules"]
            if mode == "preview" or not sync_project:
                args.append("--no-sync")
            self.logs.append("$ " + " ".join(args))
        threading.Thread(target=self._run, args=(args,), daemon=True).start()
        return True, "Run started."

    def _run(self, args: list[str]) -> None:
        exit_code = 1
        try:
            process = subprocess.Popen(
                args,
                cwd=self.script.parent,
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


def page(token: str) -> str:
    safe_token = json.dumps(token)
    return f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Document Sync</title><style>
:root{{--primary:#6750a4;--on-primary:#fff;--surface:#fffbfe;--surface-2:#f4eff7;--outline:#79747e;--text:#1d1b20;--good:#146c2e;--warn:#a03f00}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--surface);color:var(--text);font:15px/1.45 Roboto,Arial,sans-serif}}main{{max-width:1160px;margin:auto;padding:32px 20px 48px}}h1{{margin:0;font-size:32px;letter-spacing:-.4px}}.sub{{color:#5f5963;margin:7px 0 26px}}.board{{display:grid;grid-template-columns:repeat(4,minmax(210px,1fr));gap:16px;overflow:auto;padding-bottom:8px}}.stage,.panel{{background:var(--surface-2);border-radius:18px;padding:18px;border:1px solid #e6e0e9}}.stage h2{{font-size:16px;margin:0 0 8px}}.stage p{{margin:0;color:#5f5963}}.stage.active{{border-color:var(--primary);box-shadow:0 1px 3px #0002}}.controls{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:24px}}.panel h2{{font-size:19px;margin:0 0 8px}}.panel p{{margin:0 0 16px;color:#5f5963}}button{{border:0;border-radius:999px;background:var(--primary);color:var(--on-primary);padding:11px 18px;font:600 14px Roboto,Arial;cursor:pointer;box-shadow:0 1px 2px #0003}}button.secondary{{background:#e8def8;color:#21005d}}button:disabled{{opacity:.55;cursor:not-allowed}}label{{display:flex;gap:9px;align-items:flex-start;margin:16px 0;color:#403b43}}input{{margin-top:3px;accent-color:var(--primary)}}.status{{margin-top:22px;padding:13px 15px;border-radius:12px;background:#e8def8;color:#21005d;font-weight:600}}.status.good{{background:#d8f6dd;color:var(--good)}}.status.bad{{background:#ffdad4;color:#8c1d18}}pre{{margin:18px 0 0;min-height:280px;max-height:460px;overflow:auto;background:#201a1b;color:#f5eff0;border-radius:14px;padding:16px;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;overflow-wrap:anywhere}}.meta{{color:#5f5963;font-size:13px;margin-top:8px}}@media(max-width:760px){{main{{padding:24px 14px}}.board{{grid-template-columns:repeat(4,78vw)}}.controls{{grid-template-columns:1fr}}}}
</style></head><body><main>
<h1>Document Sync</h1><p class='sub'>Local-only control panel · Preview first, then apply when the destinations look correct.</p>
<section class='board' aria-label='Sync workflow'><article class='stage'><h2>1 · Preview</h2><p>List managed routes and folder-name changes. No files move.</p></article><article class='stage'><h2>2 · Review</h2><p>Read the live log and confirm destinations, especially collisions.</p></article><article class='stage'><h2>3 · Apply</h2><p>Move approved managed documents and normalize folder names.</p></article><article class='stage'><h2>4 · Verify</h2><p>Confirm exit code 0 and review the final recorded actions.</p></article></section>
<section class='controls'><article class='panel'><h2>Safe preview</h2><p>Runs <code>--plan-managed-rules --no-sync</code>. It cannot rename, move, or sync files.</p><button id='preview'>Preview routing</button></article><article class='panel'><h2>Apply managed routes</h2><p>Moves only approved current-year-root files and renames eligible <code>AT.</code> folders.</p><label><input id='sync-project' type='checkbox'>Also run the final rclone project sync (external change)</label><button id='apply' class='secondary'>Apply changes</button></article></section>
<div id='status' class='status'>Checking local runner…</div><p class='meta'>Only one run can execute at a time. This app listens only on 127.0.0.1 and requires this browser session’s request token.</p><pre id='logs' aria-live='polite'>No run yet.</pre>
</main><script>
const token={safe_token}, status=document.querySelector('#status'), logs=document.querySelector('#logs'), preview=document.querySelector('#preview'), apply=document.querySelector('#apply'), syncProject=document.querySelector('#sync-project');
function setState(data){{const running=data.running, failed=!running&&data.exitCode!==null&&data.exitCode!==0;status.textContent=running?`Running ${{data.mode}}…`:failed?`Last run failed (exit ${{data.exitCode}}).` : data.exitCode===0?'Last run completed successfully.':'Ready for a preview.';status.className='status '+(running?'':failed?'bad':'good');preview.disabled=running;apply.disabled=running;logs.textContent=data.logs.length?data.logs.join('\n'):'No run yet.';logs.scrollTop=logs.scrollHeight;}}
async function refresh(){{try{{setState(await (await fetch('/api/status',{{cache:'no-store'}})).json())}}catch(error){{status.textContent='Unable to reach the local runner.';status.className='status bad';}}}}
async function run(mode){{if(mode==='apply'&&!confirm('Apply managed routes and folder-name changes now?')) return;const response=await fetch('/api/run',{{method:'POST',headers:{{'Content-Type':'application/json','X-CSRF-Token':token}},body:JSON.stringify({{mode:mode,syncProject:mode==='apply'&&syncProject.checked}})}});const data=await response.json();if(!response.ok){{status.textContent=data.message||'Run could not start.';status.className='status bad';}}await refresh();}}
preview.onclick=()=>run('preview');apply.onclick=()=>run('apply');refresh();setInterval(refresh,1000);
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
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/":
            body = page(APP_STATE.csrf_token).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/status":
            self.send_json(HTTPStatus.OK, APP_STATE.snapshot())
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"message": "Not found."})

    def do_POST(self) -> None:
        if self.path != "/api/run":
            self.send_json(HTTPStatus.NOT_FOUND, {"message": "Not found."})
            return
        if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), APP_STATE.csrf_token):
            self.send_json(HTTPStatus.FORBIDDEN, {"message": "Invalid local request token."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 2 <= length <= 2048:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
            mode = payload.get("mode")
            sync_project = payload.get("syncProject", False)
            if not isinstance(mode, str) or not isinstance(sync_project, bool):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self.send_json(HTTPStatus.BAD_REQUEST, {"message": "Invalid run request."})
            return
        started, message = APP_STATE.start(mode, sync_project)
        self.send_json(HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT, {"message": message, **APP_STATE.snapshot()})


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
