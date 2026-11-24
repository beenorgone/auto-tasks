import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import docsync_telegram_inbox as bridge

spec = importlib.util.spec_from_file_location('app', Path(__file__).with_name('doc-sync-app.py'))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class InboxIntegrationTests(unittest.TestCase):
    def test_bridge_uses_server_credential_and_preserves_conflicts(self):
        calls = []
        class Review(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                calls.append((self.path, self.headers.get('Authorization')))
                body = json.dumps({'items': [], 'counts': {}, 'ready': False}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(data)
                self.send_response(409)
                self.end_headers()
        with tempfile.TemporaryDirectory() as temporary, ThreadingHTTPServer(('127.0.0.1', 0), Review) as server:
            token_file = Path(temporary) / 'token.json'
            token_file.write_text(json.dumps({'token': 'private-review-token'}))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(bridge, 'TOKEN_FILE', token_file), patch.object(bridge, 'REVIEW_URL', f'http://127.0.0.1:{server.server_port}'):
                    self.assertFalse(bridge.request()['ready'])
                    self.assertEqual(calls[0], ('/api/items', 'Bearer private-review-token'))
                    for bad in ({'chat': True, 'message': 1, 'action': 'approve'}, {'chat': 1, 'message': 2, 'action': 'download'}):
                        with self.assertRaises(ValueError):
                            bridge.request(bad)
                    with self.assertRaises(bridge.DecisionConflict):
                        bridge.request({'chat': 1, 'message': 2, 'action': 'hold', 'url': 'https://example.com'})
                    self.assertEqual(calls[1], {'chat': 1, 'message': 2, 'action': 'hold'})
            finally:
                server.shutdown()
                thread.join()

    def test_routes_require_local_csrf_and_report_unavailable_service(self):
        with ThreadingHTTPServer(('127.0.0.1', 0), app.AppHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            try:
                connection.request('GET', '/telegram')
                response = connection.getresponse()
                markup = response.read().decode()
                self.assertEqual(response.status, 200)
                self.assertIn('href="/telegram" aria-current="page"', markup)
                self.assertIn('name="docsync-token"', markup)
                with patch.object(bridge, 'request', return_value={'ok': True}) as dispatch:
                    payload = json.dumps({'chat': 1, 'message': 2, 'action': 'approve'})
                    for headers, expected in [({}, 403), ({'X-CSRF-Token': app.APP_STATE.csrf_token, 'Origin': 'https://evil.example'}, 403), ({'X-CSRF-Token': app.APP_STATE.csrf_token}, 200)]:
                        connection.request('POST', '/api/telegram/decision', payload, headers)
                        response = connection.getresponse()
                        self.assertEqual(response.status, expected)
                        response.read()
                    dispatch.assert_called_once_with(json.loads(payload))
                with patch.object(bridge, 'request', side_effect=bridge.InboxUnavailable):
                    connection.request('GET', '/api/telegram/items')
                    response = connection.getresponse()
                    self.assertEqual(response.status, 503)
                    self.assertIn('message', json.load(response))
            finally:
                connection.close()
                server.shutdown()
                thread.join()

    def test_redirect_never_forwards_credentials(self):
        self.assertIsNone(bridge.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))


if __name__ == '__main__':
    unittest.main()
