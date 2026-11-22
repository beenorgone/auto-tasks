"""Read today's recorded classification and routing events for the local app."""

from collections import Counter
import datetime as dt
from html import escape
import json
import os
from pathlib import Path
import re

from docsync_layout import CATEGORY_TARGETS


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


def list_today(state, day):
    """Keep the last recorded classification per source, including manual-review outcomes."""
    latest = {}
    for events in (organizer_events(state, day), purchase_events(state, day)):
        for row in events:
            at, source, _, _, _ = row
            if source not in latest or latest[source][0] <= at:
                latest[source] = row
    return sorted(latest.values(), reverse=True)


def render_today(root, template, *, day=None, state=None):
    current = day or dt.date.today()
    state = state or Path(os.getenv('STATE_DIR') or Path.home() / 'Documents/.auto-tasks/ivar-documents-organizer')
    history = list_today(state, current)
    table_rows = []
    for at, source, destination, category, moved in history[:MAX_ROWS]:
        group = CATEGORY_TARGETS.get(category, category)
        location = Path(destination) if destination else None
        if location and location.is_absolute() and location.is_relative_to(root):
            location = location.relative_to(root)
        status = 'Đã chuyển' if moved else 'Chờ duyệt'
        table_rows.append(
            f'<tr><td>{escape(at[:5])}</td><td><strong>{escape(Path(source).name)}</strong>'
            f'<small>{escape(source)}</small></td><td>{escape(group or "Chưa rõ")}</td>'
            f'<td>{escape(str(location) if location else "—")}</td>'
            f'<td><span class="chip {"moved" if moved else "review"}">{status}</span></td></tr>'
        )
    if not table_rows:
        table_rows.append('<tr><td colspan="5">Chưa ghi nhận lượt phân loại hôm nay.</td></tr>')
    counts = Counter(row[4] for row in history)
    summary = f'{counts[True]} đã chuyển · {counts[False]} chờ duyệt'
    if len(history) > MAX_ROWS:
        summary += f' · Hiện {MAX_ROWS} mục gần nhất'
    return (template.replace('__DAY__', current.strftime('%d/%m/%Y'))
            .replace('__SUMMARY__', summary).replace('__ROWS__', ''.join(table_rows)))
