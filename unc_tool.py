#!/usr/bin/env python3
"""
unc_tool.py - CLI and local web interface for querying and managing the IVAR UNC Database.

Usage:
    python3 unc_tool.py scan               # Scan and index UNC directory
    python3 unc_tool.py search [filters]   # Search payment orders
    python3 unc_tool.py show <ID or REF>   # Display details of a payment order
    python3 unc_tool.py open <ID or REF>   # Open original PDF document
    python3 unc_tool.py stats              # View summary statistics
    python3 unc_tool.py export [options]   # Export records to CSV / JSON
    python3 unc_tool.py serve [options]    # Launch local Material web UI
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

import unc_db


def format_vnd(amount: Optional[float]) -> str:
    if amount is None:
        return "N/A"
    return f"{amount:,.0f} VND"


def print_table(rows: List[Dict[str, Any]], columns: List[Tuple[str, str, int]]) -> None:
    if not rows:
        print("No matching records found.")
        return

    # Header
    header_parts = []
    sep_parts = []
    for col_key, col_title, width in columns:
        header_parts.append(f"{col_title:<{width}}")
        sep_parts.append("-" * width)
    print(" | ".join(header_parts))
    print("-+-".join(sep_parts))

    # Rows
    for r in rows:
        row_parts = []
        for col_key, col_title, width in columns:
            val = str(r.get(col_key) or "")
            if len(val) > width:
                val = val[: width - 3] + "..."
            row_parts.append(f"{val:<{width}}")
        print(" | ".join(row_parts))


def cmd_scan(args: argparse.Namespace) -> None:
    source_dir = unc_db.get_source_dir(args.source)
    db_path = unc_db.get_db_path(args.db)

    print(f"Scanning source directory: {source_dir}")
    print(f"Target SQLite database:   {db_path}")

    stats = unc_db.scan_and_index_inventory(source_dir, db_path, force=args.force)

    print("\n" + "=" * 60)
    print("UNC DATABASE INVENTORY SUMMARY")
    print("=" * 60)
    print(f"Total Source PDF Files Found:  {stats['total_files_found']}")
    print(f"Files Newly Indexed:          {stats['files_indexed_new']}")
    print(f"Files Updated:                {stats['files_updated']}")
    print(f"Files Unchanged:              {stats['files_unchanged']}")
    print(f"Unique Documents (Content):   {stats['unique_documents']}")
    print(f"Duplicate Content Groups:     {stats['duplicate_files']}")
    print("-" * 60)

    print("Breakdown by Payment Category:")
    for cat, count in stats["by_category"].items():
        print(f"  - {cat:<22}: {count:>4} documents")

    print("-" * 60)
    print("Breakdown by Bank / Issuer:")
    for bank, count in stats["by_bank"].items():
        print(f"  - {bank:<22}: {count:>4} documents")

    print("-" * 60)
    print("Breakdown by Review State:")
    for state, count in stats["by_review_state"].items():
        print(f"  - {state:<22}: {count:>4} documents")

    if stats.get("missing_metadata"):
        print("-" * 60)
        print("Missing Metadata Report (for bank UNCs):")
        for k, v in stats["missing_metadata"].items():
            print(f"  - {k:<28}: {v:>4}")
    print("=" * 60)


def cmd_search(args: argparse.Namespace) -> None:
    db_path = unc_db.get_db_path(args.db)

    rows, total_count = unc_db.search_unc_documents(
        db_path,
        query=args.query,
        category=args.category,
        account=args.account,
        name=args.name,
        bank=args.bank,
        po=args.po,
        ref=args.ref,
        date_from=args.date_from,
        date_to=args.date_to,
        year=args.year,
        month=args.month,
        amount_min=args.amount_min,
        amount_max=args.amount_max,
        review_state=args.review_state,
        limit=args.limit,
        offset=args.offset,
    )

    if args.open and rows:
        paths = rows[0].get("file_paths")
        if paths:
            first_path = paths[0]
            print(f"Opening first result: {first_path}")
            open_file_in_viewer(first_path)

    if args.format == "paths":
        for r in rows:
            for p in r.get("file_paths", []):
                print(p)
        return

    if args.format == "json":
        print(json.dumps({"total_matches": total_count, "results": rows}, ensure_ascii=False, indent=2))
        return

    if args.format == "csv":
        fieldnames = [
            "id",
            "reference_no",
            "document_date",
            "payment_category",
            "beneficiary_name",
            "beneficiary_account",
            "beneficiary_bank",
            "amount",
            "currency",
            "po_list",
            "review_state",
            "transfer_details",
        ]
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        return

    # Table format
    print(f"\nFound {total_count} matching documents (showing {len(rows)}):")
    # Prepare formatted rows
    display_rows = []
    for r in rows:
        d = dict(r)
        d["amount_fmt"] = format_vnd(d.get("amount"))
        d["date_short"] = str(d.get("document_date") or "")[:10]
        display_rows.append(d)

    cols = [
        ("id", "ID", 4),
        ("reference_no", "Số UNC / Ref", 16),
        ("date_short", "Ngày Date", 10),
        ("payment_category", "Phân loại Category", 18),
        ("beneficiary_name", "Người thụ hưởng Beneficiary", 32),
        ("beneficiary_account", "Số TK TK", 16),
        ("amount_fmt", "Số tiền Amount", 16),
        ("po_list", "Mã PO", 12),
        ("review_state", "Trạng thái", 12),
    ]
    print_table(display_rows, cols)
    print(f"Total: {total_count} results. Use '--limit' or '--offset' for pagination.")


def cmd_show(args: argparse.Namespace) -> None:
    db_path = unc_db.get_db_path(args.db)
    doc = unc_db.get_document_details(db_path, args.identifier)
    if not doc:
        print(f"Error: Document with ID or Reference '{args.identifier}' not found.", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 70)
    print(f"CHI TIẾT ỦY NHIỆM CHI (UNC DETAILS) #{doc['id']}")
    print("=" * 70)
    print(f"Số tham chiếu (Ref No)   : {doc.get('reference_no') or 'N/A'}")
    print(f"Loại chứng từ (Doc Type) : {doc.get('doc_type')} ({doc.get('bank_issuer')})")
    print(f"Ngày giao dịch (Date)    : {doc.get('document_date') or 'N/A'}")
    print(f"Số tiền (Amount)         : {format_vnd(doc.get('amount'))}")
    if doc.get("amount_in_words"):
        print(f"Bằng chữ (In words)      : {doc.get('amount_in_words')}")
    print(f"Trạng thái GD (Status)   : {doc.get('transaction_status') or 'N/A'}")
    print("-" * 70)
    print(f"PHÂN LOẠI CHI PHÍ (Category) : {doc.get('payment_category')} (Độ tin cậy: {doc.get('category_confidence')})")
    print(f"Trạng thái rà soát (Review)  : {doc.get('review_state')} (Parse: {doc.get('parse_status')})")
    if doc.get("review_notes"):
        print(f"Ghi chú rà soát (Notes)      : {doc.get('review_notes')}")
    print("-" * 70)
    print("THÔNG TIN ĐƠN VỊ TRẢ TIỀN (SENDER / APPLICANT):")
    print(f"  - Tên đơn vị           : {doc.get('sender_name') or 'N/A'}")
    print(f"  - Số tài khoản         : {doc.get('sender_account') or 'N/A'}")
    print(f"  - Tại ngân hàng        : {doc.get('sender_bank') or 'N/A'}")
    print("-" * 70)
    print("THÔNG TIN ĐƠN VỊ THỤ HƯỞNG (BENEFICIARY):")
    print(f"  - Tên người/đơn vị     : {doc.get('beneficiary_name') or 'N/A'}")
    print(f"  - Số tài khoản         : {doc.get('beneficiary_account') or 'N/A'}")
    print(f"  - Tại ngân hàng        : {doc.get('beneficiary_bank') or 'N/A'}")
    print("-" * 70)
    print(f"NỘI DUNG THANH TOÁN (Remarks):")
    print(f"  {doc.get('transfer_details') or 'N/A'}")
    print("-" * 70)
    pos = doc.get("purchase_orders", [])
    if pos:
        po_str = ", ".join(p["po_number"] for p in pos)
        print(f"ĐƠN ĐẶT HÀNG (Purchase Orders): {po_str}")
    else:
        print("ĐƠN ĐẶT HÀNG (Purchase Orders): Không có")
    print("-" * 70)
    print("TẬP TIN GỐC (SOURCE PDF PATHS):")
    for f in doc.get("files", []):
        print(f"  - Path: {f['file_path']}")
        print(f"    Size: {f['file_size']:,} bytes | SHA-256: {f['file_sha256'][:16]}...")
    print("=" * 70 + "\n")


def open_file_in_viewer(file_path: str) -> None:
    p = Path(file_path)
    if not p.exists():
        print(f"Error: File does not exist: {file_path}", file=sys.stderr)
        return

    # Safe system open via xdg-open or gio open
    opener = "xdg-open" if shutil.which("xdg-open") else "gio"
    args = [opener, str(p)] if opener == "xdg-open" else ["gio", "open", str(p)]
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Document opened in viewer: {p.name}")
    except Exception as e:
        print(f"Failed to open viewer: {e}", file=sys.stderr)


def cmd_open(args: argparse.Namespace) -> None:
    db_path = unc_db.get_db_path(args.db)
    doc = unc_db.get_document_details(db_path, args.identifier)
    if not doc:
        print(f"Error: Document '{args.identifier}' not found.", file=sys.stderr)
        sys.exit(1)

    files = doc.get("files", [])
    if not files:
        print("Error: No associated file found for this document.", file=sys.stderr)
        sys.exit(1)

    open_file_in_viewer(files[0]["file_path"])


def cmd_stats(args: argparse.Namespace) -> None:
    db_path = unc_db.get_db_path(args.db)
    stats = unc_db.get_stats_overview(db_path)

    print("\n" + "=" * 65)
    print("UNC DATABASE ANALYTICS & OVERVIEW")
    print("=" * 65)
    print(f"Total Unique Documents : {stats['total_documents']:,}")
    print(f"Total Physical Files   : {stats['total_files']:,}")
    print(f"Duplicate File Groups  : {stats['duplicate_content_groups']}")
    print(f"Total Disbursed Amount : {format_vnd(stats['total_amount_vnd'])}")
    print(f"Distinct POs Linked    : {stats['distinct_purchase_orders']}")
    print("-" * 65)
    print("By Payment Classification:")
    for k, v in stats["by_category"].items():
        pct = (v / stats["total_documents"]) * 100 if stats["total_documents"] else 0
        print(f"  - {k:<20}: {v:>4} ({pct:>5.1f}%)")
    print("-" * 65)
    print("By Issuing Bank:")
    for k, v in stats["by_bank"].items():
        print(f"  - {k:<20}: {v:>4}")
    print("-" * 65)
    print("By Document Year:")
    for k, v in sorted(stats["by_year"].items()):
        print(f"  - Năm {k:<16}: {v:>4}")
    print("-" * 65)
    print("By Review State:")
    for k, v in stats["by_review_state"].items():
        print(f"  - {k:<20}: {v:>4}")
    print("=" * 65 + "\n")


def cmd_export(args: argparse.Namespace) -> None:
    db_path = unc_db.get_db_path(args.db)
    rows, _ = unc_db.search_unc_documents(db_path, limit=10000)

    out_path = Path(args.output).resolve() if args.output else Path("unc_export.csv").resolve()

    if out_path.suffix.lower() == ".json":
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    else:
        fieldnames = [
            "id",
            "reference_no",
            "document_date",
            "payment_category",
            "beneficiary_name",
            "beneficiary_account",
            "beneficiary_bank",
            "amount",
            "currency",
            "po_list",
            "review_state",
            "transfer_details",
            "sender_name",
            "sender_account",
            "bank_issuer",
        ]
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

    print(f"Exported {len(rows)} documents to: {out_path}")


# ---------------------------------------------------------------------------
# LOCAL MATERIAL WEB UI
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IVAR UNC Search & Verification Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&family=Google+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --primary: #1a73e8;
      --primary-hover: #1557b0;
      --primary-light: #e8f0fe;
      --surface: #ffffff;
      --background: #f8f9fa;
      --border: #dadce0;
      --text-main: #202124;
      --text-muted: #5f6368;
      --success: #1e8e3e;
      --warning: #f9ab00;
      --danger: #d93025;
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 16px;
      --shadow-1: 0 1px 3px rgba(60,64,67,0.12), 0 1px 2px rgba(60,64,67,0.24);
      --shadow-2: 0 4px 8px rgba(60,64,67,0.15), 0 1px 3px rgba(60,64,67,0.3);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Roboto', 'Google Sans', sans-serif;
      background: var(--background);
      color: var(--text-main);
      line-height: 1.5;
    }
    header {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 100;
      box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 20px;
      font-weight: 500;
      color: var(--primary);
    }
    .brand-badge {
      background: #e1f5fe;
      color: #0288d1;
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .nav-tabs {
      display: flex;
      gap: 8px;
    }
    .tab-btn {
      padding: 8px 16px;
      border: none;
      background: transparent;
      font-size: 14px;
      font-weight: 500;
      color: var(--text-muted);
      border-radius: 20px;
      cursor: pointer;
      transition: all 0.2s;
    }
    .tab-btn:hover { background: #f1f3f4; color: var(--text-main); }
    .tab-btn.active {
      background: var(--primary-light);
      color: var(--primary);
      font-weight: 600;
    }
    main {
      max-width: 1440px;
      margin: 0 auto;
      padding: 20px 24px;
    }
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }
    .stat-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: 16px 20px;
      box-shadow: var(--shadow-1);
    }
    .stat-title { font-size: 12px; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }
    .stat-val { font-size: 24px; font-weight: 700; margin: 4px 0; color: var(--text-main); }
    .stat-sub { font-size: 12px; color: var(--text-muted); }

    .search-panel {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      padding: 16px 20px;
      margin-bottom: 20px;
      box-shadow: var(--shadow-1);
    }
    .search-row-primary {
      display: flex;
      gap: 12px;
      align-items: center;
      margin-bottom: 12px;
    }
    .search-input {
      flex: 1;
      padding: 10px 16px;
      border: 1px solid var(--border);
      border-radius: 24px;
      font-size: 14px;
      outline: none;
      transition: border 0.2s;
    }
    .search-input:focus { border-color: var(--primary); box-shadow: 0 0 0 2px rgba(26,115,232,0.2); }
    .filter-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }
    .chip {
      padding: 4px 12px;
      background: #f1f3f4;
      border: 1px solid transparent;
      border-radius: 16px;
      font-size: 13px;
      color: var(--text-muted);
      cursor: pointer;
      user-select: none;
      transition: all 0.15s;
    }
    .chip:hover { background: #e8eaed; }
    .chip.active {
      background: var(--primary-light);
      color: var(--primary);
      border-color: #aecbfa;
      font-weight: 500;
    }
    .filter-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      padding-top: 10px;
      border-top: 1px dashed var(--border);
    }
    .filter-control label { font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; display: block; margin-bottom: 4px; }
    .filter-control input, .filter-control select {
      width: 100%;
      padding: 6px 10px;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      font-size: 13px;
      outline: none;
    }
    .filter-control input:focus, .filter-control select:focus { border-color: var(--primary); }

    .content-table-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      overflow: hidden;
      box-shadow: var(--shadow-1);
    }
    table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
    th {
      background: #f8f9fa;
      padding: 12px 14px;
      font-weight: 600;
      color: var(--text-muted);
      border-bottom: 1px solid var(--border);
      font-size: 12px;
      text-transform: uppercase;
    }
    td {
      padding: 12px 14px;
      border-bottom: 1px solid #f1f3f4;
      vertical-align: middle;
    }
    tr:hover td { background: #f8fafd; cursor: pointer; }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
    }
    .badge-verified { background: #e6f4ea; color: #137333; }
    .badge-review { background: #fef7e0; color: #b06000; }
    .badge-non-unc { background: #fce8e6; color: #c5221f; }
    .badge-cat { background: #e8f0fe; color: #1967d2; font-family: 'JetBrains Mono', monospace; font-size: 11px; }
    .acc-number { font-family: 'JetBrains Mono', monospace; font-weight: 500; }
    .amt-number { font-weight: 600; color: #202124; }
    .po-tag {
      background: #f3e8fd;
      color: #7627bb;
      padding: 1px 6px;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 600;
      margin-right: 4px;
      display: inline-block;
    }

    /* Modal Dialog */
    .modal-backdrop {
      display: none;
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0,0,0,0.4);
      z-index: 1000;
      align-items: center;
      justify-content: center;
      backdrop-filter: blur(2px);
    }
    .modal-backdrop.open { display: flex; }
    .modal-card {
      background: var(--surface);
      border-radius: var(--radius-lg);
      width: 90%;
      max-width: 800px;
      max-height: 90vh;
      overflow-y: auto;
      box-shadow: var(--shadow-2);
      padding: 24px 28px;
      position: relative;
    }
    .modal-close {
      position: absolute;
      top: 20px;
      right: 20px;
      font-size: 24px;
      background: none;
      border: none;
      cursor: pointer;
      color: var(--text-muted);
    }
    .modal-header { margin-bottom: 20px; }
    .modal-title { font-size: 20px; font-weight: 600; color: var(--text-main); }
    .modal-section { margin-bottom: 20px; }
    .modal-section-title { font-size: 12px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; margin-bottom: 8px; border-bottom: 1px solid var(--border); padding-bottom: 4px; }
    .detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; font-size: 13px; }
    .detail-item span.lbl { color: var(--text-muted); display: block; font-size: 11px; }
    .detail-item span.val { font-weight: 500; }
    .action-row { display: flex; gap: 12px; margin-top: 24px; justify-content: flex-end; }
    .btn {
      padding: 8px 18px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      border: none;
      transition: all 0.2s;
    }
    .btn-primary { background: var(--primary); color: #fff; }
    .btn-primary:hover { background: var(--primary-hover); }
    .btn-secondary { background: #f1f3f4; color: var(--text-main); }
    .btn-secondary:hover { background: #e8eaed; }

    /* Kanban view */
    .kanban-board {
      display: none;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
      align-items: start;
    }
    .kanban-col {
      background: #f1f3f4;
      border-radius: var(--radius-md);
      padding: 12px;
      min-height: 400px;
    }
    .kanban-col-header {
      font-size: 13px;
      font-weight: 700;
      color: var(--text-main);
      padding: 6px 8px 12px;
      display: flex;
      justify-content: space-between;
    }
    .kanban-card {
      background: var(--surface);
      border-radius: var(--radius-sm);
      padding: 12px;
      margin-bottom: 10px;
      box-shadow: var(--shadow-1);
      cursor: pointer;
      transition: transform 0.15s, box-shadow 0.15s;
    }
    .kanban-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-2); }
    .k-amount { font-weight: 700; font-size: 14px; margin-bottom: 4px; }
    .k-ben { font-size: 12px; font-weight: 500; color: #202124; margin-bottom: 6px; }
    .k-footer { font-size: 11px; color: var(--text-muted); display: flex; justify-content: space-between; align-items: center; }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
        <path d="M4 10v7h3v-7H4zm6 0v7h3v-7h-3zm6 0v7h3v-7h-3zM2 22h19v-3H2v3zm9-20L2 6v2h19V6l-10-4z"/>
      </svg>
      <span>IVAR UNC Database</span>
      <span class="brand-badge">PROD / VERIFIED</span>
    </div>
    <div class="nav-tabs">
      <button class="tab-btn active" onclick="switchTab('table')">Danh sách (Table)</button>
      <button class="tab-btn" onclick="switchTab('kanban')">Kanban Phân loại</button>
      <button class="tab-btn" onclick="filterReviewOnly()">Cần rà soát (<span id="reviewCountBadge">0</span>)</button>
    </div>
  </header>

  <main>
    <div class="stats-grid" id="statsGrid">
      <div class="stat-card">
        <div class="stat-title">Tổng số chứng từ</div>
        <div class="stat-val" id="statTotalDocs">...</div>
        <div class="stat-sub" id="statTotalFiles">... files nguồn</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Tổng số tiền chi</div>
        <div class="stat-val" id="statTotalAmount" style="color: var(--primary);">...</div>
        <div class="stat-sub">VNĐ</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Thanh toán NCC</div>
        <div class="stat-val" id="statSupplierCount" style="color: var(--success);">...</div>
        <div class="stat-sub" id="statSupplierAmount">Đã xác minh</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Hàng đợi rà soát</div>
        <div class="stat-val" id="statReviewCount" style="color: var(--warning);">...</div>
        <div class="stat-sub">Review Queue</div>
      </div>
    </div>

    <div class="search-panel">
      <div class="search-row-primary">
        <input type="text" id="searchInput" class="search-input" placeholder="Tìm kiếm tự do (Số UNC, Đơn vị thụ hưởng, Số tài khoản, Ngân hàng, Nội dung, Mã PO...)" oninput="debouncedSearch()">
      </div>

      <div class="filter-chips">
        <div class="chip active" onclick="setChipFilter('', this)">Tất cả</div>
        <div class="chip" onclick="setChipFilter('supplier_payment', this)">Nhà cung cấp</div>
        <div class="chip" onclick="setChipFilter('payroll', this)">Chi lương</div>
        <div class="chip" onclick="setChipFilter('advertising', this)">Quảng cáo</div>
        <div class="chip" onclick="setChipFilter('electricity', this)">Tiền điện</div>
        <div class="chip" onclick="setChipFilter('social_insurance', this)">BHXH</div>
        <div class="chip" onclick="setChipFilter('internal_transfer', this)">Chuyển nội bộ</div>
        <div class="chip" onclick="setChipFilter('refund', this)">Hoàn tiền</div>
        <div class="chip" onclick="setChipFilter('other', this)">Khác / Rà soát</div>
      </div>

      <div class="filter-grid">
        <div class="filter-control">
          <label>Phân loại (Category)</label>
          <select id="filterCategory" onchange="debouncedSearch()">
            <option value="">-- Tất cả --</option>
            <option value="supplier_payment">supplier_payment</option>
            <option value="payroll">payroll</option>
            <option value="advertising">advertising</option>
            <option value="electricity">electricity</option>
            <option value="social_insurance">social_insurance</option>
            <option value="internal_transfer">internal_transfer</option>
            <option value="refund">refund</option>
            <option value="other">other (khác)</option>
          </select>
        </div>
        <div class="filter-control">
          <label>Số tài khoản thụ hưởng</label>
          <input type="text" id="filterAccount" placeholder="VD: 0031..., 1048..." oninput="debouncedSearch()">
        </div>
        <div class="filter-control">
          <label>Mã Đơn hàng (PO)</label>
          <input type="text" id="filterPO" placeholder="VD: PO8275, 7795..." oninput="debouncedSearch()">
        </div>
        <div class="filter-control">
          <label>Ngân hàng</label>
          <input type="text" id="filterBank" placeholder="VD: VietinBank, VCB, ACB..." oninput="debouncedSearch()">
        </div>
        <div class="filter-control">
          <label>Trạng thái rà soát</label>
          <select id="filterReviewState" onchange="debouncedSearch()">
            <option value="">-- Tất cả --</option>
            <option value="VERIFIED">VERIFIED (Đã xác minh)</option>
            <option value="NEEDS_REVIEW">NEEDS_REVIEW (Cần rà soát)</option>
            <option value="NON_UNC">NON_UNC (Thông báo ngoài)</option>
          </select>
        </div>
      </div>
    </div>

    <!-- Table View -->
    <div class="content-table-card" id="tableView">
      <table>
        <thead>
          <tr>
            <th>Số UNC</th>
            <th>Ngày GD</th>
            <th>Phân loại</th>
            <th>Đơn vị thụ hưởng</th>
            <th>Số TK thụ hưởng</th>
            <th>Số tiền</th>
            <th>Mã PO</th>
            <th>Trạng thái</th>
          </tr>
        </thead>
        <tbody id="tableBody">
          <tr><td colspan="8" style="text-align: center; padding: 30px;">Đang tải dữ liệu...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Kanban View -->
    <div class="kanban-board" id="kanbanView"></div>
  </main>

  <!-- Modal Detail -->
  <div class="modal-backdrop" id="detailModal">
    <div class="modal-card">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      <div class="modal-header">
        <h2 class="modal-title" id="mRef">UNC Details</h2>
        <div style="margin-top: 6px; display: flex; gap: 8px;">
          <span class="badge" id="mStateBadge"></span>
          <span class="badge badge-cat" id="mCatBadge"></span>
        </div>
      </div>

      <div class="modal-section">
        <div class="modal-section-title">Thông tin giao dịch</div>
        <div class="detail-grid">
          <div class="detail-item"><span class="lbl">Ngày chứng từ:</span><span class="val" id="mDate"></span></div>
          <div class="detail-item"><span class="lbl">Ngân hàng phát hành:</span><span class="val" id="mIssuer"></span></div>
          <div class="detail-item"><span class="lbl">Số tiền:</span><span class="val amt-number" id="mAmount" style="color: var(--primary); font-size: 16px;"></span></div>
          <div class="detail-item"><span class="lbl">Bằng chữ:</span><span class="val" id="mAmountWords"></span></div>
          <div class="detail-item"><span class="lbl">Trạng thái NH:</span><span class="val" id="mTxStatus"></span></div>
          <div class="detail-item"><span class="lbl">Phương pháp trích xuất:</span><span class="val" id="mMethod"></span></div>
        </div>
      </div>

      <div class="modal-section">
        <div class="modal-section-title">Đơn vị trả tiền (Applicant)</div>
        <div class="detail-grid">
          <div class="detail-item"><span class="lbl">Tên tài khoản:</span><span class="val" id="mSenderName"></span></div>
          <div class="detail-item"><span class="lbl">Số tài khoản:</span><span class="val acc-number" id="mSenderAcc"></span></div>
          <div class="detail-item"><span class="lbl">Tại ngân hàng:</span><span class="val" id="mSenderBank"></span></div>
        </div>
      </div>

      <div class="modal-section">
        <div class="modal-section-title">Đơn vị thụ hưởng (Beneficiary)</div>
        <div class="detail-grid">
          <div class="detail-item"><span class="lbl">Tên thụ hưởng:</span><span class="val" id="mBenName" style="font-weight: 700;"></span></div>
          <div class="detail-item">
            <span class="lbl">Số tài khoản thụ hưởng:</span>
            <span class="val acc-number" id="mBenAcc" style="font-size: 15px; color: #b71c1c;"></span>
          </div>
          <div class="detail-item"><span class="lbl">Ngân hàng thụ hưởng:</span><span class="val" id="mBenBank"></span></div>
        </div>
      </div>

      <div class="modal-section">
        <div class="modal-section-title">Nội dung chuyển tiền & PO</div>
        <div style="background: #f8f9fa; padding: 12px; border-radius: var(--radius-sm); font-size: 13px; margin-bottom: 8px;" id="mRemarks"></div>
        <div>
          <span style="font-size: 12px; color: var(--text-muted); font-weight: 600;">Mã PO liên quan: </span>
          <span id="mPOs"></span>
        </div>
      </div>

      <div class="modal-section">
        <div class="modal-section-title">Tập tin nguồn (Source Files)</div>
        <div id="mFiles" style="font-size: 12px; font-family: monospace; word-break: break-all;"></div>
      </div>

      <div class="action-row">
        <button class="btn btn-secondary" onclick="closeModal()">Đóng</button>
        <button class="btn btn-primary" id="btnOpenPdf" onclick="openCurrentPdf()">Mở File PDF Gốc</button>
      </div>
    </div>
  </div>

  <script>
    let currentDocs = [];
    let currentDoc = null;
    let debounceTimer = null;

    async function loadStats() {
      try {
        const res = await fetch('/api/stats');
        const s = await res.json();
        document.getElementById('statTotalDocs').innerText = s.total_documents.toLocaleString();
        document.getElementById('statTotalFiles').innerText = s.total_files.toLocaleString() + ' files nguồn';
        document.getElementById('statTotalAmount').innerText = (s.total_amount_vnd).toLocaleString('vi-VN') + ' đ';
        document.getElementById('statSupplierCount').innerText = (s.by_category.supplier_payment || 0) + ' chứng từ';
        document.getElementById('statReviewCount').innerText = (s.by_review_state.NEEDS_REVIEW || 0) + ' cần duyệt';
        document.getElementById('reviewCountBadge').innerText = (s.by_review_state.NEEDS_REVIEW || 0);
      } catch(e) { console.error(e); }
    }

    async function loadDocuments() {
      const q = document.getElementById('searchInput').value;
      const cat = document.getElementById('filterCategory').value;
      const acc = document.getElementById('filterAccount').value;
      const po = document.getElementById('filterPO').value;
      const bank = document.getElementById('filterBank').value;
      const review = document.getElementById('filterReviewState').value;

      const params = new URLSearchParams({
        query: q,
        category: cat,
        account: acc,
        po: po,
        bank: bank,
        review_state: review,
        limit: 200
      });

      const res = await fetch('/api/documents?' + params.toString());
      const data = await res.json();
      currentDocs = data.results || [];
      renderTable(currentDocs);
      renderKanban(currentDocs);
    }

    function debouncedSearch() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(loadDocuments, 250);
    }

    function renderTable(docs) {
      const tb = document.getElementById('tableBody');
      if (!docs.length) {
        tb.innerHTML = '<tr><td colspan="8" style="text-align: center; padding: 30px; color: #5f6368;">Không tìm thấy chứng từ phù hợp.</td></tr>';
        return;
      }
      tb.innerHTML = docs.map(d => {
        const stateClass = d.review_state === 'VERIFIED' ? 'badge-verified' : (d.review_state === 'NON_UNC' ? 'badge-non-unc' : 'badge-review');
        const pos = d.po_list ? d.po_list.split(',').map(p => `<span class="po-tag">${p.trim()}</span>`).join('') : '<span style="color:#bdc1c6">-</span>';
        const amt = d.amount ? d.amount.toLocaleString('vi-VN') + ' đ' : '-';
        return `
          <tr onclick="showDetail(${d.id})">
            <td><strong>${d.reference_no || 'N/A'}</strong></td>
            <td>${d.document_date ? d.document_date.substring(0, 10) : '-'}</td>
            <td><span class="badge badge-cat">${d.payment_category}</span></td>
            <td>${d.beneficiary_name || '-'}</td>
            <td class="acc-number">${d.beneficiary_account || '-'}</td>
            <td class="amt-number">${amt}</td>
            <td>${pos}</td>
            <td><span class="badge ${stateClass}">${d.review_state}</span></td>
          </tr>
        `;
      }).join('');
    }

    function renderKanban(docs) {
      const kb = document.getElementById('kanbanView');
      const categories = [
        { id: 'supplier_payment', title: 'Nhà cung cấp' },
        { id: 'payroll', title: 'Chi lương' },
        { id: 'advertising', title: 'Quảng cáo' },
        { id: 'electricity', title: 'Tiền điện' },
        { id: 'social_insurance', title: 'BHXH' },
        { id: 'internal_transfer', title: 'Chuyển nội bộ' },
        { id: 'refund', title: 'Hoàn tiền' },
        { id: 'other', title: 'Khác / Rà soát' }
      ];

      kb.innerHTML = categories.map(cat => {
        const catDocs = docs.filter(d => d.payment_category === cat.id);
        const cards = catDocs.map(d => {
          const amt = d.amount ? d.amount.toLocaleString('vi-VN') + ' đ' : '-';
          return `
            <div class="kanban-card" onclick="showDetail(${d.id})">
              <div class="k-amount">${amt}</div>
              <div class="k-ben">${d.beneficiary_name || 'N/A'}</div>
              <div class="k-footer">
                <span class="acc-number" style="font-size: 11px;">${d.beneficiary_account || ''}</span>
                <span>${d.document_date ? d.document_date.substring(0, 10) : ''}</span>
              </div>
            </div>
          `;
        }).join('');

        return `
          <div class="kanban-col">
            <div class="kanban-col-header">
              <span>${cat.title}</span>
              <span class="badge" style="background: #e8eaed; color: #202124;">${catDocs.length}</span>
            </div>
            ${cards}
          </div>
        `;
      }).join('');
    }

    async function showDetail(docId) {
      const res = await fetch('/api/document/' + docId);
      currentDoc = await res.json();
      if (!currentDoc) return;

      document.getElementById('mRef').innerText = (currentDoc.reference_no || 'UNC #' + currentDoc.id);
      document.getElementById('mDate').innerText = currentDoc.document_date || 'N/A';
      document.getElementById('mIssuer').innerText = currentDoc.bank_issuer || 'N/A';
      document.getElementById('mAmount').innerText = currentDoc.amount ? currentDoc.amount.toLocaleString('vi-VN') + ' VND' : 'N/A';
      document.getElementById('mAmountWords').innerText = currentDoc.amount_in_words || 'N/A';
      document.getElementById('mTxStatus').innerText = currentDoc.transaction_status || 'N/A';
      document.getElementById('mMethod').innerText = currentDoc.extraction_method || 'N/A';

      document.getElementById('mSenderName').innerText = currentDoc.sender_name || 'N/A';
      document.getElementById('mSenderAcc').innerText = currentDoc.sender_account || 'N/A';
      document.getElementById('mSenderBank').innerText = currentDoc.sender_bank || 'N/A';

      document.getElementById('mBenName').innerText = currentDoc.beneficiary_name || 'N/A';
      document.getElementById('mBenAcc').innerText = currentDoc.beneficiary_account || 'N/A';
      document.getElementById('mBenBank').innerText = currentDoc.beneficiary_bank || 'N/A';

      document.getElementById('mRemarks').innerText = currentDoc.transfer_details || 'N/A';

      const stateBadge = document.getElementById('mStateBadge');
      stateBadge.innerText = currentDoc.review_state;
      stateBadge.className = 'badge ' + (currentDoc.review_state === 'VERIFIED' ? 'badge-verified' : (currentDoc.review_state === 'NON_UNC' ? 'badge-non-unc' : 'badge-review'));
      document.getElementById('mCatBadge').innerText = currentDoc.payment_category;

      const pos = currentDoc.purchase_orders || [];
      document.getElementById('mPOs').innerHTML = pos.length ? pos.map(p => `<span class="po-tag">${p.po_number}</span>`).join('') : '<span style="color: #9aa0a6;">Không có</span>';

      const files = currentDoc.files || [];
      document.getElementById('mFiles').innerHTML = files.map(f => `<div>• ${f.file_path}</div>`).join('');

      document.getElementById('detailModal').classList.add('open');
    }

    function closeModal() {
      document.getElementById('detailModal').classList.remove('open');
    }

    async function openCurrentPdf() {
      if (!currentDoc) return;
      await fetch('/api/open?id=' + currentDoc.id);
    }

    function switchTab(mode) {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      if (mode === 'table') {
        event.target.classList.add('active');
        document.getElementById('tableView').style.display = 'block';
        document.getElementById('kanbanView').style.display = 'none';
      } else {
        event.target.classList.add('active');
        document.getElementById('tableView').style.display = 'none';
        document.getElementById('kanbanView').style.display = 'grid';
      }
    }

    function setChipFilter(cat, elem) {
      document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
      elem.classList.add('active');
      document.getElementById('filterCategory').value = cat;
      loadDocuments();
    }

    function filterReviewOnly() {
      document.getElementById('filterReviewState').value = 'NEEDS_REVIEW';
      loadDocuments();
    }

    // Init
    loadStats();
    loadDocuments();
  </script>
</body>
</html>
"""


class UNCHTTPServerHandler(BaseHTTPRequestHandler):
    db_path: Path = unc_db.DEFAULT_DB_PATH

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        if path == "/" or path == "/index.html":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            return

        if path == "/api/stats":
            stats = unc_db.get_stats_overview(self.db_path)
            self.send_json_response(stats)
            return

        if path == "/api/documents":
            q = query_params.get("query", [""])[0] or None
            cat = query_params.get("category", [""])[0] or None
            acc = query_params.get("account", [""])[0] or None
            po = query_params.get("po", [""])[0] or None
            bank = query_params.get("bank", [""])[0] or None
            review = query_params.get("review_state", [""])[0] or None
            limit = int(query_params.get("limit", ["100"])[0])

            rows, total = unc_db.search_unc_documents(
                self.db_path,
                query=q,
                category=cat,
                account=acc,
                po=po,
                bank=bank,
                review_state=review,
                limit=limit,
            )
            self.send_json_response({"total": total, "results": rows})
            return

        if path.startswith("/api/document/"):
            doc_id = path.split("/")[-1]
            doc = unc_db.get_document_details(self.db_path, doc_id)
            if doc:
                self.send_json_response(doc)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Document not found")
            return

        if path == "/api/open":
            doc_id = query_params.get("id", [""])[0]
            if doc_id:
                doc = unc_db.get_document_details(self.db_path, doc_id)
                if doc and doc.get("files"):
                    open_file_in_viewer(doc["files"][0]["file_path"])
                    self.send_json_response({"status": "opened", "file": doc["files"][0]["file_path"]})
                    return
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid document ID")
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def send_json_response(self, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy HTTP request logging in terminal
        pass


def cmd_serve(args: argparse.Namespace) -> None:
    db_path = unc_db.get_db_path(args.db)
    UNCHTTPServerHandler.db_path = db_path

    host = args.host
    port = args.port

    server = ThreadingHTTPServer((host, port), UNCHTTPServerHandler)
    url = f"http://{host}:{port}/"
    print("\n" + "=" * 60)
    print(f"IVAR UNC MATERIAL DASHBOARD ONLINE")
    print("=" * 60)
    print(f"Server URL:     {url}")
    print(f"Database Path:  {db_path}")
    print("Press Ctrl+C to stop the server.")
    print("=" * 60 + "\n")

    if args.open:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping UNC web server...")
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unc_tool.py",
        description="Search, inspect, and verify IVAR bank payment orders (UNC).",
    )
    parser.add_argument("--db", help="Path to SQLite database (default: ivar-documents-organizer/unc.db)")

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # scan
    p_scan = subparsers.add_parser("scan", aliases=["import"], help="Scan and index UNC source files")
    p_scan.add_argument("--source", help="Source folder containing UNC PDFs")
    p_scan.add_argument("--force", action="store_true", help="Force re-extraction of all files")
    p_scan.set_defaults(func=cmd_scan)

    # search
    p_search = subparsers.add_parser("search", help="Search UNC documents by various criteria")
    p_search.add_argument("-q", "--query", help="Full-text search query across all fields")
    p_search.add_argument(
        "-c",
        "--category",
        choices=[
            "supplier_payment",
            "payroll",
            "advertising",
            "electricity",
            "social_insurance",
            "internal_transfer",
            "refund",
            "other",
        ],
        help="Payment category filter",
    )
    p_search.add_argument("-a", "--account", help="Beneficiary account number filter")
    p_search.add_argument("-n", "--name", help="Beneficiary name filter")
    p_search.add_argument("-b", "--bank", help="Beneficiary bank filter")
    p_search.add_argument("-p", "--po", help="Purchase order number filter (e.g. PO8275 or 8275)")
    p_search.add_argument("-r", "--ref", help="Reference / UNC number filter")
    p_search.add_argument("--from", dest="date_from", help="Start date (YYYY-MM-DD)")
    p_search.add_argument("--to", dest="date_to", help="End date (YYYY-MM-DD)")
    p_search.add_argument("-y", "--year", type=int, help="Document year filter")
    p_search.add_argument("-m", "--month", type=int, help="Document month filter")
    p_search.add_argument("--amount-min", type=float, help="Minimum amount filter")
    p_search.add_argument("--amount-max", type=float, help="Maximum amount filter")
    p_search.add_argument(
        "-s", "--review-state", choices=["VERIFIED", "NEEDS_REVIEW", "NON_UNC"], help="Review state filter"
    )
    p_search.add_argument("-l", "--limit", type=int, default=50, help="Max results to display")
    p_search.add_argument("--offset", type=int, default=0, help="Offset for pagination")
    p_search.add_argument(
        "--format", choices=["table", "json", "csv", "paths"], default="table", help="Output format"
    )
    p_search.add_argument("--open", action="store_true", help="Open the first matching PDF in viewer")
    p_search.set_defaults(func=cmd_search)

    # show
    p_show = subparsers.add_parser("show", help="Display complete record for a UNC")
    p_show.add_argument("identifier", help="Document ID or Reference number")
    p_show.set_defaults(func=cmd_show)

    # open
    p_open = subparsers.add_parser("open", help="Open original PDF in system viewer")
    p_open.add_argument("identifier", help="Document ID or Reference number")
    p_open.set_defaults(func=cmd_open)

    # stats
    p_stats = subparsers.add_parser("stats", aliases=["summary"], help="Display analytics and summary metrics")
    p_stats.set_defaults(func=cmd_stats)

    # export
    p_export = subparsers.add_parser("export", help="Export UNC records to CSV or JSON")
    p_export.add_argument("-o", "--output", help="Output file path (default: unc_export.csv)")
    p_export.set_defaults(func=cmd_export)

    # serve
    p_serve = subparsers.add_parser("serve", aliases=["ui"], help="Start local Material web control panel")
    p_serve.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8788, help="Port to listen on (default: 8788)")
    p_serve.add_argument("--open", action="store_true", help="Open browser on start")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
