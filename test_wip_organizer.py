import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("wip_organizer", Path(__file__).with_name("wip-organizer.py"))
WIP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WIP)


class WipOrganizerTests(unittest.TestCase):
    def test_default_scopes_include_automation_folders(self):
        for scope in ("00_INBOX", "10_HO_SO", "20_BAO_CAO", "80_THAM_CHIEU", "90_CACH_LY_KY_THUAT"):
            self.assertIn(scope, WIP.DEFAULT_SCOPES)

    def test_deterministic_rules(self):
        for path, category in [
            ("GHTK_bao_cao_van_hanh_va_dong_tien_21_08_S12734516.xlsx", "ghtk_reports"),
            ("bien_ban_doi_soat_08_2026.xlsx", "ghtk_reports"),
            ("CHI_TIET_CONG_NO_PHAI_TRA FUJAN.xlsx", "finance_ops"),
            ("iv-mtt-batch36-20260819-163502.xlsx", "vat"),
            ("Order.all.20260701_20260731.xlsx", "shopee_reports"),
            ("AT. QC reports/743-2026-00172508.pdf", "quality_regulatory"),
            ("Review Label/2026.05.14/Print Label Organic Matcha Powder.pdf", "packaging"),
            ("ho so danh gia CUC 20260421/Mau nhat ky luu mau.pdf", "quality_regulatory"),
            ("Shopee_mass_upload_2026-08-17_100629.xlsx", "commerce"),
            ("ivarvietnam.com-1787197766379.log", "technical"),
            ("mau-giay-uy-quyen-ca-nhan-cho-ca-nhan_2003225702.docx", "legal_corporate"),
            ("HBL-LSUHPG26071866QDS (3).pdf", "sourcing_logistics"),
            ("AT. Sourcing contracts/DRAFT SC8965 Bioway.docx", "sourcing_logistics"),
            ("log.adff603fbfbd.txt", "technical"),
        ]:
            actual, confidence, _, _, _ = WIP.classify(Path(path), WIP.DEFAULT_RULES)
            self.assertEqual(category, actual)
            self.assertGreaterEqual(confidence, 0.85)

    def test_managed_move_rules_route_to_at_folders(self):
        for path, category, target in [
            ("iv-mtt-batch36-20260819-163502.xlsx", "vat", "20_BAO_CAO/VAT"),
            ("Order.all.20260701_20260731.xlsx", "shopee_reports", "20_BAO_CAO/Shopee"),
            ("GHTK_bao_cao_van_hanh_va_dong_tien_21_08_S12734516.xlsx", "ghtk_reports", "20_BAO_CAO/GHTK"),
            ("bien_ban_doi_soat_08_2026.xlsx", "ghtk_reports", "20_BAO_CAO/GHTK"),
            ("11090087_SAOKE_TK_202608.xlsx", "bank_statements", "20_BAO_CAO/Sao ke"),
        ]:
            actual_category, confidence, reason, actual_target, route_mode = WIP.classify(Path(path), WIP.DEFAULT_RULES)
            self.assertEqual(category, actual_category)
            self.assertEqual(target, actual_target)
            self.assertEqual("managed_move", route_mode)
            self.assertGreaterEqual(confidence, 0.85)
            self.assertTrue(reason.startswith("rule: "))

    def test_unrelated_ghtk_text_is_not_managed_routed(self):
        category, _confidence, _reason, _target, route_mode = WIP.classify(
            Path("GHTK bao cao thang 08 khong dung dinh dang.xlsx"), WIP.DEFAULT_RULES
        )
        self.assertNotEqual("ghtk_reports", category)
        self.assertNotEqual("managed_move", route_mode)

    def test_route_command_reports_managed_move_and_review_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            rules_file = Path(temporary) / "rules.json"
            rules_file.write_text(json.dumps(WIP.DEFAULT_RULES, ensure_ascii=False), encoding="utf-8")

            category, confidence, target, route_mode, reason = WIP.route_file(
                rules_file, "GHTK_bao_cao_van_hanh_va_dong_tien_21_08_S12734516.xlsx"
            )
            self.assertEqual(("ghtk_reports", "20_BAO_CAO/GHTK", "managed_move"), (category, target, route_mode))
            self.assertGreaterEqual(confidence, 0.85)
            self.assertTrue(reason)

            category, _confidence, target, route_mode, _reason = WIP.route_file(
                rules_file, "mau-giay-uy-quyen-ca-nhan-cho-ca-nhan_2003225702.docx"
            )
            self.assertEqual(("legal_corporate", "review_only"), (category, route_mode))
            self.assertIsNotNone(target)

            category, _confidence, target, route_mode, _reason = WIP.route_file(rules_file, "unclassified-file.bin")
            self.assertEqual(("needs_review", "review_only"), (category, route_mode))
            self.assertIsNone(target)

    def test_route_command_cli_exit_code_reflects_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            rules_file = Path(temporary) / "rules.json"
            rules_file.write_text(json.dumps(WIP.DEFAULT_RULES, ensure_ascii=False), encoding="utf-8")
            approved = WIP.main([
                "route", "--rules", str(rules_file), "--path", "iv-mtt-batch36-20260819-163502.xlsx", "--format", "tsv",
            ])
            self.assertEqual(0, approved)
            rejected = WIP.main([
                "route", "--rules", str(rules_file), "--path", "unclassified-file.bin", "--format", "tsv",
            ])
            self.assertEqual(WIP.ROUTE_NOT_APPROVED, rejected)

    def test_existing_rules_are_migrated_without_overwriting_custom_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "2026"
            root.mkdir()
            rules = json.loads(json.dumps(WIP.DEFAULT_RULES))
            rules["categories"] = [item for item in rules["categories"] if item["id"] != "bank_statements"]
            for item in rules["categories"]:
                if item["id"] == "vat":
                    item["target"] = "AT.VAT"
                if item["id"] == "commerce":
                    item["target"] = "Custom sales target"
            WIP.ensure_state(root)
            WIP.rules_path(root).write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
            migrated = WIP.load_rules(root)
            targets = {item["id"]: item["target"] for item in migrated["categories"]}
            self.assertEqual("20_BAO_CAO/VAT", targets["vat"])
            self.assertEqual("20_BAO_CAO/Sao ke", targets["bank_statements"])
            self.assertEqual("Custom sales target", targets["commerce"])

    def test_scan_is_index_only_and_dashboard_is_generated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "2026"
            root.mkdir()
            source = root / "GHTK_bao_cao_van_hanh.xlsx"
            source.write_bytes(b"report")
            original = source.read_bytes()
            result = WIP.scan(root, [], True)
            self.assertEqual(1, result["ghtk_reports"])
            self.assertEqual(original, source.read_bytes())
            output = WIP.dashboard(root)
            self.assertIn("GHTK reports", output.read_text(encoding="utf-8"))

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
