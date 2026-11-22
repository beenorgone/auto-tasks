"""Prevent old files, wrong report types and paused workflows from being uploaded."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

import docsync_workflows as workflows


def workbook(sheet):
    file = io.BytesIO()
    with ZipFile(file, 'w') as archive:
        archive.writestr('xl/workbook.xml', f'<workbook><sheets><sheet name="{sheet}"/></sheets></workbook>')
    return file.getvalue()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        self.root = folder / 'WIP'
        self.ghtk = self.root / workflows.GHTK_WORKFLOW_FOLDER
        self.ghtk.mkdir(parents=True)
        config = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(folder / 'private')})
        config.start()
        self.addCleanup(config.stop)
        self.client = Mock()
        self.allowed = {workflows.FLOW}
        self.carrier_modes = [workflows.intake.CARRIER_RECONCILE, workflows.intake.CARRIER_DRAFT]
        self.expected_type = 'pickup_cashflow'
        self.client.request.side_effect = self.request
        transport = patch.object(workflows.intake, 'IntakeClient', return_value=self.client)
        transport.start()
        self.addCleanup(transport.stop)
        settle = patch.object(workflows, 'SETTLE_NS', 0)
        settle.start()
        self.addCleanup(settle.stop)
        self.uploaded = []

    def request(self, route, *, upload=None, **kwargs):
        if route == workflows.intake.ERP + '/flows':
            return 200, {'policy': 'guard', 'flows': [{'flow': flow, 'allowed': True} for flow in self.allowed],
                         'quota': {'max_file_bytes': 4194304}}
        self.assertEqual(route, workflows.intake.EIM)
        if upload is None:
            return 200, {'carrier_modes': self.carrier_modes}
        filename, stream, size, fields = upload
        self.uploaded.append((filename, stream.read()))
        self.assertEqual(fields['type'], self.expected_type)
        self.assertEqual(len(self.uploaded[-1][1]), size)
        if fields.get('carrier_mode') == workflows.intake.CARRIER_DRAFT:
            return 202, {'status': 'building', 'report_id': 13, 'matched': 7, 'unmatched': 2}
        return 201, {'status': 'reconciled', 'report_id': 12, 'matched': 7, 'unmatched': 2}

    def make_report(self, day, sheet='BC Dòng tiền'):
        file = self.ghtk / f'GHTK_bao_cao_van_hanh_va_dong_tien_{day}_11_S12.xlsx'
        file.write_bytes(workbook(sheet))
        return file

    def enable(self):
        workflows.save_credential(self.root, 'operator', 'fixture-password')
        workflows.set_enabled(self.root, workflows.PICKUP, True)

    def test_default_off_and_require_verified_credential(self):
        self.make_report('21')
        workflows.process_one(self.root)
        self.client.request.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'Lưu tài khoản'):
            workflows.set_enabled(self.root, workflows.PICKUP, True)
        self.assertFalse(workflows.settings(self.root)[workflows.PICKUP][0])
        self.assertTrue(workflows.settings(self.root)[workflows.LOCAL][0])

    def test_fresh_file_sends_once_and_snapshot_type_is_verified(self):
        self.make_report('20')
        self.enable()
        wrong = self.make_report('21', 'Biên bản đối soát')
        fresh = self.make_report('22')
        (self.ghtk / 'Bien ban doi soat GHTK.xlsx').write_bytes(workbook('BC Dòng tiền'))
        link = self.ghtk / 'GHTK_bao_cao_van_hanh_va_dong_tien_23_11_S12.xlsx'
        link.symlink_to(fresh)
        for _ in range(4):
            workflows.process_one(self.root)
        self.assertEqual(self.uploaded, [(fresh.name, fresh.read_bytes())])
        states = {Path(item['path']).name: item['state'] for item in workflows.overview(self.root)['activity']}
        self.assertEqual(states, {wrong.name: 'review', fresh.name: 'done'})

    def test_pause_during_preflight_prevents_upload(self):
        self.enable()
        self.make_report('22')

        def pause(route, **kwargs):
            result = self.request(route, **kwargs)
            workflows.set_enabled(self.root, workflows.PICKUP, False)
            return result

        self.client.request.side_effect = pause
        workflows.process_one(self.root)
        self.assertEqual(self.uploaded, [])
        self.assertEqual(workflows.overview(self.root)['activity'][0]['state'], 'paused')

    def test_accepts_reports_in_the_active_routing_folder(self):
        self.enable()
        routed = self.root / workflows.REPORT_TARGETS['ghtk_reports']
        routed.mkdir(parents=True, exist_ok=True)
        file = self.make_report('22')
        file = file.rename(routed / file.name)
        workflows.process_one(self.root)
        self.assertEqual(self.uploaded, [(file.name, file.read_bytes())])

    def test_replacing_credentials_pauses_and_keeps_password_off_overview(self):
        self.enable()
        self.assertEqual(workflows.credential_path().stat().st_mode & 0o777, 0o600)
        workflows.save_credential(self.root, 'other', 'new-fixture-password')
        data = workflows.overview(self.root)
        self.assertTrue(data['configured'])
        self.assertFalse(workflows.settings(self.root)[workflows.PICKUP][0])
        self.assertNotIn('new-fixture-password', json.dumps(data))

    def test_quota_wait_also_blocks_subsequent_files(self):
        self.enable()
        self.make_report('21')
        self.make_report('22')

        def rate_limited(route, *, upload=None, **kwargs):
            if upload is not None:
                raise workflows.intake.IntakeError('HTTP 429', status=429)
            return self.request(route, **kwargs)

        self.client.request.reset_mock()
        self.client.request.side_effect = rate_limited
        workflows.process_one(self.root)
        self.assertEqual(self.client.request.call_count, 2)
        self.assertEqual(workflows.overview(self.root)['activity'][0]['state'], 'retry')
        workflows.process_one(self.root)
        self.assertEqual(self.client.request.call_count, 2)

    def test_payment_workflow_requires_draft_permission_when_requested(self):
        self.enable()
        before = workflows.settings(self.root)[workflows.CARRIER]
        self.allowed.update(('eim_carrier_delivery', 'eim_carrier_auto_complete'))
        with self.assertRaisesRegex(ValueError, 'chưa được duyệt dựng lô'):
            workflows.set_enabled(self.root, workflows.CARRIER, True, draft_batches=True)
        self.assertFalse(before[0])
        self.assertEqual(workflows.settings(self.root)[workflows.CARRIER], before)
        self.assertTrue(workflows.settings(self.root)[workflows.PICKUP][0])

    def test_payment_report_type_and_server_policy_checked_before_send(self):
        self.enable()
        self.allowed.add('eim_carrier_delivery')
        workflows.set_enabled(self.root, workflows.CARRIER, True)
        self.expected_type = 'carrier_delivery'
        valid_csv = ('STT,Mã đơn hàng,Mã đơn hàng shop,Trạng thái đơn hàng,Ngày hoàn thành,Tiền CoD\n'
                     '1,fixture,123,Đã hoàn thành,2026-11-22,10000\n').encode('utf-8-sig')
        first = self.ghtk / 'bien_ban_doi_soat_S12_TA_1794856942_453.csv'
        first.write_bytes(valid_csv)
        second = self.ghtk / 'bien_ban_doi_soat_S12_TA_1794856943_453.xlsx'
        second.write_bytes(workbook('BC Dòng tiền'))
        workflows.process_one(self.root)
        workflows.process_one(self.root)
        self.assertEqual(self.uploaded, [(first.name, valid_csv)])
        self.assertEqual({Path(item['path']).name: item['state'] for item in workflows.overview(self.root)['activity']},
                         {first.name: 'done', second.name: 'review'})
        self.carrier_modes.clear()
        third = self.ghtk / 'bien_ban_doi_soat_S12_TA_1794856944_453.csv'
        third.write_bytes(valid_csv)
        workflows.process_one(self.root)
        self.assertEqual(len(self.uploaded), 1)
        self.assertFalse(workflows.settings(self.root)[workflows.CARRIER][0])
        self.assertTrue(workflows.settings(self.root)[workflows.PICKUP][0])

    def test_draft_mode_is_sent_explicitly_even_if_account_can_auto_complete(self):
        self.enable()
        self.allowed.update(('eim_carrier_delivery', 'eim_carrier_draft_batches', 'eim_carrier_auto_complete'))
        workflows.set_enabled(self.root, workflows.CARRIER, True, draft_batches=True)
        self.expected_type = 'carrier_delivery'
        filename = 'bien_ban_doi_soat_S12_TA_1794856942_453.csv'
        (self.ghtk / filename).write_text(
            'STT,Mã đơn hàng,Mã đơn hàng shop,Trạng thái đơn hàng,Ngày hoàn thành,Tiền CoD\n', encoding='utf-8')
        workflows.process_one(self.root)
        payload = self.client.request.call_args.kwargs['upload'][-1]
        self.assertEqual(payload['carrier_mode'], 'draft_only')
        self.assertIn('xếp hàng dựng lô hóa đơn nháp', workflows.overview(self.root)['activity'][0]['outcome'])


if __name__ == '__main__':
    unittest.main()
