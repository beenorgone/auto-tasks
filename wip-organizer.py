#!/usr/bin/env python3
"""Read-only first WIP indexing and Kanban reporting for Documents/2026.

This tool deliberately has no move, delete, or filing command.  It records
classification proposals in SQLite and renders a local, static dashboard so a
person can approve a dossier before any future filing automation is enabled.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


TOOL_ROOT = Path(__file__).resolve().parent
DOCUMENTS_ROOT = TOOL_ROOT.parent
YEAR_ROOT = DOCUMENTS_ROOT / "2026"
STATE_DIR_NAME = ".wip"
DEFAULT_SCOPES = (
    "WIP ke toan thue",
    "Review Label",
    "ho so danh gia CUC 20260421",
    "Tự công bố sản phẩm trong nước-20260607T192041Z-3-001",
)
WIP_DIRECTORIES = ("00_INBOX", "10_WIP", "90_NEEDS_REVIEW", "91_TECH_QUARANTINE", "99_ARCHIVE_MANIFESTS")
VALID_STATUSES = ("captured", "auto_classified", "needs_review", "in_progress", "waiting_external", "approved", "filed", "archived")

DEFAULT_RULES = {
    "version": 2,
    "excluded_paths": [
        "03/Adults/**", "03/Kids/**", "03/Tram Nam Co Don/**", "tieng goi noi hoang da - Jack London/**",
        ".wip/**", "00_INBOX/.*/**",
    ],
    "generated_name_patterns": [r"^pdftohtml", r"^pdftotext", r"^text\.txt$", r"^agents\.md$"],
    "categories": [
        {"id": "finance_ops", "label": "Finance & Operations", "confidence": 0.94,
         "patterns": [r"(?:^|/)(?:wip ke toan thue|acb saoke)(?:/|$)", r"order\.", r"ghtk_", r"bien_ban_doi_soat", r"c[oô]ng n[oợ]", r"chi[_ -]?tiet[_ -]?cong[_ -]?no", r"sao ?ke", r"so_tien_gui", r"iv-mtt-batch", r"h[oó]a[- ]?d[oơ]n", r"invoice", r"\bunc\b"],
         "target": "IVAR/Accounting - Ke toan/Data - Du lieu"},
        {"id": "quality_regulatory", "label": "Quality & Regulatory", "confidence": 0.93,
         "patterns": [r"tự công bố", r"ho so danh gia cuc", r"\bcoa\b", r"\bqcvn\b", r"attp", r"ki[eể]m nghi[eệ]m", r"ssop", r"chứng nh[aậ]n", r"c[oô]ng b[oố]"],
         "target": "IVAR/QC - Quan ly chat luong"},
        {"id": "packaging", "label": "Packaging & Product Artwork", "confidence": 0.92,
         "patterns": [r"review label", r"\blabel\b", r"sachet", r"\bbox\b", r"bao b[iì]", r"in an"],
         "target": "IVAR/Manufacturing - San xuat/In an"},
        {"id": "commerce", "label": "Commerce & Sales", "confidence": 0.90,
         "patterns": [r"b2b", r"shopee", r"b[aả]ng gi[aá]", r"ch[ưu]ơ?ng tr[iì]nh", r"thong bao", r"shop-stats", r"mass_upload"],
         "target": "IVAR/Marketing-Sales-CS"},
        {"id": "legal_corporate", "label": "Legal & Corporate", "confidence": 0.88,
         "patterns": [r"h[oợ]p d[oồ]ng", r"u[yỷ][ _-]?quy[eề]n", r"dkkd", r"bhxh", r"v[ăa]n ph[oò]ng", r"sepay", r"vietnam bank", r"vietinbank"],
         "target": "IVAR/Legal - Phap ly"},
        {"id": "technical", "label": "Technical / Quarantine", "confidence": 0.97,
         "patterns": [r"\.(?:log|deb|dmg|tar\.gz)$", r"(?:^|/)(?:plugin codes|code)(?:/|$)", r"^new\.html$"],
         "target": "2026/91_TECH_QUARANTINE"},
    ],
}


def state_dir(year_root: Path) -> Path:
    return year_root / STATE_DIR_NAME


def db_path(year_root: Path) -> Path:
    return state_dir(year_root) / "index.sqlite"


def rules_path(year_root: Path) -> Path:
    return state_dir(year_root) / "rules.json"


def ensure_state(year_root: Path) -> tuple[Path, Path]:
    state = state_dir(year_root)
    state.mkdir(parents=True, exist_ok=True)
    for name in WIP_DIRECTORIES:
        (year_root / name).mkdir(exist_ok=True)
    rule_file = rules_path(year_root)
    if not rule_file.exists():
        rule_file.write_text(json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return db_path(year_root), rule_file


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            modified_at INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            category TEXT NOT NULL,
            confidence REAL NOT NULL,
            classification_reason TEXT NOT NULL,
            proposed_target TEXT,
            dossier_id TEXT NOT NULL,
            status TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_documents_dossier ON documents(dossier_id);
        CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
        CREATE TABLE IF NOT EXISTS dossiers (
            dossier_id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            proposed_target TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            subject TEXT NOT NULL,
            detail TEXT NOT NULL
        );
        """
    )
    return conn


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def normalized(value: str) -> str:
    return value.replace("đ", "d").replace("Đ", "D").casefold()


def load_rules(year_root: Path) -> dict:
    with rules_path(year_root).open(encoding="utf-8") as handle:
        return json.load(handle)


def is_generated(path: Path, rules: dict) -> bool:
    return any(re.search(pattern, path.name, re.I) for pattern in rules["generated_name_patterns"])


def is_excluded(relative: Path, rules: dict) -> bool:
    relative_text = relative.as_posix()
    for pattern in rules["excluded_paths"]:
        # Path.match supports ** and keeps the policy readable for non-developers.
        if relative.match(pattern) or relative_text == pattern.rstrip("/**"):
            return True
    return False


def classify(relative: Path, rules: dict) -> tuple[str, float, str, str | None]:
    text = normalized(relative.as_posix())
    for category in rules["categories"]:
        matched = [pattern for pattern in category["patterns"] if re.search(pattern, text, re.I)]
        if matched:
            return category["id"], float(category["confidence"]), f"rule: {matched[0]}", category["target"]
    return "needs_review", 0.0, "no deterministic rule matched", None


def extract_period(name: str, modified_at: int) -> str:
    match = re.search(r"(20\d{2})[-._]?(0[1-9]|1[0-2])(?:[-._]?\d{2})?", name)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return dt.datetime.fromtimestamp(modified_at).strftime("%Y-%m")


def dossier_for(category: str, relative: Path, modified_at: int) -> str:
    basename = normalized(relative.name)
    if category == "needs_review":
        subject = re.sub(r"[^a-z0-9]+", "-", normalized(relative.stem)).strip("-")[:32] or "document"
    elif "ghtk" in basename:
        subject = "ghtk"
    elif basename.startswith("order."):
        subject = "orders"
    elif "doi soat" in basename or "đối soát" in relative.name.casefold():
        subject = "reconciliation"
    elif "label" in basename:
        subject = "labels"
    elif "shopee" in basename:
        subject = "shopee"
    else:
        subject = normalized(relative.parts[0] if len(relative.parts) > 1 else "root")
        subject = re.sub(r"[^a-z0-9]+", "-", subject).strip("-")[:32] or "general"
    return f"{category.upper()}-{extract_period(relative.name, modified_at)}-{subject.upper()}"


def files_for_scan(year_root: Path, scopes: Iterable[Path], root_files: bool, rules: dict) -> Iterable[Path]:
    seen: set[Path] = set()
    if root_files:
        for entry in year_root.iterdir():
            if entry.is_file():
                seen.add(entry)
    for scope in scopes:
        if not scope.exists():
            continue
        if scope.is_file():
            seen.add(scope)
        else:
            for entry in scope.rglob("*"):
                if entry.is_file():
                    seen.add(entry)
    for entry in sorted(seen):
        relative = entry.relative_to(year_root)
        if not is_excluded(relative, rules) and not is_generated(entry, rules):
            yield entry


def mark_scopes_inactive(conn: sqlite3.Connection, year_root: Path, scopes: Iterable[Path], root_files: bool) -> None:
    """Reconcile only the requested scope, preserving documents from other scans."""
    if root_files:
        conn.execute("UPDATE documents SET active = 0 WHERE instr(path, '/') = 0")
    for scope in scopes:
        if scope == year_root:
            conn.execute("UPDATE documents SET active = 0")
            continue
        try:
            relative = scope.relative_to(year_root).as_posix()
        except ValueError:
            continue
        conn.execute("UPDATE documents SET active = 0 WHERE path = ? OR path LIKE ?", (relative, relative + "/%"))


def scan(year_root: Path, source_names: list[str], root_files: bool) -> dict[str, int]:
    database, _ = ensure_state(year_root)
    rules = load_rules(year_root)
    sources = [year_root / name for name in (source_names or list(DEFAULT_SCOPES))]
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    counters: Counter[str] = Counter()
    with open_db(database) as conn:
        mark_scopes_inactive(conn, year_root, sources, root_files)
        for path in files_for_scan(year_root, sources, root_files, rules):
            stat = path.stat()
            relative = path.relative_to(year_root)
            prior = conn.execute("SELECT sha256, byte_size, modified_at, status FROM documents WHERE path = ?", (relative.as_posix(),)).fetchone()
            sha = prior["sha256"] if prior and prior["byte_size"] == stat.st_size and prior["modified_at"] == stat.st_mtime_ns else digest(path)
            category, confidence, reason, target = classify(relative, rules)
            dossier_id = dossier_for(category, relative, stat.st_mtime)
            status = prior["status"] if prior and prior["status"] not in {"captured", "auto_classified", "needs_review"} else ("auto_classified" if confidence >= 0.85 else "needs_review")
            conn.execute(
                """INSERT INTO documents(path, sha256, byte_size, modified_at, observed_at, category, confidence, classification_reason, proposed_target, dossier_id, status, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, byte_size=excluded.byte_size,
                     modified_at=excluded.modified_at, observed_at=excluded.observed_at, category=excluded.category,
                     confidence=excluded.confidence, classification_reason=excluded.classification_reason,
                     proposed_target=excluded.proposed_target, dossier_id=excluded.dossier_id, status=excluded.status, active=1""",
                (relative.as_posix(), sha, stat.st_size, stat.st_mtime_ns, now, category, confidence, reason, target, dossier_id, status),
            )
            conn.execute(
                """INSERT INTO dossiers(dossier_id, category, status, proposed_target, updated_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(dossier_id) DO UPDATE SET category=excluded.category, proposed_target=excluded.proposed_target, updated_at=excluded.updated_at""",
                (dossier_id, category, status, target, now),
            )
            counters[category] += 1
        conn.execute("INSERT INTO events(created_at, event_type, subject, detail) VALUES (?, 'scan', 'documents', ?)", (now, json.dumps(dict(counters), ensure_ascii=False)))
    return dict(counters)


def set_dossier_status(year_root: Path, dossier_id: str, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    database, _ = ensure_state(year_root)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open_db(database) as conn:
        found = conn.execute("SELECT 1 FROM dossiers WHERE dossier_id = ?", (dossier_id,)).fetchone()
        if not found:
            raise ValueError(f"dossier not found: {dossier_id}")
        conn.execute("UPDATE dossiers SET status = ?, updated_at = ? WHERE dossier_id = ?", (status, now, dossier_id))
        conn.execute("UPDATE documents SET status = ? WHERE dossier_id = ? AND active = 1", (status, dossier_id))
        conn.execute("INSERT INTO events(created_at, event_type, subject, detail) VALUES (?, 'status_change', ?, ?)", (now, dossier_id, status))


def dashboard(year_root: Path, output: Path | None = None) -> Path:
    database, _ = ensure_state(year_root)
    output = output or state_dir(year_root) / "dashboard.html"
    labels_by_category = {item["id"]: item["label"] for item in load_rules(year_root)["categories"]}
    labels_by_category["needs_review"] = "Needs review"
    with open_db(database) as conn:
        rows = conn.execute(
            """SELECT d.dossier_id, d.category, d.status, d.proposed_target, d.updated_at, COUNT(doc.path) AS document_count,
                      GROUP_CONCAT(doc.path, '||') AS paths
                 FROM dossiers d JOIN documents doc ON doc.dossier_id = d.dossier_id
                 WHERE doc.active = 1 GROUP BY d.dossier_id ORDER BY d.updated_at DESC"""
        ).fetchall()
    by_status: dict[str, list[sqlite3.Row]] = {status: [] for status in VALID_STATUSES}
    for row in rows:
        by_status.setdefault(row["status"], []).append(row)
    columns = ["captured", "auto_classified", "needs_review", "in_progress", "waiting_external", "approved", "filed"]
    labels = {"captured": "Captured", "auto_classified": "Auto-classified", "needs_review": "Needs review", "in_progress": "In progress", "waiting_external": "Waiting external", "approved": "Approved", "filed": "Filed"}
    board = []
    for status in columns:
        cards = []
        for row in by_status[status]:
            paths = (row["paths"] or "").split("||")
            preview = "<br>".join(html.escape(path) for path in paths[:3])
            extra = f"<div class='more'>+{len(paths)-3} files</div>" if len(paths) > 3 else ""
            target = html.escape(row["proposed_target"] or "No target proposed")
            category_label = html.escape(labels_by_category.get(row["category"], row["category"]))
            cards.append(f"<article class='card'><h3>{html.escape(row['dossier_id'])}</h3><p class='category'>{category_label} · {row['document_count']} files</p><p class='target'>{target}</p><p class='paths'>{preview}</p>{extra}</article>")
        board.append(f"<section class='column'><header><h2>{labels[status]}</h2><span>{len(by_status[status])}</span></header>{''.join(cards) or '<p class=empty>No dossier</p>'}</section>")
    total = sum(row["document_count"] for row in rows)
    output.write_text(f"""<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>2026 WIP Kanban</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f6f7fb;color:#1c1b20;font:14px/1.45 Arial,sans-serif}}main{{padding:24px;max-width:2200px;margin:auto}}h1{{margin:0;font-size:28px}}.subtitle{{color:#5f6368}}.board{{display:grid;grid-template-columns:repeat(7,minmax(265px,1fr));gap:16px;overflow-x:auto;padding:8px 0 20px}}.column{{background:#eef0f7;border-radius:16px;padding:12px;min-height:280px}}.column header{{display:flex;align-items:center;justify-content:space-between;margin:2px 4px 12px}}.column h2{{font-size:15px;margin:0}}.column header span{{background:#d9e2ff;color:#17386d;border-radius:999px;padding:2px 9px;font-weight:bold}}.card{{background:#fff;border-radius:12px;padding:13px;margin:10px 0;box-shadow:0 1px 3px #0002;border-left:4px solid #4f73c7}}.card h3{{font-size:13px;margin:0 0 6px;overflow-wrap:anywhere}}.category{{color:#4a5568;margin:0 0 8px;text-transform:capitalize}}.target{{background:#eef4ff;color:#174ea6;margin:0 -2px 9px;padding:5px 7px;border-radius:6px;overflow-wrap:anywhere}}.paths{{color:#5f6368;font-size:12px;margin:0;overflow-wrap:anywhere}}.more,.empty{{color:#697386;font-style:italic}}@media(max-width:700px){{main{{padding:16px}}.board{{grid-template-columns:repeat(7,84vw)}}}}</style>
<main><h1>2026 WIP Kanban</h1><p class='subtitle'>{len(rows)} dossiers · {total} active documents · Index only: no file has been moved or deleted.</p><div class='board'>{''.join(board)}</div></main>""", encoding="utf-8")
    return output


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--year-root", type=Path, default=YEAR_ROOT, help="2026 folder; defaults to Documents/2026")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create empty WIP folders, state database and editable rules.json")
    scan_parser = sub.add_parser("scan", help="Index files only; never moves, renames, copies or deletes")
    scan_parser.add_argument("--source", action="append", default=[], help="relative folder or file to index; repeatable")
    scan_parser.add_argument("--no-root-files", action="store_true", help="do not include loose files directly under 2026")
    dash = sub.add_parser("dashboard", help="Render a static Material-style Kanban HTML page")
    dash.add_argument("--output", type=Path)
    status = sub.add_parser("set-status", help="Update a dossier status in the index; does not touch documents")
    status.add_argument("dossier_id")
    status.add_argument("status", choices=VALID_STATUSES)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    year_root = args.year_root.resolve()
    if not year_root.is_dir():
        raise SystemExit(f"Year root does not exist: {year_root}")
    if args.command == "init":
        database, rule_file = ensure_state(year_root)
        print(f"Initialized {state_dir(year_root)}\nRules: {rule_file}\nDatabase: {database}")
    elif args.command == "scan":
        counters = scan(year_root, args.source, not args.no_root_files)
        print("Indexed (no files moved): " + ", ".join(f"{key}={value}" for key, value in sorted(counters.items())))
    elif args.command == "dashboard":
        print(dashboard(year_root, args.output))
    elif args.command == "set-status":
        set_dossier_status(year_root, args.dossier_id, args.status)
        print(f"Updated {args.dossier_id} to {args.status}; no files changed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
