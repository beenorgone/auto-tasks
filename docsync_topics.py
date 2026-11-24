"""Work-topic proposals with immutable, audited file/folder decisions."""
import hashlib
import fcntl
from contextlib import contextmanager
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from docsync_layout import TOPICS, within, safe_directory, topic_path
from docsync_purchase import DOSSIER_ZIP, safe

REVIEW_FILE = 'topic-telegram-state.json'
HOLD = 'giu_lai'
REVIEW_FOLDERS = ('00_INBOX', '10_HO_SO', '20_BAO_CAO', '80_THAM_CHIEU')


def read_records(state):
    path = state / REVIEW_FILE
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def review_file(root, relative):
    """Select a regular file in the Telegram topic intake scope."""
    if not isinstance(relative, str) or not relative or any(ord(char) < 32 for char in relative):
        raise ValueError('Đường dẫn file không hợp lệ.')
    path = safe_directory(root, relative)
    rel = path.relative_to(root)
    if (rel.as_posix() != relative or any(part.startswith('.') for part in rel.parts)
            or str(rel.parent) not in ('.', *REVIEW_FOLDERS)
            or path.name.lower().endswith(('.part', '.tmp', '.crdownload', '.download', '.log'))
            or DOSSIER_ZIP.fullmatch(path.name) or not safe(path) or not path.is_file()):
        raise ValueError('File đã thay đổi hoặc không còn trong vùng chờ phân loại.')
    return path


def save_records(state, records):
    """Replace the existing topic decision index while the organizer lock is held."""
    with NamedTemporaryFile('w', encoding='utf-8', dir=state, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(records, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(state / REVIEW_FILE)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def organizer_lock(state):
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'organizer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def resolve_file(root, state, relative, choice, verify):
    """Apply a local decision using the same allow-list, audit and lock as Telegram."""
    return resolve_files(root, state, [relative], choice, lambda _, path: verify(path))[relative]


def resolve_files(root, state, relatives, choice, verify):
    """Validate every selected file before applying one audited move plan."""
    if not isinstance(choice, str) or choice not in (*TOPICS, HOLD):
        raise ValueError('Chọn một nhóm phân loại hoặc Giữ lại.')
    with organizer_lock(state):
        records = read_records(state)
        routes, hashes, results = {}, {}, {}
        for relative in relatives:
            path = review_file(root, relative)
            verify(relative, path)
            checksum = fingerprint(path)
            verify(relative, path)
            existing = records.get(relative, {})
            if existing.get('sha256') != checksum:
                existing = {}
            if existing.get('status', '').startswith('wordquest_'):
                raise ValueError('File này đang thuộc luồng WordQuest.')
            destination = (Path(TOPICS[choice]) / path.name).as_posix() if choice != HOLD else ''
            if destination:
                routes[relative] = destination
                hashes[relative] = checksum
            results[relative] = destination
            records[relative] = {
                **existing, 'sha256': checksum, 'signature': metadata_key(path), 'topic': choice,
                'workspace': str(root), 'origin': 'web', 'notified': True,
                'decided': time.time(), 'created': existing.get('created', time.time()),
                'status': 'completed' if destination else 'held', 'destination': destination,
            }
        if routes:
            apply_routes(root, state, routes, locked=True, expected_hashes=hashes)
        save_records(state, records)
    return results


def is_held_file(root, state, source, sha):
    if not source.is_relative_to(root):
        return False
    relative = source.relative_to(root).as_posix()
    record = read_records(state).get(relative, {})
    return (record.get('status') == 'held' and record.get('sha256') == sha
            and record.get('workspace', str(root)) == str(root))


def metadata_key(path):
    from docsync_purchase import signature
    value = signature(path)
    if value is None:
        raise ValueError('Source is not stable: ' + str(path))
    return json.dumps(value)


def cached_fingerprint(path, record):
    if record.get('signature') == metadata_key(path) and record.get('sha256'):
        return record['sha256']
    return fingerprint(path)


def settled(path):
    paths = [path]
    if path.is_dir():
        paths.extend(path.rglob('*'))
    for item in paths:
        stat = item.lstat()
        if item.is_symlink() or time.time() - max(stat.st_mtime, stat.st_ctime) < 20:
            return False
    return True


def fingerprint(path):
    """Bind approvals to the full relative inventory and bytes, never mtime alone."""
    from docsync_purchase import safe, sha256
    if not safe(path) or not path.exists():
        raise ValueError(f'Unsafe/missing topic source: {path}')
    if path.is_file():
        return sha256(path)
    digest = hashlib.sha256()
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            item = Path(parent) / name
            if item.is_symlink() or item.name.endswith(('.part', '.tmp', '.crdownload')):
                raise ValueError(f'Unstable topic source: {item}')
            relative = item.relative_to(path).as_posix()
            value = ['dir', relative] if item.is_dir() else ['file', relative, sha256(item)]
            digest.update((json.dumps(value, ensure_ascii=False) + '\n').encode())
        dirs.sort()
    return digest.hexdigest()


def candidates(root, wip, records=None):
    """Only direct work intake and leftover legacy branches; never personal archives."""
    root = Path(root)
    rules = wip.load_rules(root)
    active = {item['id']: item for item in wip.active_categories(rules)}
    manual = {}
    database = wip.db_path(root)
    if database.exists():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            manual = {row['path']: dict(row) for row in conn.execute("SELECT * FROM documents WHERE active=1 AND classification_reason LIKE 'manual review:%'")}
    scopes = []
    for item in root.iterdir():
        if item.name.startswith('.') or item.is_symlink():
            continue
        if item.is_file() or (item.is_dir() and item.name not in (*TOPICS.values(), 'Others', 'duplicates', '00_INBOX', '10_HO_SO', '20_BAO_CAO', '80_THAM_CHIEU', '90_CACH_LY_KY_THUAT', '99_NHAT_KY') and not re.fullmatch(r'20\d{2}', item.name)):
            scopes.append(item)
    for legacy in REVIEW_FOLDERS:
        folder = root / legacy
        if folder.is_dir() and not folder.is_symlink():
            scopes.extend(folder.iterdir())
    for source in sorted(scopes):
        if source.is_symlink() or source.name.startswith('.') or source.name.endswith(('.part', '.tmp', '.crdownload')):
            continue
        if source.is_dir() and not any(source.iterdir()):
            continue
        if DOSSIER_ZIP.fullmatch(source.name):
            continue  # Purchase grouping keeps its own stronger contract.
        relative = source.relative_to(root).as_posix()
        if wip.is_excluded(Path(relative), rules) or wip.is_generated(source, rules):
            continue
        category, confidence, reason, target, mode = wip.classify(Path(relative), rules, source if source.is_file() else None)
        topic = next((name for name in TOPICS.values() if target and within(target, name)), None)
        # More than one matching work topic is a human decision, even when the
        # first-match classifier would pick a high-confidence rule.
        matches = set()
        for rule in active.values():
            if any(re.search(pattern, wip.normalized(source.name), re.I) for pattern in rule['patterns']):
                match = next((name for name in TOPICS.values() if within(rule['target'], name)), None)
                if match: matches.add(match)
        configured_mode = active.get(category, {}).get('route_mode')
        automatic = topic and configured_mode != 'review_only' and confidence >= .93 and len(matches) <= 1
        destination = (Path(target) / source.name).as_posix() if topic else None
        stage = 'ready' if automatic else 'needs_review'
        old = manual.get(relative)
        if old and source.is_file() and old['byte_size'] == source.stat().st_size and old['modified_at'] == source.stat().st_mtime_ns:
            automatic, stage = False, 'needs_review'
            reason = 'Giữ quyết định thủ công hiện có; cần chốt chủ đề qua Telegram'
            category = old['category']
            target = topic_path(old['proposed_target']) if old['proposed_target'] else None
            topic = next((name for name in TOPICS.values() if target and within(target, name)), None)
            destination = (Path(target) / source.name).as_posix() if topic else None
        if destination:
            if safe_directory(root, destination).exists():
                stage, reason = 'needs_review', 'Đích đã tồn tại; cần kiểm tra trùng, không ghi đè'
        record = (records or {}).get(relative, {})
        if record.get('workspace', str(root)) == str(root) and record.get('status') in ('held', 'pending', 'queued', 'wordquest_requested', 'wordquest_ready', 'wordquest_failed'):
            try:
                unchanged = cached_fingerprint(source, record) == record['sha256']
            except ValueError:
                unchanged = False
            if unchanged:
                stage = 'waiting_external' if record['status'] == 'pending' else 'needs_review'
                automatic = False
                reason = 'Đã gửi Telegram; chờ bạn chọn chủ đề' if stage == 'waiting_external' else 'Giữ nguyên; chờ quyết định phân loại'
                if record['status'].startswith('wordquest_'):
                    stage = 'waiting_external'
                    reason = {'wordquest_requested':'WordQuest đang đọc từ trong ảnh',
                              'wordquest_ready':'Bài từ ảnh đã sẵn sàng; mở link Telegram để kiểm tra và ôn luyện',
                              'wordquest_failed':'Chưa đọc được từ trong ảnh; ảnh gốc giữ nguyên'}[record['status']]
        yield dict(source=source, relative=relative, target=destination, stage=stage,
                   reason=reason, suggested=topic, category=category)


def apply_routes(root, state, routes, locked=False, expected_hashes=None):
    """Use the migration engine for integrity checks, DB backup/remap and rollback."""
    from docsync_migrate_layout import apply_plan
    folders, files = {}, {}
    for source, destination in routes.items():
        path = safe_directory(root, source)
        safe_directory(root, destination)
        (folders if path.is_dir() else files)[source] = destination
    if not routes:
        return None
    return apply_plan(root, state, folders, files, locked=locked, expected_hashes=expected_hashes)


def cards(root, wip, state):
    records = read_records(state)
    return [dict(path=c['relative'], target=c['target'], stage=c['stage'], category=c['category'],
                 reason=c['reason'], mode='managed_move' if c['stage'] == 'ready' else 'review_only', dossier=None,
                 next='Tự xếp theo chủ đề ở lượt DocSync tiếp theo' if c['stage'] == 'ready' else 'Chọn chủ đề hoặc giữ lại qua bot Telegram')
            for c in candidates(root, wip, records)]


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Check whether a file has a matching hold decision.')
    parser.add_argument('root', type=Path)
    parser.add_argument('state', type=Path)
    parser.add_argument('source', type=Path)
    parser.add_argument('sha')
    args = parser.parse_args()
    try:
        raise SystemExit(0 if is_held_file(args.root, args.state, args.source, args.sha) else 1)
    except (OSError, ValueError) as error:
        parser.exit(2, f'{error}\n')
