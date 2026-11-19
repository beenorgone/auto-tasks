#!/usr/bin/env python3
"""Local Material-style control panel for the IVAR document synchronization job."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import secrets
import shlex
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from docsync_core import board, health, WIP
from docsync_paths import work_root
import docsync_erp as ERP
import docsync_eim as EIM
import sqlite3
import urllib.parse


APP_ROOT = Path(__file__).resolve().parent
SYNC_SCRIPT = APP_ROOT / "ivar-documents-organizer.sh"
WIP_SCRIPT = APP_ROOT / "wip-organizer.py"
DOCUMENTS_ROOT = Path.home() / "Documents"
LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_LOG_LINES = 400
MAX_ERP_BODY = 64 * 1024
MAX_PUSH_PATHS = 20


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
        self.year_root = year_root or work_root()
        self.dashboard_path = self.year_root / ".wip" / "dashboard.html"
        self.health_cache = {}
        self.health_cached_at = 0.0
        self.health_lock = threading.Lock()
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
                "kanbanAvailable": self.dashboard_path.is_file(),
                "yearRoot": str(self.year_root),
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
        if not self.year_root.is_dir():
            return False, f"WIP & Reference folder is unavailable: {self.year_root}"
        with self.lock:
            if self.running:
                return False, "A document sync run is already in progress."
            self.running = True
            self.mode = mode
            self.started_at = time.time()
            self.finished_at = None
            self.exit_code = None
            self.logs.clear()
            commands = self.commands_for(mode, sync_project)
        threading.Thread(target=self._run, args=(commands,), daemon=True).start()
        return True, "Run started."

    def commands_for(self, mode: str, sync_project: bool) -> list[list[str]]:
        wip_base = [sys.executable, str(self.wip_script), "--year-root", str(self.year_root)]
        wip_commands = [
            [*wip_base, "scan"],
            [*wip_base, "dashboard", "--output", str(self.dashboard_path)],
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

    def _run(self, commands: list[list[str]]) -> None:
        exit_code = 1
        try:
            for args in commands:
                with self.lock:
                    self.logs.append("$ " + shlex.join(args))
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


ERP_STYLE = """<style>
.erp-today{margin-top:24px;align-items:stretch!important}.erp-today .toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:12px 0}
.erp-today input[type=date],.erp-today input[type=search],.erp-today select,.erp-today textarea{font:14px Roboto,Arial;padding:8px 10px;border:1px solid var(--outline);border-radius:10px;background:#fff;color:var(--text);margin:0}
.erp-today table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden}
.erp-today th,.erp-today td{padding:8px 10px;border-bottom:1px solid #e6e0e9;text-align:left;vertical-align:top;font-size:13px}
.erp-today th{background:#efe9f4;font-weight:600}.erp-today tr.group td{background:#f7f2fa;font-weight:700;color:#4a4458}
.erp-today .path{overflow-wrap:anywhere;max-width:420px}.erp-today .muted{color:var(--muted);font-size:12px}
.erp-badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;background:#e8def8;color:#21005d}
.erp-badge.pushed,.erp-badge.duplicate{background:#d8f6dd;color:var(--good)}.erp-badge.error,.erp-badge.stale{background:#ffdad4;color:var(--bad)}
.erp-badge.assigned{background:#fff4e7;color:#5d3a00}.erp-editor{display:grid;gap:8px;padding:12px;background:#f7f2fa;border-radius:12px;margin-top:8px}
.erp-editor .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.erp-editor label{margin:0;align-items:center}
.erp-today button.small{padding:6px 12px;font-size:12px}.erp-scroll{overflow:auto}
@media(max-width:760px){.erp-today td.path{max-width:60vw}}
</style>"""

ERP_HTML = """<section class='panel erp-today' aria-labelledby='erp-today-title'>
<h2 id='erp-today-title'>Hôm nay → ERP IVAR</h2>
<p>Tổng hợp file mới tải / sửa / được sắp xếp trong ngày theo nhóm. Chọn bản ghi + field cho từng file, tùy chọn AI tóm tắt / phân tích thành Ghi chú AI, rồi tick và đẩy lên ERP. Không có file nào tự đẩy.</p>
<div class='toolbar'><input id='erp-date' type='date' aria-label='Ngày'><button id='erp-refresh' class='tonal'>Tải bảng ngày</button>
<label><input id='erp-only-open' type='checkbox' checked>Ẩn file đã lên ERP</label>
<button id='erp-push' class='secondary' disabled>Đẩy file đã chọn lên ERP</button><span id='erp-summary' class='muted'></span></div>
<div id='erp-status' class='status' hidden></div>
<div class='erp-scroll'><table><thead><tr><th><input id='erp-all' type='checkbox' aria-label='Chọn tất cả file đã gán'></th><th>File</th><th>ERP</th><th></th></tr></thead><tbody id='erp-rows'><tr><td colspan='4' class='muted'>Bấm “Tải bảng ngày”.</td></tr></tbody></table></div>
</section>"""

ERP_SCRIPT = r"""<script>
(()=>{
const $=s=>document.querySelector(s), rows=$('#erp-rows'), statusBox=$('#erp-status'), pushBtn=$('#erp-push');
const TYPES={po:'Đơn mua (PO)',ir:'Phiếu nhập kho',qc_test:'Kiểm nghiệm (QC)',tcb:'Tự công bố (TCB)',seller:'Nhà cung cấp',bom:'Định mức (BoM)',qtbm:'Quy trình / biểu mẫu',pth:'Phiếu thu hồi',mo:'Lệnh sản xuất (MO)',so:'Đơn bán (SO)'};
const LABELS={unassigned:'Chưa gán',assigned:'Chờ đẩy',stale:'File đã đổi — gán lại',pushed:'Đã lên ERP',duplicate:'ERP đã có',error:'Lỗi'};
let data=null;
const el=(tag,props={},...kids)=>{const n=document.createElement(tag);for(const[k,v]of Object.entries(props)){if(k==='class')n.className=v;else if(k in n)n[k]=v;else n.setAttribute(k,v);}for(const c of kids)if(c!=null)n.append(c);return n;};
function say(text,kind){statusBox.hidden=!text;statusBox.textContent=text||'';statusBox.className='status '+(kind||'');}
async function api(path,body){
  const opts=body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(body)}:{cache:'no-store'};
  const res=await fetch(path,opts);let json={};try{json=await res.json();}catch(e){}
  if(!res.ok)throw new Error(json.message||('HTTP '+res.status));return json;
}
function selected(){return [...rows.querySelectorAll('input[data-push]:checked')].map(i=>i.dataset.push);}
function syncPush(){const n=selected().length;pushBtn.disabled=!n;pushBtn.textContent=n?`Đẩy ${n} file lên ERP`:'Đẩy file đã chọn lên ERP';}
function render(){
  rows.replaceChildren();if(!data)return;
  const hide=$('#erp-only-open').checked;let shown=0;
  $('#erp-summary').textContent=`${data.total} file · `+Object.entries(data.counts).filter(([,v])=>v).map(([k,v])=>`${LABELS[k]}: ${v}`).join(' · ');
  for(const group of data.groups){
    const files=group.files.filter(f=>!hide||!['pushed','duplicate'].includes(f.erp));if(!files.length)continue;
    rows.append(el('tr',{class:'group'},el('td',{colSpan:4,textContent:`${group.category} (${files.length})`})));
    for(const f of files){shown++;rows.append(row(f));}
  }
  if(!shown)rows.append(el('tr',{},el('td',{colSpan:4,class:'muted',textContent:'Không có file nào cần xử lý trong ngày này.'})));
  syncPush();
}
function row(f){
  const a=f.assignment, canPush=a&&['assigned','error'].includes(f.erp);
  const box=el('input',{type:'checkbox',disabled:!canPush,title:canPush?'Chọn để đẩy':'Gán bản ghi trước'});if(canPush){box.dataset.push=f.path;box.onchange=syncPush;}
  const info=el('td',{class:'path'},el('strong',{textContent:f.name}),el('div',{class:'muted',textContent:`${f.path} · ${Math.ceil(f.size/1024)} KB · ${f.modified.replace('T',' ')}`}),el('div',{class:'muted',textContent:f.reason}));
  if(f.too_large)info.append(el('div',{class:'muted',textContent:'Quá 40 MB — không đẩy được.'}));
  const erp=el('td',{},el('span',{class:'erp-badge '+f.erp,textContent:LABELS[f.erp]||f.erp}));
  if(a){erp.append(el('div',{class:'muted',textContent:`${TYPES[a.type]||a.type} · ${a.label||('#'+a.id)} › ${a.field_label||a.field}${a.ai?' · AI '+(a.ai_mode==='analysis'?'phân tích':'tóm tắt'):''}`}));
    if(a.message)erp.append(el('div',{class:'muted',textContent:a.message}));
    if(a.record_url)erp.append(el('a',{href:a.record_url,target:'_blank',rel:'noopener noreferrer',textContent:'Mở bản ghi'}));}
  else if(f.eim)erp.append(el('div',{class:'muted',textContent:`EIM tự đẩy: ${f.eim.status==='sent'?'đã gửi':f.eim.status==='duplicate'?'đã có':f.eim.status==='retry'?'sẽ thử lại':'bị từ chối'} · ${f.eim.message||''}`}));
  else if(f.suggestion)erp.append(el('div',{class:'muted',textContent:`Gợi ý: ${f.suggestion.label}${f.suggestion.query?' · tìm “'+f.suggestion.query+'”':''}`}));
  const act=el('td',{});const tr=el('tr',{},el('td',{},box),info,erp,act);
  if(!f.too_large){const btn=el('button',{class:'small tonal',textContent:a?'Sửa gán':'Gán bản ghi'});btn.onclick=()=>openEditor(f,info,btn);act.append(btn);}
  if(a){const rm=el('button',{class:'small secondary',textContent:'Bỏ gán'});rm.onclick=async()=>{try{await api('/api/erp/unassign',{path:f.path});await load();}catch(e){say(e.message,'bad');}};act.append(rm);}
  return tr;
}
function openEditor(f,cell,btn){
  if(cell.querySelector('.erp-editor')){cell.querySelector('.erp-editor').remove();return;}
  const a=f.assignment||{}, s=f.suggestion||{};
  const type=el('select',{'aria-label':'Loại bản ghi'});for(const[k,v]of Object.entries(TYPES))type.append(el('option',{value:k,textContent:v}));type.value=a.type||s.type||'po';
  const q=el('input',{type:'search',placeholder:'Mã / tên / ID bản ghi',value:a.id?String(a.id):(s.query||'')});
  const find=el('button',{class:'small',textContent:'Tìm'});const pick=el('select',{'aria-label':'Bản ghi'},el('option',{value:'',textContent:'— chọn bản ghi —'}));
  const field=el('select',{'aria-label':'Field file'},el('option',{value:'',textContent:'— chọn field —'}));
  const ai=el('select',{'aria-label':'AI'},el('option',{value:'none',textContent:'Không dùng AI'}),el('option',{value:'summary',textContent:'AI tóm tắt → Ghi chú AI'}),el('option',{value:'analysis',textContent:'AI phân tích → Ghi chú AI'}));ai.value=a.ai?(a.ai_mode||'summary'):'none';
  const ins=el('textarea',{rows:2,maxLength:2000,placeholder:'Yêu cầu riêng cho AI (tuỳ chọn)',value:a.instruction||''});
  const save=el('button',{class:'small',textContent:'Lưu gán',disabled:true});const note=el('span',{class:'muted'});
  const ed=el('div',{class:'erp-editor'},el('div',{class:'row'},type,q,find),el('div',{class:'row'},pick),el('div',{class:'row'},field,ai),ins,el('div',{class:'row'},save,note));
  cell.append(ed);
  let labels={};
  async function search(){note.textContent='Đang tìm…';pick.length=1;field.length=1;save.disabled=true;
    try{const r=await api('/api/erp/targets?'+new URLSearchParams({type:type.value,q:q.value}));
      for(const x of r.records){pick.append(el('option',{value:String(x.id),textContent:`#${x.id} · ${x.label}${x.extra?' · '+x.extra:''}${x.date?' · '+x.date:''}`}));labels[x.id]=x.label;}
      note.textContent=r.records.length?`${r.records.length} bản ghi bạn được sửa`:'Không thấy bản ghi (hoặc không có quyền sửa).';
      if(a.id&&type.value===a.type){pick.value=String(a.id);if(pick.value)await fields();}else if(r.records.length===1){pick.value=String(r.records[0].id);await fields();}
    }catch(e){note.textContent=e.message;}}
  async function fields(){field.length=1;save.disabled=true;if(!pick.value)return;note.textContent='Đang đọc field…';
    try{const r=await api('/api/erp/record?'+new URLSearchParams({type:type.value,id:pick.value}));
      for(const x of r.fields)field.append(el('option',{value:x.key,textContent:`${x.label} (${x.count}${x.max?'/'+x.max:''})`}));
      const want=a.field&&type.value===a.type?a.field:(s.type===type.value?s.field:'');if(want&&r.fields.some(x=>x.key===want))field.value=want;
      note.textContent=r.ai_available?'':'AI tài liệu đang tắt trên ERP.';save.disabled=!field.value;
    }catch(e){note.textContent=e.message;}}
  find.onclick=search;q.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();search();}};type.onchange=search;pick.onchange=fields;field.onchange=()=>{save.disabled=!field.value;};
  save.onclick=async()=>{save.disabled=true;note.textContent='Đang lưu…';
    try{await api('/api/erp/assign',{path:f.path,type:type.value,id:Number(pick.value),field:field.value,label:labels[pick.value]||'',field_label:field.selectedOptions[0].textContent.replace(/ \(.*\)$/,''),ai:ai.value!=='none',ai_mode:ai.value==='analysis'?'analysis':'summary',instruction:ins.value});
      await load(true);say('Đã gán. Tick file rồi bấm đẩy lên ERP.','good');}catch(e){note.textContent=e.message;save.disabled=false;}};
  search();
}
async function load(keep){if(!keep)say('');try{data=await api('/api/today?'+new URLSearchParams({date:$('#erp-date').value}));render();}catch(e){say(e.message,'bad');}}
$('#erp-date').value=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
$('#erp-refresh').onclick=()=>load();$('#erp-only-open').onchange=render;
$('#erp-all').onchange=e=>{rows.querySelectorAll('input[data-push]').forEach(i=>{i.checked=e.target.checked;});syncPush();};
pushBtn.onclick=async()=>{const paths=selected();if(!paths.length)return;if(!confirm(`Đẩy ${paths.length} file lên ERP? File sẽ được gắn vào bản ghi đã chọn.`))return;
  pushBtn.disabled=true;say(`Đang đẩy ${paths.length} file…`);
  try{const r=await api('/api/erp/push',{paths});const bad=r.results.filter(x=>x.status==='error');
    say(bad.length?`${r.results.length-bad.length} xong, ${bad.length} lỗi: `+bad.map(x=>x.path+' — '+x.message).join(' | '):`Đã xử lý ${r.results.length} file.`,bad.length?'bad':'good');}
  catch(e){say(e.message,'bad');}await load(true);};
load();
})();
</script>"""


def page(token: str) -> str:
    return _base_page(token).replace("</main>", ERP_HTML + "</main>", 1).replace("</head>", ERP_STYLE + "</head>", 1).replace("</body>", ERP_SCRIPT + "</body>", 1)


def _base_page(token: str) -> str:
    safe_token = json.dumps(token)
    return f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Document Sync</title><style>
:root{{--primary:#6750a4;--on-primary:#fff;--surface:#fffbfe;--surface-2:#f4eff7;--outline:#79747e;--text:#1d1b20;--muted:#5f5963;--good:#146c2e;--bad:#8c1d18}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--surface);color:var(--text);font:15px/1.45 Roboto,Arial,sans-serif}}main{{max-width:1240px;margin:auto;padding:32px 20px 48px}}h1{{margin:0;font-size:32px;letter-spacing:-.4px}}.sub{{color:var(--muted);margin:7px 0 26px}}.board{{display:grid;grid-template-columns:repeat(5,minmax(190px,1fr));gap:14px;overflow:auto;padding-bottom:8px}}.stage,.panel{{background:var(--surface-2);border-radius:18px;padding:18px;border:1px solid #e6e0e9}}.stage h2{{font-size:16px;margin:0 0 8px}}.stage p,.panel p{{margin:0;color:var(--muted)}}.stage.active{{border-color:var(--primary);box-shadow:0 1px 3px #0002}}.controls{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-top:24px}}.panel{{display:flex;flex-direction:column;align-items:flex-start}}.panel h2{{font-size:19px;margin:0 0 8px}}.actions{{display:flex;flex-wrap:wrap;gap:10px;margin-top:auto;padding-top:18px}}button,.button-link{{border:0;border-radius:999px;background:var(--primary);color:var(--on-primary);padding:11px 18px;font:600 14px Roboto,Arial;cursor:pointer;box-shadow:0 1px 2px #0003;text-decoration:none}}button.secondary,.button-link.secondary{{background:#e8def8;color:#21005d}}button.tonal{{background:#d8e2ff;color:#17315f}}button:disabled,.button-link.disabled{{opacity:.55;cursor:not-allowed;pointer-events:none}}label{{display:flex;gap:9px;align-items:flex-start;margin:16px 0 0;color:#403b43}}input{{margin-top:3px;accent-color:var(--primary)}}.confirm-row{{display:none;margin-top:14px;padding:12px;border-radius:12px;background:#fff4e7;color:#5d3a00}}.confirm-row.visible{{display:block}}.confirm-row p{{color:inherit;margin-bottom:10px}}.status{{margin-top:22px;padding:13px 15px;border-radius:12px;background:#e8def8;color:#21005d;font-weight:600}}.status.good{{background:#d8f6dd;color:var(--good)}}.status.bad{{background:#ffdad4;color:var(--bad)}}pre{{margin:18px 0 0;min-height:280px;max-height:460px;overflow:auto;background:#201a1b;color:#f5eff0;border-radius:14px;padding:16px;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;overflow-wrap:anywhere}}code{{font-size:.92em}}.meta{{color:var(--muted);font-size:13px;margin-top:8px}}@media(max-width:900px){{.controls{{grid-template-columns:1fr}}}}@media(max-width:760px){{main{{padding:24px 14px}}.board{{grid-template-columns:repeat(5,78vw)}}}}
</style></head><body><main>
<h1>Document Sync</h1><p class='sub'>Local-only control panel · WIP rules, Kanban review, managed routing, and verification in one flow.</p>
<section class='board' aria-label='Sync workflow'><article class='stage'><h2>1 · Preview</h2><p>Preview managed routes and accounting actions without changing source documents.</p></article><article class='stage'><h2>2 · Scan</h2><p>Index root files and configured WIP folders with the canonical rules.</p></article><article class='stage'><h2>3 · Review</h2><p>Open the Kanban board and resolve items that require a human decision.</p></article><article class='stage'><h2>4 · Apply</h2><p>Apply managed routes and accounting rules, then verify the results.</p></article><article class='stage'><h2>5 · Verify</h2><p>Refresh Kanban, confirm exit code 0, and inspect recorded actions.</p></article></section>
<section class='controls'>
<article class='panel'><h2>Safe preview</h2><p>Runs the organizer with <code>--dry-run --plan-managed-rules --no-sync</code>, then refreshes the WIP index and Kanban. Source documents are not moved, renamed, copied, or deleted.</p><div class='actions'><button id='preview'>Preview routing</button></div></article>
<article class='panel'><h2>WIP / Kanban</h2><p>Classifies root files and configured WIP folders. This updates only the local index and dashboard; <code>review_only</code> rules never move documents.</p><div class='actions'><button id='wip-scan' class='tonal'>Refresh Kanban</button><a id='open-kanban' class='button-link secondary disabled' href='/kanban' target='_blank' rel='noopener' aria-disabled='true'>Open Kanban</a></div></article>
<article class='panel'><h2>Apply managed routes</h2><p>Runs managed routes, existing invoice/UNC and intake rules, files reports under <code>20_BAO_CAO</code>, then refreshes Kanban.</p><label><input id='sync-project' type='checkbox'>Also run the final rclone project sync (external change)</label><div class='actions'><button id='apply' class='secondary'>Apply changes</button></div><div id='apply-confirm' class='confirm-row' role='status'><p>Apply managed routes and accounting actions now?</p><div class='actions'><button id='confirm-apply'>Yes, apply</button><button id='cancel-apply' class='secondary'>Cancel</button></div></div></article>
</section>
<section class='panel' style='margin-top:24px'><h2>System health · Intake Kanban</h2><p id='health-summary'>Checking watcher and timer…</p><p id='queue-summary'></p><details style='width:100%;margin-top:12px'><summary>Files and next actions</summary><div id='queue-files'></div></details><p class='meta'>CLI: docsync board · docsync status · docsync preview</p></section>
<div id='status' class='status'>Checking local runner…</div><p class='meta'>Only one run can execute at a time. This app listens only on 127.0.0.1 and requires this browser session’s request token.</p><pre id='logs' aria-live='polite'>No run yet.</pre>
</main><script>
const token={safe_token}, status=document.querySelector('#status'), logs=document.querySelector('#logs'), preview=document.querySelector('#preview'), wipScan=document.querySelector('#wip-scan'), apply=document.querySelector('#apply'), confirmApply=document.querySelector('#confirm-apply'), cancelApply=document.querySelector('#cancel-apply'), applyConfirm=document.querySelector('#apply-confirm'), syncProject=document.querySelector('#sync-project'), openKanban=document.querySelector('#open-kanban');
function setState(data){{const running=data.running, failed=!running&&data.exitCode!==null&&data.exitCode!==0, modeLabel=data.mode==='wip_scan'?'WIP scan':data.mode;status.textContent=data.exitCode===75?'Another organizer run is active. Retry after it finishes.':running?`Running ${{modeLabel}}…`:failed?`Last run failed (exit ${{data.exitCode}}).`:data.exitCode===0?'Last run completed successfully.':'Ready for a preview.';status.className='status '+(running?'':failed?'bad':'good');[preview,wipScan,apply,confirmApply,cancelApply].forEach(el=>el.disabled=running);syncProject.disabled=running;openKanban.classList.toggle('disabled',!data.kanbanAvailable);openKanban.setAttribute('aria-disabled',String(!data.kanbanAvailable));logs.textContent=data.logs.length?data.logs.join('\\n'):'No run yet.';logs.scrollTop=logs.scrollHeight;}}
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
preview.onclick=()=>run('preview');wipScan.onclick=()=>run('wip_scan');apply.onclick=()=>applyConfirm.classList.add('visible');confirmApply.onclick=()=>run('apply');cancelApply.onclick=()=>applyConfirm.classList.remove('visible');openKanban.onclick=event=>{{if(openKanban.getAttribute('aria-disabled')==='true') event.preventDefault();}};refresh();setInterval(refresh,1000);
</script></body></html>"""


def erp_classifier(year_root: Path):
    rule_file = WIP.rules_path(year_root)
    rules = json.loads(rule_file.read_text()) if rule_file.exists() else WIP.DEFAULT_RULES
    return lambda relative, path: WIP.classify(relative, rules, path)


def erp_get(path: str, query: dict, year_root: Path, client_factory=None) -> dict:
    """Read-only ERP/today endpoints. Raises ERP.ErpError / ValueError with a user-facing message."""
    one = lambda key: (query.get(key) or [''])[0]
    if path == '/api/today':
        day = dt.date.fromisoformat(one('date')) if one('date') else None
        return ERP.today(year_root, day, erp_classifier(year_root), eim_status=EIM.status_for_paths(ERP.STATE))
    client = (client_factory or (lambda: ERP.ErpClient(ERP.load_config())))()
    kind = one('type')
    if kind not in ERP.TYPES:
        raise ValueError('Loại bản ghi không hợp lệ.')
    if path == '/api/erp/targets':
        return {'records': client.targets(kind, one('q')[:100])}
    record_id = int(one('id') or 0)
    if record_id <= 0:
        raise ValueError('Thiếu ID bản ghi.')
    return client.record(kind, record_id)


def erp_post(path: str, payload: dict, year_root: Path, client_factory=None) -> dict:
    if path == '/api/erp/unassign':
        return {'removed': ERP.unassign(str(payload.get('path', '')))}
    if path == '/api/erp/assign':
        kind, record_id, field = str(payload.get('type', '')), int(payload.get('id') or 0), str(payload.get('field', ''))
        client = (client_factory or (lambda: ERP.ErpClient(ERP.load_config())))()
        # Re-read the record so a forged or stale field never reaches the assignment index.
        record = client.record(kind, record_id)
        fields = {item['key']: item for item in record.get('fields', [])}
        if field not in fields:
            raise ValueError('Bản ghi không có field này.')
        saved = ERP.assign(year_root, str(payload.get('path', '')), kind, record_id, field,
                           label=str(record.get('record', {}).get('label', '')), field_label=str(fields[field]['label']),
                           ai=payload.get('ai') is True, ai_mode=str(payload.get('ai_mode', 'summary')),
                           instruction=str(payload.get('instruction', ''))[:2000])
        return {'assignment': ERP.public_assignment(saved)}
    if path == '/api/erp/push':
        paths = payload.get('paths')
        if not isinstance(paths, list) or not paths or len(paths) > MAX_PUSH_PATHS or not all(isinstance(p, str) for p in paths):
            raise ValueError(f'Chọn 1–{MAX_PUSH_PATHS} file.')
        client = (client_factory or (lambda: ERP.ErpClient(ERP.load_config())))()
        return {'results': ERP.push(year_root, client, paths)}
    raise LookupError


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

    def send_html(self, status: HTTPStatus, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
            self.send_html(HTTPStatus.OK, body)
            return
        if self.path == "/kanban":
            dashboard = APP_STATE.dashboard_path
            state_root = (APP_STATE.year_root / ".wip").resolve()
            try:
                resolved = dashboard.resolve(strict=True)
                if state_root not in resolved.parents or resolved.stat().st_size > 10 * 1024 * 1024:
                    raise OSError
                body = resolved.read_bytes()
            except OSError:
                body = b"<!doctype html><meta charset='utf-8'><title>Kanban unavailable</title><p>Refresh the WIP / Kanban index from Document Sync first.</p>"
                self.send_html(HTTPStatus.NOT_FOUND, body)
                return
            self.send_html(HTTPStatus.OK, body)
            return
        if self.path == "/api/overview":
            self.send_json(HTTPStatus.OK, APP_STATE.overview())
            return
        if self.path == "/api/status":
            self.send_json(HTTPStatus.OK, APP_STATE.snapshot())
            return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path in ("/api/today", "/api/erp/targets", "/api/erp/record"):
            try:
                data = erp_get(parsed.path, urllib.parse.parse_qs(parsed.query), APP_STATE.year_root)
            except (ERP.ErpError, ValueError, OSError) as error:
                self.send_json(HTTPStatus.BAD_REQUEST, {"message": str(error)})
                return
            self.send_json(HTTPStatus.OK, data)
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"message": "Not found."})

    def do_POST(self) -> None:
        if not self.local_request():
            return
        if self.path not in ("/api/run", "/api/erp/assign", "/api/erp/unassign", "/api/erp/push"):
            self.send_json(HTTPStatus.NOT_FOUND, {"message": "Not found."})
            return
        if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), APP_STATE.csrf_token):
            self.send_json(HTTPStatus.FORBIDDEN, {"message": "Invalid local request token."})
            return
        if self.path != "/api/run":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 2 <= length <= MAX_ERP_BODY:
                    raise ValueError("Yêu cầu không hợp lệ.")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Yêu cầu không hợp lệ.")
                data = erp_post(self.path, payload, APP_STATE.year_root)
            except (ERP.ErpError, ValueError, TypeError, OSError) as error:
                self.send_json(HTTPStatus.BAD_REQUEST, {"message": str(error)})
                return
            self.send_json(HTTPStatus.OK, data)
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
