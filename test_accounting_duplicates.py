"""Exercise the real shell handlers with isolated files and SQLite state."""
import hashlib
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class AccountingDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.year = self.home / 'Documents/WIP & Reference'
        self.year.mkdir(parents=True)
        self.archive = self.home / 'archive'
        self.archive.mkdir()
        self.state = self.home / 'state'
        self.duplicates = self.year / 'duplicates'
        # Load production functions without running main or touching live data.
        source = (ROOT / 'ivar-documents-organizer.sh').read_text()
        self.library = source.rsplit('\nmain "$@"', 1)[0]

    def run_handler(self, name, *args, dry_run=False):
        harness = self.home / 'handler.sh'
        harness.write_text(self.library + '\nAPPLY_MANAGED_RULES=0\n'
                           + ('DRY_RUN=1\n' if dry_run else '')
                           + shlex.join([name, *map(str, args)]) + '\n')
        env = {**os.environ, 'HOME': str(self.home),
               'STATE_DIR': str(self.state), 'IVAR_DATA': str(self.archive),
               'CURRENT_DATE': '2026-10-28'}
        result = subprocess.run(['sh', str(harness), '--quiet', '--no-sync'],
                                env=env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def seed(self, target, payload, status='invoice', size=None):
        self.run_handler('db_insert', self.year / 'original', 1,
                         len(payload) if size is None else size,
                         hashlib.sha256(payload).hexdigest(), target, status,
                         '2026', '10', '', '', '', '')

    def intake(self, name='download.txt', payload=b'accounting document'):
        path = self.year / name
        path.write_bytes(payload)
        return path

    def process(self, source, dry_run=False):
        self.run_handler('process_source_file', source, self.home, dry_run=dry_run)

    def test_accounting_types_go_to_year_duplicates(self):
        for status in ('unc', 'invoice', 'invoice-xml'):
            with self.subTest(status=status):
                payload = status.encode()
                source = self.intake(status + '.txt', payload)
                target = self.archive / source.name
                target.write_bytes(payload)
                self.seed(target, payload, status)
                self.process(source)
                self.assertFalse(source.exists())
                self.assertEqual(payload, (self.duplicates / source.name).read_bytes())
                self.assertEqual(payload, target.read_bytes())
        self.assertFalse((self.year / '10/duplicates').exists())
        self.assertFalse((self.year / '2026/2026-10/duplicates').exists())

    def test_size_in_database_must_match(self):
        source = self.intake()
        target = self.archive / 'invoice.txt'
        target.write_bytes(source.read_bytes())
        self.seed(target, source.read_bytes(), size=source.stat().st_size + 1)
        self.process(source)
        self.assertTrue(source.exists())
        self.assertFalse(self.duplicates.exists())

    def test_changed_archive_size_or_content_is_not_duplicate(self):
        source = self.intake()
        target = self.archive / 'invoice.txt'
        self.seed(target, source.read_bytes())
        for changed in (b'x', b'x' * source.stat().st_size):
            with self.subTest(changed=changed):
                target.write_bytes(changed)
                self.process(source)
                self.assertTrue(source.exists())
                self.assertFalse(self.duplicates.exists())

    def test_missing_archive_is_not_duplicate(self):
        source = self.intake()
        self.seed(self.archive / 'missing.txt', source.read_bytes())
        self.process(source)
        self.assertTrue(source.exists())
        self.assertFalse(self.duplicates.exists())

    def test_newer_stale_or_quarantine_record_does_not_hide_valid_archive(self):
        source = self.intake()
        target = self.archive / 'invoice.txt'
        target.write_bytes(source.read_bytes())
        self.seed(target, source.read_bytes())
        self.seed(self.archive / 'missing.txt', source.read_bytes())
        self.seed(self.archive / 'old-duplicate.txt', source.read_bytes(), 'duplicate')
        self.process(source)
        self.assertFalse(source.exists())
        self.assertEqual(target.read_bytes(), (self.duplicates / source.name).read_bytes())

    def test_generic_monthly_and_duplicate_records_are_ignored(self):
        for status in ('bucketed', 'year-inbox', 'managed-route', 'duplicate'):
            with self.subTest(status=status):
                payload = status.encode()
                source = self.intake(status + '.txt', payload)
                target = self.year / '2026/2026-09/documents' / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                self.seed(target, payload, status)
                self.process(source)
                self.assertTrue(source.exists())
                self.assertEqual(payload, target.read_bytes())
        self.assertFalse(self.duplicates.exists())

    def test_same_source_is_not_its_own_duplicate(self):
        source = self.intake()
        self.seed(source, source.read_bytes())
        self.process(source)
        self.assertTrue(source.exists())
        self.assertFalse(self.duplicates.exists())

    def test_quarantine_collision_preserves_all_copies(self):
        source = self.intake()
        payload = source.read_bytes()
        sha = hashlib.sha256(payload).hexdigest()
        target = self.archive / 'invoice.txt'
        target.write_bytes(payload)
        self.seed(target, payload)
        self.duplicates.mkdir()
        (self.duplicates / source.name).write_bytes(b'first copy')
        (self.duplicates / f'download_{sha}.txt').write_bytes(b'second copy')
        self.process(source)
        self.assertEqual(b'first copy', (self.duplicates / source.name).read_bytes())
        self.assertEqual(b'second copy', (self.duplicates / f'download_{sha}.txt').read_bytes())
        self.assertEqual(payload, (self.duplicates / f'download_{sha}_1.txt').read_bytes())

    def test_preview_finds_duplicate_without_moving_or_changing_db(self):
        source = self.intake()
        target = self.archive / 'invoice.txt'
        target.write_bytes(source.read_bytes())
        self.seed(target, source.read_bytes())
        db = self.state / 'processed.db'
        before = db.read_bytes()
        self.process(source, dry_run=True)
        self.assertTrue(source.exists())
        self.assertFalse(self.duplicates.exists())
        self.assertEqual(before, db.read_bytes())
        self.assertIn('DRY-RUN mv', (self.state / 'logs/2026-10-28.log').read_text())

    def test_unc_destination_without_database_record(self):
        source = self.intake('UNC.pdf')
        target = self.archive / 'ngan hang/UNC/2026/10/UNC.pdf'
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
        text = self.home / 'unc-text.txt'
        text.write_text('UNC')
        self.run_handler('process_unc_pdf', source, '2026', '10', 1,
                         source.stat().st_size, hashlib.sha256(source.read_bytes()).hexdigest(),
                         0, text)
        self.assertEqual(target.read_bytes(), (self.duplicates / source.name).read_bytes())
        self.assertFalse(source.exists())

    def test_invoice_pdf_and_xml_destination_without_database_records(self):
        source = self.intake('download.pdf')
        sidecar = self.intake('download.xml', b'invoice XML')
        dest = self.archive / 'hoa don dau vao/2026/10/TEST VENDOR 1234567890'
        dest.mkdir(parents=True)
        target = dest / '1234567890_42.pdf'
        target.write_bytes(source.read_bytes())
        (dest / sidecar.name).write_bytes(sidecar.read_bytes())
        self.run_handler('process_invoice_pdf', source, '2026', '10', 'TEST VENDOR',
                         '1234567890', '42', 0, 1, source.stat().st_size,
                         hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertFalse(source.exists())
        self.assertFalse(sidecar.exists())
        self.assertEqual(target.read_bytes(), (self.duplicates / source.name).read_bytes())
        self.assertEqual(b'invoice XML', (self.duplicates / sidecar.name).read_bytes())

    def test_database_pdf_duplicate_also_handles_xml_seen_before_pdf(self):
        source = self.intake('download.pdf')
        sidecar = self.intake('download.xml', b'archived invoice XML')
        target = self.archive / '123_42.pdf'
        target.write_bytes(source.read_bytes())
        xml_target = self.archive / '123_42.xml'
        xml_target.write_bytes(sidecar.read_bytes())
        self.seed(target, source.read_bytes())
        # No XML DB record: compare the actual XML next to the archived PDF.
        self.process(sidecar)
        self.assertTrue(sidecar.exists())
        self.process(source)
        self.assertFalse(source.exists())
        self.assertFalse(sidecar.exists())
        self.assertEqual(xml_target.read_bytes(), (self.duplicates / sidecar.name).read_bytes())

    def test_invoice_xml_same_name_with_different_size_stays_in_archive(self):
        source = self.intake('download.xml', b'new XML with more content')
        dest = self.archive / 'hoa don dau vao/2026/10/TEST VENDOR 1234567890'
        dest.mkdir(parents=True)
        target = dest / '1234567890_42.xml'
        target.write_bytes(b'old XML')
        payload = source.read_bytes()
        sha = hashlib.sha256(payload).hexdigest()
        self.run_handler('process_invoice_xml', source, '2026', '10', 'TEST VENDOR',
                         '1234567890', '42', 1, len(payload), sha)
        self.assertEqual(b'old XML', target.read_bytes())
        self.assertEqual(payload, (dest / f'{sha}_{target.name}').read_bytes())
        self.assertFalse(self.duplicates.exists())


if __name__ == '__main__':
    unittest.main()
