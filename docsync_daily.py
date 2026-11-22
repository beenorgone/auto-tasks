"""Read daily WIP classifications and attach explicitly chosen files to ERP."""

from contextlib import closing, contextmanager
import datetime as dt
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from types import SimpleNamespace

from docsync_core import WIP
import docsync_intake as intake
import docsync_workflows as workflows


PAGE_SIZE = 75
TEMP_SUFFIXES = ('.part', '.crdownload', '.tmp', '.download')


def file_token(key, relative, info):
    version = f'{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}'
    return hmac.digest(key, os.fsencode(relative) + b'\0' + version.encode('ascii'), 'sha256').hex()


def available_files(root, rules):
    for folder, directories, filenames in os.walk(root, followlinks=False):
        parent = Path(folder)
        directories[:] = [name for name in directories if not name.startswith('.')
                          and name != 'duplicates' and not (parent / name).is_symlink()
                          and not WIP.is_excluded((parent / name).relative_to(root), rules)]
        for name in filenames:
            if name.startswith('.') or name.lower().endswith(TEMP_SUFFIXES):
                continue
            path = parent / name
            relative = path.relative_to(root)
            if WIP.is_excluded(relative, rules) or WIP.is_generated(relative, rules):
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                yield relative, info


def indexed_rows(root, files):
    database = WIP.db_path(root)
    if not files or not database.is_file():
        return {}
    paths = [str(relative) for relative, _ in files]
    placeholders = ','.join('?' for _ in paths)
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        result = db.execute(
            'SELECT path, category, classification_reason, modified_at, byte_size FROM documents '
            f'WHERE active=1 AND path IN ({placeholders}) LIMIT ?', (*paths, len(paths))
        )
        return {row['path']: row for row in result}


def automatic_state(relative, info, rules, now):
    return bool(workflows.matches_report(relative, info, rules, now, before_routing=rules[workflows.LOCAL][0]))


def automatic_activity(root, files):
    if not files:
        return {}
    paths = {relative.as_posix(): workflows.file_signature(info) for relative, info in files}
    placeholders = ','.join('?' for _ in paths)
    with workflows.database(root) as db:
        if db is None:
            return {}
        rows = db.execute(
            f'SELECT path, signature, state, outcome FROM attempts WHERE path IN ({placeholders}) LIMIT ?',
            (*paths, len(paths)),
        )
        return {row['path']: {'state': row['state'], 'outcome': row['outcome']} for row in rows
                if paths[row['path']] == row['signature']}


def list_day(root, key, date_text='', page=1):
    """List WIP files touched on the chosen local day without changing the index."""
    if not root.is_dir():
        raise OSError('Không tìm thấy thư mục WIP & Reference.')
    day = dt.date.fromisoformat(date_text) if date_text else dt.date.today()
    start = int(dt.datetime.combine(day, dt.time.min).timestamp()) * 1_000_000_000
    end = int(dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min).timestamp()) * 1_000_000_000
    rules_file = WIP.rules_path(root)
    rules = WIP.migrate_rules(json.loads(rules_file.read_text(encoding='utf-8')))[0] if rules_file.is_file() else WIP.DEFAULT_RULES
    labels = {category['id']: category['label'] for category in rules['categories']}
    files = [(relative, info) for relative, info in available_files(root, rules)
             if start <= max(info.st_mtime_ns, info.st_ctime_ns) < end]
    files.sort(key=lambda item: (max(item[1].st_mtime_ns, item[1].st_ctime_ns), str(item[0])), reverse=True)
    total_pages = max(1, (len(files) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 1), total_pages)
    selection = files[(page - 1)*PAGE_SIZE:page*PAGE_SIZE]
    indexed = indexed_rows(root, selection)
    auto_rules = workflows.settings(root)
    activity = automatic_activity(root, selection)
    now = time.time_ns()
    rows = []
    for relative, info in selection:
        old = indexed.get(str(relative))
        if old and old['modified_at'] == info.st_mtime_ns and old['byte_size'] == info.st_size:
            category = old['category']
        else:
            category, *_ = WIP.classify(relative, rules, root / relative)
        rows.append({
            'path': relative.as_posix(), 'name': relative.name, 'size': info.st_size,
            'time': dt.datetime.fromtimestamp(max(info.st_mtime_ns, info.st_ctime_ns) / 1e9).astimezone().isoformat(),
            'category': labels.get(category, 'Chờ phân loại' if category == 'needs_review' else category),
            'automatic': automatic_state(relative, info, auto_rules, now),
            'activity': activity.get(relative.as_posix()),
            'token': file_token(key, relative, info),
        })
    return {'day': day.isoformat(), 'page': page, 'pages': total_pages, 'total': len(files),
            'files': rows, 'configured': workflows.credential_configured(for_erp=True)}


def file_parts(relative):
    if not isinstance(relative, str) or not relative or '\0' in relative:
        raise ValueError('Chọn file từ bảng DocSync.')
    parts = relative.split('/')
    if (any(not part or part.startswith('.') or part == 'duplicates' for part in parts)
            or parts[-1].lower().endswith(TEMP_SUFFIXES)):
        raise ValueError('Chọn file từ bảng DocSync.')
    return parts


@contextmanager
def selected_source(root, key, relative, token):
    """Open the listed version of a regular file without following any symlink."""
    parts = file_parts(relative)
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{64}', token):
        raise ValueError('Chọn lại file trên bảng DocSync.')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory = os.open(root.resolve(), flags)
        try:
            for part in parts[:-1]:
                next_directory = os.open(part, flags, dir_fd=directory)
                os.close(directory)
                directory = next_directory
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
    except OSError:
        raise ValueError('File đã đổi vị trí hoặc không còn đọc được. Làm mới danh sách.') from None
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not hmac.compare_digest(token, file_token(key, relative, info)):
            raise ValueError('File đã thay đổi. Làm mới danh sách và chọn lại.')
        if automatic_state(Path(relative), info, workflows.settings(root), time.time_ns()):
            raise ValueError('File đang theo luồng đối soát tự động. Xem trạng thái ở tab Workflows.')
        yield stream


def record_type(payload):
    key = payload.get('type')
    if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,19}', key):
        raise ValueError('Chọn loại bản ghi ERP.')
    return key


def record_id(payload):
    value = payload.get('id')
    if type(value) is not int or not 0 < value < 10**13:
        raise ValueError('Chọn bản ghi ERP.')
    return value


def dispatch(root, key, action, payload):
    """Reuse the shared intake client and keep the set of proxy routes explicit."""
    if action == 'credential':
        workflows.save_credential(root, payload.get('user'), payload.get('password'), for_erp=True)
        return {'configured': True}
    client = workflows.saved_client(for_erp=True)
    if action == 'types':
        flows, limit = intake.access_policy(client)
        if workflows.ATTACH_FLOW not in flows:
            raise ValueError('Tài khoản chưa được duyệt quyền gắn file ERP (erp_attach).')
        types = client.request(intake.ERP + '/types')[1].get('types', [])
        return {'types': types, 'max_bytes': limit, 'ai_allowed': 'erp_attach_ai' in flows}
    kind = record_type(payload)
    if action == 'targets':
        query = payload.get('query')
        if not isinstance(query, str) or not 0 < len(query.strip()) <= 100:
            raise ValueError('Nhập mã đơn hoặc tên bản ghi.')
        candidates = client.request(intake.ERP + '/targets', query={'type': kind, 'q': query.strip()})[1]
        # Target labels and IDs are sufficient here. The API can include other customer details.
        return {'records': [{name: row.get(name) for name in ('id', 'label', 'date', 'status')}
                            for row in candidates.get('records', [])]}
    target_id = record_id(payload)
    if action == 'record':
        target = client.request(intake.ERP + '/record', query={'type': kind, 'id': target_id})[1]
        return {'fields': [{name: row.get(name) for name in ('key', 'label', 'count', 'max')}
                           for row in target.get('fields', [])], 'ai_available': target.get('ai_available') is True}
    relative, token = payload.get('path'), payload.get('token')
    parts = file_parts(relative)
    field, ai = payload.get('field'), payload.get('ai')
    if not isinstance(field, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', field) or type(ai) is not bool:
        raise ValueError('Chọn trường nhận file và xác nhận tùy chọn AI.')
    with selected_source(root, key, relative, token):
        pass
    args = SimpleNamespace(action='attach', send=True, file=Path(parts[-1]), type=kind, id=target_id,
                           field=field, ai_mode='analysis' if ai else None)
    return intake.run(args, client, open_source=lambda _: selected_source(root, key, relative, token))
