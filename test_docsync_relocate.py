import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from docsync_core import ROOT, WIP
from docsync_paths import work_root, month_root
from docsync_relocate import apply_plan, build_plan, map_absolute


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.old = self.home / 'Documents/2026'
        self.new = self.old.with_name('WIP & Reference')
        self.old.mkdir(parents=True)
        self.state = self.home / 'state'
        self.state.mkdir()
        self.audit = self.home / 'audit'

    def fixture(self):
        source = self.old / '03/documents/notes.txt'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'keep original document')
        (self.old / 'loose.txt').write_bytes(b'loose document')
        WIP.scan(self.old, ['03'], True)
        with sqlite3.connect(WIP.db_path(self.old)) as conn:
            conn.execute("UPDATE documents SET status='approved', classification_reason='manual review: retain decision' WHERE path='03/documents/notes.txt'")
        with sqlite3.connect(self.state / 'processed.db') as conn:
            conn.execute('CREATE TABLE processed_files(source_path TEXT,final_path TEXT,notes TEXT)')
            conn.execute('INSERT INTO processed_files VALUES (?,?,?)',
                         (str(source), str(source), str(source)+'\tbucketed'))
        with sqlite3.connect(self.state / 'vendors.db') as conn:
            conn.execute('CREATE TABLE vendors(last_source_path TEXT,last_final_path TEXT)')
            conn.execute('INSERT INTO vendors VALUES (?,?)', (str(source),str(source)))
        report = self.old / '99_NHAT_KY/moves.html'
        report.write_text('<a href="'+source.as_uri()+'">Open document</a>')
        return source

    def test_plan_is_read_only_and_uses_padded_month_names(self):
        source = self.fixture()
        self.assertEqual({'03':'2026/2026-03'},build_plan(self.old,self.new)['month_moves'])
        self.assertTrue(source.is_file())
        self.assertFalse(self.new.exists())

    def test_move_preserves_documents_reviews_and_remaps_paths_and_links(self):
        source = self.fixture()
        before = source.stat()
        report, audit = apply_plan(self.old,self.new,self.state,self.audit)
        target = self.new/'2026/2026-03/documents/notes.txt'
        self.assertEqual('verified',audit['status'])
        self.assertEqual(b'keep original document',target.read_bytes())
        self.assertEqual((before.st_ino,before.st_mtime_ns),(target.stat().st_ino,target.stat().st_mtime_ns))
        self.assertFalse(self.old.exists())
        self.assertFalse((self.new/'03').exists())
        self.assertIn(target.as_uri(),(self.new/'99_NHAT_KY/moves.html').read_text())
        self.assertTrue(report.exists())
        with sqlite3.connect(self.new/'.wip/index.sqlite') as conn:
            self.assertEqual(('approved','manual review: retain decision'),conn.execute("SELECT status,classification_reason FROM documents WHERE path='2026/2026-03/documents/notes.txt'").fetchone())
        with sqlite3.connect(self.state/'processed.db') as conn:
            row=conn.execute('SELECT * FROM processed_files').fetchone()
            self.assertEqual((str(target),str(target),str(target)+'\tbucketed'),row)
        with sqlite3.connect(self.state/'vendors.db') as conn:
            self.assertEqual((str(target),str(target)),conn.execute('SELECT * FROM vendors').fetchone())
        rules=json.loads((self.new/'.wip/rules.json').read_text())
        self.assertIn('2026/2026-03/Adults/**',rules['excluded_paths'])

    def test_existing_destination_aborts_without_changing_source(self):
        source=self.fixture();self.new.mkdir()
        with self.assertRaises(FileExistsError):apply_plan(self.old,self.new,self.state,self.audit)
        self.assertTrue(source.exists())
        self.assertEqual([],list(self.new.iterdir()))

    def test_nested_year_conflict_and_symlink_are_rejected(self):
        (self.old/'2026').mkdir()
        with self.assertRaises(FileExistsError):build_plan(self.old,self.new)
        (self.old/'2026').rmdir()
        outside=self.home/'outside';outside.mkdir()
        (self.old/'03').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):build_plan(self.old,self.new)

    def test_metadata_failure_rolls_back_root_months_and_databases(self):
        source=self.fixture()
        with patch('docsync_relocate.rewrite_metadata',side_effect=RuntimeError('injected failure')):
            with self.assertRaisesRegex(RuntimeError,'injected failure'):
                apply_plan(self.old,self.new,self.state,self.audit)
        self.assertTrue(source.is_file())
        self.assertFalse(self.new.exists())
        with sqlite3.connect(self.state/'processed.db') as conn:
            self.assertEqual(str(source),conn.execute('SELECT final_path FROM processed_files').fetchone()[0])
        with sqlite3.connect(self.old/'.wip/index.sqlite') as conn:
            self.assertEqual('approved',conn.execute("SELECT status FROM documents WHERE path='03/documents/notes.txt'").fetchone()[0])
        self.assertEqual('rolled_back',json.loads((self.audit/'manifest.json').read_text())['status'])

    def test_prefix_mapping_does_not_rewrite_other_years_or_similar_names(self):
        plan=build_plan(self.old,self.new)
        for value in ('/tmp/2026/test','/home/20260/file',str(self.old)+'0/test'):
            self.assertEqual(value,map_absolute(value,plan))
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(self.new,work_root(self.home))
        self.assertEqual(self.new/'2027/2027-01',month_root(self.new,2027,1))
        with self.assertRaises(ValueError):month_root(self.new,2026,13)

    def test_year_boundary_rollover_and_new_intake_use_stable_root(self):
        work=self.home/'Documents/WIP & Reference';work.mkdir()
        old_month=work/'2026/2026-12';old_month.mkdir(parents=True)
        (old_month/'previous.txt').write_bytes(b'previous-month document')
        (work/'incoming.txt').write_bytes(b'current-year document')
        archive=self.home/'accounting';archive.mkdir()
        env={**os.environ,'HOME':str(self.home),'STATE_DIR':str(self.state),
             'IVAR_DATA':str(archive),'CURRENT_DATE':'2027-01-01'}
        env.pop('DOCSYNC_ROOT',None)
        result=subprocess.run(['sh',str(ROOT/'ivar-documents-organizer.sh'),'--no-sync','--quiet'],env=env,capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual(b'previous-month document',(old_month/'documents/previous.txt').read_bytes())
        self.assertEqual(b'current-year document',(work/'2027/2027-01/documents/incoming.txt').read_bytes())
        self.assertFalse((self.home/'Documents/2027').exists())
        self.assertFalse((work/'01').exists())


if __name__=='__main__':unittest.main()
