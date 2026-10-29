#!/usr/bin/env python3
"""Debounced non-recursive intake watcher with bounded polling and retry."""
import docsync_purchase as PURCHASE
import subprocess
import time
from pathlib import Path
from docsync_paths import work_root

ROOT = Path(__file__).resolve().parent
POLL_SECONDS = 5
SETTLE_SECONDS = 20
COOLDOWN_SECONDS = 60

def snapshot(roots):
    result = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if path.name.startswith('.') or path.suffix.lower() in ('.part', '.crdownload', '.tmp') or path.is_symlink():
                continue
            try:
                if path.is_file():
                    stat = path.stat()
                    result[str(path)] = (stat.st_size, stat.st_mtime_ns)
            except FileNotFoundError:
                pass
    return result

class Queue:
    def __init__(self, initial):
        self.previous = initial
        self.pending = False
        self.changed_at = 0
        self.next_run = 0

    def observe(self, current, now):
        if any(self.previous.get(path) != signature for path, signature in current.items()):
            self.pending = True
            self.changed_at = now
        self.previous = current
        return self.pending and now - self.changed_at >= SETTLE_SECONDS and now >= self.next_run

    def complete(self, code, now):
        self.pending = code != 0
        self.next_run = now + COOLDOWN_SECONDS


def main():
    home = Path.home()
    def roots():
        year = work_root(home)
        return [year, year / '00_INBOX', year / '00_INBOX/Hoa don', home / 'Documents', home / 'Downloads']
    def current():
        return {**snapshot(roots()), **PURCHASE.snapshot(PURCHASE.purchase_root(home))}
    queue = Queue(current())
    print('DocSync watcher ready: direct intake + purchase dossiers; settle=20s; cooldown=60s', flush=True)
    while True:
        time.sleep(POLL_SECONDS)
        if queue.observe(current(), time.monotonic()):
            print('Stable intake change detected; running shared organizer', flush=True)
            code = subprocess.call(['/bin/sh', str(ROOT / 'ivar-documents-organizer.sh'), '--apply-managed-rules', '--no-sync'])
            queue.complete(code, time.monotonic())
            print(f'Organizer exit={code}; retry={queue.pending}', flush=True)

if __name__ == '__main__':
    main()
