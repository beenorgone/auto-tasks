import base64
import datetime as dt
import email.parser
import email.policy
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path

import docsync_erp as ERP

SPEC = importlib.util.spec_from_file_location("doc_sync_app_erp", Path(__file__).with_name("doc-sync-app.py"))
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeErp:
    """Records requests; answers like /wp-json/ivar/v1/docsync/*, dedupes by SHA-256 per field."""

    def __init__(self):
        self.requests = []
        self.stored = {}

    def __call__(self, request, timeout):
        self.requests.append(request)
        path = request.full_url.split('/docsync', 1)[1]
        if path.startswith('/upload'):
            message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                b'Content-Type: ' + request.get_header('Content-type').encode() + b'\r\n\r\n' + request.data)
            form = {part.get_param('name', header='content-disposition'): part for part in message.iter_parts()}
            digest = form['sha256'].get_content().strip()
            key = (form['type'].get_content(), form['id'].get_content(), form['field'].get_content())
            seen = self.stored.setdefault(key, set())
            status = 'duplicate' if digest in seen else 'uploaded'
            seen.add(digest)
            self.last_file = (form['file'].get_filename(), form['file'].get_payload(decode=True))
            ai = form['ai'].get_content() == '1'
            body = {'status': status, 'attachment_id': 9, 'attachment_url': 'https://erp.test/f.pdf',
                    'record': {'url': 'https://erp.test/po/42'}, 'ai': {'queued': ai and status == 'uploaded', 'mode': form['ai_mode'].get_content()}}
        elif path.startswith('/record'):
            body = {'record': {'id': 42, 'label': 'PO42 Acme'}, 'fields': [{'key': 'invoices', 'label': 'Hoá đơn', 'max': 0, 'count': 1, 'sha256': []}], 'ai_available': True}
        elif path.startswith('/forbidden'):
            raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, io.BytesIO(b'{"message":"no"}'))
        else:
            body = {'records': [{'id': 42, 'label': 'PO42 Acme'}]}
        return FakeResponse(json.dumps(body).encode())


class DocSyncErpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / 'WIP & Reference'
        (self.root / '00_INBOX').mkdir(parents=True)
        self.state = base / 'state'
        self.state.mkdir()
        self.invoice = self.root / '00_INBOX' / 'Hoá đơn PO42 Acme.pdf'
        self.invoice.write_bytes(b'%PDF-1.4 invoice')
        self.fake = FakeErp()
        self.client = ERP.ErpClient({'base_url': 'https://erp.test', 'username': 'ke-toan', 'password': 'abcd efgh'}, self.fake)

    def tearDown(self):
        self.temp.cleanup()

    def test_suggestion_maps_invoice_to_po_field_with_reference(self):
        self.assertEqual(ERP.suggest('00_INBOX/Hoá đơn PO42 Acme.pdf'),
                         {'type': 'po', 'field': 'invoices', 'label': 'Hoá đơn PO', 'query': 'PO42'})
        self.assertEqual(ERP.suggest('COA lot 7.pdf')['type'], 'qc_test')
        self.assertIsNone(ERP.suggest('anh-chup-man-hinh.png'))

    def test_today_groups_by_category_and_skips_old_hidden_and_partial_files(self):
        old = self.root / 'old.pdf'
        old.write_text('x')
        stamp = (dt.datetime.now() - dt.timedelta(days=3)).timestamp()
        os.utime(old, (stamp, stamp))
        (self.root / '.secret.pdf').write_text('x')
        (self.root / 'tai-ve.pdf.part').write_text('x')
        (self.root / '.wip').mkdir()
        (self.root / '.wip' / 'index.db').write_text('x')
        classify = lambda relative, path: ('finance_ops', 0.9, 'rule: hoa don', None, 'review_only')
        data = ERP.today(self.root, dt.date.today(), classify, self.state)
        paths = [f['path'] for g in data['groups'] for f in g['files']]
        # old.pdf keeps a 3-day-old mtime but its ctime is today (like a file the organizer moved today).
        self.assertEqual(['00_INBOX/Hoá đơn PO42 Acme.pdf', 'old.pdf'], sorted(paths))
        self.assertEqual('finance_ops', data['groups'][0]['category'])
        self.assertEqual(2, data['counts']['unassigned'])
        self.assertEqual(0, ERP.today(self.root, dt.date.today() - dt.timedelta(days=10), classify, self.state)['total'])

    def test_assign_rejects_paths_outside_root_and_symlinks(self):
        outside = Path(self.temp.name) / 'outside.pdf'
        outside.write_text('x')
        (self.root / 'link.pdf').symlink_to(outside)
        for bad in ('../outside.pdf', str(outside), 'link.pdf', 'missing.pdf'):
            with self.assertRaises(ERP.ErpError):
                ERP.assign(self.root, bad, 'po', 42, 'invoices', state=self.state)
        with self.assertRaises(ERP.ErpError):
            ERP.assign(self.root, '00_INBOX/Hoá đơn PO42 Acme.pdf', 'po', 42, '../meta', state=self.state)

    def test_push_uploads_once_binds_sha_and_retry_is_duplicate(self):
        relative = '00_INBOX/Hoá đơn PO42 Acme.pdf'
        ERP.assign(self.root, relative, 'po', 42, 'invoices', ai=True, ai_mode='analysis', instruction='So với PO', state=self.state)
        dry = ERP.push(self.root, self.client, dry_run=True, state=self.state)
        self.assertEqual('would_push', dry[0]['status'])
        self.assertEqual([], self.fake.requests)
        result = ERP.push(self.root, self.client, state=self.state)
        self.assertEqual('pushed', result[0]['status'])
        self.assertIn('AI đang phân tích', result[0]['message'])
        request = self.fake.requests[0]
        self.assertEqual('Basic ' + base64.b64encode(b'ke-toan:abcd efgh').decode(), request.get_header('Authorization'))
        self.assertTrue(request.full_url.startswith('https://erp.test/wp-json/ivar/v1/docsync/upload'))
        self.assertEqual(('Hoá đơn PO42 Acme.pdf', b'%PDF-1.4 invoice'), self.fake.last_file)
        saved = ERP.load_assignments(self.state)[relative]
        self.assertEqual('pushed', saved['status'])
        self.assertNotIn('password', json.dumps(saved))
        # Pushed files are not re-sent by default; an explicit retry hits ERP dedupe.
        self.assertEqual([], ERP.push(self.root, self.client, state=self.state))
        again = ERP.push(self.root, self.client, [relative], state=self.state)
        self.assertEqual('duplicate', again[0]['status'])

    def test_changed_file_after_assignment_is_not_pushed(self):
        relative = '00_INBOX/Hoá đơn PO42 Acme.pdf'
        ERP.assign(self.root, relative, 'po', 42, 'invoices', state=self.state)
        self.invoice.write_bytes(b'%PDF-1.4 edited later')
        result = ERP.push(self.root, self.client, state=self.state)
        self.assertEqual('error', result[0]['status'])
        self.assertEqual([], self.fake.requests)
        row = ERP.today(self.root, dt.date.today(), None, self.state)['groups'][0]['files'][0]
        self.assertEqual('stale', row['erp'])

    def test_http_errors_become_actionable_messages_without_secret(self):
        with self.assertRaises(ERP.ErpError) as caught:
            self.client._call('GET', '/forbidden')
        self.assertIn('403', str(caught.exception))
        self.assertNotIn('abcd', str(caught.exception))

    def test_config_requires_https_and_private_password_file(self):
        def write(config):
            (self.state / 'erp.json').write_text(json.dumps(config))
        secret = Path(self.temp.name) / 'pw'
        secret.write_text('abcd efgh ijkl\n')
        os.chmod(secret, 0o644)
        write({'base_url': 'http://erp.example', 'username': 'u', 'password_file': str(secret)})
        with self.assertRaises(ERP.ErpError):
            ERP.load_config(self.state, {})
        write({'base_url': 'https://erp.example', 'username': 'u', 'password': 'inline'})
        with self.assertRaises(ERP.ErpError):
            ERP.load_config(self.state, {})
        write({'base_url': 'https://erp.example', 'username': 'u', 'password_file': str(secret)})
        with self.assertRaises(ERP.ErpError):
            ERP.load_config(self.state, {})
        os.chmod(secret, 0o600)
        config = ERP.load_config(self.state, {})
        self.assertEqual('abcdefghijkl', config['password'])
        self.assertEqual('envpw', ERP.load_config(self.state, {'DOCSYNC_ERP_APP_PASSWORD': 'envpw'})['password'])

    def test_web_assign_rechecks_field_on_erp_and_push_limits_batch(self):
        original = ERP.STATE
        ERP.STATE = self.state
        try:
            relative = '00_INBOX/Hoá đơn PO42 Acme.pdf'
            with self.assertRaises(ValueError):
                APP.erp_post('/api/erp/assign', {'path': relative, 'type': 'po', 'id': 42, 'field': 'files'}, self.root, lambda: self.client)
            data = APP.erp_post('/api/erp/assign', {'path': relative, 'type': 'po', 'id': 42, 'field': 'invoices', 'ai': True},
                                self.root, lambda: self.client)
            self.assertEqual('PO42 Acme', data['assignment']['label'])
            self.assertEqual('Hoá đơn', data['assignment']['field_label'])
            with self.assertRaises(ValueError):
                APP.erp_post('/api/erp/push', {'paths': ['a'] * 21}, self.root, lambda: self.client)
            pushed = APP.erp_post('/api/erp/push', {'paths': [relative]}, self.root, lambda: self.client)
            self.assertEqual('pushed', pushed['results'][0]['status'])
            today = APP.erp_get('/api/today', {}, self.root)
            self.assertEqual(1, today['counts']['pushed'])
        finally:
            ERP.STATE = original

    def test_page_includes_erp_panel_without_breaking_token(self):
        html = APP.page('tok123')
        self.assertIn("id='erp-rows'", html)
        self.assertIn('"tok123"', html)
        self.assertLess(html.index('const token='), html.index("$('#erp-date')"))


if __name__ == '__main__':
    unittest.main()
