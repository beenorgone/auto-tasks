"""Machine-local workflow settings and bounded GHTK intake."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from tempfile import NamedTemporaryFile
import time
from types import SimpleNamespace
import unicodedata
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import docsync_intake as intake
from docsync_carrier import PAYMENT_NAME, validate_payment_statement
from docsync_layout import GHTK_WORKFLOW_FOLDER, REPORT_TARGETS


LOCAL = 'local_sort'
PICKUP = 'ghtk_pickup'
CARRIER = 'ghtk_carrier'
DRAFT = 'carrier_draft_batches'
FLOW = 'eim_pickup_cashflow'
ATTACH_FLOW = 'erp_attach'
REPORT_TYPES = {PICKUP: 'pickup_cashflow', CARRIER: 'carrier_delivery'}
DEFAULTS = {LOCAL: True, PICKUP: False, CARRIER: False, DRAFT: False}
GHTK_FOLDERS = tuple(dict.fromkeys(map(Path, (GHTK_WORKFLOW_FOLDER, REPORT_TARGETS['ghtk_reports']))))
GHTK_SOURCE = ' / '.join(folder.as_posix() for folder in GHTK_FOLDERS)
PICKUP_NAME = re.compile(r'GHTK_bao_cao_van_hanh_va_dong_tien_\d{2}_\d{2}_S\d+\.xlsx', re.IGNORECASE | re.ASCII)
SETTLE_NS = 20_000_000_000
RETENTION_SECONDS = 60 * 86400
MAX_ATTEMPTS = 4

CATALOG = (
    (LOCAL, 'Phân loại chứng từ trên máy', 'File mới trong vùng tiếp nhận DocSync',
     'Phân loại GHTK, Shopee, PO, hóa đơn, sao kê và các nhóm đang dùng. Hồ sơ chưa rõ tiếp tục chờ duyệt.'),
    (PICKUP, 'GHTK · Đối soát lấy hàng', f'{GHTK_SOURCE} · báo cáo XLSX Vận hành và Dòng tiền',
     'Gửi EIM đối soát lấy hàng. Đơn đủ điều kiện có thể được xếp hàng chuyển sang Đang giao.'),
    (CARRIER, 'GHTK · Đối soát thanh toán', f'{GHTK_SOURCE} · biên bản thanh toán GHTK CSV/XLSX',
     'Tự gửi biên bản để đối soát và tùy chọn dựng lô hóa đơn nháp.'),
    ('finance', 'Hóa đơn, sao kê và báo cáo Shopee', 'PDF/XML, sao kê và báo cáo sàn',
     'Tiếp tục phân loại trên máy. Duyệt riêng nếu bổ sung luồng nhập hoặc ghi giao dịch.'),
)


@contextmanager
def database(root, write=False):
    folder = root / '.docsync'
    path = folder / 'workflows.sqlite3'
    if not write and not path.is_file():
        yield None
        return
    if not root.is_dir() or folder.is_symlink() or path.is_symlink():
        raise OSError('Thư mục trạng thái Workflows không hợp lệ.')
    if write:
        folder.mkdir(exist_ok=True)
    db = sqlite3.connect(path, timeout=3) if write else sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        with db:
            if write:
                db.execute('CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, since_ns INTEGER NOT NULL)')
                db.execute(
                    'CREATE TABLE IF NOT EXISTS attempts (path TEXT PRIMARY KEY, signature TEXT NOT NULL, '
                    'state TEXT NOT NULL, tries INTEGER NOT NULL, next_at INTEGER NOT NULL, '
                    'updated_at INTEGER NOT NULL, outcome TEXT NOT NULL)'
                )
            yield db
    finally:
        db.close()


def settings(root):
    """Read the same settings from the watcher and local web app."""
    result = {key: (enabled, 0) for key, enabled in DEFAULTS.items()}
    with database(root) as db:
        if db is not None:
            for row in db.execute('SELECT id, enabled, since_ns FROM settings LIMIT ?', (len(result),)):
                if row['id'] in result:
                    result[row['id']] = (bool(row['enabled']), row['since_ns'])
    return result


def set_enabled(root, key, enabled, *, draft_batches=None):
    """Start counting eligible new files at the time the user enables a flow."""
    if (key not in DEFAULTS or key == DRAFT or type(enabled) is not bool
            or (draft_batches is not None and (key != CARRIER or type(draft_batches) is not bool))):
        raise ValueError('Workflow hoặc trạng thái không hợp lệ.')
    if key in REPORT_TYPES and enabled:
        if not credential_configured():
            raise ValueError('Lưu tài khoản DocSync trước khi bật gửi tự động.')
        if key == CARRIER:
            desired = settings(root)[DRAFT][0] if draft_batches is None else draft_batches
            client = saved_client()
            allowed, _ = intake.access_policy(client)
            intake.require_carrier_mode(client, allowed, intake.CARRIER_DRAFT if desired else intake.CARRIER_RECONCILE)
    states = [(key, enabled, time.time_ns())]
    if draft_batches is not None:
        states.append((DRAFT, draft_batches, time.time_ns()))
    with database(root, write=True) as db:
        db.executemany(
            'INSERT INTO settings(id, enabled, since_ns) VALUES (?, ?, ?) '
            'ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled, since_ns=excluded.since_ns '
            'WHERE settings.enabled <> excluded.enabled',
            states,
        )


def credential_path(*, for_erp=False):
    config_root = Path(os.getenv('XDG_CONFIG_HOME') or Path.home() / '.config')
    return config_root / 'docsync' / ('erp-attachment.json' if for_erp else 'intake.json')


def load_credential(*, for_erp=False):
    """Read a private, user-owned credential file without following links."""
    path = credential_path(for_erp=for_erp)
    if for_erp and not path.exists() and not path.is_symlink():
        path = credential_path()
    if path.parent.is_symlink():
        raise ValueError('Thư mục tài khoản DocSync phải nằm trên máy hiện tại.')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('File tài khoản DocSync cần thuộc người dùng hiện tại và có quyền 0600.')
        contents = stream.read(4097)
        if len(contents) > 4096:
            raise ValueError('File tài khoản DocSync quá lớn.')
        value = json.loads(contents)
    if (not isinstance(value, dict) or
            not all(isinstance(value.get(key), str) and value[key] for key in ('user', 'password'))):
        raise ValueError('Tài khoản DocSync trên máy chưa hợp lệ.')
    return value


def credential_configured(*, for_erp=False):
    try:
        load_credential(for_erp=for_erp)
    except (OSError, ValueError):
        return False
    return True


def saved_client(*, for_erp=False):
    try:
        credential = load_credential(for_erp=for_erp)
    except (OSError, ValueError):
        raise ValueError('Lưu tài khoản DocSync trước khi gửi file.') from None
    return intake.IntakeClient(intake.DEFAULT_ORIGIN, credential['user'], credential['password'])


def save_credential(root, user, password, *, for_erp=False):
    """Verify scope using a read-only API call, then store the app password privately."""
    if not isinstance(user, str) or not isinstance(password, str):
        raise ValueError('Nhập username và Application Password.')
    user, password = user.strip(), password.strip()
    if not 0 < len(user) <= 100 or not 0 < len(password) <= 128:
        raise ValueError('Nhập username và Application Password hợp lệ.')
    client = intake.IntakeClient(intake.DEFAULT_ORIGIN, user, password)
    allowed, _ = intake.access_policy(client)
    required = {ATTACH_FLOW} if for_erp else {'eim_' + report_type for report_type in REPORT_TYPES.values()}
    if not required.intersection(allowed):
        raise ValueError('Tài khoản cần quyền gắn file ERP trên IVAR.' if for_erp
                         else 'Tài khoản cần ít nhất một quyền đối soát GHTK trên IVAR.')
    path = credential_path(for_erp=for_erp)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('Thư mục tài khoản DocSync cần thuộc người dùng hiện tại và có quyền 0700.')
    if not for_erp:
        for key in REPORT_TYPES:
            set_enabled(root, key, False)
    with NamedTemporaryFile(mode='w', dir=path.parent, delete=False, encoding='utf-8') as stream:
        temporary = Path(stream.name)
        try:
            json.dump({'user': user, 'password': password}, stream)
            stream.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def overview(root):
    states = settings(root)
    configured = credential_configured()
    with database(root) as db:
        rows = [] if db is None else db.execute(
            'SELECT path, state, updated_at, outcome FROM attempts ORDER BY updated_at DESC LIMIT 12'
        ).fetchall()
    return {
        'configured': configured,
        'workflows': [
            {'id': key, 'title': title, 'source': source, 'description': description,
             'enabled': states[key][0] if key in states else None,
             **({'draft_batches': states[DRAFT][0]} if key == CARRIER else {})}
            for key, title, source, description in CATALOG
        ],
        'activity': [dict(row) for row in rows],
    }


def validate_pickup_workbook(stream):
    """Validate the header of the exact snapshot that will be sent to EIM."""
    try:
        with ZipFile(stream) as workbook:
            info = workbook.getinfo('xl/workbook.xml')
            if info.file_size > 256 * 1024:
                raise ValueError
            data = workbook.read(info)
            if b'<!DOCTYPE' in data:
                raise ValueError
            xml = ElementTree.fromstring(data)
            for node in xml.findall('.//{*}sheet'):
                name = unicodedata.normalize('NFKD', node.get('name', '').casefold().replace('đ', 'd'))
                if ''.join(c for c in name if not unicodedata.combining(c)) == 'bc dong tien':
                    return
    except (BadZipFile, ElementTree.ParseError, KeyError, ValueError, RuntimeError, NotImplementedError):
        pass
    raise intake.IntakeError('Cần kiểm tra file XLSX có đúng mẫu GHTK và có sheet BC Dòng tiền.')


def file_signature(info):
    return ':'.join(map(str, (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)))


def report_workflow(name):
    if PICKUP_NAME.fullmatch(name):
        return PICKUP
    if PAYMENT_NAME.fullmatch(name):
        return CARRIER
    return None


def matches_report(relative, info, rules, now_ns, *, before_routing=False):
    key = report_workflow(relative.name)
    if not key or not rules[key][0]:
        return None
    _, since_ns = rules[key]
    expected = relative.parent in GHTK_FOLDERS
    if before_routing:
        expected |= relative.parent in (Path('.'), Path('00_INBOX'))
    changed_at = max(info.st_mtime_ns, info.st_ctime_ns)
    if (expected and stat.S_ISREG(info.st_mode) and min(info.st_ctime_ns, info.st_mtime_ns) > since_ns
            and 0 <= now_ns - changed_at <= RETENTION_SECONDS * 1_000_000_000):
        return key
    return None


def candidates(root, rules, now_ns):
    for relative in GHTK_FOLDERS:
        folder = root / relative
        if folder.is_symlink() or folder.parent.is_symlink() or not folder.is_dir():
            continue
        for file in sorted(folder.iterdir()):
            if not report_workflow(file.name):
                continue
            try:
                info = file.lstat()
            except FileNotFoundError:
                continue
            key = matches_report(file.relative_to(root), info, rules, now_ns)
            if key and now_ns - max(info.st_mtime_ns, info.st_ctime_ns) >= SETTLE_NS:
                yield file, file_signature(info), key


def record(root, relative, signature, state, tries, outcome, next_at=0):
    with database(root, write=True) as db:
        db.execute(
            'INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET '
            'signature=excluded.signature, state=excluded.state, tries=excluded.tries, next_at=excluded.next_at, '
            'updated_at=excluded.updated_at, outcome=excluded.outcome',
            (relative, signature, state, tries, next_at, int(time.time()), outcome),
        )
        db.execute('DELETE FROM attempts WHERE path IN (SELECT path FROM attempts WHERE updated_at < ? LIMIT 20)',
                   (int(time.time()) - RETENTION_SECONDS,))


def process_one(root):
    """Send at most one new GHTK report per watcher pass."""
    rules = settings(root)
    if not any(rules[key][0] for key in REPORT_TYPES):
        return
    with (root / '.docsync' / 'workflows.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        rules = settings(root)
        starts = [rules[key][1] for key in REPORT_TYPES if rules[key][0]]
        if not starts:
            return
        now = time.time_ns()
        with database(root) as db:
            retries = db.execute(
                "SELECT path, updated_at FROM attempts WHERE state = 'retry' AND next_at > ? "
                "AND updated_at >= ? ORDER BY updated_at DESC LIMIT 100",
                (now // 1_000_000_000, min(starts) // 1_000_000_000),
            )
            deferred = set()
            for item in retries:
                flow = report_workflow(Path(item['path']).name)
                if flow and rules[flow][0] and item['updated_at'] >= rules[flow][1] // 1_000_000_000:
                    deferred.add(flow)
            for file, signature, key in candidates(root, rules, now):
                if key in deferred:
                    continue
                relative = file.relative_to(root).as_posix()
                row = db.execute('SELECT signature, state, tries FROM attempts WHERE path = ?', (relative,)).fetchone()
                if row and row['signature'] == signature:
                    if row['state'] in ('done', 'review'):
                        continue
                    tries = row['tries'] + 1
                else:
                    tries = 1
                break
            else:
                return
        if settings(root)[key] == rules[key]:
            submit(root, file, relative, signature, tries, key, rules[key][1], rules[DRAFT][0])


class WorkflowPaused(intake.IntakeError):
    """The user paused or replaced the rule before the upload started."""


def submit(root, file, relative, signature, tries, key, since, draft_batches):
    carrier_mode = None
    if key == CARRIER:
        carrier_mode = intake.CARRIER_DRAFT if draft_batches else intake.CARRIER_RECONCILE

    def validate(stream):
        rules = settings(root)
        if rules[key] != (True, since) or (key == CARRIER and rules[DRAFT][0] != draft_batches):
            raise WorkflowPaused('Đã tạm dừng trước khi gửi lên EIM.')
        if file_signature(file.lstat()) != signature:
            raise WorkflowPaused('File vừa thay đổi. Watcher sẽ chờ file ổn định.')
        if key == PICKUP:
            validate_pickup_workbook(stream)
        else:
            validate_payment_statement(stream, file.suffix.lower())

    record(root, relative, signature, 'running', tries, 'Đang gửi báo cáo lên EIM.')
    try:
        client = saved_client()
        args = SimpleNamespace(action='reconcile', type=REPORT_TYPES[key], file=file, send=True, carrier_mode=carrier_mode)
        result = intake.run(args, client, validate_snapshot=validate)
    except WorkflowPaused as error:
        record(root, relative, signature, 'paused', tries, str(error))
        return
    except intake.IntakeError as error:
        status = error.status
        retry = status in (0, 409, 429) or (status is not None and status >= 500)
        if retry and tries < MAX_ATTEMPTS:
            delay = 3600 if status == 429 else 60 * 2 ** (tries - 1)
            record(root, relative, signature, 'retry', tries, str(error), int(time.time()) + delay)
        else:
            record(root, relative, signature, 'review', tries, str(error))
            if status in (401, 403):
                set_enabled(root, key, False)
        return
    except (OSError, ValueError):
        record(root, relative, signature, 'review', tries, 'Kiểm tra quyền đọc file và tài khoản DocSync.')
        return
    data = result['result']
    if result['duplicate']:
        message = f'EIM đã có báo cáo #{data["report_id"]}.'
    else:
        message = f'Báo cáo #{data["report_id"]} · Khớp {int(data.get("matched", 0))} · Chưa khớp {int(data.get("unmatched", 0))}.'
        if data.get('status') == 'building':
            message += ' EIM đã xếp hàng dựng lô hóa đơn nháp.'
    record(root, relative, signature, 'done', tries, message)
