#!/usr/bin/env python3
"""DocSync → EIM: tự đẩy file đối soát GHTK lên hub kế toán qua API tiếp nhận của eim
(`/wp-json/eim/v1/intake/reconcile-report`, App Password của người dùng máy này — DocSync chỉ là một client).

Hai loại file, nhận theo tên (EIM kiểm lại cả nội dung):
  - BC dòng tiền GHTK (TIỀN HÀNG LẤY THÀNH CÔNG) → "Đối soát lấy hàng" (pickup_cashflow)
  - Biên bản đối soát thanh toán GHTK            → "Đối soát thanh toán VC" (carrier_delivery)
    → EIM dựng lô hóa đơn NHÁP theo auto rules + bot ERP nhắn Telegram để vào review.

Chạy bằng timer (systemd/docsync-eim.timer) hoặc `docsync eim sync`. Mỗi file:
  1. chỉ .xlsx/.csv đã đứng yên ≥ 20 giây (tải xong), nằm trong gốc làm việc, không symlink;
  2. SHA-256 đã đẩy (state local) → bỏ qua; EIM cũng tự chặn file đã có trên database (cùng
     SHA, hoặc cùng loại + ngày + tên đã upload tay) và trả `duplicate`;
  3. lỗi mạng / 5xx / 409 → thử lại có giãn cách (≤ 6 lần); 4xx khác (sai định dạng, thiếu quyền)
     → `rejected`, không thử lại tới khi file đổi nội dung.
Không xác nhận tiền: EIM để nút "Đã nhận thanh toán" cho người duyệt.

TÙY CHỌN, MẶC ĐỊNH TẮT — hai lớp, cả hai phải bật:
  1. máy này: `"auto_flows": ["eim_pickup_cashflow", "eim_carrier_delivery"]` trong ivar-documents-organizer/erp.json
     (chỉ nhóm file có luồng trong danh sách mới được đẩy; không khai = không đẩy gì);
  2. server: người duyệt bảo mật bật luồng đó cho tài khoản này (ERP → AI App Passwords → Luồng tự động).
     Server chưa duyệt → 403 `intake_flow_disabled` → `blocked`, thử lại sau 6 giờ, không đẩy dồn.
Dựng lô nháp / tự chuyển Đã hoàn thành là luồng riêng ở server, client không bật được.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Iterable

import docsync_erp as ERP
from docsync_paths import work_root

STATE_NAME = 'eim-intake.json'
SETTLE_SECONDS = 20
RETRY_DELAYS = (60, 300, 900, 1800, 3600, 7200)
MAX_BYTES = 20 * 1024 * 1024
FOLDERS = ('', '00_INBOX', '00_INBOX/Hoa don', '20_BAO_CAO/GHTK', 'AT. GHTK reports')
KEEP = 400
BLOCKED_RECHECK = 6 * 3600
FLOWS = {'pickup_cashflow': 'eim_pickup_cashflow', 'carrier_delivery': 'eim_carrier_delivery'}


def _plain(text: str) -> str:
    text = unicodedata.normalize('NFD', text.replace('đ', 'd').replace('Đ', 'D'))
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn').lower()


def detect(name: str) -> str | None:
    """'carrier_delivery' | 'pickup_cashflow' | '' (GHTK nhưng để EIM đọc nội dung) | None (không phải file GHTK)."""
    if not name.lower().endswith(('.xlsx', '.csv')):
        return None
    n = _plain(name)
    if re.search(r'bien[ _-]?ban[ _-]?doi[ _-]?soat|doi[ _-]?soat[ _-]?thanh[ _-]?toan', n):
        return 'carrier_delivery'
    if re.search(r'dong[ _-]?tien|tien[ _-]?hang[ _-]?lay|cash[ _-]?flow', n):
        return 'pickup_cashflow'
    if re.search(r'(?:^|[^a-z])ghtk', n):
        return ''
    return None


def candidates(root: Path) -> Iterable[tuple[Path, str]]:
    seen = set()
    for folder in FOLDERS:
        base = root / folder if folder else root
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            kind = detect(path.name)
            if kind is None or path in seen or not ERP.safe_file(root, path):
                continue
            seen.add(path)
            yield path, kind


def load_state(state_dir: Path) -> dict:
    try:
        data = json.loads((state_dir / STATE_NAME).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def save_state(state_dir: Path, data: dict) -> None:
    if len(data) > KEEP:
        data = dict(sorted(data.items(), key=lambda item: item[1].get('at', ''))[-KEEP:])
    state_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=state_dir, prefix='.eim-intake-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state_dir / STATE_NAME)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def due(record: dict | None, now: float) -> bool:
    if not record:
        return True
    if record.get('status') not in ('retry', 'blocked'):
        return False  # sent / duplicate / rejected: xong với đúng bản file này
    return now >= float(record.get('next_at', 0))


def wanted(kind: str, flows: set[str]) -> bool:
    """Nhóm file có nằm trong danh sách luồng máy này cho phép không ('' = để server đọc nội dung)."""
    if kind == '':
        return bool(flows & set(FLOWS.values()))
    return FLOWS.get(kind, '') in flows


def sync(root: Path, client, state_dir: Path, now: float | None = None, log=print,
         flows: set[str] | None = None) -> list[dict]:
    """Một lượt quét. Người gọi giữ organizer.lock. Trả danh sách kết quả của lượt này."""
    now = time.time() if now is None else now
    flows = local_flows(state_dir) if flows is None else flows
    if not flows:
        return []
    state = load_state(state_dir)
    results = []
    for path, kind in candidates(root):
        if not wanted(kind, flows):
            continue
        info = path.stat()
        if now - max(info.st_mtime, info.st_ctime) < SETTLE_SECONDS:
            continue  # còn đang tải / đang được organizer chuyển
        if info.st_size <= 0 or info.st_size > MAX_BYTES:
            continue
        digest = ERP.sha256(path)
        record = state.get(digest)
        if not due(record, now):
            continue
        relative = path.relative_to(root).as_posix()
        attempts = int((record or {}).get('attempts', 0)) + 1
        entry = {'path': relative, 'name': path.name, 'kind': kind, 'attempts': attempts,
                 'at': dt.datetime.fromtimestamp(now).isoformat(timespec='seconds')}
        try:
            response = client.eim_report(path, digest, kind)
            entry.update(status='duplicate' if response.get('status') == 'duplicate' else 'sent',
                         remote=response.get('status', ''), type=response.get('type', kind),
                         report_id=response.get('report_id'), report_date=response.get('report_date', ''),
                         message=_describe(response))
        except ERP.ErpError as error:
            if error.code == 'intake_flow_disabled':
                # Chưa được duyệt ở server: không phải lỗi file — kiểm lại thưa, không dồn request.
                entry.update(status='blocked', next_at=now + BLOCKED_RECHECK, attempts=0, message=str(error))
                state[digest] = entry
                save_state(state_dir, state)
                results.append(entry)
                log(f"EIM: {relative} — blocked · {entry['message']}")
                continue
            transient = error.status in (0, 409, 429) or error.status >= 500
            if transient and attempts <= len(RETRY_DELAYS):
                entry.update(status='retry', next_at=now + RETRY_DELAYS[attempts - 1], message=str(error))
            else:
                entry.update(status='rejected', message=str(error))
        state[digest] = entry
        save_state(state_dir, state)  # sau mỗi file: dừng giữa chừng không mất kết quả đã gửi
        results.append(entry)
        log(f"EIM: {relative} — {entry['status']} · {entry['message']}")
    return results


def _describe(r: dict) -> str:
    if r.get('status') == 'duplicate':
        return f"Đã có trên EIM (báo cáo #{r.get('report_id')}) — không đối soát lại"
    label = 'thanh toán VC' if r.get('type') == 'carrier_delivery' else 'lấy hàng'
    text = f"Đối soát {label} #{r.get('report_id')} · khớp {r.get('matched', 0)}, không khớp {r.get('unmatched', 0)}"
    if r.get('type') == 'carrier_delivery':
        text += (' · tự dựng lô nháp đang tắt cho tài khoản này' if r.get('batches_disabled')
                 else ' · đang dựng lô HĐ nháp, Telegram sẽ báo khi xong')
    return text


def status_for_paths(state_dir: Path) -> dict[str, dict]:
    """Đường dẫn → trạng thái gần nhất (cho bảng 'Hôm nay')."""
    out = {}
    for record in sorted(load_state(state_dir).values(), key=lambda r: r.get('at', '')):
        out[record.get('path', '')] = record
    return out


def local_flows(state_dir: Path) -> set[str]:
    """Luồng máy này được phép tự đẩy (erp.json → auto_flows). Không khai / sai = rỗng = tắt."""
    try:
        config = json.loads((state_dir / ERP.CONFIG_NAME).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    raw = config.get('auto_flows', [])
    return {f for f in raw if f in FLOWS.values()} if isinstance(raw, list) else set()


def enabled(state_dir: Path) -> bool:
    return bool(local_flows(state_dir))


def main() -> int:
    state_dir = ERP.STATE
    if not enabled(state_dir):
        return 0
    with (state_dir / 'organizer.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # organizer đang chuyển file; timer thử lại
        client = ERP.ErpClient(ERP.load_config(state_dir))
        sync(work_root(), client, state_dir)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ERP.ErpError as error:
        print(f'EIM DocSync: {error}', flush=True)
        raise SystemExit(1)
