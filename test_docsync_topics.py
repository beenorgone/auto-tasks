import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from docsync_core import WIP
from docsync_layout import TOPICS, CATEGORY_TARGETS
from docsync_migrate_layout import build_plan, apply_plan
import docsync_topics as topics
import docsync_telegram as bridge


class TopicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'work'; self.root.mkdir()
        self.state = Path(self.temp.name)/'state'
        self.records = {}; self.sent = []; self.complete = []
        self.config = {'login':'owner', 'bot':'bot'}
        clock = patch.object(bridge.time, 'time', return_value=time.time()+30)
        clock.start(); self.addCleanup(clock.stop)

    def remote(self, config, payload):
        if payload['op'] == 'read_topics':
            return {'records': {key: self.records[key] for key in payload['ids'] if key in self.records}}
        if payload['op'] == 'submit_topics':
            self.sent.append(payload)
            for proposal in payload['proposals']:
                self.records[proposal['id']] = {'status':'pending','sent':time.time()}
            return {'records': self.records}
        if payload['op'] == 'complete':
            self.complete.append(payload['id']); return {'status':'completed'}
        raise AssertionError(payload)

    def sync(self):
        with contextlib.redirect_stdout(io.StringIO()):
            bridge.sync_topics(self.root,self.state,self.config,self.remote,WIP)

    def file(self, relative, content=b'original'):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content);return path

    def test_legacy_layout_moves_hashes_and_manual_decisions(self):
        a=self.file('20_BAO_CAO/Sao ke/report.xlsx')
        b=self.file('10_HO_SO/Nhap hang/Hop dong/agreement.pdf')
        self.file('80_THAM_CHIEU/Ma QR/unknown.png')
        self.file('99_NHAT_KY/old-audit.json', b'{}')
        before={p.name: topics.fingerprint(p) for p in (a,b)}
        plan=build_plan(self.root)
        self.assertEqual(3,len(plan['entries']))
        report,audit=apply_plan(self.root,self.state)
        self.assertEqual('verified',audit['status'])
        self.assertEqual(before[a.name],topics.fingerprint(self.root/'Kế toán - tài chính/Sao kê/report.xlsx'))
        self.assertEqual(before[b.name],topics.fingerprint(self.root/'Mua hàng - dịch vụ/Hop dong/agreement.pdf'))
        self.assertTrue((self.root/'80_THAM_CHIEU/Ma QR/unknown.png').exists())
        self.assertFalse((self.root/'20_BAO_CAO').exists())
        self.assertFalse((self.root/'10_HO_SO').exists())
        self.assertTrue(str(report.relative_to(self.root)).startswith('.docsync/Nhật ký/'))
        self.assertEqual(set(TOPICS.values()),{p.name for p in self.root.iterdir() if p.name in TOPICS.values()})
        self.assertEqual([],build_plan(self.root)['entries'])

    def test_automatic_routes_and_batched_questions_are_idempotent(self):
        self.file('Specs Organic FLOUR.pdf')
        for i in range(7): self.file(f'unknown-{i}.pdf')
        self.sync()
        self.assertTrue((self.root/'QC/Specs Organic FLOUR.pdf').exists())
        self.assertEqual([5,2],[len(p['proposals']) for p in self.sent])
        self.sync()
        self.assertEqual(2,len(self.sent))
        self.assertTrue(all((self.root/f'unknown-{i}.pdf').exists() for i in range(7)))

    def test_approval_preserves_bundle_and_remaps_index(self):
        a=self.file('Unclear/note.txt'); self.file('Unclear/attachment.pdf')
        WIP.scan(self.root,['Unclear'],True)
        digest=topics.fingerprint(a.parent)
        self.sync();key=next(iter(self.records))
        self.records[key].update(status='approved',topic='san_xuat')
        self.sync()
        self.assertFalse(a.parent.exists())
        self.assertEqual(digest,topics.fingerprint(self.root/'Sản xuất/Unclear'))
        with WIP.open_db(WIP.db_path(self.root)) as conn:
            self.assertTrue(conn.execute('SELECT 1 FROM documents WHERE path=?',('Sản xuất/Unclear/note.txt',)).fetchone())
        self.assertEqual([key],self.complete)
        self.sync();self.assertEqual([key],self.complete)

    def test_changed_bundle_and_collisions_never_use_stale_approval(self):
        source=self.file('Unclear/note.txt')
        self.sync();key=next(iter(self.records))
        self.file('Unclear/new.txt')
        self.records[key].update(status='approved',topic='qc')
        self.sync()
        self.assertTrue(source.exists());self.assertFalse((self.root/'QC/Unclear').exists())
        self.assertEqual(2,len(self.records))

    def test_delete_file_uses_trash_and_completes_once(self):
        source=self.file('unknown.pdf'); self.sync(); key=next(iter(self.records))
        self.records[key]['status']='delete_requested'
        self.sync(); self.sync()
        record=json.loads((self.state/topics.REVIEW_FILE).read_text())['unknown.pdf']
        self.assertFalse(source.exists())
        self.assertEqual('deleted', record['status'])
        self.assertEqual(b'original', Path(record['trash']).read_bytes())
        self.assertEqual([key], self.complete)

    def test_held_file_stays_even_with_new_matching_rule(self):
        source=self.file('unknown.pdf');self.sync();key=next(iter(self.records))
        self.records[key]['status']='held';self.sync()
        WIP.ensure_state(self.root)
        rules=json.loads(json.dumps(WIP.DEFAULT_RULES))
        rules['categories'][0]['patterns'].append(r'^unknown')
        WIP.rules_path(self.root).write_text(json.dumps(rules))
        self.sync();self.assertTrue(source.exists());self.assertEqual(1,len(self.sent))

    def test_preview_and_custom_rules_do_not_write(self):
        source=self.file('unknown.pdf')
        rules=json.loads(json.dumps(WIP.DEFAULT_RULES));rules['version']=9
        WIP.ensure_state(self.root);WIP.rules_path(self.root).write_text(json.dumps(rules))
        before=WIP.rules_path(self.root).read_bytes()
        list(topics.candidates(self.root,WIP))
        self.assertEqual(before,WIP.rules_path(self.root).read_bytes())
        source.unlink();source.symlink_to('/etc/passwd')
        self.assertEqual([],list(topics.candidates(self.root,WIP)))


if __name__ == '__main__': unittest.main()
