#!/usr/bin/env python3
"""DocSync command-line Kanban and shared organizer entry point."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from docsync_core import ROOT, WIP, STAGES, LABELS, board, health

def main(argv=None):
    parser = argparse.ArgumentParser(description='DocSync: scan → board → review → preview → apply → verify')
    parser.add_argument('--year-root', type=Path, default=Path.home() / 'Documents' / str(WIP.dt.date.today().year))
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
    args = parser.parse_args(argv)
    root = args.year_root.resolve()
    command = args.command or 'board'
    if command in ('preview', 'apply'):
        if root != (Path.home() / 'Documents' / str(WIP.dt.date.today().year)).resolve():
            parser.error('preview/apply only support the current-year Documents root')
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

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, WIP.sqlite3.Error) as error:
        print(f'DocSync error: {error}', file=sys.stderr)
        raise SystemExit(1)
