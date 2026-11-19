import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import docsync_eim as EIM
import docsync_erp as ERP


class FakeClient:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses if isinstance(responses, dict) else list(responses or [])

    def eim_report(self, path, digest, kind):
        self.calls.append((path.name, digest, kind))
        if isinstance(self.responses, dict):
            queue = self.responses.get(path.name, [])
            item = queue.pop(0) if queue else {'status': 'reconciled', 'report_id': 7, 'type': kind, 'matched': 3}
        else:
            item = self.responses.pop(0) if self.responses else None
        if item is None:
            item = {'status': 'reconciled', 'report_id': 7, 'type': kind or 'pickup_cashflow', 'matched': 3}
        if isinstance(item, Exception):
            raise item
        return item


class DocSyncEimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / 'WIP & Reference'
        (self.root / '20_BAO_CAO' / 'GHTK').mkdir(parents=True)
        (self.root / '00_INBOX').mkdir()
        self.state = base / 'state'
        self.state.mkdir()
        self.now = time.time() + 60  # every file counts as settled

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, data=b'xlsx-bytes'):
        path = self.root / relative
        path.write_bytes(data)
        return path

    def test_detects_only_ghtk_reconciliation_files(self):
        self.assertEqual('carrier_delivery', EIM.detect('bien_ban_doi_soat_18112026.xlsx'))
        self.assertEqual('carrier_delivery', EIM.detect('Biên bản đối soát 18-11.xlsx'))
        self.assertEqual('pickup_cashflow', EIM.detect('GHTK_Bao cao dong tien.xlsx'))
        self.assertEqual('', EIM.detect('GHTK_export_123.csv'))
        self.assertIsNone(EIM.detect('Hoa don ACB.pdf'))
        self.assertIsNone(EIM.detect('bien_ban_doi_soat.pdf'))

    def test_pushes_new_file_once_and_skips_same_bytes_after_move(self):
        self.write('00_INBOX/bien_ban_doi_soat_1811.xlsx')
        self.write('00_INBOX/ghi-chu.xlsx')
        client = FakeClient([{'status': 'building', 'report_id': 9, 'type': 'carrier_delivery', 'matched': 40, 'unmatched': 1}])
        results = EIM.sync(self.root, client, self.state, self.now, log=lambda _: None)
        self.assertEqual(['sent'], [r['status'] for r in results])
        self.assertEqual('carrier_delivery', client.calls[0][2])
        self.assertIn('lô HĐ nháp', results[0]['message'])
        # Organizer moves the file into 20_BAO_CAO/GHTK: same SHA → not pushed again.
        os.replace(self.root / '00_INBOX/bien_ban_doi_soat_1811.xlsx', self.root / '20_BAO_CAO/GHTK/bien_ban_doi_soat_1811.xlsx')
        self.assertEqual([], EIM.sync(self.root, client, self.state, self.now, log=lambda _: None))
        self.assertEqual(1, len(client.calls))

    def test_unsettled_file_waits(self):
        self.write('20_BAO_CAO/GHTK/GHTK_dong_tien.xlsx')
        client = FakeClient()
        self.assertEqual([], EIM.sync(self.root, client, self.state, time.time(), log=lambda _: None))
        self.assertEqual([], client.calls)

    def test_server_duplicate_is_final(self):
        self.write('20_BAO_CAO/GHTK/GHTK_dong_tien.xlsx')
        client = FakeClient([{'status': 'duplicate', 'report_id': 4, 'type': 'pickup_cashflow'}])
        result = EIM.sync(self.root, client, self.state, self.now, log=lambda _: None)[0]
        self.assertEqual('duplicate', result['status'])
        self.assertIn('không đối soát lại', result['message'])
        self.assertEqual([], EIM.sync(self.root, client, self.state, self.now + 99999, log=lambda _: None))

    def test_network_errors_retry_with_backoff_but_bad_files_are_rejected(self):
        self.write('20_BAO_CAO/GHTK/GHTK_dong_tien.xlsx', b'a')
        self.write('20_BAO_CAO/GHTK/bien_ban_doi_soat.xlsx', b'b')
        client = FakeClient({'bien_ban_doi_soat.xlsx': [ERP.ErpError('ERP HTTP 422: Không nhận ra loại file', 422)],
                             'GHTK_dong_tien.xlsx': [ERP.ErpError('Không kết nối được ERP', 0)]})
        first = {r['name']: r for r in EIM.sync(self.root, client, self.state, self.now, log=lambda _: None)}
        self.assertEqual('rejected', first['bien_ban_doi_soat.xlsx']['status'])
        self.assertEqual('retry', first['GHTK_dong_tien.xlsx']['status'])
        # Too early → nothing; after the first delay → retried and succeeds.
        self.assertEqual([], EIM.sync(self.root, client, self.state, self.now + 10, log=lambda _: None))
        later = EIM.sync(self.root, client, self.state, self.now + EIM.RETRY_DELAYS[0] + 1, log=lambda _: None)
        self.assertEqual([('GHTK_dong_tien.xlsx', 'sent')], [(r['name'], r['status']) for r in later])
        self.assertEqual(2, later[0]['attempts'])

    def test_edited_file_is_a_new_version(self):
        path = self.write('20_BAO_CAO/GHTK/bien_ban_doi_soat.xlsx', b'v1')
        client = FakeClient([ERP.ErpError('ERP HTTP 422: thiếu cột', 422)])
        EIM.sync(self.root, client, self.state, self.now, log=lambda _: None)
        path.write_bytes(b'v2 fixed')
        self.assertEqual(['sent'], [r['status'] for r in EIM.sync(self.root, client, self.state, time.time() + 60, log=lambda _: None)])

    def test_symlink_and_oversize_are_ignored(self):
        outside = Path(self.temp.name) / 'bien_ban_doi_soat.xlsx'
        outside.write_bytes(b'x')
        (self.root / '00_INBOX' / 'bien_ban_doi_soat.xlsx').symlink_to(outside)
        self.write('20_BAO_CAO/GHTK/GHTK_dong_tien.xlsx', b'0' * (EIM.MAX_BYTES + 1))
        client = FakeClient()
        self.assertEqual([], EIM.sync(self.root, client, self.state, self.now, log=lambda _: None))

    def test_enabled_requires_config_and_respects_opt_out(self):
        self.assertFalse(EIM.enabled(self.state))
        (self.state / 'erp.json').write_text(json.dumps({'base_url': 'https://erp.example'}))
        self.assertTrue(EIM.enabled(self.state))
        (self.state / 'erp.json').write_text(json.dumps({'base_url': 'https://erp.example', 'eim_auto_push': False}))
        self.assertFalse(EIM.enabled(self.state))

    def test_client_posts_to_eim_route(self):
        seen = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"status":"building","report_id":3}'

        def opener(request, timeout):
            seen.append(request)
            return Resp()

        client = ERP.ErpClient({'base_url': 'https://erp.example', 'username': 'u', 'password': 'p'}, opener)
        path = self.write('00_INBOX/bien_ban_doi_soat.xlsx')
        self.assertEqual(3, client.eim_report(path, 'a' * 64, 'carrier_delivery')['report_id'])
        self.assertEqual('https://erp.example/wp-json/eim/v1/intake/reconcile-report', seen[0].full_url)
        self.assertIn(b'name="type"\r\n\r\ncarrier_delivery', seen[0].data)


if __name__ == '__main__':
    unittest.main()
