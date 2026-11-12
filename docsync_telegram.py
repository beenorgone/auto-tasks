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
                            capture_output=True, text=True, timeout=30)
    for line in reversed(result.stdout.splitlines()):
        if line.startswith('DOCSYNC_JSON:'):
            response = json.loads(line.removeprefix('DOCSYNC_JSON:'))
            if result.returncode == 0 and 'error' not in response:
                return response
            raise RuntimeError('Telegram bridge: ' + str(response.get('error', 'remote_failure')))
    # Do not surface remote logs/config contents in notifications or exceptions.
    raise RuntimeError(f'Telegram bridge unavailable (SSH/WP exit {result.returncode})')


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
        if record['status'] == 'completed' and not record.get('notified'):
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
        sync(purchase.purchase_root(), purchase.intake_paths(), STATE, config)


if __name__ == '__main__':
    main()
