"""Canonical year-local folder layout. Folder names describe content, not workflow state."""
from pathlib import Path

REPORT_TARGETS = {
    'vat': '20_BAO_CAO/VAT',
    'shopee_reports': '20_BAO_CAO/Shopee',
    'ghtk_reports': '20_BAO_CAO/GHTK',
    'bank_statements': '20_BAO_CAO/Sao ke',
}
CATEGORY_TARGETS = {
    **REPORT_TARGETS,
    'finance_ops': '10_HO_SO/Ke toan',
    'quality_regulatory': '10_HO_SO/Chat luong',
    'packaging': '10_HO_SO/Bao bi',
    'commerce': '10_HO_SO/Kinh doanh',
    'legal_corporate': '10_HO_SO/Phap ly',
    'sourcing_logistics': '10_HO_SO/Nhap hang',
    'technical': '90_CACH_LY_KY_THUAT',
}
B2B_PRICES = '10_HO_SO/Kinh doanh/B2B/Bang gia'
B2B_RECONCILIATION = '10_HO_SO/Kinh doanh/B2B/Doi soat cong no'
DIRECTORIES = (
    '00_INBOX/Hoa don', *CATEGORY_TARGETS.values(), B2B_PRICES,
    B2B_RECONCILIATION, '80_THAM_CHIEU/Bieu mau/Sao ke',
    '80_THAM_CHIEU/Quy chuan', '99_NHAT_KY', 'Others',
)
SCOPES = ('00_INBOX', '10_HO_SO', '20_BAO_CAO', '80_THAM_CHIEU', '90_CACH_LY_KY_THUAT')
# Keep transition scopes readable until an explicit audited migration removes them.
LEGACY_SCOPES = (
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
FOLDER_CATEGORIES = {
    **{target: category for category, target in CATEGORY_TARGETS.items()},
    B2B_PRICES: 'commerce', B2B_RECONCILIATION: 'commerce',
    '00_INBOX/Hoa don': 'finance_ops',
    '80_THAM_CHIEU/Bieu mau/Sao ke': 'bank_statements',
    '80_THAM_CHIEU/Quy chuan': 'quality_regulatory',
}

def within(relative, folder):
    return relative == folder or relative.startswith(folder + '/')

def is_filed_report(relative):
    return any(within(str(relative), folder) for folder in REPORT_TARGETS.values())

def is_intake(relative):
    return len(Path(relative).parts) == 1 or within(str(relative), '00_INBOX')

def approved_target(root, target):
    if target not in REPORT_TARGETS.values():
        return False
    root = Path(root)
    candidate = root / target
    # Reject symlink ancestors, including symlinks resolving inside the root.
    return (not root.is_symlink() and candidate.resolve().is_relative_to(root.resolve())
            and not any(p.is_symlink() for p in [candidate, candidate.parent]))


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
