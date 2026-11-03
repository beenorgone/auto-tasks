#!/usr/bin/env python3
"""
test_unc_database.py - Comprehensive test suite for UNC database, parsers, and search mechanism.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import unc_db


class TestUNCDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = unc_db.DEFAULT_DB_PATH
        cls.source_dir = unc_db.DEFAULT_SOURCE_DIR

        # Ensure database is indexed
        if not cls.db_path.exists():
            unc_db.scan_and_index_inventory(cls.source_dir, cls.db_path, force=False)

    def test_database_exists_and_populated(self):
        """Verify database exists and has expected inventory counts."""
        self.assertTrue(self.db_path.exists(), "Database file should exist")
        stats = unc_db.get_stats_overview(self.db_path)
        self.assertGreaterEqual(stats["total_documents"], 316, "Should have at least 316 unique documents")
        self.assertGreaterEqual(stats["total_files"], 320, "Should have at least 320 source files")
        self.assertGreaterEqual(stats["total_amount_vnd"], 4_000_000_000, "Disbursed amount > 4 billion VND")

    def test_schema_and_indexes(self):
        """Verify all tables and indexes exist."""
        with unc_db.get_db_connection(self.db_path) as con:
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            self.assertIn("unc_documents", tables)
            self.assertIn("unc_files", tables)
            self.assertIn("unc_purchase_orders", tables)
            self.assertIn("unc_extraction_cache", tables)
            self.assertIn("unc_fts", tables)

            indexes = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
            self.assertIn("idx_unc_doc_ref", indexes)
            self.assertIn("idx_unc_doc_category", indexes)
            self.assertIn("idx_unc_doc_ben_acc", indexes)
            self.assertIn("idx_unc_po_num", indexes)

    def test_vietinbank_document_parsing(self):
        """Verify representative VietinBank UNC is accurately parsed."""
        # Test doc with ref 1031926F12827289
        doc = unc_db.get_document_details(self.db_path, "1031926F12827289")
        self.assertIsNotNone(doc, "Document 1031926F12827289 should exist")
        self.assertEqual(doc["bank_issuer"], "VietinBank")
        self.assertEqual(doc["doc_type"], "UNC")
        self.assertEqual(doc["reference_no"], "1031926F12827289")
        self.assertEqual(doc["sender_account"], "113600098383")
        self.assertEqual(doc["beneficiary_name"], "CT TNHH OCA VIET NHAT")
        self.assertEqual(doc["beneficiary_account"], "1048479205")
        self.assertIn("VCB", doc["beneficiary_bank"])
        self.assertEqual(doc["amount"], 35700000.0)
        self.assertEqual(doc["currency"], "VND")
        self.assertEqual(doc["payment_category"], "supplier_payment")
        self.assertEqual(doc["review_state"], "VERIFIED")

        # Verify purchase order extraction
        pos = [p["po_number"] for p in doc["purchase_orders"]]
        self.assertIn("PO8275", pos)

    def test_acb_document_parsing(self):
        """Verify representative ACB UNC is accurately parsed."""
        # Test ACB payment 2626
        doc = unc_db.get_document_details(self.db_path, "2626")
        self.assertIsNotNone(doc, "ACB Document 2626 should exist")
        self.assertEqual(doc["bank_issuer"], "ACB")
        self.assertEqual(doc["doc_type"], "UNC")
        self.assertEqual(doc["sender_name"], "HA TRUNG KIEN")
        self.assertEqual(doc["sender_account"], "8700507")
        self.assertEqual(doc["beneficiary_name"], "HA DUY DUC")
        self.assertEqual(doc["beneficiary_account"], "0031000310916")
        self.assertEqual(doc["amount"], 9000000.0)
        self.assertEqual(doc["payment_category"], "payroll")
        self.assertEqual(doc["review_state"], "VERIFIED")

    def test_acb_virtual_account(self):
        """Verify ACB UNC with alphanumeric virtual account (M369S...) is accurately parsed."""
        doc = unc_db.get_document_details(self.db_path, "2504")
        self.assertIsNotNone(doc, "ACB Document 2504 should exist")
        self.assertEqual(doc["beneficiary_name"], "CONG TY CO PHAN GHTKPAY")
        self.assertEqual(doc["beneficiary_account"], "M369S000000036990")
        self.assertEqual(doc["amount"], 388760.0)
        self.assertEqual(doc["payment_category"], "supplier_payment")

    def test_sender_beneficiary_separation(self):
        """Verify sender and beneficiary details are cleanly separated."""
        doc = unc_db.get_document_details(self.db_path, "2505")
        self.assertIsNotNone(doc, "Document 2505 should exist")
        self.assertEqual(doc["sender_name"], "CONG TY TNHH THUONG MAI VA DICH VU IVAR VIET NAM")
        self.assertEqual(doc["sender_account"], "51364767")
        self.assertIn("ACB", doc["sender_bank"])

        self.assertEqual(doc["beneficiary_name"], "CONG TY TNHH TM VA DV IVAR VIET NAM")
        self.assertEqual(doc["beneficiary_account"], "113600098383")
        self.assertIn("VietinBank", doc["beneficiary_bank"])
        self.assertEqual(doc["payment_category"], "internal_transfer")

    def test_leading_zero_preservation(self):
        """Verify accounts with leading zeros retain leading zeros in DB and queries."""
        results, count = unc_db.search_unc_documents(self.db_path, account="0031000310916")
        self.assertEqual(count, 1)
        self.assertEqual(results[0]["beneficiary_account"], "0031000310916")

        # Check another leading zero account (Eurofins Vietcombank account)
        results2, count2 = unc_db.search_unc_documents(self.db_path, account="0071000695552")
        self.assertGreaterEqual(count2, 1)
        for r in results2:
            self.assertTrue(r["beneficiary_account"].startswith("007"))

    def test_multiple_purchase_orders(self):
        """Verify one UNC can reference and link multiple POs."""
        doc = unc_db.get_document_details(self.db_path, "1031926F16071609")
        self.assertIsNotNone(doc)
        po_numbers = {p["po_number"] for p in doc["purchase_orders"]}
        self.assertIn("PO6940", po_numbers)
        self.assertIn("PO7795", po_numbers)

        # Searching by either PO finds the document
        r1, c1 = unc_db.search_unc_documents(self.db_path, po="PO6940")
        r2, c2 = unc_db.search_unc_documents(self.db_path, po="7795")
        self.assertEqual(c1, 1)
        self.assertEqual(c2, 1)
        self.assertEqual(r1[0]["id"], r2[0]["id"])

    def test_non_unc_document_handling(self):
        """Verify non-UNC documents (BHXH, ASC Trans) are recognized and flagged."""
        results, count = unc_db.search_unc_documents(self.db_path, review_state="NON_UNC")
        self.assertEqual(count, 2, "Should have exactly 2 non-UNC documents")
        for r in results:
            self.assertEqual(r["doc_type"], "NON_UNC")
            self.assertIn(r["bank_issuer"], ["ASC Trans", "Bảo Hiểm Xã Hội"])
            self.assertIsNone(r["sender_account"])
            self.assertIsNone(r["beneficiary_account"])

    def test_search_by_category(self):
        """Verify searching by payment classification."""
        for cat in ["supplier_payment", "payroll", "advertising", "electricity", "social_insurance", "refund"]:
            res, cnt = unc_db.search_unc_documents(self.db_path, category=cat)
            self.assertGreater(cnt, 0, f"Category {cat} should have records")
            for r in res:
                self.assertEqual(r["payment_category"], cat)

    def test_search_combined_filters(self):
        """Verify combined search filters work together."""
        # Category + PO
        res, cnt = unc_db.search_unc_documents(self.db_path, category="supplier_payment", po="PO8275")
        self.assertGreaterEqual(cnt, 1)
        for r in res:
            self.assertEqual(r["payment_category"], "supplier_payment")
            self.assertIn("PO8275", r["po_list"])

        # Category + Year
        res_yr, cnt_yr = unc_db.search_unc_documents(self.db_path, category="electricity", year=2026)
        self.assertGreaterEqual(cnt_yr, 1)
        for r in res_yr:
            self.assertEqual(r["payment_category"], "electricity")
            self.assertEqual(r["document_year"], 2026)

    def test_full_text_search(self):
        """Verify FTS5 full-text search."""
        res, cnt = unc_db.search_unc_documents(self.db_path, query="thue van phong")
        self.assertGreaterEqual(cnt, 1)
        self.assertIn("thue Van phong", res[0]["transfer_details"])

    def test_duplicate_file_association(self):
        """Verify physical duplicate files map to single document ID."""
        with unc_db.get_db_connection(self.db_path) as con:
            cur = con.execute(
                """
                SELECT document_id, COUNT(*) as cnt
                FROM unc_files
                GROUP BY document_id
                HAVING cnt > 1
                """
            )
            rows = cur.fetchall()
            self.assertGreaterEqual(len(rows), 4, "Should have at least 4 duplicate groups")

    def test_idempotent_reimport(self):
        """Verify re-scan is completely idempotent."""
        stats = unc_db.scan_and_index_inventory(self.source_dir, self.db_path, force=False)
        self.assertEqual(stats["files_updated"], 0)
        self.assertEqual(stats["files_indexed_new"], 0)
        self.assertEqual(stats["files_unchanged"], stats["total_files_found"])

    def test_safe_path_with_newline(self):
        """Verify the file with newline in filename was safely processed."""
        with unc_db.get_db_connection(self.db_path) as con:
            cur = con.execute("SELECT * FROM unc_files WHERE file_name LIKE '%\n%' OR file_name LIKE '%\r%'")
            row = cur.fetchone()
            self.assertIsNotNone(row, "File with newline should be safely indexed in unc_files")
            self.assertIsNotNone(row["document_id"], "Should be linked to a valid document")


if __name__ == "__main__":
    unittest.main()
