import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from docsync_core import WIP, board
from docsync_layout import approved_target, B2B_PRICES, B2B_RECONCILIATION
from docsync_migrate_layout import build_plan, apply_plan

class LayoutTests(unittest.TestCase):
    def fixture(self, root):
        source = root / 'AT. GHTK reports/GHTK_test.xlsx'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'report bytes')
        labels = root / 'Review Label/label.pdf'
        labels.parent.mkdir(); labels.write_bytes(b'label bytes')
        b2b = root / 'AT. B2/Bang gia B2B.pdf'
        b2b.parent.mkdir(); b2b.write_bytes(b'price list')
        WIP.scan(root, [], True)
        with WIP.open_db(WIP.db_path(root)) as conn:
            dossier = conn.execute("SELECT dossier_id FROM documents WHERE path='Review Label/label.pdf'").fetchone()[0]
        WIP.review_dossier(root, dossier, 'packaging', None, 'approved', 'manual decision')
        (root/'Others').mkdir(exist_ok=True)
        labels.parent.rename(root/'Others/Review Label')
        state = root/'runtime';state.mkdir()
        with sqlite3.connect(state/'processed.db') as conn:
            conn.execute('CREATE TABLE processed_files(final_path TEXT, sha256 TEXT)')
            conn.execute('INSERT INTO processed_files VALUES (?,?)',(str(source),WIP.digest(source)))
        return state, dossier

    def test_migration_preserves_content_review_and_processed_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state,dossier=self.fixture(root)
            plan=build_plan(root)
            self.assertEqual(3,len(plan['entries']))
            self.assertTrue((root/'AT. GHTK reports/GHTK_test.xlsx').exists())
            report,audit=apply_plan(root,state)
            self.assertEqual('verified',audit['status'])
            self.assertEqual(3,audit['index_rows_remapped'])
            self.assertEqual(1,audit['processed_rows_remapped'])
            self.assertEqual(b'report bytes',(root/'20_BAO_CAO/GHTK/GHTK_test.xlsx').read_bytes())
            self.assertEqual(b'price list',(root/B2B_PRICES/'Bang gia B2B.pdf').read_bytes())
            self.assertEqual({'Bang gia','Doi soat cong no'}, {p.name for p in (root/'10_HO_SO/Kinh doanh/B2B').iterdir()})
            WIP.scan(root, [], True)
            with WIP.open_db(WIP.db_path(root)) as conn:
                row=conn.execute("SELECT dossier_id,classification_reason,status,proposed_target FROM documents WHERE path='10_HO_SO/Bao bi/label.pdf'").fetchone()
                self.assertEqual((dossier,'manual review: manual decision','approved','10_HO_SO/Bao bi'),tuple(row))
                self.assertEqual(0,conn.execute("SELECT COUNT(*) FROM documents WHERE active=1 AND path LIKE 'AT.%'").fetchone()[0])
            with sqlite3.connect(state/'processed.db') as conn:
                self.assertEqual(str(root/'20_BAO_CAO/GHTK/GHTK_test.xlsx'),conn.execute('SELECT final_path FROM processed_files').fetchone()[0])
            self.assertEqual([],build_plan(root)['entries'])
            self.assertEqual(1,board(root)['counts']['filed'])

    def test_conflict_aborts_before_any_move(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state,_=self.fixture(root)
            target=root/'20_BAO_CAO/GHTK/GHTK_test.xlsx';target.write_bytes(b'keep this')
            with self.assertRaises(FileExistsError): apply_plan(root,state)
            self.assertEqual(b'keep this',target.read_bytes())
            self.assertTrue((root/'AT. GHTK reports/GHTK_test.xlsx').exists())

    def test_failure_rolls_back_files_and_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state,dossier=self.fixture(root)
            with patch('docsync_migrate_layout.remap_processed',side_effect=RuntimeError('injected failure')):
                with self.assertRaisesRegex(RuntimeError,'injected failure'): apply_plan(root,state)
            self.assertEqual(b'report bytes',(root/'AT. GHTK reports/GHTK_test.xlsx').read_bytes())
            self.assertTrue((root/'Others/Review Label/label.pdf').exists())
            with WIP.open_db(WIP.db_path(root)) as conn:
                self.assertEqual(dossier,conn.execute("SELECT dossier_id FROM documents WHERE path='Review Label/label.pdf'").fetchone()[0])

    def test_symlink_target_rejected_before_initialization(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root=Path(temp);(root/'20_BAO_CAO').symlink_to(outside,target_is_directory=True)
            self.assertFalse(approved_target(root,'20_BAO_CAO/VAT'))
            with self.assertRaises(ValueError): WIP.ensure_state(root)
            self.assertEqual([],list(Path(outside).iterdir()))

    def test_folder_context_and_b2b_have_stable_categories(self):
        for path,category in [('10_HO_SO/Kinh doanh/B2B/Doi soat cong no/cong no.xlsx','commerce'),('10_HO_SO/Nhap hang/Hop dong/contract.pdf','sourcing_logistics'),('10_HO_SO/Chat luong/Inulin/COA/test.pdf','quality_regulatory')]:
            self.assertEqual(category,WIP.classify(Path(path),WIP.DEFAULT_RULES)[0])
        for name,target in [('Bang gia B2B.pdf',B2B_PRICES),('Doi soat cong no B2B.xlsx',B2B_RECONCILIATION)]:
            result=WIP.classify(Path(name),WIP.DEFAULT_RULES)
            self.assertEqual((target,'review_only'),result[3:])

    def test_v8_targets_migrate_without_custom_override(self):
        rules=json.loads(json.dumps(WIP.DEFAULT_RULES));rules['version']=8
        for rule in rules['categories']:
            if rule['id']=='vat': rule['target']='AT. VAT'
            if rule['id']=='commerce': rule['target']='Custom sales'
        migrated,changed=WIP.migrate_rules(rules)
        self.assertTrue(changed)
        values={r['id']:r['target'] for r in migrated['categories']}
        self.assertEqual('20_BAO_CAO/VAT',values['vat'])
        self.assertEqual('Custom sales',values['commerce'])
        self.assertFalse(WIP.migrate_rules(migrated)[1])

if __name__=='__main__': unittest.main()
