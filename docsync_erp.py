"""DocSync → IVAR ERP: daily file summary, explicit record/field assignments and push.

DocSync is just one client of the ERP's generic file-intake API (contract:
ivarvietnam theme docs/api/file-intake.md). Transport: HTTPS REST
`/wp-json/ivar/v1/intake/*` (+ eim `/wp-json/eim/v1/intake/*`) with the
Application Password of the person using this machine, approved for the
`erp_file_intake` (or `erp_records_intake`) scope in ERP → AI App Passwords.
Every call runs with that person's ERP permissions; several employees each use
their own credential on their own machine. This module never decides access.

Nothing is pushed automatically: a person assigns each file to a record + field
(CLI or the local web panel), then runs push. Assignments are bound to the file's
SHA-256 — edit the file after assigning and it must be confirmed again. The ERP
also deduplicates by SHA-256 per field, so retrying after a network error never
attaches the same document twice.

Config: `ivar-documents-organizer/erp.json` (git-ignored)
    {"base_url": "https://ivarvietnam.com", "username": "<wp login>",
     "password_file": "~/.config/docsync/erp-app-password"}
The password is read from DOCSYNC_ERP_APP_PASSWORD or from password_file, which
must be readable only by its owner (chmod 600). It is never written to JSON,
logs or the assignment index.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import ssl
import stat
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Iterable

from docsync_paths import work_root

STATE = Path(os.environ.get('STATE_DIR', str(Path(__file__).resolve().parent / 'ivar-documents-organizer')))
CONFIG_NAME = 'erp.json'
ASSIGNMENTS_NAME = 'erp-assignments.json'
MAX_BYTES = 40 * 1024 * 1024
TODAY_LIMIT = 500
SKIP_DIRS = {'.wip', 'duplicates', '.git', '__pycache__'}
TEMP_SUFFIXES = ('.part', '.crdownload', '.tmp', '.swp', '~')
TYPES = {
    'po': 'Đơn mua (PO)', 'ir': 'Phiếu nhập kho', 'qc_test': 'Kiểm nghiệm (QC)', 'tcb': 'Tự công bố (TCB)',
    'seller': 'Nhà cung cấp', 'bom': 'Định mức (BoM)', 'qtbm': 'Quy trình / biểu mẫu', 'pth': 'Phiếu thu hồi',
    'mo': 'Lệnh sản xuất (MO)', 'so': 'Đơn bán (SO)',
}
STATUSES = ('assigned', 'pushed', 'duplicate', 'error')
CLIENT_NAME = 'DocSync/1.1'


class ErpError(RuntimeError):
    """Failure the user can act on; message never contains the credential."""

    def __init__(self, message: str, status: int = 0, code: str = ''):
        super().__init__(message)
        self.status = status
        self.code = code


# ── Suggestions (deterministic; a person always confirms) ──────────────────────

def _plain(text: str) -> str:
    text = unicodedata.normalize('NFD', text.replace('đ', 'd').replace('Đ', 'D'))
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn').lower()


# (pattern on accent-free lowercase name/path, type, field, label)
SUGGESTIONS = (
    (r'\bunc\b|uy nhiem chi|payment|thanh toan', 'po', 'sourcing_order_payments', 'Chứng từ thanh toán PO'),
    (r'packing ?list|\bpl\b', 'po', 'pack_list', 'Packing list PO'),
    (r'bill of lading|\bb/?l\b|van don', 'po', 'bill_of_lading', 'Vận đơn PO'),
    (r'to khai|hai quan|customs|\bco\b form|c/o', 'po', 'sourcing_order_customs_docs', 'Hồ sơ hải quan PO'),
    (r'hop dong|contract|\bpi\b|proforma', 'po', 'contract_po', 'Hợp đồng PO'),
    (r'hoa ?don|invoice|\.xml$', 'po', 'invoices', 'Hoá đơn PO'),
    (r'organic|\btc\b', 'po', 'organic_tc', 'Organic TC'),
    (r'tu cong bo|\btcb\b', 'tcb', 'tcb_ban_chinh_thuc', 'Bản chính thức TCB'),
    (r'\bcoa\b|kiem nghiem|phieu ket qua|test report', 'qc_test', 'qc_test_scan_result_file', 'Bản scan kết quả QC'),
    (r'phieu nhap|\bgrn\b', 'ir', 'inventory_receipt_batch_images', 'Chứng từ phiếu nhập'),
    (r'dinh muc|\bbom\b', 'bom', 'prod_spec_files', 'File định mức'),
    (r'giao hang|\bpod\b|bien ban ban giao', 'so', 'proof_of_delivery', 'Chứng từ giao hàng SO'),
    (r'^ho so po', 'po', 'files', 'File đính kèm PO'),
)
REF = re.compile(r'\b(PO|MO|SO|IR|QC)[\s_.-]*(\d{2,})\b', re.I)


def suggest(relative: str) -> dict | None:
    """First matching ERP type/field + a search hint such as "PO123" from the name."""
    text = _plain(relative)
    name = _plain(Path(relative).name)
    for pattern, kind, field, label in SUGGESTIONS:
        if re.search(pattern, name) or re.search(pattern, text):
            ref = REF.search(Path(relative).name)
            hint = f'{ref[1].upper()}{ref[2]}' if ref else ''
            ref_type = {'PO': 'po', 'MO': 'mo', 'SO': 'so', 'IR': 'ir', 'QC': 'qc_test'}.get(ref[1].upper()) if ref else None
            if ref_type and ref_type != kind and kind == 'po' and ref_type in ('mo', 'so'):
                kind, field, label = ref_type, 'proof_of_delivery', 'Chứng từ giao hàng'
            return {'type': kind, 'field': field, 'label': label, 'query': hint}
    return None


# ── Files touched today ───────────────────────────────────────────────────────

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def safe_file(root: Path, path: Path) -> bool:
    """Regular, non-symlink file inside the work root — the only files that may leave this machine."""
    try:
        return (path.is_file() and not path.is_symlink()
                and path.resolve().is_relative_to(root.resolve())
                and not any(part.startswith('.') for part in path.relative_to(root).parts))
    except (OSError, ValueError):
        return False


def touched_on(path: Path, day: dt.date) -> bool:
    """mtime covers new downloads/edits; ctime covers files the organizer moved today."""
    info = path.stat()
    return any(dt.date.fromtimestamp(stamp) == day for stamp in (info.st_mtime, info.st_ctime))


def walk_today(root: Path, day: dt.date, limit: int = TODAY_LIMIT) -> Iterable[Path]:
    count = 0
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
        for name in sorted(files):
            if name.startswith('.') or name.endswith(TEMP_SUFFIXES):
                continue
            path = Path(directory) / name
            try:
                if path.is_symlink() or not touched_on(path, day):
                    continue
            except OSError:
                continue
            yield path
            count += 1
            if count >= limit:
                return


def today(root: Path | None = None, day: dt.date | None = None, classify: Callable | None = None,
          state: Path | None = None, eim_status: dict | None = None) -> dict:
    """Group today's files by DocSync category, with ERP suggestion + assignment state."""
    root = (root or work_root()).absolute()
    day = day or dt.date.today()
    assignments = load_assignments(state)
    rows = []
    for path in walk_today(root, day):
        relative = path.relative_to(root).as_posix()
        category, reason, target = 'needs_review', 'chưa có rule', None
        if classify is not None:
            try:
                category, _confidence, reason, target, _mode = classify(Path(relative), path)
            except (OSError, ValueError):
                pass
        info = path.stat()
        assignment = assignments.get(relative)
        if assignment and assignment.get('size') == info.st_size and assignment.get('mtime_ns') == info.st_mtime_ns:
            state_label = assignment.get('status', 'assigned')
        elif assignment:
            state_label = 'stale'  # content may have changed after the person chose a record
        else:
            state_label = 'unassigned'
        rows.append({
            'path': relative, 'name': path.name, 'category': category, 'reason': reason, 'target': target,
            'size': info.st_size, 'modified': dt.datetime.fromtimestamp(info.st_mtime).isoformat(timespec='minutes'),
            'too_large': info.st_size > MAX_BYTES, 'suggestion': suggest(relative),
            'erp': state_label, 'assignment': public_assignment(assignment),
            'eim': {k: v for k, v in ((eim_status or {}).get(relative) or {}).items()
                    if k in ('status', 'message', 'report_id', 'type', 'at')} or None,
        })
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row['category'], []).append(row)
    counts = {key: sum(r['erp'] == key for r in rows) for key in ('unassigned', 'assigned', 'stale', 'pushed', 'duplicate', 'error')}
    return {'date': day.isoformat(), 'root': str(root), 'total': len(rows), 'truncated': len(rows) >= TODAY_LIMIT,
            'counts': counts, 'groups': [{'category': key, 'files': value} for key, value in sorted(groups.items())]}


# ── Assignment index (local, atomic) ──────────────────────────────────────────

def _assignments_path(state: Path | None) -> Path:
    return (state or STATE) / ASSIGNMENTS_NAME


def load_assignments(state: Path | None = None) -> dict:
    path = _assignments_path(state)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ErpError(f'Không đọc được {path.name}: {error}') from error
    return data if isinstance(data, dict) else {}


def save_assignments(data: dict, state: Path | None = None) -> None:
    path = _assignments_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.erp-assignments-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def public_assignment(record: dict | None) -> dict | None:
    if not record:
        return None
    keys = ('type', 'id', 'label', 'field', 'field_label', 'ai', 'ai_mode', 'instruction', 'status', 'message',
            'record_url', 'attachment_url', 'updated')
    return {key: record.get(key) for key in keys}


def resolve_path(root: Path, relative: str) -> Path:
    path = (root / relative)
    if Path(relative).is_absolute() or '..' in Path(relative).parts or not safe_file(root, path):
        raise ErpError(f'File không hợp lệ hoặc nằm ngoài thư mục làm việc: {relative}')
    return path


def assign(root: Path, relative: str, kind: str, record_id: int, field: str, *, label: str = '',
           field_label: str = '', ai: bool = False, ai_mode: str = 'summary', instruction: str = '',
           state: Path | None = None) -> dict:
    if kind not in TYPES:
        raise ErpError(f'Loại bản ghi không hỗ trợ: {kind}')
    if int(record_id) <= 0 or not re.fullmatch(r'[A-Za-z0-9_\-]{1,100}', field or ''):
        raise ErpError('Thiếu bản ghi hoặc field đích.')
    path = resolve_path(root, relative)
    info = path.stat()
    if info.st_size > MAX_BYTES:
        raise ErpError(f'File vượt {MAX_BYTES // 1048576} MB: {relative}')
    data = load_assignments(state)
    previous = data.get(relative) or {}
    data[relative] = {
        'sha256': sha256(path), 'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
        'type': kind, 'id': int(record_id), 'label': label[:200], 'field': field, 'field_label': field_label[:200],
        'ai': bool(ai), 'ai_mode': 'analysis' if ai_mode == 'analysis' else 'summary', 'instruction': instruction[:2000],
        'status': 'assigned', 'message': '', 'updated': dt.datetime.now().isoformat(timespec='seconds'),
        'history': (previous.get('history') or [])[-9:],
    }
    save_assignments(data, state)
    return data[relative]


def unassign(relative: str, state: Path | None = None) -> bool:
    data = load_assignments(state)
    if relative not in data:
        return False
    del data[relative]
    save_assignments(data, state)
    return True


# ── HTTP client ───────────────────────────────────────────────────────────────

def load_config(state: Path | None = None, env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    path = (state or STATE) / CONFIG_NAME
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as error:
        raise ErpError(f'Chưa cấu hình kết nối ERP: tạo {path} (xem GUIDE-DOCSYNC.md §ERP).') from error
    except ValueError as error:
        raise ErpError(f'{path.name} không phải JSON hợp lệ.') from error
    base = str(config.get('base_url', '')).rstrip('/')
    parsed = urllib.parse.urlparse(base)
    local = parsed.hostname in ('127.0.0.1', 'localhost')
    if not parsed.hostname or (parsed.scheme != 'https' and not (local and parsed.scheme == 'http')):
        raise ErpError('base_url phải là https:// (App Password chỉ chạy qua HTTPS).')
    if 'password' in config:
        raise ErpError('Không để mật khẩu trong erp.json — dùng password_file (chmod 600) hoặc DOCSYNC_ERP_APP_PASSWORD.')
    password = env.get('DOCSYNC_ERP_APP_PASSWORD', '')
    if not password and config.get('password_file'):
        secret = Path(str(config['password_file'])).expanduser()
        try:
            mode = secret.stat().st_mode
        except OSError as error:
            raise ErpError(f'Không đọc được password_file: {secret}') from error
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ErpError(f'password_file phải chỉ chủ sở hữu đọc được: chmod 600 {secret}')
        password = secret.read_text(encoding='utf-8').strip()
    username = str(config.get('username', '')).strip()
    if not username or not password:
        raise ErpError('Thiếu username hoặc App Password cho ERP.')
    return {'base_url': base, 'username': username, 'password': password.replace(' ', ''),
            'timeout': int(config.get('timeout', 60))}


class ErpClient:
    def __init__(self, config: dict, opener: Callable | None = None):
        self.site = config['base_url']
        self.base = config['base_url'] + '/wp-json/ivar/v1/intake'
        token = base64.b64encode(f"{config['username']}:{config['password']}".encode()).decode()
        self._auth = 'Basic ' + token
        self.timeout = config.get('timeout', 60)
        self._open = opener or (lambda request, timeout: urllib.request.urlopen(
            request, timeout=timeout, context=ssl.create_default_context()))

    def _call(self, method: str, path: str, query: dict | None = None, body: bytes | None = None,
              content_type: str = '') -> dict:
        # '/wp-json/…' = đường dẫn tuyệt đối trên site (vd plugin eim); còn lại tương đối với /ivar/v1/intake.
        root = self.site if path.startswith('/wp-json/') else self.base
        url = root + path + ('?' + urllib.parse.urlencode(query) if query else '')
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header('Authorization', self._auth)
        request.add_header('Accept', 'application/json')
        request.add_header('User-Agent', CLIENT_NAME)
        request.add_header('X-Intake-Client', CLIENT_NAME)  # chỉ để server ghi nhật ký; xác thực là App Password
        if content_type:
            request.add_header('Content-Type', content_type)
        try:
            with self._open(request, self.timeout) as response:
                return json.loads(response.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode('utf-8') or '{}')
                message = payload.get('message') or payload.get('code') or ''
                code = str(payload.get('code') or '')
            except ValueError:
                message, code = '', ''
            hint = {401: 'Sai username/App Password.', 403: 'App Password chưa được duyệt phạm vi DocSync, hoặc không có quyền sửa bản ghi.'}
            raise ErpError(f'ERP HTTP {error.code}: {message or hint.get(error.code, error.reason)}', error.code, code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ErpError(f'Không kết nối được ERP: {getattr(error, "reason", error)}') from None
        except ValueError:
            raise ErpError('ERP trả về dữ liệu không phải JSON.') from None

    def flows(self) -> list:
        """Luồng tự động người dùng này đã được người duyệt bảo mật cho phép (server quyết định)."""
        return self._call('GET', '/flows').get('flows', [])

    def types(self) -> dict:
        return self._call('GET', '/types')

    def targets(self, kind: str, query: str = '') -> list:
        return self._call('GET', '/targets', {'type': kind, 'q': query[:100]}).get('records', [])

    def record(self, kind: str, record_id: int) -> dict:
        return self._call('GET', '/record', {'type': kind, 'id': int(record_id)})

    def upload(self, path: Path, kind: str, record_id: int, field: str, digest: str,
               ai: bool, ai_mode: str, instruction: str) -> dict:
        fields = {'type': kind, 'id': str(int(record_id)), 'field': field, 'sha256': digest,
                  'ai': '1' if ai else '0', 'ai_mode': ai_mode, 'instruction': instruction}
        return self._multipart('/upload', fields, path)

    def eim_report(self, path: Path, digest: str, kind: str = '', carrier: str = 'GHTK') -> dict:
        """Đẩy file đối soát GHTK lên plugin eim (hub) — đối soát + lô HĐ nháp + Telegram ở phía ERP."""
        fields = {'sha256': digest, 'type': kind, 'carrier': carrier}
        return self._multipart('/wp-json/eim/v1/intake/reconcile-report', fields, path)

    def _multipart(self, endpoint: str, fields: dict, path: Path) -> dict:
        boundary = 'docsync-' + uuid.uuid4().hex
        parts = []
        for name, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        # PHP's multipart parser reads raw UTF-8 in filename="" and ignores filename*.
        filename = re.sub(r'["\\\r\n]', '_', path.name)
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                      'Content-Type: application/octet-stream\r\n\r\n').encode())
        parts.append(path.read_bytes())
        parts.append(f'\r\n--{boundary}--\r\n'.encode())
        return self._call('POST', endpoint, body=b''.join(parts), content_type=f'multipart/form-data; boundary={boundary}')


def push(root: Path, client: ErpClient, paths: Iterable[str] | None = None, dry_run: bool = False,
         state: Path | None = None) -> list[dict]:
    """Upload confirmed assignments. Stops nothing on one failure; each result is recorded."""
    data = load_assignments(state)
    wanted = list(paths) if paths else [key for key, value in data.items() if value.get('status') in ('assigned', 'error')]
    results = []
    for relative in wanted:
        record = data.get(relative)
        if not record:
            results.append({'path': relative, 'status': 'skipped', 'message': 'Chưa gán bản ghi ERP.'})
            continue
        try:
            path = resolve_path(root, relative)
            if sha256(path) != record['sha256']:
                raise ErpError('File đã đổi nội dung sau khi gán — mở lại và xác nhận bản ghi.')
            if dry_run:
                results.append({'path': relative, 'status': 'would_push',
                                'message': f"{record['type']} #{record['id']} › {record['field']}" + (' + AI' if record['ai'] else '')})
                continue
            response = client.upload(path, record['type'], record['id'], record['field'], record['sha256'],
                                     record['ai'], record['ai_mode'], record['instruction'])
            status = response.get('status', 'pushed')
            status = 'duplicate' if status == 'duplicate' else 'pushed'
            ai = response.get('ai') or {}
            message = ('Đã có sẵn trong field (cùng SHA-256)' if status == 'duplicate'
                       else 'Đã tải lên' + (' · AI đang ' + ('phân tích' if ai.get('mode') == 'analysis' else 'tóm tắt') if ai.get('queued') else ''))
            record.update(status=status, message=message, record_url=(response.get('record') or {}).get('url', ''),
                          attachment_url=response.get('attachment_url', ''), pushed_at=dt.datetime.now().isoformat(timespec='seconds'))
        except ErpError as error:
            record.update(status='error', message=str(error))
        record['updated'] = dt.datetime.now().isoformat(timespec='seconds')
        record['history'] = (record.get('history') or [])[-9:] + [{'at': record['updated'], 'status': record['status'], 'message': record['message']}]
        results.append({'path': relative, 'status': record['status'], 'message': record['message']})
        save_assignments(data, state)  # after each file: a crash never loses a finished upload
    return results
