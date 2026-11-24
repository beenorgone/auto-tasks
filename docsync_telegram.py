#!/usr/bin/env python3
"""Bounded SSH bridge to the existing ERP Telegram bot; no copied bot secrets."""
import datetime as dt
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
import docsync_purchase as purchase
import docsync_topics as topics
from docsync_layout import TOPICS, safe_directory
from docsync_paths import work_root

STATE = Path(os.environ.get('STATE_DIR', str(Path(__file__).parent / 'ivar-documents-organizer')))


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.purchase-telegram-')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def remote(config, payload):
    # PHP code and shell arguments are fixed/quoted; proposal data travels as JSON
    # over stdin and is never interpolated into PHP or a remote shell command.
    php = '''
    if (!function_exists('ivar_tg_ai_send')) {
        require_once get_stylesheet_directory() . '/custom-functions/erp/integrations/telegram-ai/telegram-ai.php';
    }
    if (!function_exists('ivar_docsync_bridge')) { echo 'DOCSYNC_JSON:' . json_encode(['error'=>'bridge_not_installed']) . "\\n"; return; }
    $input = json_decode(stream_get_contents(STDIN), true);
    echo "DOCSYNC_JSON:" . wp_json_encode(ivar_docsync_bridge(is_array($input) ? $input : [])) . "\\n";
    '''
    command = shlex.join(['wp', '--path=' + config['wp_path'], '--skip-plugins', '--skip-themes', 'eval', php])
    result = subprocess.run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                             '--', config['host'], command], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=150 if payload.get('op') == 'wordquest_extract' else 30)
    for line in reversed(result.stdout.splitlines()):
        if line.startswith('DOCSYNC_JSON:'):
            response = json.loads(line.removeprefix('DOCSYNC_JSON:'))
            if result.returncode == 0 and 'error' not in response:
                return response
            raise RuntimeError('Telegram bridge: ' + str(response.get('error', 'remote_failure')))
    # Do not surface remote logs/config contents in notifications or exceptions.
    raise RuntimeError(f'Telegram bridge unavailable (SSH/WP exit {result.returncode})')


def trash_file(source, record, state, index, records):
    """Soft-delete an exact approved file under organizer.lock; journal before rename."""
    record['delete_requested'] = True
    target = state / 'telegram-trash' / record['id'] / source.name
    if not purchase.safe(source) or not purchase.safe(target):
        record.update(status='blocked', reason='Symlink; không xóa')
    elif not source.exists() and target.is_file() and purchase.sha256(target) == record['sha256']:
        record.update(status='deleted', trash=str(target))  # Recover rename before index save.
    elif not source.is_file():
        record.update(status='blocked', reason='Chỉ xóa file; giữ nguyên thư mục hoặc nguồn không tồn tại')
    elif purchase.sha256(source) != record['sha256']:
        record.update(status='stale', reason='File đã thay đổi; không xóa')
    elif target.exists():
        record.update(status='blocked', reason='Thùng rác đã có file; không ghi đè')
    else:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        record['trash'] = str(target)
        save(index, records)  # Durable intent includes source (index key), hash and restore path.
        source.rename(target)  # Fail closed on cross-device moves; never copy then unlink.
        if purchase.sha256(target) != record['sha256']:
            target.rename(source)
            raise RuntimeError('File changed during trash move')
        record.update(status='deleted', deleted=time.time())
    save(index, records)


def sync(root, intakes, state, config, call=remote):
    """Caller holds organizer.lock. Decisions are bound to exact file bytes."""
    index = state / 'purchase-telegram-state.json'
    records = json.loads(index.read_text()) if index.exists() else {}
    decisions = {}
    for source, record in records.items():
        if record['status'] not in ('pending', 'queued'):
            continue
        if time.time() - record['created'] > 7 * 86400:
            record['status'] = 'expired'
            continue
        response = call(config, {'op': 'read', 'id': record['id']})
        if response.get('status') == 'delete_requested':
            trash_file(Path(source), record, state, index, records)
            continue
        if response.get('status') == 'approved' and response.get('group') in purchase.GROUPS:
            path = Path(source)
            # Recover a crash after the audited rename but before saving this index.
            dates = purchase.DATE.findall(path.name)
            if not path.exists() and len(dates) == 1:
                date = dt.date.fromisoformat(dates[0])
                target = root / str(date.year) / response['group'] / f'{date.month:02d}' / path.name
                if purchase.safe(target) and target.is_file() and purchase.sha256(target) == record['sha256']:
                    record['status'] = 'completed'
                    continue
            if not purchase.safe(path) or not path.is_file() or purchase.sha256(path) != record['sha256']:
                record['status'] = 'stale'
                continue
            decisions[source] = {'sha256': record['sha256'], 'group': response['group']}
        elif response.get('status') == 'held':
            record['status'] = 'held'
    save(index, records)
    items = list(purchase.intake_candidates(root, intakes, decisions))
    if any(time.time() - max(c['source'].stat().st_mtime, c['source'].stat().st_ctime) < 20 for c in items):
        return  # Wait for a settled upload; the minute timer retries.
    purchase.route(root, state, True, intakes, intake_only=True, decisions=decisions)
    for source in decisions:
        if not Path(source).exists():
            records[source]['status'] = 'completed'
    save(index, records)
    for record in records.values():
        if record['status'] in ('blocked', 'stale') and record.get('delete_requested') and not record.get('notified'):
            call(config, {'op': 'delete_failed', 'id': record['id']})
            record['notified'] = True
            save(index, records)
        if record['status'] in ('completed', 'deleted') and not record.get('notified'):
            call(config, {'op': 'complete', 'id': record['id']})
            record['notified'] = True
            save(index, records)
    for item in items:
        if item['stage'] != 'needs_review' or item['target'] is not None:
            continue  # Collisions require a destination review, never a group override.
        source = item['source']
        dates = purchase.DATE.findall(source.name)
        try:
            date = dt.date.fromisoformat(dates[0]) if len(dates) == 1 else None
        except ValueError:
            date = None
        if date is None or not source.exists() or not purchase.safe(source):
            continue
        digest = purchase.sha256(source)
        key = str(source)
        record = records.get(key)
        if record and record['sha256'] == digest and record['status'] in ('pending', 'held', 'completed', 'expired'):
            continue
        if not record or record['sha256'] != digest or record['status'] != 'queued':
            created = time.time()
            record = dict(id=hashlib.sha256(f'{source}:{digest}:{created}'.encode()).hexdigest()[:32],
                          sha256=digest, status='queued', created=created)
            records[key] = record
            save(index, records)
        response = call(config, dict(op='submit', id=record['id'], name=source.name,
                                    date=date.isoformat(), login=config['login'], bot=config['bot']))
        record['status'] = response.get('status', 'queued')
        save(index, records)
        print(f'Telegram DocSync: {source.name} — {record["status"]}', flush=True)


def main():
    config_path = STATE / 'purchase-telegram.json'
    if not config_path.is_file():
        return
    config = json.loads(config_path.read_text())
    with (STATE / 'organizer.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # Timer retries; never compete with the main organizer.
        failures = []
        for operation in (
                lambda: sync(purchase.purchase_root(), purchase.intake_paths(), STATE, config),
                lambda: sync_topics(work_root(), STATE, config)):
            try:
                operation()
            except (RuntimeError, OSError, ValueError) as error:
                failures.append(str(error))
        if failures:
            raise RuntimeError('; '.join(failures))
    from docsync_wordquest import process as wordquest_process
    wordquest_process(STATE, config, remote, save)


def sync_topics(root, state, config, call=remote, wip=None):
    """Caller holds organizer.lock. Batch Telegram questions; never accept paths from chat."""
    if wip is None:
        from docsync_core import WIP as wip
    index = state / topics.REVIEW_FILE
    before = index.read_text() if index.exists() else ''
    records = json.loads(before) if before else {}
    proposals = list(topics.candidates(root, wip, records))
    automatic = {}
    for item in proposals:
        source = item['source']
        if item['stage'] == 'ready' and topics.settled(source):
            automatic[item['relative']] = item['target']
    if automatic:
        topics.apply_routes(root, state, automatic, locked=True)
        print(f'Topic routes: verified {len(automatic)} source items', flush=True)
    pending = {relative: r for relative, r in records.items() if r['status'] in ('pending', 'queued', 'wordquest_failed')}
    responses = {}
    approved_routes = {}
    if pending:
        ids = [r['id'] for r in pending.values()]
        for start in range(0, len(ids), 64):
            responses.update(call(config, {'op': 'read_topics', 'ids': ids[start:start+64]})['records'])
    for relative, record in pending.items():
        response = responses.get(record['id'], {})
        if time.time() - record['created'] > 7 * 86400:
            record['status'] = 'expired'
            continue
        if response.get('status') in ('wordquest_requested','wordquest_ready','wordquest_failed'):
            record['status'] = response['status']
        elif response.get('status') == 'delete_requested':
            trash_file(safe_directory(root, relative), record, state, index, records)
        elif response.get('status') == 'held':
            record['status'] = 'held'
        elif response.get('status') == 'approved' and response.get('topic') in TOPICS:
            source = safe_directory(root, relative)
            destination = TOPICS[response['topic']] + '/' + source.name
            target = safe_directory(root, destination)
            # A prior run may have finished the rename before saving this index.
            if not source.exists() and target.exists() and topics.fingerprint(target) == record['sha256']:
                record.update(status='completed', destination=destination)
                continue
            if not source.exists() or topics.fingerprint(source) != record['sha256']:
                record['status'] = 'stale'
                continue
            if target.exists():
                record.update(status='blocked', reason='Đích đã tồn tại; giữ nguyên nguồn')
                continue
            record['destination'] = destination
            approved_routes[relative] = destination
        elif response.get('sent'):
            record['status'] = response['status']
    save(index, records)
    if approved_routes:
        topics.apply_routes(root, state, approved_routes, locked=True)
        for relative, destination in approved_routes.items():
            if topics.fingerprint(root / destination) != records[relative]['sha256']:
                raise RuntimeError('Topic contents changed during filing: ' + relative)
            records[relative]['status'] = 'completed'
        save(index, records)
    for record in records.values():
        if record['status'] in ('blocked', 'stale') and record.get('delete_requested') and not record.get('notified'):
            call(config, {'op': 'delete_failed', 'id': record['id']})
            record['notified'] = True
            save(index, records)
        if record['status'] in ('completed', 'deleted') and not record.get('notified'):
            call(config, {'op': 'complete', 'id': record['id']})
            record['notified'] = True
            save(index, records)
    queued = []
    for item in topics.candidates(root, wip, records):
        if item['stage'] == 'ready':
            continue
        source, key = item['source'], item['relative']
        if not topics.settled(source):
            continue
        record = records.get(key)
        signature = topics.metadata_key(source)
        digest = topics.cached_fingerprint(source, record or {})
        if topics.metadata_key(source) != signature:
            continue
        if record and record['sha256'] == digest and record['status'] in ('pending', 'held', 'completed', 'expired', 'blocked', 'wordquest_requested', 'wordquest_ready', 'wordquest_failed'):
            continue
        if not record or record['sha256'] != digest or record['status'] != 'queued':
            created = time.time()
            record = dict(id=hashlib.sha256(f'topic:{root}:{key}:{digest}:{created}'.encode()).hexdigest()[:32],
                          sha256=digest, signature=signature, status='queued', created=created, reason=item['reason'])
            records[key] = record
            save(index, records)
        queued.append(dict(id=record['id'], name=key))
    # Five independent choices in one message, at most eight messages per run.
    for start in range(0, min(len(queued), 40), 5):
        response = call(config, dict(op='submit_topics', proposals=queued[start:start+5],
                                    login=config['login'], bot=config['bot']))
        for record in records.values():
            remote_record = response['records'].get(record['id'])
            if remote_record and remote_record.get('sent'):
                record['status'] = remote_record['status']
        save(index, records)
    print('Topic Telegram pending: ' + str(sum(r['status'] == 'pending' for r in records.values())), flush=True)
    if automatic or approved_routes or (index.exists() and index.read_text() != before):
        folder_reviews = [relative for relative, record in records.items()
                          if record['status'] in ('pending', 'held') and safe_directory(root, relative).is_dir()]
        wip.scan(root, [*wip.DEFAULT_SCOPES, *folder_reviews], True)
        wip.dashboard(root)


if __name__ == '__main__':
    main()
