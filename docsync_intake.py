"""Send explicitly selected files to the IVAR connected-app intake APIs."""

import base64
from contextlib import contextmanager
import getpass
import hashlib
from http.client import HTTPException
from itertools import chain
import json
import os
from pathlib import Path
import re
import stat
import sys
from tempfile import SpooledTemporaryFile
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


ERP = '/wp-json/ivar/v1/intake'
EIM = '/wp-json/eim/v1/intake/reconcile-report'
REPORT_TYPES = ('pickup_cashflow', 'carrier_delivery')
CHUNK_BYTES = 256 * 1024


class IntakeError(ValueError):
    """A request or local preflight failed."""


class NoRedirect(HTTPRedirectHandler):
    """Keep App Passwords and file bytes on the explicitly configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class IntakeClient:
    def __init__(self, origin, username, password):
        url = urlsplit(origin)
        if (url.scheme != 'https' or not url.hostname or url.username is not None
                or url.path not in ('', '/') or url.query or url.fragment):
            raise IntakeError('DOCSYNC_ERP_URL phải là địa chỉ HTTPS của site, không kèm đường dẫn.')
        if not username or ':' in username or not password:
            raise IntakeError('Cần tên đăng nhập WordPress và Application Password hợp lệ.')
        self.origin = f'https://{url.netloc}'
        token = base64.b64encode(f'{username}:{password}'.encode()).decode('ascii')
        self.headers = {
            'Authorization': f'Basic {token}',
            'X-Intake-Client': 'DocSync/1',
            'Accept': 'application/json',
        }
        self.opener = build_opener(NoRedirect())

    def request(self, path, *, query=None, upload=None):
        url = self.origin + path
        if query:
            url += '?' + urlencode(query)
        headers = self.headers.copy()
        data = None
        if upload:
            multipart_headers, data = multipart(*upload)
            headers.update(multipart_headers)
        request = Request(url, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=45) as response:
                return response.status, response_json(response)
        except HTTPError as error:
            with error:
                try:
                    code = response_json(error).get('code', '')
                except IntakeError:
                    code = ''
                code = code if isinstance(code, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', code) else ''
            detail = f' ({code})' if code else ''
            raise IntakeError(f'{request.get_method()} {path}: HTTP {error.code}{detail}') from None
        except (OSError, HTTPException):
            advice = ' Kiểm tra trạng thái trước khi gửi lại.' if upload else ''
            raise IntakeError(f'Không nhận được phản hồi từ API {path}.{advice}') from None


def response_json(response):
    limit = 1024 * 1024
    try:
        payload = response.read(limit + 1)
        if len(payload) > limit:
            raise ValueError
        result = json.loads(payload)
    except (ValueError, HTTPException):
        raise IntakeError('API trả phản hồi không đọc được.') from None
    if not isinstance(result, dict):
        raise IntakeError('API trả phản hồi sai định dạng.')
    return result


def multipart(filename, stream, size, fields):
    boundary = uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    # Quotes, backslashes and control characters must stay out of the part headers.
    filename = re.sub(r'["\\\x00-\x1f\x7f]', '_', filename)
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        'Content-Type: application/octet-stream\r\n\r\n'.encode('utf-8')
    )
    prefix = b''.join(parts)
    suffix = f'\r\n--{boundary}--\r\n'.encode()
    headers = {
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Content-Length': str(len(prefix) + size + len(suffix)),
    }
    return headers, chain((prefix,), iter(lambda: stream.read(CHUNK_BYTES), b''), (suffix,))


@contextmanager
def selected_file(path, limit, send):
    # Nonblocking open also handles a FIFO selected accidentally on the local filesystem.
    with SpooledTemporaryFile(max_size=8 * 1024 * 1024) as snapshot:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise IntakeError('Chọn một file thông thường.')
            if not 0 < info.st_size <= limit:
                raise IntakeError(f'File rỗng hoặc vượt trần {limit} byte/file.')
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: stream.read(CHUNK_BYTES), b''):
                size += len(chunk)
                if size > info.st_size:
                    break
                digest.update(chunk)
                if send:
                    snapshot.write(chunk)
            if size != info.st_size or os.fstat(stream.fileno()).st_mtime_ns != info.st_mtime_ns:
                raise IntakeError('File đang thay đổi. Chọn lại sau khi file được lưu xong.')
        snapshot.seek(0)
        yield snapshot, size, digest.hexdigest()


def preflight(client, args):
    _, policy = client.request(ERP + '/flows')
    if policy.get('policy') != 'guard' or not isinstance(policy.get('flows'), list):
        raise IntakeError('Site chưa trả về chính sách App Password Guard cho ứng dụng kết nối.')
    allowed = {row.get('flow') for row in policy['flows'] if isinstance(row, dict)
               and isinstance(row.get('flow'), str) and row.get('allowed') is True}
    quota = policy.get('quota')
    limit = quota.get('max_file_bytes') if isinstance(quota, dict) else None
    if type(limit) is not int or limit <= 0:
        raise IntakeError('Site chưa trả về giới hạn dung lượng một file.')

    if args.action == 'attach':
        flow = 'erp_attach'
        fields = {'type': args.type, 'id': args.id, 'field': args.field}
        route = ERP + '/upload'
        if args.ai_mode:
            if 'erp_attach_ai' not in allowed:
                raise IntakeError('Luồng erp_attach_ai chưa được chủ sở hữu duyệt cho tài khoản này.')
            fields.update(ai=1, ai_mode=args.ai_mode)
    else:
        flow = 'eim_' + args.type
        fields = {'type': args.type, 'carrier': 'GHTK'}
        route = EIM
        limit = min(limit, 20 * 1024 * 1024)
        if args.file.suffix.lower() not in ('.csv', '.xlsx'):
            raise IntakeError('Đối soát GHTK nhận file CSV hoặc XLSX.')
    if flow not in allowed:
        raise IntakeError(f'Luồng {flow} chưa được chủ sở hữu duyệt cho tài khoản này.')

    preview = {'status': 'preview', 'site': client.origin, 'destination': fields.copy()}
    if args.action == 'attach':
        _, target = client.request(ERP + '/record', query={'type': args.type, 'id': args.id})
        options = target.get('fields')
        record = target.get('record')
        if (not isinstance(record, dict) or record.get('id') != args.id or record.get('type') != args.type
                or not isinstance(options, list)
                or not any(isinstance(row, dict) and row.get('key') == args.field for row in options)):
            raise IntakeError('Bản ghi đích hoặc field file không khớp. Chạy docsync intake record để kiểm tra.')
        preview['record'] = record.get('label')
        if args.ai_mode and target.get('ai_available') is not True:
            raise IntakeError('AI xử lý tài liệu hiện chưa khả dụng trên site.')
    if args.action == 'reconcile' and args.type == 'carrier_delivery':
        preview['draft_batches'] = 'eim_carrier_draft_batches' in allowed
        preview['auto_complete_orders'] = 'eim_carrier_auto_complete' in allowed
        if preview['auto_complete_orders'] and args.send and not args.allow_order_completion:
            raise IntakeError('Tài khoản có luồng tự hoàn thành đơn. Xem kết quả preview trước khi dùng --allow-order-completion.')
    return route, fields, limit, preview


def run(args, client):
    if args.action == 'flows':
        return client.request(ERP + '/flows')[1]
    if args.action == 'targets':
        return client.request(ERP + '/targets', query={'type': args.type, 'q': args.query})[1]
    if args.action == 'record':
        return client.request(ERP + '/record', query={'type': args.type, 'id': args.id})[1]
    if args.action == 'report':
        return client.request(EIM + '/' + args.sha256)[1]

    route, fields, limit, preview = preflight(client, args)
    with selected_file(args.file, limit, args.send) as (stream, size, sha256):
        if not args.send:
            return dict(preview, file=args.file.name, bytes=size, sha256=sha256)
        fields['sha256'] = sha256
        try:
            status, result = client.request(route, upload=(args.file.name, stream, size, fields))
        except IntakeError as error:
            raise IntakeError(f'{error} SHA-256: {sha256}') from None
    if args.action == 'attach':
        valid = (status, result.get('status')) in ((200, 'duplicate'), (201, 'uploaded'))
    else:
        # EIM returns HTTP 200 for duplicates and can retain the original job status in the body.
        valid = (status == 200 or (status, result.get('status')) in ((201, 'reconciled'), (202, 'building')))
        valid = valid and type(result.get('report_id')) is int and result['report_id'] > 0
    if not valid:
        raise IntakeError(f'API trả trạng thái không rõ. Kiểm tra lịch sử với SHA-256 {sha256}.')
    return {'http_status': status, 'duplicate': status == 200, 'sha256': sha256, 'result': result}


def register(parser):
    group = parser.add_subparsers(dest='action', required=True)
    group.add_parser('flows', help='xem quyền luồng và hạn mức')
    targets = group.add_parser('targets', help='tra bản ghi ERP được phép sửa')
    targets.add_argument('--type', required=True)
    targets.add_argument('--query', default='')
    record = group.add_parser('record', help='xem field file của bản ghi ERP')
    attach = group.add_parser('attach', help='xem trước hoặc gắn file vào bản ghi ERP')
    for command in (record, attach):
        command.add_argument('--type', required=True)
        command.add_argument('--id', type=int, required=True)
    attach.add_argument('--field', required=True)
    attach.add_argument('--ai-mode', choices=('summary', 'analysis'),
                        help='gửi nội dung cho nhà cung cấp AI đã cấu hình để tạo Ghi chú AI')
    reconcile = group.add_parser('reconcile', help='xem trước hoặc gửi báo cáo đối soát GHTK')
    reconcile.add_argument('--type', required=True, choices=REPORT_TYPES)
    reconcile.add_argument('--allow-order-completion', action='store_true',
                           help='chấp nhận EIM tự hoàn thành đơn nếu tài khoản đã được duyệt luồng này')
    for command in (attach, reconcile):
        command.add_argument('file', type=Path)
        command.add_argument('--send', action='store_true', help='gửi file lên site')
    report = group.add_parser('report', help='tra trạng thái xử lý theo SHA-256')
    report.add_argument('sha256')


def execute(args):
    if args.action in ('attach', 'record') and args.id <= 0:
        raise IntakeError('ID bản ghi phải là số dương.')
    if args.action == 'report':
        if not re.fullmatch(r'[a-fA-F0-9]{64}', args.sha256):
            raise IntakeError('SHA-256 phải có 64 ký tự hex.')
        args.sha256 = args.sha256.lower()
    if not sys.stdin.isatty():
        raise IntakeError('Chạy lệnh intake trong terminal tương tác để nhập Application Password.')
    username = os.getenv('DOCSYNC_ERP_USER') or input('Tên đăng nhập WordPress: ').strip()
    password = getpass.getpass('WordPress Application Password: ')
    origin = os.getenv('DOCSYNC_ERP_URL', 'https://ivarvietnam.com')
    output = run(args, IntakeClient(origin, username, password))
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0
