"""Month routing for IVAR purchase dossiers, shared by organizer, board and watcher."""
from __future__ import annotations
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from docsync_paths import work_root

RULE = 'purchase_months'
RULE_DESCRIPTION = 'Hồ sơ mua vào: ngày YYYY-MM-DD trên tên → <năm>/<nhóm nếu có>/<MM>; giữ nguyên bộ hồ sơ'
TEMP_SUFFIXES = ('.part', '.crdownload', '.tmp')
DATE = re.compile(r'(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)')
DOSSIER_ZIP = re.compile(r'^ho so PO[\s_-]*\d+.*\.zip$', re.I)
GROUPS = ('nhap khau', 'trong nuoc')


def supplier(name):
    """Exact supplier identity after NCC; no fuzzy or partial name matches."""
    match = re.search(r' - NCC (.+)\.zip$', name, re.I)
    if not match:
        return None
    return unicodedata.normalize('NFC', match[1]).casefold().strip()


def intake_paths(home=None):
    home = Path.home() if home is None else Path(home)
    root = work_root(home)
    return (root, root / '00_INBOX', home / 'Documents', home / 'Downloads')


def held_decisions(state=None):
    state = Path(state or os.environ.get('STATE_DIR', str(Path(__file__).parent / 'ivar-documents-organizer')))
    path = state / 'purchase-telegram-state.json'
    records = json.loads(path.read_text()) if path.exists() else {}
    return {source: record for source, record in records.items() if record.get('status') == 'held'}


def intake_candidates(root, intakes, decisions=None):
    """Use only unanimous, explicitly grouped archive history for a supplier."""
    history = {}
    for entry in candidates(root):
        source = entry['source']
        relative = source.relative_to(root)
        key = supplier(source.name)
        if key and entry['stage'] == 'filed' and len(relative.parts) >= 3 and relative.parts[1] in GROUPS:
            history.setdefault(key, {}).setdefault(relative.parts[1], []).append(str(source))
    seen = set()
    for folder in intakes:
        if not folder.is_dir() or not safe(folder):
            continue
        # Same intake scope as the shell: recursive Inbox, direct files elsewhere.
        if folder.name == '00_INBOX':
            paths = []
            for parent, dirs, files in os.walk(folder, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(parent) / d).is_symlink()]
                paths.extend(Path(parent) / f for f in files)
        else:
            paths = folder.iterdir()
        for source in sorted(paths):
            if source in seen or not DOSSIER_ZIP.fullmatch(source.name) or not source.is_file() or not safe(source):
                continue
            seen.add(source)
            groups = history.get(supplier(source.name), {})
            target, reason = None, 'Chưa có lịch sử nhóm của NCC; cần quyết định qua Telegram'
            dates = DATE.findall(source.name)
            try:
                date = dt.date.fromisoformat(dates[0]) if len(dates) == 1 else None
            except ValueError:
                date = None
            if not date:
                reason = 'Tên hồ sơ cần đúng một ngày YYYY-MM-DD hợp lệ'
            elif len(groups) > 1:
                reason = 'Lịch sử NCC có cả nhập khẩu và trong nước; cần quyết định qua Telegram'
            elif len(groups) == 1:
                group = next(iter(groups))
                target = root / str(date.year) / group / f'{date.month:02d}' / source.name
                reason = f'Lịch sử NCC nhất quán: {len(groups[group])} hồ sơ thuộc {group}; tháng theo {date.isoformat()}'
            decision = (decisions or {}).get(str(source), {})
            verified = bool(decision.get('sha256')) and decision['sha256'] == sha256(source)
            if verified and decision.get('status') == 'held':
                target, reason = None, 'Bạn đã chọn giữ lại qua Telegram'
            elif date and decision.get('group') in GROUPS and verified:
                target = root / str(date.year) / decision['group'] / f'{date.month:02d}' / source.name
                reason = f'Đã duyệt qua Telegram; tháng theo {date.isoformat()}'
            stage = 'ready' if target else 'needs_review'
            if target and (not safe(target) or target.exists()):
                stage, reason = 'needs_review', 'Đích đã tồn tại hoặc có symlink; giữ nguyên nguồn'
            yield dict(source=source, target=target, stage=stage, reason=reason,
                       evidence=[p for paths in groups.values() for p in paths], intake=True,
                       expected_sha256=decision.get('sha256') if verified else None)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def purchase_root(home=None):
    home = Path.home() if home is None else Path(home)
    data = Path(os.environ.get('IVAR_DATA', str(home / 'Documents/Drives/gDriveProjects/Working/Projects/IVAR/Accounting - Ke toan/Data - Du lieu')))
    return data / 'ho so mua vao'


def safe(path):
    return not any(p.is_symlink() for p in (path, *path.parents))


def signature(path):
    """Metadata only on polls; include all attachments to settle whole dossiers."""
    if not safe(path):
        return None
    paths = [path]
    if path.is_dir():
        for folder, dirs, files in os.walk(path, followlinks=False):
            paths.extend(Path(folder) / name for name in dirs + files)
    result = []
    try:
        for item in sorted(paths):
            if item.is_symlink() or item.name.endswith(TEMP_SUFFIXES):
                return None
            stat = item.stat()
            if not (item.is_file() or item.is_dir()):
                return None
            result.append((str(item.relative_to(path)), stat.st_size, stat.st_mtime_ns, stat.st_ino))
    except FileNotFoundError:
        return None
    return tuple(result)


def candidates(root):
    """Traverse only year / optional category / quarter or month containers."""
    if not root.is_dir() or not safe(root):
        return
    def walk(folder, base, year, depth=0):
        for item in sorted(folder.iterdir()):
            if item.name.startswith('.') or item.name.endswith(TEMP_SUFFIXES) or item.is_symlink():
                continue
            if item.name.lower().startswith('ho so '):
                dates = DATE.findall(item.name)
                target, reason = None, 'Tên hồ sơ cần đúng một ngày YYYY-MM-DD hợp lệ'
                try:
                    date = dt.date.fromisoformat(dates[0]) if len(dates) == 1 else None
                    if date and str(date.year) == year:
                        target = base / f'{date.month:02d}' / item.name
                        reason = f'Tháng {date.month:02d} theo ngày {date.isoformat()} trên tên'
                    elif date:
                        reason = 'Năm trên tên khác thư mục năm; cần kiểm tra'
                except ValueError:
                    pass
                stage = 'needs_review' if target is None else 'filed' if target == item else 'ready'
                if target and (not safe(target) or (target != item and target.exists())):
                    stage, reason = 'needs_review', 'Đích đã tồn tại hoặc có symlink; giữ nguyên nguồn'
                yield dict(source=item, target=target, stage=stage, reason=reason)
            elif item.is_dir() and depth < 2:
                if depth == 0 and item.name in ('nhap khau', 'trong nuoc'):
                    yield from walk(item, item, year, depth + 1)
                elif re.fullmatch(r'Q[1-4]|0[1-9]|1[0-2]', item.name):
                    yield from walk(item, base, year, depth + 1)
    for year in sorted(root.iterdir()):
        if re.fullmatch(r'20\d{2}', year.name) and year.is_dir() and safe(year):
            yield from walk(year, year, year.name)


def snapshot(root):
    return {str(c['source']): signature(c['source']) for c in candidates(root) if c['stage'] == 'ready'}


def cards(root, intakes=()):
    result = [dict(path=str(c['source']), target=str(c['target'] or ''), category=RULE,
                 stage=c['stage'], reason=c['reason'], mode='managed_move', dossier=None,
                 next={'ready': 'docsync preview → docsync apply', 'filed': 'Đã đúng tháng',
                       'needs_review': 'Xem lý do; xác nhận nhóm qua Telegram nếu chưa rõ; chạy preview lại'}[c['stage']])
            for c in [*candidates(root), *intake_candidates(root, intakes, held_decisions())]]
    state = Path(os.environ.get('STATE_DIR', str(Path(__file__).parent / 'ivar-documents-organizer')))
    path = state / 'purchase-telegram-state.json'
    records = json.loads(path.read_text()) if path.exists() else {}
    for card in result:
        record = records.get(card['path'], {})
        if (card['stage'] == 'needs_review' and record.get('status') == 'pending'
                and time.time() - record['created'] <= 7 * 86400
                and sha256(Path(card['path'])) == record['sha256']):
            card.update(stage='waiting_external', reason='Đã gửi yêu cầu Telegram; chờ bạn chọn nhóm hoặc giữ lại',
                        next='Trả lời lệnh /docsync trong tin nhắn bot; máy kiểm tra mỗi phút')
    return result


def rename_no_replace(source, target):
    # Linux atomic rename with RENAME_NOREPLACE, including directories.
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def route(root, state, apply=False, intakes=(), intake_only=False, decisions=None):
    """Caller holds the shared organizer lock. Preview never creates directories."""
    decisions = {**held_decisions(state), **(decisions or {})}
    items = list(intake_candidates(root, intakes, decisions))
    if not intake_only:
        items.extend(candidates(root))
    for item in items:
        source, target = item['source'], item['target']
        if item['stage'] == 'filed':
            continue
        if item['stage'] != 'ready':
            print(f'REVIEW {RULE}: {source} — {item["reason"]}')
            continue
        before = signature(source)
        if before is None:
            print(f'WAIT {RULE}: {source} — bộ hồ sơ chưa ổn định hoặc có symlink')
            continue
        print(f'{"APPLY" if apply else "PLAN"} {RULE}: {source} -> {target}')
        if not apply:
            continue
        checksum = sha256(source) if item.get('intake') else None
        if item.get('expected_sha256') and checksum != item['expected_sha256']:
            print(f'REVIEW {RULE}: {source} — file thay đổi sau khi duyệt; giữ nguyên')
            continue
        if not safe(source) or not safe(target) or signature(source) != before:
            raise RuntimeError(f'Purchase dossier changed: {source}')
        target.parent.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)
        record = dict(source=str(source), destination=str(target), time=dt.datetime.now().astimezone().isoformat(),
                      reason=item['reason'], evidence=item.get('evidence', []), sha256=checksum)
        with (state / 'purchase-months.jsonl').open('a') as log:
            log.write(json.dumps({**record, 'status': 'planned'}, ensure_ascii=False) + '\n')
            log.flush()
            os.fsync(log.fileno())
            rename_no_replace(source, target)
            if signature(target) != before:
                raise RuntimeError(f'Purchase dossier changed during move: {target}')
            if checksum and sha256(target) != checksum:
                raise RuntimeError(f'Purchase dossier checksum mismatch: {target}')
            log.write(json.dumps({**record, 'status': 'completed'}, ensure_ascii=False) + '\n')
            log.flush()
            os.fsync(log.fileno())


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--intake', type=Path, action='append', default=[])
    args = parser.parse_args()
    route(args.root, args.state, args.apply, args.intake)
