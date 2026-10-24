"""Month routing for IVAR purchase dossiers, shared by organizer, board and watcher."""
from __future__ import annotations
import ctypes
import datetime as dt
import json
import os
import re
from pathlib import Path

RULE = 'purchase_months'
RULE_DESCRIPTION = 'Hồ sơ mua vào: ngày YYYY-MM-DD trên tên → <năm>/<nhóm nếu có>/<MM>; giữ nguyên bộ hồ sơ'
TEMP_SUFFIXES = ('.part', '.crdownload', '.tmp')
DATE = re.compile(r'(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)')


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


def cards(root):
    return [dict(path=str(c['source']), target=str(c['target'] or ''), category=RULE,
                 stage=c['stage'], reason=c['reason'], mode='managed_move', dossier=None,
                 next={'ready': 'docsync preview → docsync apply', 'filed': 'Đã đúng tháng',
                       'needs_review': 'Kiểm tra tên ngày/năm và đích trùng; sửa rồi chạy preview'}[c['stage']])
            for c in candidates(root)]


def rename_no_replace(source, target):
    # Linux atomic rename with RENAME_NOREPLACE, including directories.
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def route(root, state, apply=False):
    """Caller holds the shared organizer lock. Preview never creates directories."""
    for item in candidates(root):
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
        if not safe(source) or not safe(target) or signature(source) != before:
            raise RuntimeError(f'Purchase dossier changed: {source}')
        target.parent.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)
        record = dict(source=str(source), destination=str(target), time=dt.datetime.now().astimezone().isoformat())
        with (state / 'purchase-months.jsonl').open('a') as log:
            log.write(json.dumps({**record, 'status': 'planned'}, ensure_ascii=False) + '\n')
            log.flush()
            os.fsync(log.fileno())
            rename_no_replace(source, target)
            if signature(target) != before:
                raise RuntimeError(f'Purchase dossier changed during move: {target}')
            log.write(json.dumps({**record, 'status': 'completed'}, ensure_ascii=False) + '\n')
            log.flush()
            os.fsync(log.fileno())


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    route(args.root, args.state, args.apply)
