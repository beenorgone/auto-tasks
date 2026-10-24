import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import docsync_purchase as purchase
from docsync_watcher import Queue


class PurchaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'purchases'
        self.state = Path(self.temp.name) / 'state'

    def file(self, relative, content='original'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def route(self, apply=False):
        with contextlib.redirect_stdout(io.StringIO()):
            purchase.route(self.root, self.state, apply)

    def test_preview_apply_bundle_and_idempotence(self):
        name = 'ho so PO12 - 2026-08-25 - NCC Test'
        archive = self.file(f'2026/nhap khau/Q2/{name}/{name}.zip')
        note = self.file(f'2026/nhap khau/Q2/{name}/ghi chu.txt', 'keep together')
        self.route()
        self.assertTrue(archive.exists())
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root/'2026/nhap khau/08').exists())
        self.route(True)
        target = self.root/'2026/nhap khau/08'/name
        self.assertEqual('original', (target/archive.name).read_text())
        self.assertEqual('keep together', (target/note.name).read_text())
        self.assertEqual('filed', purchase.cards(self.root)[0]['stage'])
        log = (self.state/'purchase-months.jsonl').read_bytes()
        self.route(True)
        self.assertEqual(log, (self.state/'purchase-months.jsonl').read_bytes())

    def test_invalid_ambiguous_year_and_collision_are_review_only(self):
        for name in ('ho so 2026-02-30.zip', 'ho so 2026-01-01 2026-02-01.zip', 'ho so 2025-01-01.zip', 'ho so missing.zip'):
            self.file('2026/'+name)
        self.file('2026/ho so 2026-02-01.zip', 'source')
        self.file('2026/02/ho so 2026-02-01.zip', 'destination')
        self.route(True)
        self.assertEqual(5, sum(c['stage']=='needs_review' for c in purchase.cards(self.root)))
        self.assertEqual('destination', (self.root/'2026/02/ho so 2026-02-01.zip').read_text())
        self.assertFalse(self.state.exists())

    def test_symlink_and_partial_bundle_never_move(self):
        source = self.file('2026/ho so 2026-03-01/unfinished.part')
        self.route(True)
        self.assertTrue(source.exists())
        source.unlink()
        source.symlink_to('/etc/passwd')
        self.route(True)
        self.assertTrue(source.is_symlink())
        self.assertFalse(self.state.exists())

    def test_watch_nested_changes_settle_and_retry(self):
        source = self.file('2026/trong nuoc/ho so 2026-03-01/ghi chu.txt')
        q = Queue(purchase.snapshot(self.root))
        source.write_text('changed attachment')
        self.assertFalse(q.observe(purchase.snapshot(self.root), 10))
        self.assertTrue(q.observe(purchase.snapshot(self.root), 30))
        q.complete(75, 30)
        self.assertFalse(q.observe(purchase.snapshot(self.root), 40))
        self.assertTrue(q.observe(purchase.snapshot(self.root), 90))

    def test_atomic_move_refuses_existing_destination(self):
        a = self.file('a', 'a'); b = self.file('b', 'b')
        with self.assertRaises(FileExistsError):
            purchase.rename_no_replace(a, b)
        self.assertEqual('a', a.read_text())
        self.assertEqual('b', b.read_text())

    def test_shell_preview_apply_integration(self):
        source = self.file('2026/trong nuoc/ho so 2026-04-09.zip')
        env = {**os.environ, 'HOME': self.temp.name, 'IVAR_DATA': str(self.root.parent),
               'STATE_DIR': str(self.state)}
        # Shell contract fixes the leaf name to "ho so mua vao".
        renamed = self.root.parent / 'ho so mua vao'
        self.root.rename(renamed)
        source = renamed / source.relative_to(self.root)
        script = Path(__file__).parent / 'ivar-documents-organizer.sh'
        for flag in ('--plan-managed-rules', '--apply-managed-rules'):
            result = subprocess.run(['sh', str(script), flag, '--no-sync'], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn('purchase_months:', result.stdout)
            if flag == '--plan-managed-rules':
                self.assertTrue(source.exists())
        self.assertFalse(source.exists())
        self.assertEqual('original', (source.parent/'04'/source.name).read_text())

    def test_scope_and_symlink_parent(self):
        self.file('2025/ho so 2025-10-01.zip')
        self.file('2026/other/ho so 2026-10-01.zip')
        self.assertEqual(1, len(purchase.cards(self.root)))
        alias = self.root.parent/'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual([], purchase.cards(alias))

if __name__ == '__main__':
    unittest.main()
