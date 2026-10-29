#!/usr/bin/env python3
"""Audited sibling-root rename and YYYY/YYYY-MM month archive migration."""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from docsync_paths import archive_relative


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def sqlite_backup(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def build_plan(source, destination):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source.is_symlink() or not source.is_dir() or not re.fullmatch(r'\d{4}', source.name):
        raise ValueError('Source must be a real four-digit year directory')
    if destination.parent != source.parent or destination == source:
        raise ValueError('Destination must be a different sibling directory')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f'Destination already exists: {destination}')
    if (source / source.name).exists() or (source / source.name).is_symlink():
        raise FileExistsError('The new year archive would collide with existing content')
    months = [p.name for p in sorted(source.iterdir()) if re.fullmatch(r'0[1-9]|1[0-2]', p.name)]
    if any(not (source / month).is_dir() or (source / month).is_symlink() for month in months):
        raise ValueError('A month entry is not a real directory')
    return dict(source=str(source), destination=str(destination), year=source.name,
                month_moves={m: archive_relative(m, source.name) for m in months})


def map_relative(relative, plan):
    first, separator, rest = str(relative).partition('/')
    return plan['month_moves'].get(first, first) + (separator + rest if separator else '')


def map_absolute(value, plan):
    if not isinstance(value, str):
        return value
    old = plan['source']
    if value == old:
        return plan['destination']
    if value.startswith(old + '/'):
        return plan['destination'] + '/' + map_relative(value[len(old) + 1:], plan)
    return value


def inventory(root):
    entries = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            if (Path(current) / name).is_symlink():
                raise ValueError(f'Symlink requires separate review: {Path(current) / name}')
        for name in files:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if relative in ('.wip/index.sqlite-wal', '.wip/index.sqlite-shm'):
                continue
            stat = path.stat()
            entries[relative] = dict(size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                                     inode=stat.st_ino, mode=stat.st_mode, sha256=digest(path))
            if len(entries) % 2000 == 0:
                print(f'Inventoried {len(entries)} files', flush=True)
    return entries


def remap_database(database, plan, kind):
    if not database.exists():
        return 0
    count = 0
    with sqlite3.connect(database) as conn:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError(f'Invalid SQLite database: {database}')
        if kind == 'index':
            for (old,) in conn.execute('SELECT path FROM documents').fetchall():
                new = map_relative(old, plan)
                if old != new:
                    conn.execute('UPDATE documents SET path=? WHERE path=?', (new, old))
                    count += 1
            for table in ('documents', 'dossiers'):
                for (old,) in conn.execute(f'SELECT DISTINCT proposed_target FROM {table}').fetchall():
                    if old is None:
                        continue
                    new = map_absolute(old, plan) if old.startswith('/') else map_relative(old, plan)
                    if old != new:
                        conn.execute(f'UPDATE {table} SET proposed_target=? WHERE proposed_target=?', (new, old))
            conn.execute('INSERT INTO events(created_at,event_type,subject,detail) VALUES (?,?,?,?)',
                         (dt.datetime.now(dt.timezone.utc).isoformat(), 'root_relocation',
                          plan['destination'], json.dumps(plan, ensure_ascii=False)))
        else:
            table, columns = ('processed_files', ('source_path', 'final_path', 'notes')) if kind == 'processed' else ('vendors', ('last_source_path', 'last_final_path'))
            for row in conn.execute(f'SELECT rowid,{",".join(columns)} FROM {table}').fetchall():
                values = tuple(map_absolute(value, plan) for value in row[1:])
                if values != row[1:]:
                    conn.execute(f'UPDATE {table} SET ' + ','.join(f'{column}=?' for column in columns) + ' WHERE rowid=?', (*values, row[0]))
                    count += 1
    return count


def rewrite_metadata(root, plan, backup_dir):
    changed = set()
    def save(path, content):
        before = path.read_bytes()
        data = content.encode('utf-8')
        if data == before:
            return
        relative = path.relative_to(root).as_posix()
        backup = backup_dir / 'files' / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        path.write_bytes(data)
        changed.add(relative)
    rules = root / '.wip/rules.json'
    if rules.exists():
        data = json.loads(rules.read_text())
        data['excluded_paths'] = list(dict.fromkeys(map_relative(p, plan) for p in data.get('excluded_paths', [])))
        for category in data.get('categories', []):
            target = category.get('target')
            if target:
                category['target'] = map_absolute(target, plan) if target.startswith('/') else map_relative(target, plan)
        save(rules, json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    # Preserve historical source/destination text; change clickable current links.
    reports = root / '99_NHAT_KY'
    for path in reports.glob('*.html'):
        content = path.read_text()
        for old, new in plan['month_moves'].items():
            content = content.replace((Path(plan['source']) / old).as_uri() + '/',
                                      (root / new).as_uri() + '/')
        content = content.replace(Path(plan['source']).as_uri() + '/', root.as_uri() + '/')
        save(path, content)
    for path in reports.glob('*.json'):
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get('events'), list):
            continue
        for event in data['events']:
            if 'current_path' in event:
                event['current_path'] = map_absolute(event['current_path'], plan)
            if 'chain' in event:
                event['chain'] = [map_absolute(p, plan) for p in event['chain']]
        data['relocation'] = dict(time=dt.datetime.now().astimezone().isoformat(), **plan)
        save(path, json.dumps(data, ensure_ascii=False, indent=2))
    return changed


def apply_plan(source, destination, state, audit_dir):
    source, destination, state, audit_dir = map(lambda p: Path(p).absolute(), (source, destination, state, audit_dir))
    if audit_dir.is_relative_to(source) or audit_dir.is_relative_to(destination):
        raise ValueError('Audit/backup directory must be outside the relocated tree')
    audit_dir.mkdir(parents=True, exist_ok=True)
    manifest = audit_dir / 'manifest.json'
    if manifest.exists():
        raise FileExistsError(f'Audit already exists: {manifest}')
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'organizer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = build_plan(source, destination)
        entries = inventory(source)
        audit = {**plan, 'started_at': dt.datetime.now().astimezone().isoformat(),
                 'status': 'planned', 'entries': entries, 'moved_months': []}
        def save():
            temporary = manifest.with_suffix('.tmp')
            temporary.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
            temporary.replace(manifest)
        save()
        databases = [(source / '.wip/index.sqlite', 'index.sqlite', 'index'),
                     (state / 'processed.db', 'processed.db', 'processed'),
                     (state / 'vendors.db', 'vendors.db', 'vendors')]
        backed = []
        for path, name, kind in databases:
            if path.exists():
                sqlite_backup(path, audit_dir / name)
                backed.append((path, name, kind))
        # Abort before renaming if a document changed while hashing/backing up.
        for relative, signature in entries.items():
            if relative.startswith('.wip/index.sqlite'):
                continue
            stat = (source / relative).stat()
            if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (signature['size'], signature['mtime_ns'], signature['inode']):
                raise ValueError(f'Source changed during preflight: {relative}')
        audit['status'] = 'applying'; save()
        renamed = False
        modified = set()
        try:
            source.rename(destination)
            renamed = True
            if plan['month_moves']:
                (destination / plan['year']).mkdir()
            for old, new in plan['month_moves'].items():
                (destination / old).rename(destination / new)
                audit['moved_months'].append(old); save()
            audit['database_rows_remapped'] = {}
            for path, name, kind in backed:
                active = destination / '.wip/index.sqlite' if kind == 'index' else path
                audit['database_rows_remapped'][kind] = remap_database(active, plan, kind)
            modified = rewrite_metadata(destination, plan, audit_dir)
            audit['metadata_files_updated'] = sorted(modified)
            # SQLite is the only binary document intentionally changed in-tree.
            if '.wip/index.sqlite' in entries:
                modified.add('.wip/index.sqlite')
            for number, (relative, signature) in enumerate(entries.items(), 1):
                target = destination / map_relative(relative, plan)
                if relative in modified:
                    if not target.is_file():
                        raise ValueError(f'Missing metadata after relocation: {target}')
                    continue
                stat = target.stat()
                if (stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_mode) != (signature['size'], signature['mtime_ns'], signature['inode'], signature['mode']) or digest(target) != signature['sha256']:
                    raise ValueError(f'Integrity mismatch after relocation: {target}')
                if number % 2000 == 0:
                    print(f'Verified {number} files', flush=True)
            expected = {map_relative(p, plan) for p in entries}
            actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()
                      and p.relative_to(destination).as_posix() not in ('.wip/index.sqlite-shm', '.wip/index.sqlite-wal')}
            if actual != expected:
                raise ValueError(f'Inventory changed: added={sorted(actual-expected)}, missing={sorted(expected-actual)}')
            audit['verified_files'] = len(entries) - len(modified)
            audit['file_count'] = len(entries)
            audit['status'] = 'verified'
            audit['completed_at'] = dt.datetime.now().astimezone().isoformat(); save()
            report = destination / '99_NHAT_KY' / ('root-relocation-' + audit_dir.name + '.json')
            report.parent.mkdir(exist_ok=True)
            shutil.copy2(manifest, report)
            return report, audit
        except Exception as error:
            audit['error'] = str(error)
            if renamed:
                for month in reversed(audit['moved_months']):
                    (destination / plan['month_moves'][month]).rename(destination / month)
                year = destination / plan['year']
                if plan['month_moves'] and year.is_dir() and not any(year.iterdir()):
                    year.rmdir()
                destination.rename(source)
            # Metadata backups live outside the renamed tree and survive rollback.
            for path, name, kind in backed:
                for suffix in ('-wal', '-shm'):
                    Path(str(path) + suffix).unlink(missing_ok=True)
                sqlite_backup(audit_dir / name, path)
            file_backup = audit_dir / 'files'
            if file_backup.exists():
                for backup in file_backup.rglob('*'):
                    if backup.is_file():
                        shutil.copy2(backup, source / backup.relative_to(file_backup))
            audit['status'] = 'rolled_back'; save()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--audit-dir', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply:
        if args.audit_dir is None:
            parser.error('--apply requires an external --audit-dir')
        report, audit = apply_plan(args.source, args.destination,
                                  args.state_dir or args.source.parent / '.auto-tasks/ivar-documents-organizer', args.audit_dir)
        print(f'Verified root rename, {len(audit["month_moves"])} month directories, {audit["file_count"]} files. Audit: {report}')
    else:
        print(json.dumps(build_plan(args.source, args.destination), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
