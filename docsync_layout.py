"""Shared work-topic layout. Workflow state lives in Kanban, never root buckets."""
from pathlib import Path

TOPICS = {
    'san_xuat': 'Sản xuất', 'mua_hang': 'Mua hàng - dịch vụ',
    'giao_van': 'Giao vận', 'kinh_doanh': 'Kinh doanh', 'qc': 'QC',
    'ke_toan': 'Kế toán - tài chính', 'phap_ly': 'Pháp Lý',
}
INVOICE_INTAKE = 'Kế toán - tài chính/Hóa đơn'
INVOICE_ARCHIVE = 'hoa don dau vao'
UNC_ARCHIVE = 'ngan hang/UNC'
BANK_ARCHIVE = 'ngan hang/sao ke'
META_ARCHIVE = 'chi phi Facebook'
META_UNC = 'UNC'
META_INVOICE = 'hoa don'
PURCHASE_ARCHIVE = 'ho so mua vao'
PO_REPORT = 'Mua hàng - dịch vụ/PO'
MO_REPORT = 'Sản xuất/Lệnh sản xuất'
DUPLICATES = 'duplicates'
AUDIT_DIRECTORY = '.docsync/Nhật ký'
TECH_DIRECTORY = '.docsync/Cách ly kỹ thuật'
GHTK_WORKFLOW_FOLDER = 'Giao vận/GHTK'
REPORT_TARGETS = {
    'vat': 'Kế toán - tài chính/VAT',
    'shopee_reports': 'Giao vận/Shopee',
    'ghtk_reports': GHTK_WORKFLOW_FOLDER,
    'bank_statements': 'Kế toán - tài chính/Sao kê',
}
CATEGORY_TARGETS = {
    **REPORT_TARGETS,
    'finance_ops': TOPICS['ke_toan'], 'quality_regulatory': TOPICS['qc'],
    'packaging': 'Sản xuất/Bao bì', 'production': TOPICS['san_xuat'],
    'commerce': TOPICS['kinh_doanh'], 'legal_corporate': TOPICS['phap_ly'],
    'purchasing': TOPICS['mua_hang'], 'sourcing_logistics': 'Giao vận/Chứng từ',
    'technical': TECH_DIRECTORY,
}
B2B_PRICES = 'Kinh doanh/B2B/Bang gia'
B2B_RECONCILIATION = 'Kinh doanh/B2B/Doi soat cong no'
LAYOUT_MOVES = {
    '00_INBOX/Hoa don': INVOICE_INTAKE,
    '10_HO_SO/Ke toan': TOPICS['ke_toan'],
    '10_HO_SO/Chat luong': TOPICS['qc'],
    '10_HO_SO/Bao bi': CATEGORY_TARGETS['packaging'],
    '10_HO_SO/Kinh doanh': TOPICS['kinh_doanh'],
    '10_HO_SO/Nhap hang': TOPICS['mua_hang'],
    '10_HO_SO/Phap ly': TOPICS['phap_ly'],
    '20_BAO_CAO/VAT': REPORT_TARGETS['vat'],
    '20_BAO_CAO/Shopee': REPORT_TARGETS['shopee_reports'],
    '20_BAO_CAO/GHTK': REPORT_TARGETS['ghtk_reports'],
    '20_BAO_CAO/Sao ke': REPORT_TARGETS['bank_statements'],
    '20_BAO_CAO/Nhap hang': PO_REPORT,
    '80_THAM_CHIEU/Bieu mau/Sao ke': 'Kế toán - tài chính/Biểu mẫu/Sao kê',
    '80_THAM_CHIEU/Quy chuan': 'QC/Quy chuẩn',
    '90_CACH_LY_KY_THUAT': TECH_DIRECTORY,
    '99_NHAT_KY': AUDIT_DIRECTORY,
}


def topic_path(relative):
    for old, new in sorted(LAYOUT_MOVES.items(), key=lambda pair: len(pair[0]), reverse=True):
        if relative == old or relative.startswith(old + '/'):
            return new + relative[len(old):]
    return relative


DIRECTORIES = (
    *TOPICS.values(), INVOICE_INTAKE, *CATEGORY_TARGETS.values(), B2B_PRICES,
    B2B_RECONCILIATION, 'Kế toán - tài chính/Biểu mẫu/Sao kê',
    'QC/Quy chuẩn', AUDIT_DIRECTORY, 'Others',
)
SCOPES = tuple(TOPICS.values())
# Keep transition scopes readable until an explicit audited migration removes them.
LEGACY_SCOPES = (
    '00_INBOX', '10_HO_SO', '20_BAO_CAO', '80_THAM_CHIEU', '90_CACH_LY_KY_THUAT',
    'AT. GHTK reports', 'AT. B2', 'AT. VAT', 'AT. Shopee reports',
    'AT. QC reports', 'AT. Sourcing contracts', 'AT. Invoice intake', 'AT. SAOKE',
    'WIP ke toan thue', 'Review Label', 'ho so danh gia CUC 20260421',
    'Tự công bố sản phẩm trong nước-20260607T192041Z-3-001',
)
OLD_TARGETS = {
    'vat': ('AT.VAT', 'AT. VAT'),
    'shopee_reports': ('AT.Shopee reports', 'AT. Shopee reports'),
    'ghtk_reports': ('AT.GHTK reports', 'AT. GHTK reports'),
    'bank_statements': ('AT.SAOKE', 'AT. SAOKE'),
    'finance_ops': ('IVAR/Accounting - Ke toan/Data - Du lieu',),
    'quality_regulatory': ('IVAR/QC - Quan ly chat luong',),
    'packaging': ('IVAR/Manufacturing - San xuat/In an',),
    'commerce': ('IVAR/Marketing-Sales-CS',),
    'legal_corporate': ('IVAR/Legal - Phap ly',),
    'sourcing_logistics': ('IVAR/Sourcing - Nhap hang/Logistics',),
    'technical': ('2026/91_TECH_QUARANTINE', '91_TECH_QUARANTINE'),
}
for _old, _new in LAYOUT_MOVES.items():
    for _category, _target in CATEGORY_TARGETS.items():
        if _new == _target:
            OLD_TARGETS[_category] = (*OLD_TARGETS.get(_category, ()), _old)
# The legacy combined sourcing/logistics category used the purchase folder.
OLD_TARGETS['sourcing_logistics'] += ('10_HO_SO/Nhap hang',)
FOLDER_CATEGORIES = {
    **{target: category for category, target in CATEGORY_TARGETS.items()},
    B2B_PRICES: 'commerce', B2B_RECONCILIATION: 'commerce',
    INVOICE_INTAKE: 'finance_ops',
    'Kế toán - tài chính/Biểu mẫu/Sao kê': 'bank_statements',
    'QC/Quy chuẩn': 'quality_regulatory',
    TOPICS['giao_van']: 'sourcing_logistics',
}
for _old, _new in LAYOUT_MOVES.items():
    if _new in FOLDER_CATEGORIES:
        FOLDER_CATEGORIES[_old] = FOLDER_CATEGORIES[_new]

def within(relative, folder):
    return relative == folder or relative.startswith(folder + '/')

def is_filed_report(relative):
    return any(within(str(relative), folder) for folder in REPORT_TARGETS.values())

def is_intake(relative):
    return len(Path(relative).parts) == 1 or within(str(relative), '00_INBOX') or within(str(relative), INVOICE_INTAKE)

def approved_target(root, target, *, configured=False):
    if not isinstance(target, str) or not target or any(ord(char) < 32 for char in target):
        return False
    allowed = target in REPORT_TARGETS.values()
    if configured:
        allowed |= any(within(target, topic) for topic in TOPICS.values()) and not within(target, INVOICE_INTAKE)
    if not allowed or Path(target).as_posix() != target or any(part.startswith('.') for part in Path(target).parts):
        return False
    try:
        candidate = safe_directory(root, target)
        return all(not parent.exists() or parent.is_dir() for parent in (candidate, *candidate.parents))
    except (OSError, ValueError, RuntimeError):
        return False


def safe_directory(root, relative):
    root = Path(root)
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or root.is_symlink():
        raise ValueError(f'Unsafe directory: {relative}')
    target = root / rel
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Directory outside year root: {relative}')
    if any(root.joinpath(*rel.parts[:i]).is_symlink() for i in range(1, len(rel.parts) + 1)):
        raise ValueError(f'Symlink directory: {relative}')
    return target


def system_file_routes(paths):
    """Describe the built-in file operations using the organizer's effective paths."""
    accounting = Path(paths['IVAR_DATA'])
    work = Path(paths['DOCSYNC_ROOT'])
    accounting_period = '<năm>/<tháng>'
    intake_sources = [paths[key] for key in ('DOCSYNC_DOCUMENTS_SCAN', 'DOCSYNC_DOWNLOADS_SCAN') if paths[key]]

    def operation(name, condition, *destinations, action='Chuyển file'):
        return {'name': name, 'condition': condition, 'action': action,
                'destinations': [str(destination) for destination in destinations]}

    return [
        {'title': 'Kế toán và ngân hàng', 'operations': [
            operation('Hóa đơn đầu vào PDF/XML',
                      'Nhận diện thông tin hóa đơn từ PDF hoặc XML. PDF có thể mang theo XML cùng tên. Thiếu kỳ thì đưa vào gốc kho hóa đơn.',
                      accounting / INVOICE_ARCHIVE / accounting_period / '<nhà cung cấp và MST>',
                      accounting / INVOICE_ARCHIVE),
            operation('Ủy nhiệm chi (UNC)',
                      'Nhận diện UNC theo tên hoặc nội dung. Dùng kỳ đọc được từ chứng từ, hoặc kỳ hiện tại nếu thiếu.',
                      accounting / UNC_ARCHIVE / accounting_period),
            operation('Bản sao UNC cho chi phí Meta',
                      'Khi xử lý UNC nhận diện là chi phí Meta/Facebook, lưu thêm một bản ở kho chi phí.',
                      accounting / META_ARCHIVE / '<năm>' / META_UNC, action='Sao chép thêm'),
            operation('Sao kê Saokenhanh',
                      'Tên file dạng saokenhanh-DD.MM.YYYY-DD.MM.YYYY-… và ngày bắt đầu hợp lệ. Đích cùng tên được giữ nguyên.',
                      accounting / BANK_ARCHIVE / '<năm>'),
        ]},
        {'title': 'Tiếp nhận và hồ sơ', 'operations': [
            operation('Hồ sơ mua vào ZIP',
                      'ZIP mang mã PO, ngày hợp lệ và nhóm nhà cung cấp xác định được từ lịch sử hoặc quyết định đã duyệt.',
                      accounting / PURCHASE_ARCHIVE / '<năm>/<nhap khau hoặc trong nuoc>/<tháng>'),
            operation('Tài liệu PO rời', 'Tên chứa mã PO. Nếu đích có file cùng tên, giữ nguồn để kiểm tra.',
                      work / PO_REPORT),
            operation('Tài liệu MO rời', 'Tên chứa mã MO. Nếu trùng tên đích, dùng tên có mã SHA-256.',
                      work / MO_REPORT),
            operation('File từ thư mục quét bổ sung',
                      ('File còn lại sau các bước nhận diện trong ' + ' · '.join(intake_sources) + '.')
                      if intake_sources else 'Chưa đặt thư mục quét bổ sung trong Cài đặt thư mục.',
                      work),
        ]},
        {'title': 'Bản trùng và dọn file', 'operations': [
            operation('Hóa đơn PDF/XML trùng',
                      'Bản lưu hiện có khớp dung lượng và SHA-256. Đổi tên bản nhận vào với hậu tố _duplicate.',
                      '<cùng thư mục nguồn>', action='Đổi tên'),
            operation('UNC trùng', 'Bản lưu hiện có khớp dung lượng và SHA-256. Giữ bản trùng để xem lại.',
                      work / DUPLICATES),
            operation('File .log ở gốc WIP', 'Organizer dọn file .log nằm trực tiếp ở thư mục WIP.',
                      action='Xóa file'),
        ]},
        {'title': 'Khi yêu cầu quét lại kho', 'operations': [
            operation('Quét lại PDF hóa đơn và UNC',
                      'Chạy với --rescan cho kho hóa đơn và UNC, hoặc --rescan-unc cho UNC. Tính lại tên và kỳ từ chứng từ.',
                      accounting / INVOICE_ARCHIVE / accounting_period,
                      accounting / UNC_ARCHIVE / accounting_period, action='Đổi tên hoặc chuyển lại'),
            operation('Quét lại PDF chi phí Meta',
                      'Chạy với --rescan cho hóa đơn và UNC chi phí Meta, hoặc --rescan-unc cho UNC chi phí Meta.',
                      accounting / META_ARCHIVE / '<năm>' / META_INVOICE,
                      accounting / META_ARCHIVE / '<năm>' / META_UNC, action='Đổi tên hoặc chuyển lại'),
        ]},
    ]


if __name__ == '__main__':
    import shlex
    for key, value in {
        'INVOICE_INTAKE_REL': INVOICE_INTAKE, 'INVOICE_ARCHIVE_REL': INVOICE_ARCHIVE,
        'UNC_ARCHIVE_REL': UNC_ARCHIVE, 'BANK_ARCHIVE_REL': BANK_ARCHIVE, 'META_ARCHIVE_REL': META_ARCHIVE,
        'META_UNC_REL': META_UNC, 'META_INVOICE_REL': META_INVOICE, 'PURCHASE_ARCHIVE_REL': PURCHASE_ARCHIVE,
        'PO_REPORT_REL': PO_REPORT, 'MO_REPORT_REL': MO_REPORT, 'DUPLICATES_REL': DUPLICATES,
    }.items():
        print(key + '=' + shlex.quote(value))
