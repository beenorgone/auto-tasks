"""Shared read-only status/board for the CLI and local web app."""
from __future__ import annotations
import datetime as dt
import fcntl
import importlib.util
import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path
import docsync_purchase as PURCHASE
from docsync_layout import is_filed_report, is_intake, approved_target

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('docsync_wip', ROOT / 'wip-organizer.py')
WIP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(WIP)
STAGES = ('needs_review', 'in_progress', 'waiting_external', 'ready', 'filed', 'archived')
LABELS = {'needs_review': 'Chờ duyệt', 'in_progress': 'Đang xử lý', 'waiting_external': 'Chờ bên ngoài', 'ready': 'Sẵn sàng chuyển', 'filed': 'Đã lưu hồ sơ', 'archived': 'Lưu trữ'}

def board(year_root: Path) -> dict:
    rule_file = WIP.rules_path(year_root)
    rules = json.loads(rule_file.read_text()) if rule_file.exists() else WIP.DEFAULT_RULES
    records = {}
    database = WIP.db_path(year_root)
    if database.exists():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute('SELECT * FROM documents WHERE active=1 ORDER BY path'):
                records[row['path']] = dict(row)
    cards = []
    root_paths = {p.name: p for p in year_root.iterdir() if p.is_file() and not p.name.startswith('.') and not p.is_symlink()}
    for relative in sorted(set(records) | set(root_paths)):
        path = year_root / relative
        if not path.resolve().is_relative_to(year_root.resolve()):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        old = records.get(relative)
        stat = path.stat()
        unchanged = old and old['byte_size'] == stat.st_size and old['modified_at'] == stat.st_mtime_ns
        category, confidence, reason, target, mode = WIP.classify(Path(relative), rules, path)
        status = old['status'] if unchanged else 'captured'
        if unchanged and old['classification_reason'].startswith('manual review:'):
            category, reason, target = old['category'], old['classification_reason'], old['proposed_target']
            mode = 'review_only'  # Manual target decisions never bypass the routing allow-list.
        if status in ('filed', 'archived', 'in_progress', 'waiting_external'):
            stage = status
        elif is_filed_report(relative):
            stage = 'filed'
        elif mode == 'managed_move' and is_intake(relative) and approved_target(year_root, target):
            stage = 'ready'
        else:
            stage = 'needs_review'
        next_action = {'ready': 'docsync preview → docsync apply', 'needs_review': 'docsync review <dossier> <category>', 'in_progress': 'Hoàn thiện hồ sơ; cập nhật trạng thái', 'waiting_external': 'Theo dõi phản hồi', 'filed': 'Kiểm tra hồ sơ tại đích', 'archived': 'Không cần thao tác'}[stage]
        if mode == 'review_only' and status == 'approved':
            next_action = 'Đã duyệt phân loại; chuyển thủ công theo đích đề xuất'
        cards.append(dict(path=relative, category=category, stage=stage, reason=reason, target=target, mode=mode,
                          dossier=old['dossier_id'] if unchanged else None, next=next_action))
    if year_root.resolve() == (Path.home() / 'Documents' / str(dt.date.today().year)).resolve():
        cards.extend(PURCHASE.cards(PURCHASE.purchase_root()))
    return {'cards': cards, 'counts': {stage: sum(c['stage'] == stage for c in cards) for stage in STAGES}}

def health(year_root: Path) -> dict:
    units = ('ivar-watcher.service', 'ivar-documents-organizer.timer', 'ivar-documents-organizer.service')
    services = {}
    for unit in units:
        try:
            result = subprocess.run(['systemctl', '--user', 'show', unit, '--property=ActiveState,SubState,Result,ExecMainExitTimestamp,NextElapseUSecMonotonic'], capture_output=True, text=True, timeout=2)
            services[unit] = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line) if result.returncode == 0 else {'ActiveState': 'unknown'}
        except (OSError, subprocess.TimeoutExpired):
            services[unit] = {'ActiveState': 'unknown'}
    log_path = year_root.parent / '.auto-tasks/ivar-documents-organizer/logs' / (dt.date.today().isoformat() + '.log')
    recent = []
    if log_path.is_file():
        with log_path.open('rb') as handle:
            handle.seek(max(0, log_path.stat().st_size - 32768))
            recent = handle.read().decode('utf-8', errors='replace').splitlines()[-100:]
    timer = services.get('ivar-documents-organizer.timer', {})
    try:
        raw = timer.get('NextElapseUSecMonotonic', '0')
        units = {'us': 1, 'ms': 1000, 's': 1_000_000, 'min': 60_000_000, 'h': 3_600_000_000, 'd': 86_400_000_000, 'w': 604_800_000_000}
        next_us = int(raw) if raw.isdigit() else sum(float(number) * units[unit] for number, unit in re.findall(r'([0-9.]+)(us|ms|min|s|h|d|w)', raw))
        next_run = (dt.datetime.now().astimezone() + dt.timedelta(seconds=next_us / 1_000_000 - time.monotonic())).isoformat(timespec='seconds') if next_us else None
    except ValueError:
        next_run = None
    busy = False
    lock_path = year_root.parent / '.auto-tasks/ivar-documents-organizer/organizer.lock'
    if lock_path.is_file():
        with lock_path.open('rb') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
            except BlockingIOError:
                busy = True
    return {'services': services, 'recentLogs': recent, 'nextRun': next_run, 'organizerBusy': busy,
            'lastTimerRun': services.get('ivar-documents-organizer.service', {}).get('ExecMainExitTimestamp', '')}
