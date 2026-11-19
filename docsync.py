#!/usr/bin/env python3
"""DocSync command-line Kanban and shared organizer entry point."""
import docsync_purchase as PURCHASE
import docsync_erp as ERP
import docsync_eim as EIM
import datetime as dt
import argparse
import json
import subprocess
import sys
from pathlib import Path
from docsync_core import ROOT, WIP, STAGES, LABELS, board, health
from docsync_paths import work_root

def main(argv=None):
    parser = argparse.ArgumentParser(description='DocSync: scan → board → review → preview → apply → verify')
    parser.add_argument('--root', '--year-root', dest='year_root', type=Path, default=work_root(),
                        help='WIP & Reference root (--year-root remains an alias)')
    sub = parser.add_subparsers(dest='command')
    for name in ('board', 'status'):
        p = sub.add_parser(name)
        p.add_argument('--json', action='store_true')
        if name == 'board':
            p.add_argument('--stage', choices=STAGES)
            p.add_argument('--limit', type=int, default=8, help='cards per column; 0 shows all')
    sub.add_parser('scan')
    sub.add_parser('rules')
    sub.add_parser('preview')
    apply = sub.add_parser('apply')
    apply.add_argument('--sync-project', action='store_true', help='also run external rclone sync')
    review = sub.add_parser('review')
    review.add_argument('dossier')
    review.add_argument('category')
    review.add_argument('--note', default='')
    review.add_argument('--status', choices=('in_progress', 'waiting_external', 'approved'), default='in_progress')
    state = sub.add_parser('set-status')
    state.add_argument('dossier')
    state.add_argument('status', choices=WIP.VALID_STATUSES)
    today = sub.add_parser('today', help='bảng tổng hợp file trong ngày theo nhóm + trạng thái ERP')
    today.add_argument('--date', type=dt.date.fromisoformat, help='YYYY-MM-DD (mặc định hôm nay)')
    today.add_argument('--json', action='store_true')
    erp = sub.add_parser('erp', help='kết nối ERP IVAR: tra bản ghi, gán file, đẩy file')
    erp_sub = erp.add_subparsers(dest='erp_command', required=True)
    erp_sub.add_parser('check', help='kiểm tra kết nối + App Password')
    find = erp_sub.add_parser('find', help='tìm bản ghi được sửa')
    find.add_argument('type', choices=sorted(ERP.TYPES))
    find.add_argument('query', nargs='?', default='')
    fields = erp_sub.add_parser('fields', help='field file của một bản ghi')
    fields.add_argument('type', choices=sorted(ERP.TYPES))
    fields.add_argument('id', type=int)
    assign = erp_sub.add_parser('assign', help='gán file (đường dẫn trong gốc làm việc) vào bản ghi + field')
    assign.add_argument('path')
    assign.add_argument('type', choices=sorted(ERP.TYPES))
    assign.add_argument('id', type=int)
    assign.add_argument('field')
    assign.add_argument('--ai', choices=('none', 'summary', 'analysis'), default='none', help='AI tạo Ghi chú AI sau khi tải')
    assign.add_argument('--instruction', default='', help='yêu cầu riêng cho AI')
    assign.add_argument('--no-verify', action='store_true', help='không hỏi ERP để kiểm field (offline)')
    unassign = erp_sub.add_parser('unassign')
    unassign.add_argument('path')
    push = erp_sub.add_parser('push', help='đẩy các file đã gán (mặc định: mọi file chờ / lỗi)')
    push.add_argument('paths', nargs='*')
    push.add_argument('--dry-run', action='store_true')
    eim = sub.add_parser('eim', help='tự đẩy file đối soát GHTK lên EIM (đối soát + lô HĐ nháp + Telegram)')
    eim_sub = eim.add_subparsers(dest='eim_command', required=True)
    eim_sub.add_parser('sync', help='quét và đẩy ngay (timer docsync-eim cũng chạy lệnh này)')
    eim_status = eim_sub.add_parser('status', help='các file đã đẩy gần đây')
    eim_status.add_argument('--limit', type=int, default=20)
    eim_retry = eim_sub.add_parser('retry', help='cho phép đẩy lại một file bị từ chối (sau khi đã sửa)')
    eim_retry.add_argument('path')
    args = parser.parse_args(argv)
    root = args.year_root.resolve()
    command = args.command or 'board'
    if command == 'today':
        return print_today(root, args.date, args.json)
    if command == 'erp':
        return run_erp(root, args)
    if command == 'eim':
        return run_eim(root, args)
    if command in ('preview', 'apply'):
        if root != work_root().resolve():
            parser.error('preview/apply only support the configured WIP & Reference root')
        flags = ['--plan-managed-rules'] if command == 'preview' else ['--apply-managed-rules']
        if command == 'preview' or not args.sync_project:
            flags.append('--no-sync')
        result = subprocess.run(['/bin/sh', str(ROOT / 'ivar-documents-organizer.sh'), *flags])
        if result.returncode:
            return result.returncode
        WIP.scan(root, [], True)
        WIP.dashboard(root)
        print('Next: docsync board')
    elif command == 'rules':
        rule_file = WIP.rules_path(root)
        rules = json.loads(rule_file.read_text()) if rule_file.exists() else WIP.DEFAULT_RULES
        for rule in rules['categories']:
            print(f"{rule['id']}: {rule['label']} | {rule.get('route_mode', 'review_only')} → {rule['target']}")
        print(f'{PURCHASE.RULE}: {PURCHASE.RULE_DESCRIPTION} | managed_move | {PURCHASE.purchase_root()}')
        print('Next: docsync review <dossier> <category> --note <reason>')
    elif command == 'scan':
        print(json.dumps(WIP.scan(root, [], True), ensure_ascii=False))
        WIP.dashboard(root)
        print('Next: docsync board → docsync review <dossier> <category>')
    elif command == 'review':
        WIP.review_dossier(root, args.dossier, args.category, None, args.status, args.note)
        WIP.dashboard(root)
        print('Review recorded. Review-only categories require manual filing. Next: docsync board')
    elif command == 'set-status':
        WIP.set_dossier_status(root, args.dossier, args.status)
        WIP.dashboard(root)
        print('Status recorded. Next: docsync board')
    else:
        data = health(root) if command == 'status' else board(root)
        if command == 'board' and getattr(args, 'stage', None):
            data['cards'] = [card for card in data['cards'] if card['stage'] == args.stage]
        if getattr(args, 'json', False):
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif command == 'status':
            print("Organizer: " + ("running" if data["organizerBusy"] else "idle"))
            for unit, values in data['services'].items():
                print(f"{unit}: {values.get('ActiveState', 'unknown')}/{values.get('SubState', '?')} result={values.get('Result', '—')}")
            print(f"Last timer completion: {data['lastTimerRun'] or 'unknown'} | Next: {data['nextRun'] or 'unknown'}")
            print('\n'.join(data['recentLogs'][-12:]))
            print('Next: docsync board → docsync preview')
        else:
            print('DocSync Kanban · ' + ' → '.join(f"{LABELS[k]}: {data['counts'][k]}" for k in STAGES))
            for stage in STAGES:
                if getattr(args, 'stage', None) and args.stage != stage:
                    continue
                print(f"\n{LABELS[stage]} ({data['counts'][stage]})")
                cards = [c for c in data['cards'] if c['stage'] == stage]
                limit = max(0, getattr(args, 'limit', 8))
                shown = cards[:limit] if limit else cards
                for card in shown:
                    # Escape terminal control characters in untrusted file names/reasons.
                    clean = lambda v: str(v).encode('unicode_escape').decode() if any(ord(c) < 32 or ord(c) == 127 for c in str(v)) else str(v)
                    print(f"  • {clean(card['path'])} [{card['category']}]")
                    print(f"    {clean(card['reason'])} → {clean(card['target'] or 'Chưa có đích')}")
                    print(f"    {card['next']} | dossier: {clean(card['dossier'] or 'chạy docsync scan')}")
                if len(shown) < len(cards):
                    print(f'  … {len(cards)-len(shown)} more; docsync board --stage {stage} --limit 0')
    return 0

def classifier(root):
    rule_file = WIP.rules_path(root)
    rules = json.loads(rule_file.read_text()) if rule_file.exists() else WIP.DEFAULT_RULES
    return lambda relative, path: WIP.classify(relative, rules, path)


def clean(value):
    """Escape terminal control characters in untrusted file names / ERP text."""
    text = str(value)
    return text.encode('unicode_escape').decode() if any(ord(c) < 32 or ord(c) == 127 for c in text) else text


ERP_LABELS = {'unassigned': 'chưa gán', 'assigned': 'chờ đẩy', 'stale': 'file đã đổi — gán lại',
              'pushed': 'đã lên ERP', 'duplicate': 'ERP đã có', 'error': 'lỗi'}


def print_today(root, day, as_json):
    data = ERP.today(root, day, classifier(root), eim_status=EIM.status_for_paths(ERP.STATE))
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"DocSync hôm nay {data['date']} · {data['total']} file · " +
          ' · '.join(f"{ERP_LABELS[k]}: {v}" for k, v in data['counts'].items() if v))
    for group in data['groups']:
        print(f"\n[{group['category']}] ({len(group['files'])})")
        for row in group['files']:
            line = f"  • {clean(row['path'])}  [{ERP_LABELS.get(row['erp'], row['erp'])}]"
            a = row['assignment']
            if a:
                line += f"  → {a['type']} #{a['id']} › {a['field']}" + (f" + AI {a['ai_mode']}" if a['ai'] else '')
            elif row.get('eim'):
                line += f"  EIM: {row['eim']['status']} · {row['eim'].get('message', '')}"
            elif row['suggestion']:
                sg = row['suggestion']
                line += f"  gợi ý: {sg['type']} › {sg['field']}" + (f" (tìm '{sg['query']}')" if sg['query'] else '')
            print(line)
    if data['truncated']:
        print(f'\n… dừng ở {ERP.TODAY_LIMIT} file')
    print('\nNext: docsync erp find <type> <từ khóa> → docsync erp assign <file> <type> <id> <field> [--ai summary] → docsync erp push')
    return 0


def run_erp(root, args):
    command = args.erp_command
    if command == 'unassign':
        print('Đã bỏ gán.' if ERP.unassign(args.path) else 'File chưa được gán.')
        return 0
    client = None
    if command != 'assign' or not args.no_verify:
        client = ERP.ErpClient(ERP.load_config())
    if command == 'check':
        types = client.types().get('types', [])
        print('Kết nối ERP OK · ' + ', '.join(t['type'] for t in types))
    elif command == 'find':
        for row in client.targets(args.type, args.query):
            print(f"{row['id']:>7}  {clean(row['label'])}  {clean(row.get('status', ''))} {clean(row.get('date', ''))} {clean(row.get('extra', ''))}")
    elif command == 'fields':
        data = client.record(args.type, args.id)
        print(clean(data['record']['label']) + ' · ' + clean(data['record']['url']))
        for field in data['fields']:
            limit = f"/{field['max']}" if field['max'] else ''
            print(f"  {field['key']}: {clean(field['label'])} ({field['count']}{limit} file)")
    elif command == 'assign':
        label = field_label = ''
        if client:
            data = client.record(args.type, args.id)
            known = {f['key']: f for f in data['fields']}
            if args.field not in known:
                raise ERP.ErpError('Bản ghi không có field này. Chọn: ' + ', '.join(known))
            label, field_label = data['record']['label'], known[args.field]['label']
        record = ERP.assign(root, args.path, args.type, args.id, args.field, label=label, field_label=field_label,
                            ai=args.ai != 'none', ai_mode=args.ai, instruction=args.instruction)
        print(f"Đã gán {clean(args.path)} → {record['type']} #{record['id']} › {record['field']} · SHA-256 {record['sha256'][:12]}…")
        print('Next: docsync erp push --dry-run → docsync erp push')
    elif command == 'push':
        for result in ERP.push(root, client, args.paths or None, args.dry_run):
            print(f"  {result['status']:<10} {clean(result['path'])} — {clean(result['message'])}")
    return 0


def run_eim(root, args):
    state_dir = ERP.STATE
    if args.eim_command == 'sync':
        import fcntl
        with (state_dir / 'organizer.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('Organizer đang chạy — thử lại sau.')
                return 75
            results = EIM.sync(root, ERP.ErpClient(ERP.load_config()), state_dir, log=lambda line: print(clean(line)))
        if not results:
            print('Không có file đối soát GHTK mới cần đẩy.')
        return 0
    state = EIM.load_state(state_dir)
    if args.eim_command == 'retry':
        hits = [key for key, value in state.items() if value.get('path') == args.path]
        for key in hits:
            del state[key]
        EIM.save_state(state_dir, state)
        print(f'Đã mở lại {len(hits)} bản ghi; lượt sync sau sẽ đẩy lại.' if hits else 'Không thấy file này trong lịch sử EIM.')
        return 0
    rows = sorted(state.values(), key=lambda r: r.get('at', ''), reverse=True)[:max(1, args.limit)]
    print('EIM DocSync: ' + ('bật' if EIM.enabled(state_dir) else 'tắt (thiếu erp.json hoặc eim_auto_push=false)'))
    for r in rows:
        print(f"  {r.get('at', '')}  {r.get('status', ''):<9} {clean(r.get('path', ''))} — {clean(r.get('message', ''))}")
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, WIP.sqlite3.Error, ERP.ErpError) as error:
        print(f'DocSync error: {error}', file=sys.stderr)
        raise SystemExit(1)
