import importlib.util
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("wip_organizer", Path(__file__).with_name("wip-organizer.py"))
WIP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WIP)


class WipOrganizerTests(unittest.TestCase):
    def test_default_scopes_include_automation_folders(self):
        self.assertIn("AT.GHTK reports", WIP.DEFAULT_SCOPES)
        self.assertIn("AT.B2", WIP.DEFAULT_SCOPES)
        self.assertIn("AT.VAT", WIP.DEFAULT_SCOPES)
        self.assertIn("AT.Shopee reports", WIP.DEFAULT_SCOPES)
        self.assertIn("AT.QC reports", WIP.DEFAULT_SCOPES)
        self.assertIn("AT.Sourcing contracts", WIP.DEFAULT_SCOPES)
        self.assertIn("AT.Invoice intake", WIP.DEFAULT_SCOPES)

    def test_deterministic_rules(self):
        for path, category in [
            ("GHTK_bao_cao_van_hanh_va_dong_tien_21_08_S12734516.xlsx", "finance_ops"),
            ("CHI_TIET_CONG_NO_PHAI_TRA FUJAN.xlsx", "finance_ops"),
            ("iv-mtt-batch36-20260819-163502.xlsx", "vat"),
            ("Order.all.20260701_20260731.xlsx", "shopee_reports"),
            ("AT.QC reports/743-2026-00172508.pdf", "quality_regulatory"),
            ("Review Label/2026.05.14/Print Label Organic Matcha Powder.pdf", "packaging"),
            ("ho so danh gia CUC 20260421/Mau nhat ky luu mau.pdf", "quality_regulatory"),
            ("Shopee_mass_upload_2026-08-17_100629.xlsx", "commerce"),
            ("ivarvietnam.com-1787197766379.log", "technical"),
            ("mau-giay-uy-quyen-ca-nhan-cho-ca-nhan_2003225702.docx", "legal_corporate"),
            ("HBL-LSUHPG26071866QDS (3).pdf", "sourcing_logistics"),
            ("AT.Sourcing contracts/DRAFT SC8965 Bioway.docx", "sourcing_logistics"),
            ("log.adff603fbfbd.txt", "technical"),
        ]:
            actual, confidence, _, _ = WIP.classify(Path(path), WIP.DEFAULT_RULES)
            self.assertEqual(category, actual)
            self.assertGreaterEqual(confidence, 0.85)

    def test_scan_is_index_only_and_dashboard_is_generated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "2026"
            root.mkdir()
            source = root / "GHTK_bao_cao_van_hanh.xlsx"
            source.write_bytes(b"report")
            original = source.read_bytes()
            result = WIP.scan(root, [], True)
            self.assertEqual(1, result["finance_ops"])
            self.assertEqual(original, source.read_bytes())
            output = WIP.dashboard(root)
            self.assertIn("Finance &amp; Operations", output.read_text(encoding="utf-8"))

    def test_personal_book_scope_is_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "2026"
            book = root / "03" / "Adults" / "book.pdf"
            book.parent.mkdir(parents=True)
            book.write_bytes(b"book")
            WIP.scan(root, ["03"], False)
            with WIP.open_db(WIP.db_path(root)) as conn:
                self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def test_manual_review_routes_index_without_changing_source_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "2026"
            root.mkdir()
            source = root / "unidentified-document.docx"
            source.write_bytes(b"source data")
            WIP.scan(root, [], True)
            with WIP.open_db(WIP.db_path(root)) as conn:
                dossier_id = conn.execute("SELECT dossier_id FROM documents").fetchone()[0]
            WIP.review_dossier(root, dossier_id, "legal_corporate", None, "in_progress", "authorization evidence")
            self.assertEqual(b"source data", source.read_bytes())
            with WIP.open_db(WIP.db_path(root)) as conn:
                row = conn.execute("SELECT category, status, confidence, classification_reason FROM documents").fetchone()
                self.assertEqual(("legal_corporate", "in_progress", 1.0, "manual review: authorization evidence"), tuple(row))
            html = WIP.dashboard(root).read_text(encoding="utf-8")
            self.assertIn("Complete the dossier checklist", html)


if __name__ == "__main__":
    unittest.main()
