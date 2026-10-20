import copy
import fcntl
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from docsync_core import WIP, board, ROOT
from docsync_watcher import Queue

class DocSyncTests(unittest.TestCase):
    def test_new_patterns_and_personal_program_exclusion(self):
        for name, category in [('iv-company-batch80-20261011-110028.xlsx','vat'), ('orders_2026-10-02_2026-10-07.xlsx','shopee_reports'), ('Chương trình Tích hợp quốc tế Cambridge.pdf','needs_review')]:
            self.assertEqual(category, WIP.classify(Path(name), WIP.DEFAULT_RULES)[0])

    def test_bank_requires_content_not_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'Danh sach giao dich.xlsx'
            for content, mode in [('customer transactions', 'review_only'), ('VietinBank Số tài khoản Số dư', 'managed_move')]:
                with zipfile.ZipFile(source,'w') as archive:
                    archive.writestr('xl/sharedStrings.xml', f'<sst><si><t>{content}</t></si></sst>')
                self.assertEqual(mode, WIP.classify(Path(source.name), WIP.DEFAULT_RULES, source)[4])
            source.write_bytes(b'not an Excel file')
            self.assertEqual('review_only', WIP.classify(Path(source.name), WIP.DEFAULT_RULES, source)[4])

    def test_migration_preserves_custom_mode_target_and_is_idempotent(self):
        rules = copy.deepcopy(WIP.DEFAULT_RULES)
        rules['version'] = 7
        rules['categories'][0].update(patterns=['iv-mtt-batch','custom-vat'], target='Custom', route_mode='review_only')
        updated, changed = WIP.migrate_rules(rules)
        self.assertTrue(changed)
        self.assertEqual('Custom', updated['categories'][0]['target'])
        self.assertEqual('review_only', updated['categories'][0]['route_mode'])
        self.assertIn('custom-vat', updated['categories'][0]['patterns'])
        self.assertFalse(WIP.migrate_rules(updated)[1])

    def test_manual_review_survives_scan_until_content_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'unknown.txt'; source.write_text('original')
            WIP.scan(root, [], True)
            with WIP.open_db(WIP.db_path(root)) as conn:
                dossier = conn.execute('SELECT dossier_id FROM documents').fetchone()[0]
            WIP.review_dossier(root,dossier,'legal_corporate',None,'approved','confirmed')
            WIP.scan(root, [], True)
            with WIP.open_db(WIP.db_path(root)) as conn:
                row = conn.execute('SELECT category,status FROM documents').fetchone()
                self.assertEqual(('legal_corporate','approved'),tuple(row))
            source.write_text('different content')
            WIP.scan(root, [], True)
            with WIP.open_db(WIP.db_path(root)) as conn:
                row = conn.execute('SELECT category,status FROM documents').fetchone()
                self.assertEqual(('needs_review','needs_review'),tuple(row))

    def test_board_is_read_only_and_distinguishes_ready_review_filed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'iv-company-batch1.xlsx').write_text('vat')
            (root/'unknown.txt').write_text('unknown')
            WIP.scan(root, [], True)
            target=root/'AT. GHTK reports';target.mkdir()
            (target/'GHTK_report.xlsx').write_text('filed')
            WIP.scan(root, [], True)
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.name.endswith("-shm")}
            result = board(root)
            self.assertEqual(1,result['counts']['ready'])
            self.assertEqual(1,result['counts']['needs_review'])
            self.assertEqual(1,result['counts']['filed'])
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.name.endswith("-shm")})

    def test_shared_lock_rejects_second_run_before_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state=root/'state';state.mkdir()
            with (state/'organizer.lock').open('w') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                env={**os.environ,'HOME':str(root),'STATE_DIR':str(state)}
                result=subprocess.run(['sh',str(ROOT/'ivar-documents-organizer.sh'),'--no-sync'],env=env,capture_output=True,text=True)
                self.assertEqual(75,result.returncode)
                self.assertIn('BUSY',result.stderr)
                self.assertFalse((state/'processed.db').exists())

    def test_watcher_waits_for_settle_and_retries_busy(self):
        q=Queue({})
        self.assertFalse(q.observe({'a':(1,1)},0))
        self.assertFalse(q.observe({'a':(2,2)},10))
        self.assertFalse(q.observe({'a':(2,2)},29))
        self.assertTrue(q.observe({'a':(2,2)},30))
        q.complete(75,30)
        self.assertFalse(q.observe({'a':(2,2)},60))
        self.assertTrue(q.observe({'a':(2,2)},90))
        q.complete(0,90)
        self.assertFalse(q.observe({'a':(2,2)},160))

    def test_cli_board_json(self):
        with tempfile.TemporaryDirectory() as temp:
            result=subprocess.run([str(ROOT/'docsync'),'--year-root',temp,'board','--json'],text=True,capture_output=True)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertEqual([],json.loads(result.stdout)['cards'])

if __name__ == '__main__':
    unittest.main()
