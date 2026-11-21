"""Hold the preview/send boundary and retry semantics of connected-app intake."""

import argparse
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

import docsync_intake as intake


class IntakeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.file = Path(temp.name) / 'Hồ sơ.csv'
        self.file.write_bytes(b'order,total\n123,200\n')
        self.parser = argparse.ArgumentParser()
        intake.register(self.parser)
        self.client = Mock()
        self.client.origin = 'https://example.test'
        self.target = {'record': {'id': 123, 'type': 'po', 'label': 'PO123'},
                       'fields': [{'key': 'attachments'}], 'ai_available': True}

    def policy(self, *flows, limit=4194304):
        return 200, {'policy': 'guard', 'flows': [{'flow': flow, 'allowed': True} for flow in flows],
                     'quota': {'max_file_bytes': limit}}

    def attach(self, *options):
        return self.parser.parse_args([
            'attach', str(self.file), '--type', 'po', '--id', '123', '--field', 'attachments', *options,
        ])

    def reconcile(self, *options):
        return self.parser.parse_args(['reconcile', str(self.file), '--type', 'carrier_delivery', *options])

    def test_preview_only_reads_and_returns_selected_destination(self):
        self.client.request.side_effect = [self.policy('erp_attach'), (200, self.target)]
        preview = intake.run(self.attach(), self.client)
        self.assertEqual(preview['status'], 'preview')
        self.assertEqual(preview['record'], 'PO123')
        self.assertEqual(preview['sha256'], hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertEqual([call.args[0] for call in self.client.request.call_args_list],
                         [intake.ERP + '/flows', intake.ERP + '/record'])

    def test_attach_uses_hash_of_exact_bytes_sent_and_leaves_source(self):
        expected = self.file.read_bytes()
        reads = iter([self.policy('erp_attach', 'erp_attach_ai'), (200, self.target)])

        def request(path, *, upload=None, **kwargs):
            if upload is None:
                return next(reads)
            name, stream, size, fields = upload
            self.assertEqual(path, intake.ERP + '/upload')
            self.assertEqual(name, self.file.name)
            self.assertEqual(size, len(expected))
            self.assertEqual(stream.read(), expected)
            self.assertEqual(fields, {'type': 'po', 'id': 123, 'field': 'attachments', 'ai': 1,
                                      'ai_mode': 'summary', 'sha256': hashlib.sha256(expected).hexdigest()})
            return 201, {'status': 'uploaded'}

        self.client.request.side_effect = request
        result = intake.run(self.attach('--send', '--ai-mode', 'summary'), self.client)
        self.assertEqual(self.file.read_bytes(), expected)
        self.assertFalse(result['duplicate'])

    def test_disabled_flow_denies_write(self):
        self.client.request.return_value = self.policy('erp_attach')
        with self.assertRaisesRegex(intake.IntakeError, 'erp_attach_ai'):
            intake.run(self.attach('--send', '--ai-mode', 'summary'), self.client)
        self.client.request.assert_called_once_with(intake.ERP + '/flows')

    def test_wrong_field_and_over_limit_stop_before_upload(self):
        for policy, fields, error in [
            (self.policy('erp_attach'), [], 'field file không khớp'),
            (self.policy('erp_attach', limit=1), self.target['fields'], 'vượt trần'),
        ]:
            with self.subTest(error=error):
                self.client.reset_mock()
                self.client.request.side_effect = [policy, (200, {**self.target, 'fields': fields})]
                with self.assertRaisesRegex(intake.IntakeError, error):
                    intake.run(self.attach('--send'), self.client)
                self.assertEqual(self.client.request.call_count, 2)

    def test_carrier_delivery_requires_opt_in_when_auto_complete_is_enabled(self):
        self.client.request.return_value = self.policy('eim_carrier_delivery', 'eim_carrier_auto_complete')
        self.assertTrue(intake.run(self.reconcile(), self.client)['auto_complete_orders'])
        self.client.request.reset_mock()
        with self.assertRaisesRegex(intake.IntakeError, '--allow-order-completion'):
            intake.run(self.reconcile('--send'), self.client)
        self.client.request.assert_called_once_with(intake.ERP + '/flows')

    def test_eim_duplicate_can_return_original_job_status(self):
        self.client.request.side_effect = [self.policy('eim_carrier_delivery'),
                                           (200, {'status': 'drafted', 'report_id': 17})]
        result = intake.run(self.reconcile('--send'), self.client)
        self.assertTrue(result['duplicate'])
        self.assertEqual(result['result']['status'], 'drafted')

    def test_http_errors_do_not_retry_and_credentials_stay_off_redirects(self):
        with self.assertRaises(intake.IntakeError):
            intake.IntakeClient('http://example.test', 'user', 'secret')
        client = intake.IntakeClient('https://example.test', 'user', 'secret')
        client.opener = Mock()
        path = intake.ERP + '/upload'
        client.opener.open.side_effect = HTTPError(client.origin + path, 307, 'redirect', {},
                                                  BytesIO(b'{"code":"redirect"}'))
        with self.assertRaisesRegex(intake.IntakeError, 'HTTP 307'):
            client.request(path, upload=('Report\r\n".csv', BytesIO(b'report'), 6, {'type': 'po'}))
        client.opener.open.assert_called_once()
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        body = b''.join(request.data)
        self.assertEqual(int(request.get_header('Content-length')), len(body))
        self.assertIn(b'filename="Report___.csv"', body)
        self.assertIn(b'\r\n\r\nreport\r\n', body)
        self.assertIsNone(intake.NoRedirect().redirect_request(request, None, 307, '', {}, 'https://other.test'))


if __name__ == '__main__':
    unittest.main()
