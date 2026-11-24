"""Local bulk decisions bound to the listed versions of WIP files."""

import datetime as dt
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from docsync_core import WIP
from docsync_daily import file_parts, file_token, selected_source
from docsync_layout import AUDIT_DIRECTORY, safe_directory
from docsync_purchase import rename_no_replace, sha256
import docsync_topics as topics


MAX_FILES = 30
TRASH = '.docsync/Trash'


def selection_for(root, key, path):
    """Generate UI selection metadata for an existing regular file inside WIP."""
    if not key or not path.is_relative_to(root):
        return None
    relative = path.relative_to(root).as_posix()
    try:
        file_parts(relative)
        source = safe_directory(root, relative)
        info = source.lstat()
    except (OSError, ValueError, RuntimeError):
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return {'path': relative, 'name': path.name, 'token': file_token(key, relative, info)}


def renamed_path(relative, name):
    if (not isinstance(name, str) or name != name.strip() or not name or name.startswith('.')
            or any(c in '/\\' or ord(c) < 32 for c in name) or len(os.fsencode(name)) > 255):
        raise ValueError('Tên mới phải là tên file hợp lệ, gồm cả phần mở rộng nếu có.')
    target = Path(relative).with_name(name).as_posix()
    file_parts(target)
    return target


def audit_file(root, day):
    return safe_directory(root, f'{AUDIT_DIRECTORY}/file-actions-{day}.jsonl')


def audit(root, event):
    path = audit_file(root, dt.date.today())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def event(action, relative, target, checksum, stage):
    return {'action': action, 'source': relative, 'destination': target, 'sha256': checksum,
            'time': dt.datetime.now().astimezone().isoformat(), 'status': stage}


def dispatch(root, state, key, payload):
    """Apply up to thirty explicit per-file decisions under the organizer lock."""
    files, action = payload.get('files'), payload.get('action')
    if (action not in ('classify', 'hold', 'rename', 'trash') or not isinstance(files, list)
            or not 1 <= len(files) <= MAX_FILES or not all(isinstance(item, dict) for item in files)):
        raise ValueError('Chọn một thao tác và từ 1 đến 30 file.')
    selected = {}
    for item in files:
        relative = item.get('path')
        file_parts(relative)
        if relative in selected:
            raise ValueError('Danh sách chọn có file trùng.')
        selected[relative] = item
    rules = WIP.load_rules(root)

    def verify(relative, _path):
        if WIP.is_excluded(Path(relative), rules) or WIP.is_generated(Path(relative), rules):
            raise ValueError('File đang thuộc vùng bỏ qua trong Cài đặt.')
        with selected_source(root, key, relative, selected[relative].get('token')):
            pass

    if action in ('classify', 'hold'):
        choice = payload.get('topic') if action == 'classify' else topics.HOLD
        if action == 'classify' and choice == topics.HOLD:
            raise ValueError('Chọn nhóm đích để phân loại.')
        moved = topics.resolve_files(root, state, selected, choice, verify)
        return {'results': [{'path': path, 'destination': destination} for path, destination in moved.items()]}

    with topics.organizer_lock(state):
        routes = {}
        hashes = {}
        targets = set()
        records = topics.read_records(state)
        for relative, item in selected.items():
            verify(relative, root / relative)
            source = safe_directory(root, relative)
            checksum = sha256(source)
            verify(relative, source)
            if records.get(relative, {}).get('status', '').startswith('wordquest_'):
                raise ValueError('File đang thuộc luồng WordQuest.')
            target = renamed_path(relative, item.get('name')) if action == 'rename' else f'{TRASH}/{uuid4().hex}/{source.name}'
            if target == relative:
                continue
            dest = safe_directory(root, target)
            if target in targets or dest.exists():
                raise FileExistsError(f'Đích đã có file cùng tên: {dest.name}')
            targets.add(target)
            routes[relative], hashes[relative] = target, checksum
        results = []
        for relative, target in routes.items():
            audit(root, event(action, relative, target, hashes[relative], 'planned'))
        if action == 'rename' and routes:
            topics.apply_routes(root, state, routes, locked=True, expected_hashes=hashes)

        for relative, target in routes.items():
            source, dest = root / relative, root / target
            if action == 'trash':
                try:
                    verify(relative, source)
                    dest.parent.mkdir(parents=True, exist_ok=False)
                    rename_no_replace(source, dest)
                except (ValueError, OSError) as error:
                    results.append({'path': relative, 'error': f'Chưa đưa vào thùng rác: {error}'})
                    continue
            previous = records.pop(relative, None)
            if action == 'rename' and previous and previous.get('status') == 'held' and previous.get('sha256') == hashes[relative]:
                records[target] = {**previous, 'signature': topics.metadata_key(dest), 'workspace': str(root)}
            audit(root, event(action, relative, target, hashes[relative], 'completed'))
            results.append({'path': relative, 'destination': target})
        if routes:
            topics.save_records(state, records)
        return {'results': results, 'unchanged': len(selected) - len(routes)}
