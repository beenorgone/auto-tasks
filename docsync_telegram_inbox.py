"""Bounded loopback bridge to the existing Telegram approval service.

The review credential stays on disk/server; DocSync never downloads or approves
anything while listing files. The inbox service owns decisions and file identity checks.
"""
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

REVIEW_URL = 'http://127.0.0.1:8766'
TOKEN_FILE = Path.home() / '.local/share/telegram-file-inbox/review-token.json'
MAX_RESPONSE = 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class InboxUnavailable(Exception):
    pass


class DecisionConflict(Exception):
    pass


def request(payload=None):
    if payload is not None:
        if (type(payload.get('chat')) is not int or type(payload.get('message')) is not int
                or payload.get('action') not in ('approve', 'hold')):
            raise ValueError('Invalid decision')
        payload = {key: payload[key] for key in ('chat', 'message', 'action')}
    try:
        token = json.loads(TOKEN_FILE.read_text(encoding='utf-8'))['token']
        if not isinstance(token, str) or not token:
            raise ValueError('Missing token')
        headers = {'Authorization': 'Bearer ' + token, 'Origin': REVIEW_URL}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers['Content-Type'] = 'application/json'
        req = Request(REVIEW_URL + ('/api/items' if body is None else '/api/decision'),
                      data=body, headers=headers)
        # Never forward credentials through environment proxies or redirects.
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=4) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError('Response too large')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Invalid response')
        return result
    except HTTPError as error:
        if error.code == 409:
            raise DecisionConflict from None
        raise InboxUnavailable from None
    except (OSError, URLError, ValueError, KeyError):
        raise InboxUnavailable from None
