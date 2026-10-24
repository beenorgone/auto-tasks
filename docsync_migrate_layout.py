#!/usr/bin/env python3
"""Explicit, audited migration of known year-local folders; default is plan only."""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from docsync_core import WIP
from docsync_layout import DIRECTORIES, CATEGORY_TARGETS, OLD_TARGETS, B2B_PRICES, B2B_RECONCILIATION

# Whole-folder mappings preserve filenames and internal dossier structure.
FOLDER_MOVES = {
    'AT. VAT': '20_BAO_CAO/VAT', 'AT.VAT': '20_BAO_CAO/VAT',
    'AT. Shopee reports': '20_BAO_CAO/Shopee', 'AT.Shopee reports': '20_BAO_CAO/Shopee',
    'AT. GHTK reports': '20_BAO_CAO/GHTK', 'AT.GHTK reports': '20_BAO_CAO/GHTK',
    'AT. SAOKE': '20_BAO_CAO/Sao ke', 'AT.SAOKE': '20_BAO_CAO/Sao ke',
    'AT. QC reports': '10_HO_SO/Chat luong/Kiem nghiem',
    'AT. Sourcing contracts': '10_HO_SO/Nhap hang/Hop dong',
    'AT. Invoice intake': '00_INBOX/Hoa don',
    'Review Label': '10_HO_SO/Bao bi',
    'WIP ke toan thue': '10_HO_SO/Ke toan',
    'ho so danh gia CUC 20260421': '10_HO_SO/Chat luong/Chung nhan/CUC/2026-04-21',
    'Tự công bố sản phẩm trong nước-20260607T192041Z-3-001': '10_HO_SO/Chat luong/Tu cong bo/2026-06',
    'Others/Review Label': '10_HO_SO/Bao bi',
    'Others/WIP ke toan thue': '10_HO_SO/Ke toan',
    'Others/ho so danh gia CUC 20260421': '10_HO_SO/Chat luong/Chung nhan/CUC/2026-04-21',
    'Others/Tự công bố sản phẩm trong nước-20260607T192041Z-3-001': '10_HO_SO/Chat luong/Tu cong bo/2026-06',
    'Others/TCB 2026-05': '10_HO_SO/Chat luong/Tu cong bo/2026-05',
    'Others/Eurofins': '10_HO_SO/Chat luong/Kiem nghiem/Eurofins',
    'Others/Ho so Inulin': '10_HO_SO/Chat luong/Inulin/COA',
    'Others/TODO Bioway': '10_HO_SO/Chat luong/COA/Bioway',
    'Others/Bột củ dền-20260629T032657Z-3-001/Bột củ dền': '10_HO_SO/Chat luong/Bot cu den',
    'Others/yeast hero images': '10_HO_SO/Bao bi/Yeast Hero/Hinh anh nhan',
    'Others/2026.08.15 ho so VietinBank x Sepay': '10_HO_SO/Phap ly/VietinBank - Sepay/2026-08-15',
    'Others/HỒ SƠ GIẢI TRÌNH BHXH': '10_HO_SO/Phap ly/BHXH',
    'Others/PO': '10_HO_SO/Phap ly/Chu ky so/Thai Son',
    'Others/T8 IVAR': '10_HO_SO/Nhap hang/2026-08',
    'Others/QCVN': '80_THAM_CHIEU/Quy chuan',
    'Others/QRs': '80_THAM_CHIEU/Ma QR',
    'Others/plans': '10_HO_SO/Kinh doanh/Ke hoach',
    'Others/plugin codes': '90_CACH_LY_KY_THUAT/Ma nguon',
    'template sao ke': '80_THAM_CHIEU/Bieu mau/Sao ke',
    '91_TECH_QUARANTINE': '90_CACH_LY_KY_THUAT',
    '99_ARCHIVE_MANIFESTS': '99_NHAT_KY',
}
EMPTY_LEGACY = ('10_WIP', '90_NEEDS_REVIEW', 'AT. B2', 'Others/Bột củ dền-20260629T032657Z-3-001')


def safe_path(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError(f'Unsafe relative path: {relative}')
    path = root / rel
    if root.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path outside root: {path}')
    for index in range(1, len(rel.parts) + 1):
        if root.joinpath(*rel.parts[:index]).is_symlink():
            raise ValueError(f'Symlink path: {path}')
    return path


def digest(path):
    return WIP.digest(path)


def build_plan(root: Path) -> dict:
    entries = []
    source_dirs = set()
    target_dirs = set(DIRECTORIES)
    destinations = set()
    unresolved_b2b = []
    def add(source, destination):
        src = safe_path(root, source); dst = safe_path(root, destination)
        if destination in destinations or dst.exists():
            raise FileExistsError(f'Destination collision; no files moved: {destination}')
        destinations.add(destination)
        stat = src.stat()
        entries.append(dict(source=source, destination=destination, sha256=digest(src),
                            size=stat.st_size, mtime_ns=stat.st_mtime_ns, inode=stat.st_ino))
    for old, new in FOLDER_MOVES.items():
        source = safe_path(root, old)
        if not source.exists():
            continue
        if not source.is_dir():
            raise ValueError(f'Expected directory: {source}')
        source_dirs.add(old); target_dirs.add(new)
        for item in sorted(source.rglob('*')):
            relative = item.relative_to(root).as_posix()
            safe_path(root, relative)
            target = (Path(new) / item.relative_to(source)).as_posix()
            if item.is_dir():
                source_dirs.add(relative); target_dirs.add(target)
            elif item.is_file():
                add(relative, target)
            else:
                raise ValueError(f'Unsupported source type: {item}')
    b2b = safe_path(root, 'AT. B2')
    if b2b.is_dir():
        for item in sorted(b2b.rglob('*')):
            relative = item.relative_to(root).as_posix()
            safe_path(root, relative)
            if item.is_dir():
                source_dirs.add(relative)
                continue
            if not item.is_file():
                raise ValueError(f'Unsupported B2B file: {item}')
            text = WIP.normalized(item.name)
            if WIP.re.search(r'b[aả]ng gi[aá]', text):
                add(relative, B2B_PRICES + '/' + item.name)
            elif WIP.re.search(r'c[oô]ng n[oợ]|d[oố]i so[aá]t', text):
                add(relative, B2B_RECONCILIATION + '/' + item.name)
            else:
                unresolved_b2b.append(relative)
    for relative in target_dirs:
        p = safe_path(root, relative)
        if p.exists() and not p.is_dir():
            raise FileExistsError(f'Target directory occupied by a file: {p}')
    # Confirm no index row at a destination would be overwritten.
    index = root / '.wip/index.sqlite'
    if index.exists():
        with sqlite3.connect(index.as_uri() + '?mode=ro', uri=True) as conn:
            for item in entries:
                if conn.execute('SELECT 1 FROM documents WHERE path=?', (item['destination'],)).fetchone():
                    raise ValueError(f'Index destination collision: {item["destination"]}')
    return dict(version=1, root=str(root), entries=entries,
                source_dirs=sorted(source_dirs | set(EMPTY_LEGACY)),
                target_dirs=sorted(target_dirs), unresolved_b2b=unresolved_b2b)


def backup_sqlite(source, destination):
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)


def remap_index(root, entries):
    index = root / '.wip/index.sqlite'
    count = 0
    if not index.exists():
        return count
    with sqlite3.connect(index) as conn:
        for item in entries:
            candidates = [item['source']]
            if item['source'].startswith('Others/'):
                candidates.append(item['source'][len('Others/'):])
            matches = []
            for old in candidates:
                row = conn.execute('SELECT sha256 FROM documents WHERE path=?', (old,)).fetchone()
                if row and row[0] == item['sha256']:
                    matches.append(old)
            if len(matches) > 1:
                raise ValueError(f'Ambiguous index rows for {item["source"]}')
            if matches:
                conn.execute('UPDATE documents SET path=? WHERE path=?', (item['destination'], matches[0]))
                count += 1
        for category, old_targets in OLD_TARGETS.items():
            for old_target in old_targets:
                for table in ('documents', 'dossiers'):
                    conn.execute(f'UPDATE {table} SET proposed_target=? WHERE proposed_target=?', (CATEGORY_TARGETS[category], old_target))
        conn.execute('INSERT INTO events(created_at,event_type,subject,detail) VALUES (?,?,?,?)',
                     (dt.datetime.now(dt.timezone.utc).isoformat(), 'layout_migration', str(root), json.dumps({'remapped': count})))
    return count


def remap_processed(root, state_dir, entries):
    database = state_dir / 'processed.db'
    count = 0
    if database.exists():
        with sqlite3.connect(database) as conn:
            for item in entries:
                old_paths = [item['source']]
                if item['source'].startswith('Others/'):
                    old_paths.append(item['source'][len('Others/'):])
                for old in old_paths:
                    cursor = conn.execute('UPDATE processed_files SET final_path=? WHERE final_path=? AND sha256=?',
                                          (str(root / item['destination']), str(root / old), item['sha256']))
                    count += cursor.rowcount
    return count


def unaffected_snapshot(root, excluded, state_dir):
    result = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        if Path(current) == state_dir:
            dirs[:] = []
            continue
        if Path(current) == root:
            dirs[:] = [name for name in dirs if name not in ('.wip', '99_NHAT_KY')]
        for name in files:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if relative not in excluded:
                stat = path.lstat()
                result[relative] = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return result


def apply_plan(root, state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'organizer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = build_plan(root)  # Recheck the live filesystem under the shared lock.
        unaffected = unaffected_snapshot(root, {item['source'] for item in plan['entries']}, state_dir)
        stamp = dt.datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')
        backup = root / '.wip' / ('layout-backup-' + stamp)
        backup.mkdir(parents=True, exist_ok=False)
        for source, name in [(root / '.wip/index.sqlite', 'index.sqlite'), (state_dir / 'processed.db', 'processed.db')]:
            if source.exists():
                backup_sqlite(source, backup / name)
        rules = root / '.wip/rules.json'
        if rules.exists(): shutil.copy2(rules, backup / 'rules.json')
        manifest = backup / 'manifest.json'
        audit = {**plan, 'status': 'applying', 'moved': [], 'created_dirs': [], 'removed_dirs': []}
        def save():
            temp = manifest.with_suffix('.tmp')
            temp.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
            temp.replace(manifest)
        def mkdir(path):
            missing = []
            while path != root and not path.exists():
                missing.append(path); path = path.parent
            for directory in reversed(missing):
                directory.mkdir()
                audit['created_dirs'].append(directory.relative_to(root).as_posix())
        save()
        try:
            for relative in plan['target_dirs']:
                mkdir(safe_path(root, relative))
            for item in plan['entries']:
                source = safe_path(root, item['source']); target = safe_path(root, item['destination'])
                if digest(source) != item['sha256'] or target.exists():
                    raise ValueError(f'Source changed or destination appeared: {source}')
                mkdir(target.parent)
                source.rename(target)
                audit['moved'].append(item['source']); save()
                stat = target.stat()
                if digest(target) != item['sha256'] or stat.st_ino != item['inode'] or stat.st_mtime_ns != item['mtime_ns']:
                    raise ValueError(f'Integrity mismatch: {target}')
            audit['index_rows_remapped'] = remap_index(root, plan['entries'])
            audit['processed_rows_remapped'] = remap_processed(root, state_dir, plan['entries'])
            WIP.ensure_state(root)
            for relative in sorted(plan['source_dirs'], key=lambda value: len(Path(value).parts), reverse=True):
                source = safe_path(root, relative)
                if source.is_dir() and not any(source.iterdir()):
                    source.rmdir(); audit['removed_dirs'].append(relative)
            if unaffected_snapshot(root, {item['destination'] for item in plan['entries']}, state_dir) != unaffected:
                raise ValueError('Unrelated files changed during migration')
            audit['unaffected_files_verified'] = len(unaffected)
            audit['status'] = 'verified'
            save()
            report = root / '99_NHAT_KY' / ('layout-migration-' + stamp + '.json')
            shutil.copy2(manifest, report)
            return report, audit
        except Exception as error:
            audit['error'] = str(error)
            # Restore metadata and reverse only this run's verified source moves.
            for item in reversed(plan['entries']):
                if item['source'] in audit['moved']:
                    source = root / item['source']; target = root / item['destination']
                    if source.exists() or not target.is_file() or digest(target) != item['sha256']:
                        audit['status'] = 'rollback_needs_review'; save(); raise
                    source.parent.mkdir(parents=True, exist_ok=True)
                    target.rename(source)
            for target, name in [(root / '.wip/index.sqlite', 'index.sqlite'), (state_dir / 'processed.db', 'processed.db')]:
                if (backup / name).exists(): backup_sqlite(backup / name, target)
            if (backup / 'rules.json').exists(): shutil.copy2(backup / 'rules.json', rules)
            for relative in audit['removed_dirs']:
                (root / relative).mkdir(parents=True, exist_ok=True)
            for relative in reversed(audit['created_dirs']):
                p = root / relative
                if p.is_dir() and not any(p.iterdir()): p.rmdir()
            audit['status'] = 'rolled_back'; save()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year-root', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = args.year_root.absolute()
    if not root.is_dir() or root.is_symlink(): parser.error('A real existing year directory is required')
    if args.apply:
        report, plan = apply_plan(root, args.state_dir or root.parent / '.auto-tasks/ivar-documents-organizer')
        print(f'Verified {len(plan["entries"])} file moves; audit: {report}')
    else:
        plan = build_plan(root)
        print(json.dumps(plan, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
