"""Protect daily ERP uploads against stale picks and automated file diversion."""

import datetime as dt
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import docsync_daily as daily
import docsync_intake as intake
import docsync_workflows as workflows


class DailyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / 'WIP'
        self.root.mkdir()
        env = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(Path(folder.name) / 'config')})
        env.start()
        self.addCleanup(env.stop)
        self.key = b'daily-files-test'
        self.file = self.root / 'ho so PO123.pdf'
        self.file.write_bytes(b'%PDF-1.4 daily fixture')
        self.client = Mock()
        self.client.request.side_effect = self.request
        self.uploads = []
        self.change_during_preflight = False
        transport = patch.object(intake, 'IntakeClient', return_value=self.client)
        transport.start()
        self.addCleanup(transport.stop)
        daily.dispatch(self.root, self.key, 'credential', {'user': 'erp', 'password': 'fixture'})

    def request(self, route, *, query=None, upload=None):
        if route == intake.ERP + '/flows':
            return 200, {'policy': 'guard',
                         'flows': [{'flow': flow, 'allowed': True} for flow in (workflows.ATTACH_FLOW, 'erp_attach_ai', workflows.FLOW)],
                         'quota': {'max_file_bytes': 4 * 1024 * 1024}}
        if route == intake.ERP + '/record':
            if self.change_during_preflight:
                self.file.write_bytes(b'%PDF-1.4 changed fixture')
            return 200, {'record': {'id': 123, 'type': 'po', 'label': 'PO123'},
                         'fields': [{'key': 'documents'}], 'ai_available': True}
        name, stream, size, fields = upload
        contents = stream.read()
        self.assertEqual(size, len(contents))
        self.uploads.append((route, name, contents, fields.copy()))
        return 201, {'status': 'uploaded', 'ai': {'queued': fields.get('ai') == 1}}

    def payload(self, *, ai=False):
        row = daily.list_day(self.root, self.key)['files'][0]
        return {'path':row['path'], 'token':row['token'], 'type':'po', 'id':123, 'field':'documents', 'ai':ai}

    def test_day_lists_local_report_groups_and_excludes_hidden_work(self):
        report = self.root / 'GHTK_report.xlsx'
        report.write_bytes(b'fixture')
        (self.root / '.secret').write_bytes(b'secret')
        (self.root / 'unfinished.pdf.crdownload').touch()
        (self.root / 'linked.pdf').symlink_to(self.file)
        result = daily.list_day(self.root, self.key)
        self.assertEqual({row['name'] for row in result['files']}, {self.file.name, report.name})
        self.assertNotEqual(*(row['category'] for row in result['files']))
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        self.assertEqual(daily.list_day(self.root, self.key, yesterday)['files'], [])

    def test_exact_destination_and_explicit_ai(self):
        for with_ai in (False, True):
            result = daily.dispatch(self.root, self.key, 'attach', self.payload(ai=with_ai))
            route, name, contents, fields = self.uploads[-1]
            self.assertEqual((route, name, contents), (intake.ERP + '/upload', self.file.name, self.file.read_bytes()))
            self.assertEqual((fields['type'], fields['id'], fields['field']), ('po', 123, 'documents'))
            self.assertEqual(fields.get('ai_mode'), 'analysis' if with_ai else None)
            self.assertEqual(result['result']['ai']['queued'], with_ai)

    def test_changed_during_remote_preflight_blocks_upload(self):
        payload = self.payload()
        self.change_during_preflight = True
        with self.assertRaisesRegex(ValueError, 'File đã thay đổi'):
            daily.dispatch(self.root, self.key, 'attach', payload)
        self.assertEqual(self.uploads, [])

    def test_swapped_directory_cannot_upload_external_file(self):
        group = self.root / 'PO'
        group.mkdir()
        nested = group / 'document.pdf'
        nested.write_bytes(b'fixture')
        row = next(item for item in daily.list_day(self.root, self.key)['files'] if item['name'] == nested.name)
        moved = self.root.parent / 'outside'
        group.rename(moved)
        group.symlink_to(moved, target_is_directory=True)
        payload = {'path':row['path'], 'token':row['token'], 'type':'po', 'id':123, 'field':'documents', 'ai':False}
        with self.assertRaisesRegex(ValueError, 'đổi vị trí'):
            daily.dispatch(self.root, self.key, 'attach', payload)
        self.assertEqual(self.uploads, [])

    def test_erp_credential_preserves_enabled_auto_flow_and_reserves_its_reports(self):
        workflows.save_credential(self.root, 'ghtk', 'fixture')
        workflows.set_enabled(self.root, workflows.PICKUP, True)
        enabled = workflows.settings(self.root)[workflows.PICKUP]
        daily.dispatch(self.root, self.key, 'credential', {'user': 'another-erp-user', 'password': 'fixture'})
        self.assertEqual(workflows.settings(self.root)[workflows.PICKUP], enabled)
        self.assertEqual(workflows.load_credential()['user'], 'ghtk')
        self.assertEqual(workflows.load_credential(for_erp=True)['user'], 'another-erp-user')
        time.sleep(0.002)
        report = self.root / 'GHTK_bao_cao_van_hanh_va_dong_tien_22_11_S12.xlsx'
        report.write_bytes(b'fixture')
        row = next(item for item in daily.list_day(self.root, self.key)['files'] if item['name'] == report.name)
        self.assertTrue(row['automatic'])
        payload = {'path':row['path'], 'token':row['token'], 'type':'po', 'id':123, 'field':'documents', 'ai':False}
        with self.assertRaisesRegex(ValueError, 'đối soát tự động'):
            daily.dispatch(self.root, self.key, 'attach', payload)
        self.assertEqual(self.uploads, [])


if __name__ == '__main__':
    unittest.main()
