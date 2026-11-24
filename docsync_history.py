"""Read today's recorded classification and routing events for the local app."""

from collections import Counter
import datetime as dt
from html import escape
import hmac
import json
from pathlib import Path
import re
import time

from docsync_layout import CATEGORY_TARGETS, TOPICS
from docsync_paths import state_directory
from docsync_daily import automatic_state, file_token
import docsync_topics as topics
import docsync_workflows as workflows


MAX_ROWS = 250
LOG_ENTRY = re.compile(r'(?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[INFO\] (?P<text>.*)')


def organizer_events(state, day):
    path = state / 'logs' / (day.isoformat() + '.log')
    if not path.is_file():
        return
    with path.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            match = LOG_ENTRY.fullmatch(line.rstrip('\r\n'))
            if not match or not match['at'].startswith(day.isoformat() + ' '):
                continue
            text = match['text']
            if text.startswith('Managed route: ') and text.endswith(']'):
                route, separator, group = text.removeprefix('Managed route: ').rpartition(' [category=')
                source, arrow, destination = route.partition(' -> ')
                if separator and arrow:
                    yield match['at'][11:], source, destination, group[:-1], True
            elif text.startswith('REVIEW: ') and text.endswith(']'):
                source, separator, fields = text.removeprefix('REVIEW: ').rpartition(' [')
                if separator:
                    parts = fields[:-1].split('\t')
                    yield match['at'][11:], source, parts[2] if len(parts) > 2 else '', parts[0], False


def purchase_events(state, day):
    path = state / 'purchase-months.jsonl'
    if not path.is_file():
        return
    with path.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            try:
                row = json.loads(line)
                if row.get('status') != 'completed':
                    continue
                at = dt.datetime.fromisoformat(row['time']).astimezone()
                if at.date() == day:
                    yield at.strftime('%H:%M:%S'), str(row['source']), str(row['destination']), 'purchasing', True
            except (ValueError, KeyError, TypeError, AttributeError):
                continue


def topic_events(root, records, day):
    for relative, record in records.items():
        if record.get('origin') != 'web' or record.get('workspace') != str(root):
            continue
        at = dt.datetime.fromtimestamp(record['decided']).astimezone()
        if at.date() != day:
            continue
        destination = record['destination']
        yield (at.strftime('%H:%M:%S'), str(root / relative), str(root / destination) if destination else '',
               record['topic'], True if destination else None)


def list_today(state, day, *, root=None, records=None):
    """Keep the last recorded classification per source, including manual-review outcomes."""
    latest = {}
    sources = [organizer_events(state, day), purchase_events(state, day)]
    if root is not None:
        sources.append(topic_events(root, records if records is not None else topics.read_records(state), day))
    for events in sources:
        for row in events:
            at, source, _, _, _ = row
            if source not in latest or latest[source][0] <= at:
                latest[source] = row
    return sorted(latest.values(), reverse=True)


def review_button(root, source, key, records):
    if not key or not Path(source).is_relative_to(root):
        return ''
    relative = Path(source).relative_to(root).as_posix()
    if records.get(relative, {}).get('status', '').startswith('wordquest_'):
        return ''
    try:
        path = topics.review_file(root, relative)
        token = file_token(key, str(path), path.stat())
    except (ValueError, OSError):
        return ''
    return (f'<button class="classify-file" type="button" data-relative="{escape(relative)}" '
            f'data-token="{token}" data-name="{escape(path.name)}" '
            f'aria-label="Phân loại {escape(path.name)}">Phân loại</button>')


def decide(root, state, key, payload):
    """Resolve one still-pending history row chosen in this browser."""
    relative = payload.get('path')
    token = payload.get('token')
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{64}', token):
        raise ValueError('Làm mới lịch sử rồi chọn lại file.')
    path = topics.review_file(root, relative)
    if not any(source == str(path) and moved is not True
               for _, source, _, _, moved in list_today(state, dt.date.today(), root=root)[:MAX_ROWS]):
        raise ValueError('File không còn chờ duyệt trong lịch sử hôm nay.')

    def verify(source):
        info = source.stat()
        if not hmac.compare_digest(token, file_token(key, str(source), info)):
            raise ValueError('File đã thay đổi. Làm mới lịch sử rồi chọn lại.')
        if automatic_state(Path(relative), info, workflows.settings(root), time.time_ns()):
            raise ValueError('File đang thuộc luồng EIM tự động. Xem tiến độ trong Workflows.')

    destination = topics.resolve_file(root, state, relative, payload.get('topic'), verify)
    return {'destination': destination}


def render_today(root, template, *, day=None, state=None, key=None):
    current = day or dt.date.today()
    state = state or state_directory()
    records = topics.read_records(state)
    history = list_today(state, current, root=root, records=records)
    table_rows = []
    for at, source, destination, category, moved in history[:MAX_ROWS]:
        group = TOPICS.get(category, CATEGORY_TARGETS.get(category, category))
        if category in ('needs_review', topics.HOLD):
            group = ''
        location = Path(destination) if destination else None
        if location and location.is_absolute() and location.is_relative_to(root):
            location = location.relative_to(root)
        status = {True: 'Đã chuyển', False: 'Chờ duyệt', None: 'Giữ lại'}[moved]
        action = review_button(root, source, key, records) if moved is not True else ''
        table_rows.append(
            f'<tr><td>{escape(at[:5])}</td><td><strong>{escape(Path(source).name)}</strong>'
            f'<small>{escape(source)}</small></td><td>{escape(group or "Chưa rõ")}</td>'
            f'<td>{escape(str(location) if location else "—")}</td>'
            f'<td><span class="chip {"moved" if moved else "review"}">{status}</span></td><td>{action}</td></tr>'
        )
    if not table_rows:
        table_rows.append('<tr><td colspan="6">Chưa ghi nhận lượt phân loại hôm nay.</td></tr>')
    counts = Counter(row[4] for row in history)
    summary = f'{counts[True]} đã chuyển · {counts[False]} chờ duyệt'
    if counts[None]:
        summary += f' · {counts[None]} giữ lại'
    if len(history) > MAX_ROWS:
        summary += f' · Hiện {MAX_ROWS} mục gần nhất'
    options = ''.join(f'<option value="{choice}">{escape(label)}</option>' for choice, label in TOPICS.items())
    options += f'<option value="{topics.HOLD}" data-hold="true">Giữ lại</option>'
    return (template.replace('__TOPICS__', options).replace('__DAY__', current.strftime('%d/%m/%Y'))
            .replace('__SUMMARY__', summary).replace('__ROWS__', ''.join(table_rows)))
