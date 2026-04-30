#!/bin/sh
# =============================================================================
# IVAR Accounting File Sync Script
# POSIX sh compatible. Supports dry-run, duplicate detection, OCR fallback
# for scanned PDFs, and a lightweight SQLite state database managed by python3.
# =============================================================================

set -eu

HOME_DIR=${HOME:-/home/beenorgone}
DOCS_ROOT="${HOME_DIR}/Documents"
DOWNLOADS_ROOT="${HOME_DIR}/Downloads"
IVAR_DATA="${IVAR_DATA:-${DOCS_ROOT}/Drives/gDriveProjects/Working/Projects/IVAR/Accounting - Ke toan/Data - Du lieu}"
STATE_DIR="${STATE_DIR:-${DOCS_ROOT}/.auto-tasks/ivar-documents-organizer}"
LOG_DIR="${STATE_DIR}/logs"
DB_PATH="${STATE_DIR}/processed.db"
VENDOR_DB_PATH="${STATE_DIR}/vendors.db"
CURRENT_DATE="${CURRENT_DATE:-$(date '+%Y-%m-%d')}"
CURRENT_YEAR=$(printf '%s' "$CURRENT_DATE" | cut -d- -f1)
CURRENT_MONTH=$(printf '%s' "$CURRENT_DATE" | cut -d- -f2)
CURRENT_DAY=$(printf '%s' "$CURRENT_DATE" | cut -d- -f3)
CURRENT_YEAR_ROOT="${DOCS_ROOT}/${CURRENT_YEAR}"
CURRENT_MONTH_ROOT="${DOCS_ROOT}/${CURRENT_YEAR}/${CURRENT_MONTH}"
DRY_RUN=0
QUIET=0
DEEP_MODE=0
NO_SYNC=0
RESCAN_MODE=0
VENDOR_LIST_MODE=0

usage() {
  cat <<'EOF'
Usage: sh ivar-documents-organizer.sh [--dry-run] [--deep] [--rescan] [--vendors] [--no-sync] [--quiet]

Options:
  --dry-run   Print planned actions without moving/copying files or writing DB.
  --deep      Force month-end rollover logic as if today were the first day of next month.
  --rescan    Re-read classified destination folders and restructure/rename files.
  --vendors   Print known vendor MST/name reference from the local vendor DB.
  --no-sync   Skip the final rclone project sync.
  --quiet     Reduce console output. Daily logs are still written.
  --help      Show this help text.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run|-n)
      DRY_RUN=1
      ;;
    --deep)
      DEEP_MODE=1
      ;;
    --rescan)
      RESCAN_MODE=1
      ;;
    --vendors)
      VENDOR_LIST_MODE=1
      ;;
    --no-sync)
      NO_SYNC=1
      ;;
    --quiet)
      QUIET=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/${CURRENT_DATE}.log"

log_line() {
  level=$1
  shift
  message=$*
  timestamp=$(date '+%Y-%m-%d %H:%M:%S')
  printf '%s [%s] %s\n' "$timestamp" "$level" "$message" >>"$LOG_FILE"
  if [ "$QUIET" -eq 0 ]; then
    printf '%s [%s] %s\n' "$timestamp" "$level" "$message"
  fi
}

info() {
  log_line INFO "$@"
}

warn() {
  log_line WARN "$@"
}

error() {
  log_line ERROR "$@"
}

run_cmd() {
  if [ "$DRY_RUN" -eq 1 ]; then
    info "DRY-RUN $*"
    return 0
  fi
  "$@"
}

ensure_dir() {
  if [ -d "$1" ]; then
    return 0
  fi
  run_cmd mkdir -p "$1"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    error "Required command not found: $1"
    exit 1
  fi
}

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

file_mtime_of() {
  if stat -c '%Y' "$1" >/dev/null 2>&1; then
    stat -c '%Y' "$1"
  else
    stat -f '%m' "$1"
  fi
}

file_size_of() {
  if stat -c '%s' "$1" >/dev/null 2>&1; then
    stat -c '%s' "$1"
  else
    stat -f '%z' "$1"
  fi
}

db_exec() {
  require_command python3
  python3 - "$DB_PATH" "$@" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
args = sys.argv[2:]
mode = args[0]

conn = sqlite3.connect(db_path)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS processed_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_path TEXT,
        source_mtime INTEGER,
        file_size INTEGER,
        sha256 TEXT,
        final_path TEXT,
        status TEXT,
        invoice_year TEXT,
        invoice_month TEXT,
        vendor_name TEXT,
        vendor_tax TEXT,
        invoice_number TEXT,
        notes TEXT,
        processed_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """
)
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_processed_source ON processed_files(source_path, source_mtime, file_size)"
)
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_processed_sha ON processed_files(sha256)"
)

if mode == "seen-source":
    source_path, source_mtime, file_size = args[1:4]
    row = conn.execute(
        """
        SELECT 1
        FROM processed_files
        WHERE source_path = ? AND source_mtime = ? AND file_size = ?
        LIMIT 1
        """,
        (source_path, source_mtime, file_size),
    ).fetchone()
    print("1" if row else "0")
elif mode == "find-sha":
    sha = args[1]
    row = conn.execute(
        """
        SELECT final_path, status
        FROM processed_files
        WHERE sha256 = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (sha,),
    ).fetchone()
    if row:
        print((row[0] or "") + "\t" + (row[1] or ""))
    else:
        print("")
elif mode == "insert":
    (
        source_path,
        source_mtime,
        file_size,
        sha,
        final_path,
        status,
        invoice_year,
        invoice_month,
        vendor_name,
        vendor_tax,
        invoice_number,
        notes,
    ) = args[1:13]
    conn.execute(
        """
        INSERT INTO processed_files (
            source_path, source_mtime, file_size, sha256, final_path, status,
            invoice_year, invoice_month, vendor_name, vendor_tax,
            invoice_number, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_path,
            int(source_mtime),
            int(file_size),
            sha,
            final_path,
            status,
            invoice_year,
            invoice_month,
            vendor_name,
            vendor_tax,
            invoice_number,
            notes,
        ),
    )
    conn.commit()
else:
    raise SystemExit(f"Unknown db mode: {mode}")
PY
}

db_seen_source() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '0\n'
    return 0
  fi
  db_exec "seen-source" "$1" "$2" "$3"
}

db_find_sha() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '\n'
    return 0
  fi
  db_exec "find-sha" "$1"
}

db_insert() {
  if [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  db_exec "insert" "$@"
}

metadata_cache_exec() {
  require_command python3
  python3 - "$DB_PATH" "$@" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
args = sys.argv[2:]
mode = args[0]

conn = sqlite3.connect(db_path)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS invoice_metadata_cache (
        sha256 TEXT PRIMARY KEY,
        is_invoice TEXT,
        invoice_year TEXT,
        invoice_month TEXT,
        invoice_day TEXT,
        vendor_name TEXT,
        vendor_tax TEXT,
        invoice_number TEXT,
        is_meta TEXT,
        seller_is_ivar TEXT,
        buyer_is_ivar TEXT,
        parse_source TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """
)

if mode == "get":
    sha = args[1]
    row = conn.execute(
        """
        SELECT is_invoice, invoice_year, invoice_month, invoice_day, vendor_name, vendor_tax,
               invoice_number, is_meta, seller_is_ivar, buyer_is_ivar, parse_source
        FROM invoice_metadata_cache
        WHERE sha256 = ?
        """,
        (sha,),
    ).fetchone()
    if row:
        print("\t".join(value or "" for value in row))
    else:
        print("")
elif mode == "put":
    (
        sha,
        is_invoice,
        invoice_year,
        invoice_month,
        invoice_day,
        vendor_name,
        vendor_tax,
        invoice_number,
        is_meta,
        seller_is_ivar,
        buyer_is_ivar,
        parse_source,
    ) = args[1:13]
    conn.execute(
        """
        INSERT INTO invoice_metadata_cache (
            sha256, is_invoice, invoice_year, invoice_month, invoice_day, vendor_name,
            vendor_tax, invoice_number, is_meta, seller_is_ivar, buyer_is_ivar, parse_source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sha256) DO UPDATE SET
            is_invoice = excluded.is_invoice,
            invoice_year = excluded.invoice_year,
            invoice_month = excluded.invoice_month,
            invoice_day = excluded.invoice_day,
            vendor_name = excluded.vendor_name,
            vendor_tax = excluded.vendor_tax,
            invoice_number = excluded.invoice_number,
            is_meta = excluded.is_meta,
            seller_is_ivar = excluded.seller_is_ivar,
            buyer_is_ivar = excluded.buyer_is_ivar,
            parse_source = excluded.parse_source,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            sha,
            is_invoice,
            invoice_year,
            invoice_month,
            invoice_day,
            vendor_name,
            vendor_tax,
            invoice_number,
            is_meta,
            seller_is_ivar,
            buyer_is_ivar,
            parse_source,
        ),
    )
    conn.commit()
else:
    raise SystemExit(f"Unknown cache mode: {mode}")
PY
}

metadata_cache_get() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '\n'
    return 0
  fi
  metadata_cache_exec "get" "$1"
}

metadata_cache_put() {
  if [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  metadata_cache_exec "put" "$@"
}

metadata_cache_import_processed() {
  if [ "$DRY_RUN" -eq 1 ] || [ ! -f "$DB_PATH" ]; then
    return 0
  fi

  require_command python3
  python3 - "$DB_PATH" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
conn = sqlite3.connect(db_path)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS invoice_metadata_cache (
        sha256 TEXT PRIMARY KEY,
        is_invoice TEXT,
        invoice_year TEXT,
        invoice_month TEXT,
        invoice_day TEXT,
        vendor_name TEXT,
        vendor_tax TEXT,
        invoice_number TEXT,
        is_meta TEXT,
        seller_is_ivar TEXT,
        buyer_is_ivar TEXT,
        parse_source TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """
)
conn.execute(
    """
    DELETE FROM invoice_metadata_cache
    WHERE vendor_name = 'Unknown Vendor'
       OR invoice_year NOT BETWEEN '2000' AND '2027'
    """
)
conn.execute(
    """
    INSERT INTO invoice_metadata_cache (
        sha256, is_invoice, invoice_year, invoice_month, invoice_day, vendor_name,
        vendor_tax, invoice_number, is_meta, seller_is_ivar, buyer_is_ivar, parse_source
    )
    SELECT sha256, '1', invoice_year, invoice_month, '', vendor_name, vendor_tax,
           invoice_number, CASE WHEN notes LIKE '%facebook%' THEN '1' ELSE '0' END,
           CASE WHEN vendor_tax = '0109555754' THEN '1' ELSE '0' END,
           '1',
           'processed-db'
    FROM processed_files
    WHERE status = 'invoice'
      AND COALESCE(sha256, '') <> ''
      AND COALESCE(vendor_tax, '') <> ''
      AND COALESCE(invoice_number, '') <> ''
      AND COALESCE(vendor_name, '') <> ''
      AND vendor_name <> 'Unknown Vendor'
      AND invoice_year BETWEEN '2000' AND '2027'
    ON CONFLICT(sha256) DO NOTHING
    """
)
conn.commit()
PY
}

vendor_db_upsert() {
  vendor_name=$1
  vendor_tax=$2
  invoice_year=$3
  invoice_month=$4
  source_path=$5
  final_path=$6

  if [ "$DRY_RUN" -eq 1 ] || [ -z "$vendor_name" ] || [ -z "$vendor_tax" ]; then
    return 0
  fi

  require_command python3
  python3 - "$VENDOR_DB_PATH" "$vendor_name" "$vendor_tax" "$invoice_year" "$invoice_month" "$source_path" "$final_path" <<'PY'
import re
import sqlite3
import sys
import unicodedata

db_path, vendor_name, vendor_tax, invoice_year, invoice_month, source_path, final_path = sys.argv[1:8]

def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()

def strip_accents(value: str) -> str:
    value = (value or "").replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFKD", value)
    return value.encode("ascii", "ignore").decode("ascii")

def folder_name(name: str, tax: str) -> str:
    value = normalize_spaces(name)
    value = value.replace("/", "-").replace("\\", "-").strip(" .")
    if tax:
        value = normalize_spaces(f"{value} {tax}")
    return value

def usable_vendor(name: str, tax: str) -> bool:
    if not name or not tax or tax == "0109555754":
        return False
    name_ascii = normalize_spaces(strip_accents(name)).upper()
    bad_markers = (
        "UNKNOWN VENDOR",
        "KY HIEU",
        "SO (NO",
        "SO NO",
        "DIA CHI",
        "ADDRESS",
        "DON VI CUNG CAP",
        "DC:",
        "TEL:",
    )
    return len(name_ascii) <= 140 and not any(marker in name_ascii for marker in bad_markers)

vendor_name = normalize_spaces(vendor_name)
vendor_tax = re.sub(r"\D", "", vendor_tax)
if not usable_vendor(vendor_name, vendor_tax):
    raise SystemExit
vendor_name_ascii = normalize_spaces(strip_accents(vendor_name)).upper()
folder = folder_name(vendor_name, vendor_tax)

conn = sqlite3.connect(db_path)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS vendors (
        vendor_tax TEXT PRIMARY KEY,
        vendor_name TEXT NOT NULL,
        vendor_name_ascii TEXT,
        folder_name TEXT,
        first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
        seen_count INTEGER NOT NULL DEFAULT 1,
        last_invoice_year TEXT,
        last_invoice_month TEXT,
        last_source_path TEXT,
        last_final_path TEXT
    )
    """
)
conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_name_ascii ON vendors(vendor_name_ascii)")
row = conn.execute("SELECT seen_count FROM vendors WHERE vendor_tax = ?", (vendor_tax,)).fetchone()
if row:
    conn.execute(
        """
        UPDATE vendors
        SET vendor_name = ?,
            vendor_name_ascii = ?,
            folder_name = ?,
            last_seen_at = CURRENT_TIMESTAMP,
            seen_count = seen_count + 1,
            last_invoice_year = ?,
            last_invoice_month = ?,
            last_source_path = ?,
            last_final_path = ?
        WHERE vendor_tax = ?
        """,
        (vendor_name, vendor_name_ascii, folder, invoice_year, invoice_month, source_path, final_path, vendor_tax),
    )
else:
    conn.execute(
        """
        INSERT INTO vendors (
            vendor_tax, vendor_name, vendor_name_ascii, folder_name,
            last_invoice_year, last_invoice_month, last_source_path, last_final_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (vendor_tax, vendor_name, vendor_name_ascii, folder, invoice_year, invoice_month, source_path, final_path),
    )
conn.commit()
PY
}

vendor_db_import_processed() {
  if [ "$DRY_RUN" -eq 1 ] || [ ! -f "$DB_PATH" ]; then
    return 0
  fi

  require_command python3
  python3 - "$VENDOR_DB_PATH" "$DB_PATH" <<'PY'
import re
import sqlite3
import sys
import unicodedata

vendor_db_path, processed_db_path = sys.argv[1:3]

def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()

def strip_accents(value: str) -> str:
    value = (value or "").replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFKD", value)
    return value.encode("ascii", "ignore").decode("ascii")

def folder_name(name: str, tax: str) -> str:
    value = normalize_spaces(name)
    value = value.replace("/", "-").replace("\\", "-").strip(" .")
    if tax:
        value = normalize_spaces(f"{value} {tax}")
    return value

def usable_vendor(name: str, tax: str) -> bool:
    if not name or not tax or tax == "0109555754":
        return False
    name_ascii = normalize_spaces(strip_accents(name)).upper()
    bad_markers = (
        "UNKNOWN VENDOR",
        "KY HIEU",
        "SO (NO",
        "SO NO",
        "DIA CHI",
        "ADDRESS",
        "DON VI CUNG CAP",
        "DC:",
        "TEL:",
    )
    return len(name_ascii) <= 140 and not any(marker in name_ascii for marker in bad_markers)

vendor_conn = sqlite3.connect(vendor_db_path)
vendor_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS vendors (
        vendor_tax TEXT PRIMARY KEY,
        vendor_name TEXT NOT NULL,
        vendor_name_ascii TEXT,
        folder_name TEXT,
        first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
        seen_count INTEGER NOT NULL DEFAULT 1,
        last_invoice_year TEXT,
        last_invoice_month TEXT,
        last_source_path TEXT,
        last_final_path TEXT
    )
    """
)
vendor_conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_name_ascii ON vendors(vendor_name_ascii)")
vendor_conn.execute(
    """
    DELETE FROM vendors
    WHERE vendor_tax = '0109555754'
       OR vendor_name_ascii LIKE '%UNKNOWN VENDOR%'
       OR vendor_name_ascii LIKE '%KY HIEU%'
       OR vendor_name_ascii LIKE '%SO (NO%'
       OR vendor_name_ascii LIKE '%SO NO%'
       OR vendor_name_ascii LIKE '%DIA CHI%'
       OR vendor_name_ascii LIKE '%ADDRESS%'
       OR vendor_name_ascii LIKE '%DON VI CUNG CAP%'
       OR vendor_name_ascii LIKE '%DC:%'
       OR vendor_name_ascii LIKE '%TEL:%'
       OR last_invoice_year > '2027'
       OR LENGTH(vendor_name_ascii) > 140
    """
)

processed_conn = sqlite3.connect(processed_db_path)
rows = processed_conn.execute(
    """
    SELECT vendor_tax, vendor_name, invoice_year, invoice_month, source_path, final_path, COUNT(*) AS seen_count
    FROM processed_files
    WHERE status IN ('invoice', 'invoice-xml')
      AND COALESCE(vendor_tax, '') <> ''
      AND COALESCE(vendor_name, '') <> ''
      AND (COALESCE(invoice_year, '') = '' OR invoice_year BETWEEN '2000' AND '2027')
    GROUP BY vendor_tax
    ORDER BY MAX(id)
    """
).fetchall()

for vendor_tax, vendor_name, invoice_year, invoice_month, source_path, final_path, seen_count in rows:
    vendor_tax = re.sub(r"\D", "", vendor_tax or "")
    vendor_name = normalize_spaces(vendor_name)
    if not usable_vendor(vendor_name, vendor_tax):
        continue
    vendor_name_ascii = normalize_spaces(strip_accents(vendor_name)).upper()
    folder = folder_name(vendor_name, vendor_tax)
    vendor_conn.execute(
        """
        INSERT INTO vendors (
            vendor_tax, vendor_name, vendor_name_ascii, folder_name, seen_count,
            last_invoice_year, last_invoice_month, last_source_path, last_final_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(vendor_tax) DO UPDATE SET
            vendor_name = excluded.vendor_name,
            vendor_name_ascii = excluded.vendor_name_ascii,
            folder_name = excluded.folder_name,
            seen_count = MAX(vendors.seen_count, excluded.seen_count),
            last_seen_at = CURRENT_TIMESTAMP,
            last_invoice_year = excluded.last_invoice_year,
            last_invoice_month = excluded.last_invoice_month,
            last_source_path = excluded.last_source_path,
            last_final_path = excluded.last_final_path
        """,
        (vendor_tax, vendor_name, vendor_name_ascii, folder, int(seen_count), invoice_year, invoice_month, source_path, final_path),
    )

vendor_conn.commit()
PY
}

vendor_db_list() {
  if [ ! -f "$VENDOR_DB_PATH" ]; then
    warn "Vendor DB not found: $VENDOR_DB_PATH"
    return 0
  fi

  require_command python3
  python3 - "$VENDOR_DB_PATH" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
rows = conn.execute(
    """
    SELECT vendor_tax, vendor_name, seen_count, COALESCE(last_invoice_year, ''), COALESCE(last_invoice_month, '')
    FROM vendors
    ORDER BY vendor_name_ascii, vendor_tax
    """
).fetchall()
print("MST\tVendor\tSeen\tLast year\tLast month")
for row in rows:
    print("\t".join(str(value) for value in row))
PY
}

vendor_db_lookup_name() {
  vendor_tax=$1
  if [ -z "$vendor_tax" ] || [ ! -f "$VENDOR_DB_PATH" ]; then
    printf '\n'
    return 0
  fi

  require_command python3
  python3 - "$VENDOR_DB_PATH" "$vendor_tax" <<'PY'
import re
import sqlite3
import sys

db_path, vendor_tax = sys.argv[1:3]
vendor_tax = re.sub(r"\D", "", vendor_tax)
conn = sqlite3.connect(db_path)
try:
    row = conn.execute("SELECT vendor_name FROM vendors WHERE vendor_tax = ?", (vendor_tax,)).fetchone()
except sqlite3.Error:
    row = None
print(row[0] if row else "")
PY
}

tmp_dir_make() {
  mktemp -d "${TMPDIR:-/tmp}/ivar-accounting-sync.XXXXXX"
}

cleanup_dir() {
  if [ -n "${1:-}" ] && [ -d "$1" ]; then
    rm -rf "$1"
  fi
}

extract_pdf_text() {
  pdf_path=$1
  out_file=$2
  : >"$out_file"

  if command -v pdftotext >/dev/null 2>&1; then
    pdftotext -layout "$pdf_path" - 2>/dev/null >"$out_file" || true
  fi

  if [ "$(wc -c <"$out_file" | tr -d ' ')" -ge 80 ]; then
    return 0
  fi

  if command -v tesseract >/dev/null 2>&1 && command -v pdftoppm >/dev/null 2>&1; then
    ocr_dir=$(tmp_dir_make)
    if pdftoppm -f 1 -l 3 -r 200 -png "$pdf_path" "${ocr_dir}/page" >/dev/null 2>&1; then
      : >"$out_file"
      found_png=0
      for image_path in "${ocr_dir}"/page-*.png; do
        if [ -f "$image_path" ]; then
          found_png=1
          tesseract "$image_path" stdout -l vie+eng --psm 6 -c preserve_interword_spaces=1 2>/dev/null >>"$out_file" || \
            tesseract "$image_path" stdout -l eng --psm 6 -c preserve_interword_spaces=1 2>/dev/null >>"$out_file" || true
          printf '\n' >>"$out_file"
        fi
      done
      if [ "$found_png" -eq 1 ]; then
        info "OCR fallback used for PDF: $pdf_path"
      fi
    else
      warn "OCR fallback failed to rasterize PDF: $pdf_path"
    fi
    cleanup_dir "$ocr_dir"
  else
    warn "Skipping OCR fallback for PDF without tesseract/pdftoppm: $pdf_path"
  fi
}

extract_text_for_file() {
  file_path=$1
  out_file=$2
  case $(printf '%s' "$file_path" | tr '[:upper:]' '[:lower:]') in
    *.pdf)
      extract_pdf_text "$file_path" "$out_file"
      ;;
    *)
      : >"$out_file"
      ;;
  esac
}

parse_invoice_metadata() {
  require_command python3
  python3 - "$1" "$2" <<'PY'
import os
import re
import sys
import unicodedata
from datetime import date

text_path = sys.argv[1]
file_path = sys.argv[2]

text = ""
try:
    with open(text_path, "r", encoding="utf-8", errors="ignore") as handle:
        text = handle.read()
except OSError:
    pass

def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()

def strip_accents(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFKD", value)
    return value.encode("ascii", "ignore").decode("ascii")

def normalize_ascii(value: str) -> str:
    return normalize_spaces(strip_accents(value))

def normalize_ascii_text(value: str) -> str:
    return strip_accents(value)

norm = normalize_ascii_text(text).upper()
filename = os.path.basename(file_path)
filename_norm = normalize_ascii(filename).upper()

has_vat_title = (
    "HOA DON GIA TRI GIA TANG" in norm
    or "HOA DON GTGT" in norm
    or "VAT INVOICE" in norm
    or re.search(r"HOA\s+DON\s+GIA\s+TRI\s+GIA\s+TANG", norm) is not None
    or re.search(r"HOA\s+DON.{0,240}GIA\s+TRI\s+GIA\s+TANG", norm, re.S) is not None
)
has_invoice_markers = (
    ("MA CQT" in norm or "MA CQT (CODE)" in norm)
    or "KY HIEU" in norm
    or "SO (NO.)" in norm
    or "SO (INVOICE NO.)" in norm
    or re.search(r"\bSO\s*\((?:NO|INVOICE\s*NO)\.?\)\.?\s*:\s*\d", norm) is not None
    or re.search(r"\bSO\s*:\s*\d", norm) is not None
)
is_invoice = 1 if (has_vat_title and has_invoice_markers) else 0

is_meta = 0
if (
    "META PLATFORMS" in norm
    or "META ADS" in norm
    or "FBADS" in norm
    or "FACEBOOK ADS" in norm
):
    is_meta = 1

if not is_invoice and is_meta and filename.lower().endswith(".pdf"):
    is_invoice = 1

vendor_name = ""
vendor_tax = ""
invoice_no = ""
invoice_year = ""
invoice_month = ""
invoice_day = ""
buyer_is_ivar = 0
seller_is_ivar = 0

lines = [normalize_spaces(line) for line in norm.splitlines()]

buyer_section = ""
buyer_match = re.search(
    r"(HO TEN NGUOI MUA HANG|CUSTOMER'?S NAME|NGUOI MUA HANG)(.+?)(HINH THUC THANH TOAN|METHOD OF PAYMENT|DONG TIEN THANH TOAN|STT|NO\))",
    norm,
    re.S,
)
if buyer_match:
    buyer_section = buyer_match.group(2)
else:
    buyer_tax_match = re.search(r"(TEN DON VI\s*:.*?MA SO THUE\s*:\s*0109555754)", norm, re.S)
    if buyer_tax_match:
        buyer_section = buyer_tax_match.group(1)

if "IVAR VIET NAM" in buyer_section or "0109555754" in re.sub(r"\D", "", buyer_section):
    buyer_is_ivar = 1

if has_vat_title and buyer_is_ivar:
    is_invoice = 1

seller_patterns = [
    r"DON VI BAN HANG\s*\(SELLER\)\s*:\s*([^\n]+)",
    r"DON VI BAN HANG\s*\(COMPANY'?S NAME\)\s*:\s*([^\n]+)",
    r"DON VI BAN HANG\s*:\s*([^\n]+)",
    r"NGUOI BAN\s*:\s*([^\n]+)",
]
for pattern in seller_patterns:
    match = re.search(pattern, norm)
    if match:
        vendor_name = normalize_spaces(match.group(1))
        break

if not vendor_name:
    header_text = norm
    header_match = re.search(r"(.+?)(HOA\s+DON.{0,240}GIA\s+TRI\s+GIA\s+TANG|HOA\s+DON\s+GTGT|VAT\s+INVOICE)", norm, re.S)
    if header_match:
        header_text = header_match.group(1)
    company_matches = re.findall(r"\bCONG\s+TY[^\n]+", header_text)
    if company_matches:
        vendor_name = normalize_spaces(company_matches[-1])

if not vendor_name:
    top_lines = []
    for line in lines:
        if not line:
            continue
        if "MA SO THUE" in line or "TAX CODE" in line:
            break
        if re.search(r"HOA\s+DON\s+GIA\s+TRI\s+GIA\s+TANG", line) or "HOA DON GTGT" in line or "VAT INVOICE" in line:
            break
        top_lines.append(line)
    if top_lines:
        vendor_name = normalize_spaces(" ".join(top_lines[-2:]))

for idx, line in enumerate(lines):
    if vendor_name:
        break
    if line.startswith("TEN DON VI:"):
        candidate = normalize_spaces(line.split(":", 1)[1])
        if candidate:
            vendor_name = candidate
            for follow in lines[idx + 1 : idx + 4]:
                if follow.startswith("MA SO THUE:"):
                    vendor_tax = re.sub(r"\D", "", follow.split(":", 1)[1])
                    break
            break

if not vendor_name:
    for idx, line in enumerate(lines):
        if line.startswith("TEN NGUOI NOP THUE:"):
            candidate = normalize_spaces(line.split(":", 1)[1])
            if candidate:
                vendor_name = candidate
                for follow in lines[idx + 1 : idx + 4]:
                    if follow.startswith("MA SO THUE:"):
                        vendor_tax = re.sub(r"\D", "", follow.split(":", 1)[1])
                        break
                break

if not vendor_name:
    signed_by_match = re.search(r"KY BOI\s*\(SIGNED BY\)\s*:\s*(.+?)\s*KY NGAY\s*\(SIGNING DATE\)\s*:", norm, re.S)
    if signed_by_match:
        signed_name = normalize_spaces(signed_by_match.group(1))
        if signed_name:
            vendor_name = signed_name

if not vendor_name:
    for idx, line in enumerate(lines):
        if line.startswith("TEN DON VI:") and idx + 1 < len(lines):
            maybe = line.replace("TEN DON VI:", "").strip() or lines[idx + 1]
            vendor_name = maybe
            break

tax_patterns = [
    r"MA SO THUE\s*\(TAX CODE\)\s*:\s*([0-9 ]{8,20})",
    r"TAX ID\s*:\s*([0-9 ]{8,20})",
]
for pattern in tax_patterns:
    match = re.search(pattern, norm)
    if match:
        vendor_tax = re.sub(r"\D", "", match.group(1))
        break

if not vendor_tax:
    first_tax = re.search(r"MA SO THUE:\s*([0-9 ]{8,20})", norm)
    if first_tax:
        vendor_tax = re.sub(r"\D", "", first_tax.group(1))

date_patterns = [
    r"NGAY(?:\s*\(DATE\))?\s*(\d{1,2})\s*THANG(?:\s*\(MONTH\))?\s*(\d{1,2})\s*NAM(?:\s*\(YEAR\))?\s*(\d{4})",
    r"NGAY\s*(\d{1,2})\s*THANG\s*(\d{1,2})\s*NAM\s*(\d{4})",
    r"(\d{2})/(\d{2})/(\d{4})",
    r"(\d{4})-(\d{2})-(\d{2})",
]
for pattern in date_patterns:
    match = re.search(pattern, norm)
    if not match:
        continue
    groups = match.groups()
    if len(groups) == 3 and len(groups[0]) == 4:
        invoice_year, invoice_month, invoice_day = groups
    elif len(groups) == 3 and len(groups[2]) == 4:
        invoice_day, invoice_month, invoice_year = groups
    break

if invoice_year and (
    not re.fullmatch(r"20\d{2}", invoice_year)
    or not invoice_month.isdigit()
    or not 1 <= int(invoice_month) <= 12
    or int(invoice_year) > date.today().year + 1
):
    invoice_year = ""
    invoice_month = ""
    invoice_day = ""

invoice_no_patterns = [
    r"INVOICE #\s*([A-Z0-9\-_./]+)",
    r"\bSO\s*:\s*\n+\s*([A-Z0-9\-_./]+)",
    r"\bSO\s*:\s*([A-Z0-9\-_./]+)",
    r"\bSO\s*\(INVOICE\s*NO\.?\)\s*:\s*([A-Z0-9\-_./]+)",
    r"\bSO\s*\(NO\.?\)\s*:\s*([A-Z0-9\-_./]+)",
    r"\bSO\s*\(INVOICE\s*NO\.?\)\.?\s*:\s*([A-Z0-9\-_./]+)",
    r"\bSO\s*\(NO\.?\)\.?\s*:\s*([A-Z0-9\-_./]+)",
]
for pattern in invoice_no_patterns:
    match = re.search(pattern, norm)
    if match:
        invoice_no = normalize_spaces(match.group(1))
        break

if is_meta:
    if not vendor_name:
        vendor_name = "Meta"
    if not vendor_tax:
        vendor_tax = "9000000327"

if vendor_tax == "9000000327":
    vendor_name = "Meta"

if "IVAR VIET NAM" in vendor_name or vendor_tax == "0109555754":
    seller_is_ivar = 1

if has_vat_title and vendor_name and not seller_is_ivar:
    is_invoice = 1

if not invoice_no:
    file_no = re.search(r"(FBADS[-_A-Z0-9]+)", filename_norm)
    if file_no:
        invoice_no = file_no.group(1)
if not invoice_no:
    file_no = re.search(r"([A-Z0-9]+_\d{4,})", filename_norm)
    if file_no:
        invoice_no = file_no.group(1)

folder_name = normalize_spaces(vendor_name)
folder_name = folder_name.replace("/", "-").replace("\\", "-").strip(" .")
invoice_no = invoice_no.replace("/", "-").replace("\\", "-").strip()

print("\t".join([
    str(is_invoice),
    invoice_year,
    invoice_month.zfill(2) if invoice_month else "",
    invoice_day.zfill(2) if invoice_day else "",
    folder_name,
    vendor_tax,
    invoice_no,
    str(is_meta),
    str(seller_is_ivar),
    str(buyer_is_ivar),
]))
PY
}

parse_xml_invoice_metadata() {
  require_command python3
  python3 - "$1" "$2" <<'PY'
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET

xml_path = sys.argv[1]
file_path = sys.argv[2]

def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()

def strip_accents(value: str) -> str:
    value = (value or "").replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFKD", value)
    return value.encode("ascii", "ignore").decode("ascii")

def normalize_ascii(value: str) -> str:
    return normalize_spaces(strip_accents(value))

def child_text(parent, tag: str) -> str:
    if parent is None:
        return ""
    for elem in parent.iter():
        if elem.tag.split("}")[-1] == tag:
            return normalize_spaces(elem.text or "")
    return ""

filename = os.path.basename(file_path)
filename_norm = normalize_ascii(filename).upper()

try:
    root = ET.parse(xml_path).getroot()
except Exception:
    print("\t".join(["0", "", "", "", "", "", "", "0", "0", "0"]))
    raise SystemExit

seller = None
buyer = None
general = None
for elem in root.iter():
    local = elem.tag.split("}")[-1]
    if local == "NBan" and seller is None:
        seller = elem
    elif local == "NMua" and buyer is None:
        buyer = elem
    elif local == "TTChung" and general is None:
        general = elem

vendor_name = child_text(seller, "Ten")
vendor_tax = re.sub(r"\D", "", child_text(seller, "MST"))
buyer_name = child_text(buyer, "Ten")
buyer_tax = re.sub(r"\D", "", child_text(buyer, "MST"))
invoice_no = child_text(general, "SHDon")
invoice_date = child_text(general, "NLap")
invoice_type = normalize_ascii(child_text(general, "THDon")).upper()

invoice_year = ""
invoice_month = ""
invoice_day = ""
date_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", invoice_date)
if date_match:
    invoice_year, invoice_month, invoice_day = date_match.groups()

is_meta = 1 if any(marker in normalize_ascii(vendor_name).upper() for marker in ("META", "FACEBOOK")) else 0
buyer_is_ivar = 1 if buyer_tax == "0109555754" or "IVAR VIET NAM" in normalize_ascii(buyer_name).upper() else 0
seller_is_ivar = 1 if vendor_tax == "0109555754" or "IVAR VIET NAM" in normalize_ascii(vendor_name).upper() else 0
is_invoice = 1 if (
    "GTGT" in invoice_type
    or "GIA TRI GIA TANG" in invoice_type
    or buyer_is_ivar
    or (vendor_tax and invoice_no and invoice_year)
) else 0

if not invoice_no:
    file_no = re.search(r"([A-Z0-9]+_\d{4,})", filename_norm)
    if file_no:
        invoice_no = file_no.group(1)

folder_name = normalize_ascii(vendor_name).upper()
folder_name = folder_name.replace("/", "-").replace("\\", "-").strip(" .")
invoice_no = invoice_no.replace("/", "-").replace("\\", "-").strip()

print("\t".join([
    str(is_invoice),
    invoice_year,
    invoice_month,
    invoice_day,
    folder_name,
    vendor_tax,
    invoice_no,
    str(is_meta),
    str(seller_is_ivar),
    str(buyer_is_ivar),
]))
PY
}

parse_unc_date_metadata() {
  require_command python3
  python3 - "$1" <<'PY'
import re
import sys
import unicodedata

text_path = sys.argv[1]
try:
    text = open(text_path, "r", encoding="utf-8", errors="ignore").read()
except OSError:
    text = ""

def strip_accents(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFKD", value)
    return value.encode("ascii", "ignore").decode("ascii")

norm = strip_accents(text)
patterns = [
    r"Ngay\s*/?\s*Date\s*:?\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})",
    r"Date\s*:?\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})",
    r"Ngay\s*:?\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})",
]
for pattern in patterns:
    match = re.search(pattern, norm, re.I)
    if match:
        day, month, year = match.groups()
        print(f"{year}\t{int(month):02d}\t{int(day):02d}")
        raise SystemExit

print("\t\t")
PY
}

classify_general_bucket() {
  file_path=$1
  lower=$(printf '%s' "$file_path" | tr '[:upper:]' '[:lower:]')
  case "$lower" in
    *.jpg|*.jpeg|*.png|*.gif|*.webp|*.heic|*.svg|*.bmp|*.tif|*.tiff)
      printf 'photos\n'
      ;;
    *.mp4|*.mov|*.avi|*.mkv|*.mp3|*.wav|*.m4a|*.aac|*.flac)
      printf 'media\n'
      ;;
    *)
      printf 'documents\n'
      ;;
  esac
}

move_to_duplicate() {
  source_path=$1
  year=$2
  month=$3
  sha=$4
  note=$5
  dup_dir="${DOCS_ROOT}/${year}/${month}/duplicates"
  ensure_dir "$dup_dir"
  base=$(basename "$source_path")
  target="${dup_dir}/${base}"

  if [ -e "$target" ]; then
    stem=${base%.*}
    ext=${base##*.}
    if [ "$stem" = "$ext" ]; then
      ext=""
    else
      ext=".${ext}"
    fi
    target="${dup_dir}/${stem}_${sha}${ext}"
  fi

  run_cmd mv "$source_path" "$target"
  info "Moved duplicate to $target ($note)"
  printf '%s\n' "$target"
}

record_processed() {
  source_path=$1
  source_mtime=$2
  file_size=$3
  sha=$4
  final_path=$5
  status=$6
  invoice_year=$7
  invoice_month=$8
  vendor_name=$9
  vendor_tax=${10}
  invoice_number=${11}
  notes=${12}
  db_insert \
    "$source_path" "$source_mtime" "$file_size" "$sha" "$final_path" "$status" \
    "$invoice_year" "$invoice_month" "$vendor_name" "$vendor_tax" "$invoice_number" "$notes"
}

copy_if_needed() {
  source_path=$1
  dest_dir=$2
  ensure_dir "$dest_dir"
  run_cmd cp "$source_path" "$dest_dir/"
}

build_invoice_filename() {
  vendor_tax=$1
  invoice_no=$2
  original_path=$3
  base=$(basename "$original_path")
  ext=${base##*.}
  if [ "$ext" = "$base" ]; then
    ext=""
  else
    ext=".$ext"
  fi

  if [ -n "$invoice_no" ]; then
    safe_no=$(printf '%s' "$invoice_no" | tr ' /' '__' | tr -cd '[:alnum:]_.-')
    printf '%s_%s%s\n' "${vendor_tax:-unknown}" "$safe_no" "$ext"
  else
    printf '%s\n' "$base"
  fi
}

build_unc_filename() {
  text_path=$1
  original_path=$2
  require_command python3
  python3 - "$text_path" "$original_path" <<'PY'
import os
import re
import sys
import unicodedata

text_path = sys.argv[1]
original_path = sys.argv[2]

try:
    with open(text_path, "r", encoding="utf-8", errors="ignore") as handle:
        text = handle.read()
except OSError:
    text = ""

base = os.path.basename(original_path)
stem, ext = os.path.splitext(base)

def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()

def strip_accents(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFKD", value)
    return value.encode("ascii", "ignore").decode("ascii")

def safe_filename_part(value: str, limit: int = 120) -> str:
    value = normalize_spaces(value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value)
    value = re.sub(r"\s*-\s*", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    value = value.strip(" .-_")
    if len(value) > limit:
        value = value[:limit].rstrip(" .-_")
    return value

def line_norm(value: str) -> str:
    return strip_accents(normalize_spaces(value)).upper()

lines = [normalize_spaces(line) for line in text.splitlines()]
norm_text = strip_accents(text)

unc_no = ""
no_patterns = [
    r"\bSo\s*No\s*:\s*([A-Z0-9][A-Z0-9._/-]*)",
    r"\bNo\s*:\s*([A-Z0-9][A-Z0-9._/-]*)",
]
for pattern in no_patterns:
    match = re.search(pattern, norm_text, re.I)
    if match:
        unc_no = match.group(1)
        break

remark = ""
stop_markers = (
    "NGAY CAP",
    "NOI CAP",
    "DATE OF ISSUE",
    "PLACE OF ISSUE",
    "KE TOAN",
    "GIAO DICH VIEN",
    "KIEM SOAT",
    "CHU TAI KHOAN",
    "ACCOUNT HOLDER",
    "CUSTOMER'S COPY",
    "BANK'S COPY",
)

for idx, line in enumerate(lines):
    normalized = line_norm(line)
    if "REMARK" not in normalized and "NOI DUNG" not in normalized:
        continue

    candidate = re.sub(
        r"(?i)\b(nội\s*dung|noi\s*dung|remarks?)\b\s*(?:/+\s*(?:remarks?|nội\s*dung|noi\s*dung))?\s*:?",
        "",
        line,
    )
    candidate = normalize_spaces(candidate)
    if not candidate:
        collected = []
        for follow in lines[idx + 1 : idx + 5]:
            follow_norm = line_norm(follow)
            if any(marker in follow_norm for marker in stop_markers):
                break
            if follow:
                collected.append(follow)
        candidate = normalize_spaces(" ".join(collected))
    if candidate:
        remark = candidate
        break

parts = ["UNC"]
safe_no = safe_filename_part(unc_no, 60)
safe_remark = safe_filename_part(remark)
if safe_no:
    parts.append(safe_no)
if safe_remark:
    parts.append(safe_remark)

if len(parts) == 1:
    print(base)
else:
    print(" ".join(parts) + ext.lower())
PY
}

unique_target_for_move() {
  source_path=$1
  target=$2
  sha=$3

  if [ "$source_path" = "$target" ]; then
    printf '%s\n' "$target"
    return 0
  fi

  if [ -e "$target" ]; then
    base=$(basename "$target")
    dir=$(dirname "$target")
    stem=${base%.*}
    ext=${base##*.}
    if [ "$stem" = "$ext" ]; then
      ext=""
    else
      ext=".${ext}"
    fi
    target="${dir}/${stem}_${sha}${ext}"
  fi

  printf '%s\n' "$target"
}

move_rescanned_file() {
  source_path=$1
  target=$2
  label=$3

  if [ "$source_path" = "$target" ]; then
    info "Rescan kept ${label}: $source_path"
    return 0
  fi

  ensure_dir "$(dirname "$target")"
  run_cmd mv "$source_path" "$target"
  info "Rescan moved ${label} -> $target"
}

infer_year_month_from_path() {
  file_path=$1
  root_path=$2
  require_command python3
  python3 - "$file_path" "$root_path" <<'PY'
import os
import re
import sys

file_path = os.path.abspath(sys.argv[1])
root_path = os.path.abspath(sys.argv[2])

try:
    rel = os.path.relpath(file_path, root_path)
except ValueError:
    print("\t")
    raise SystemExit

parts = rel.split(os.sep)
for idx in range(0, len(parts) - 1):
    if re.fullmatch(r"\d{4}", parts[idx]) and re.fullmatch(r"\d{2}", parts[idx + 1]):
        print(parts[idx] + "\t" + parts[idx + 1])
        break
else:
    print("\t")
PY
}

infer_year_from_path() {
  file_path=$1
  root_path=$2
  require_command python3
  python3 - "$file_path" "$root_path" <<'PY'
import os
import re
import sys

file_path = os.path.abspath(sys.argv[1])
root_path = os.path.abspath(sys.argv[2])

try:
    rel = os.path.relpath(file_path, root_path)
except ValueError:
    print("")
    raise SystemExit

for part in rel.split(os.sep):
    if re.fullmatch(r"\d{4}", part):
        print(part)
        break
else:
    print("")
PY
}

invoice_dest_dir_for_metadata() {
  invoice_year=$1
  invoice_month=$2
  vendor_name=$3
  vendor_tax=$4

  vendor_folder=$vendor_name
  if [ -n "$vendor_folder" ] && [ -n "$vendor_tax" ]; then
    vendor_folder="${vendor_folder} ${vendor_tax}"
  fi
  vendor_folder=$(printf '%s' "$vendor_folder" | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')
  vendor_folder=$(printf '%s' "$vendor_folder" | sed 's/[[:space:]]KY HIEU.*$//; s/[[:space:]]SERIAL.*$//; s/[[:space:]]SO (NO.*$//; s/[[:space:]]DIA CHI.*$//; s/[[:space:]]ADDRESS.*$//; s/[[:space:]]\+/ /g; s/^ //; s/ $//')
  if [ "$(printf '%s' "$vendor_folder" | wc -c | tr -d ' ')" -gt 140 ]; then
    vendor_folder=""
  fi

  if [ -n "$invoice_year" ] && [ -n "$invoice_month" ] && [ -n "$vendor_folder" ]; then
    printf '%s\n' "${IVAR_DATA}/hoa don dau vao/${invoice_year}/${invoice_month}/${vendor_folder}"
  elif [ -n "$invoice_year" ] && [ -n "$invoice_month" ]; then
    printf '%s\n' "${IVAR_DATA}/hoa don dau vao/${invoice_year}/${invoice_month}"
  else
    printf '%s\n' "${IVAR_DATA}/hoa don dau vao"
  fi
}

move_matching_invoice_xml() {
  pdf_source=$1
  dest_dir=$2
  invoice_year=$3
  invoice_month=$4
  vendor_name=$5
  vendor_tax=$6
  invoice_no=$7

  source_dir=$(dirname "$pdf_source")
  base=$(basename "$pdf_source")
  stem=${base%.*}
  xml_source=""

  if [ -f "${source_dir}/${stem}.xml" ]; then
    xml_source="${source_dir}/${stem}.xml"
  elif [ -f "${source_dir}/${stem}.XML" ]; then
    xml_source="${source_dir}/${stem}.XML"
  fi

  if [ -z "$xml_source" ]; then
    return 0
  fi

  xml_sha=$(sha256_file "$xml_source")
  xml_mtime=$(file_mtime_of "$xml_source")
  xml_size=$(file_size_of "$xml_source")
  xml_target="${dest_dir}/$(basename "$xml_source")"
  if [ -e "$xml_target" ]; then
    xml_target="${dest_dir}/${xml_sha}_$(basename "$xml_source")"
  fi

  run_cmd mv "$xml_source" "$xml_target"
  info "Moved matching invoice XML -> $xml_target"
  record_processed "$xml_source" "$xml_mtime" "$xml_size" "$xml_sha" "$xml_target" "invoice-xml" "$invoice_year" "$invoice_month" "$vendor_name" "$vendor_tax" "$invoice_no" "matching-pdf"
  vendor_db_upsert "$vendor_name" "$vendor_tax" "$invoice_year" "$invoice_month" "$xml_source" "$xml_target"
}

process_po_document() {
  source_path=$1
  file_mtime=$2
  file_size=$3
  sha=$4
  dest_dir="${CURRENT_MONTH_ROOT}/documents/file don nhap hang (PO)"
  ensure_dir "$dest_dir"
  target="${dest_dir}/$(basename "$source_path")"
  if [ -e "$target" ]; then
    target="${dest_dir}/${sha}_$(basename "$source_path")"
  fi
  run_cmd mv "$source_path" "$target"
  info "Processed PO document -> $target"
  record_processed "$source_path" "$file_mtime" "$file_size" "$sha" "$target" "po-document" "$CURRENT_YEAR" "$CURRENT_MONTH" "" "" "" "filename-po"
}

process_mo_document() {
  source_path=$1
  file_mtime=$2
  file_size=$3
  sha=$4
  dest_dir="${CURRENT_MONTH_ROOT}/documents/lệnh sản xuất (MO)"
  ensure_dir "$dest_dir"
  target="${dest_dir}/$(basename "$source_path")"
  if [ -e "$target" ]; then
    target="${dest_dir}/${sha}_$(basename "$source_path")"
  fi
  run_cmd mv "$source_path" "$target"
  info "Processed MO document -> $target"
  record_processed "$source_path" "$file_mtime" "$file_size" "$sha" "$target" "mo-document" "$CURRENT_YEAR" "$CURRENT_MONTH" "" "" "" "filename-mo"
}

process_unc_pdf() {
  source_path=$1
  invoice_year=$2
  invoice_month=$3
  file_mtime=$4
  file_size=$5
  sha=$6
  is_meta=$7
  text_file=$8
  if [ -z "$invoice_year" ]; then
    invoice_year=$CURRENT_YEAR
  fi
  if [ -z "$invoice_month" ]; then
    invoice_month=$CURRENT_MONTH
  fi
  move_dir="${IVAR_DATA}/ngan hang/UNC/${invoice_year}/${invoice_month}"
  ensure_dir "$move_dir"
  if [ "$is_meta" = "1" ]; then
    copy_dir="${IVAR_DATA}/chi phi Facebook/${invoice_year}/UNC"
    ensure_dir "$copy_dir"
    copy_if_needed "$source_path" "$copy_dir"
    note="facebook-unc"
  else
    note="unc"
  fi
  dest_name=$(build_unc_filename "$text_file" "$source_path")
  target="${move_dir}/${dest_name}"
  if [ -e "$target" ]; then
    stem=${dest_name%.*}
    ext=${dest_name##*.}
    if [ "$stem" = "$ext" ]; then
      ext=""
    else
      ext=".${ext}"
    fi
    target="${move_dir}/${stem}_${sha}${ext}"
  fi
  run_cmd mv "$source_path" "$target"
  info "Processed UNC file -> $target"
  record_processed "$source_path" "$file_mtime" "$file_size" "$sha" "$target" "unc" "$invoice_year" "$invoice_month" "" "" "" "$note"
}

process_general_bucket() {
  source_path=$1
  file_mtime=$2
  file_size=$3
  sha=$4
  if [ "$CURRENT_DAY" != "01" ] && [ "$DEEP_MODE" -ne 1 ]; then
    info "No special rule matched; keeping file in place: $source_path"
    return 0
  fi
  bucket=$(classify_general_bucket "$source_path")
  dest_dir="${CURRENT_MONTH_ROOT}/${bucket}"
  ensure_dir "$dest_dir"
  target="${dest_dir}/$(basename "$source_path")"
  if [ -e "$target" ]; then
    target="${dest_dir}/${sha}_$(basename "$source_path")"
  fi
  run_cmd mv "$source_path" "$target"
  info "Grouped file -> $target"
  record_processed "$source_path" "$file_mtime" "$file_size" "$sha" "$target" "bucketed" "$CURRENT_YEAR" "$CURRENT_MONTH" "" "" "" "$bucket"
}

process_year_inbox() {
  source_path=$1
  file_mtime=$2
  file_size=$3
  sha=$4
  dest_dir="${CURRENT_YEAR_ROOT}"
  ensure_dir "$dest_dir"
  target="${dest_dir}/$(basename "$source_path")"
  if [ -e "$target" ]; then
    target="${dest_dir}/${sha}_$(basename "$source_path")"
  fi
  run_cmd mv "$source_path" "$target"
  info "Moved unclassified root file -> $target"
  record_processed "$source_path" "$file_mtime" "$file_size" "$sha" "$target" "year-inbox" "$CURRENT_YEAR" "$CURRENT_MONTH" "" "" "" "year-root"
}

process_invoice_pdf() {
  source_path=$1
  invoice_year=$2
  invoice_month=$3
  vendor_name=$4
  vendor_tax=$5
  invoice_no=$6
  is_meta=$7
  file_mtime=$8
  file_size=$9
  sha=${10}

  dest_dir=$(invoice_dest_dir_for_metadata "$invoice_year" "$invoice_month" "$vendor_name" "$vendor_tax")
  ensure_dir "$dest_dir"

  if [ "$is_meta" = "1" ]; then
    if [ -n "$invoice_year" ]; then
      copy_if_needed "$source_path" "${IVAR_DATA}/chi phi Facebook/${invoice_year}/hoa don"
    else
      copy_if_needed "$source_path" "${IVAR_DATA}/chi phi Facebook/hoa don"
    fi
  fi

  dest_name=$(build_invoice_filename "$vendor_tax" "$invoice_no" "$source_path")
  target="${dest_dir}/${dest_name}"
  if [ -e "$target" ]; then
    target="${dest_dir}/${sha}_$(basename "$target")"
  fi
  run_cmd mv "$source_path" "$target"
  info "Processed invoice -> $target"
  move_matching_invoice_xml "$source_path" "$dest_dir" "$invoice_year" "$invoice_month" "$vendor_name" "$vendor_tax" "$invoice_no"
  record_processed "$source_path" "$file_mtime" "$file_size" "$sha" "$target" "invoice" "$invoice_year" "$invoice_month" "$vendor_name" "$vendor_tax" "$invoice_no" ""
  vendor_db_upsert "$vendor_name" "$vendor_tax" "$invoice_year" "$invoice_month" "$source_path" "$target"
}

maybe_rollover_previous_month() {
  if [ "$CURRENT_DAY" != "01" ] && [ "$DEEP_MODE" -ne 1 ]; then
    return 0
  fi

  if [ "$DEEP_MODE" -eq 1 ]; then
    prev_year=$CURRENT_YEAR
    prev_month=$CURRENT_MONTH
    prev_root=$CURRENT_MONTH_ROOT
    info "Deep mode enabled. Applying first-day rollover rules to current month: $prev_root"
  else
    prev_month_date=$(date -d "${CURRENT_YEAR}-${CURRENT_MONTH}-01 -1 month" '+%Y-%m-%d' 2>/dev/null || true)
    if [ -z "$prev_month_date" ]; then
      warn "Skipping previous month rollover because 'date -d' is unavailable."
      return 0
    fi

    prev_year=$(printf '%s' "$prev_month_date" | cut -d- -f1)
    prev_month=$(printf '%s' "$prev_month_date" | cut -d- -f2)
    prev_root="${DOCS_ROOT}/${prev_year}/${prev_month}"
  fi

  if [ ! -d "$prev_root" ]; then
    info "Previous month folder not found: $prev_root"
    return 0
  fi

  info "First day rollover rules active. Grouping loose files in $prev_root"

  find "$prev_root" -maxdepth 1 -type f | while IFS= read -r cleanup_file; do
    cleanup_base=$(basename "$cleanup_file")
    cleanup_lower=$(printf '%s' "$cleanup_base" | tr '[:upper:]' '[:lower:]')
    case "$cleanup_lower" in
      temp|temp.*|temp-*|temp_*|*.log)
        run_cmd rm -f "$cleanup_file"
        info "Deleted temporary file from previous month: $cleanup_file"
        continue
        ;;
    esac
  done

  find "$prev_root" -maxdepth 1 -type f | while IFS= read -r loose_file; do
    bucket=$(classify_general_bucket "$loose_file")
    bucket_dir="${prev_root}/${bucket}"
    ensure_dir "$bucket_dir"
    run_cmd mv "$loose_file" "$bucket_dir/$(basename "$loose_file")"
    info "Grouped previous-month loose file -> ${bucket_dir}/$(basename "$loose_file")"
  done
}

process_source_file() {
  source_path=$1
  tmp_root=$2

  if [ ! -f "$source_path" ]; then
    info "Skipping missing file: $source_path"
    return 0
  fi

  file_mtime=$(file_mtime_of "$source_path")
  file_size=$(file_size_of "$source_path")

  if [ "$(db_seen_source "$source_path" "$file_mtime" "$file_size")" = "1" ]; then
    info "Skipping already processed file: $source_path"
    return 0
  fi

  sha=$(sha256_file "$source_path")
  dup_record=$(db_find_sha "$sha")
  if [ -n "$dup_record" ]; then
    duplicate_target=$(move_to_duplicate "$source_path" "$CURRENT_YEAR" "$CURRENT_MONTH" "$sha" "$dup_record")
    record_processed "$source_path" "$file_mtime" "$file_size" "$sha" "$duplicate_target" "duplicate" "$CURRENT_YEAR" "$CURRENT_MONTH" "" "" "" "$dup_record"
    return 0
  fi

  text_file="${tmp_root}/$(basename "$source_path").txt"
  file_lower=$(printf '%s' "$source_path" | tr '[:upper:]' '[:lower:]')
  case "$file_lower" in
    *.xml)
      source_dir=$(dirname "$source_path")
      base=$(basename "$source_path")
      stem=${base%.*}
      if [ -f "${source_dir}/${stem}.pdf" ] || [ -f "${source_dir}/${stem}.PDF" ]; then
        info "Keeping matching invoice XML in place until PDF is processed: $source_path"
        return 0
      fi
      ;;
  esac
  file_name_upper=$(basename "$source_path" | tr '[:lower:]' '[:upper:]')
  parsed=""
  parse_source=""
  text_extracted=0

  if printf '%s' "$file_lower" | grep -q '\.pdf$'; then
    source_dir=$(dirname "$source_path")
    base=$(basename "$source_path")
    stem=${base%.*}
    xml_source=""
    if [ -f "${source_dir}/${stem}.xml" ]; then
      xml_source="${source_dir}/${stem}.xml"
    elif [ -f "${source_dir}/${stem}.XML" ]; then
      xml_source="${source_dir}/${stem}.XML"
    fi

    if [ -n "$xml_source" ]; then
      parsed=$(parse_xml_invoice_metadata "$xml_source" "$source_path")
      parse_source="xml"
      info "Invoice metadata parsed from XML: $xml_source"
    fi

    if [ -z "$parsed" ]; then
      cached=$(metadata_cache_get "$sha")
      if [ -n "$cached" ]; then
        cached_year=$(printf '%s' "$cached" | cut -f2)
        cached_vendor=$(printf '%s' "$cached" | cut -f5)
        if [ "$cached_vendor" != "Unknown Vendor" ] && { [ -z "$cached_year" ] || [ "$cached_year" -le 2027 ]; }; then
          parsed=$(printf '%s' "$cached" | cut -f1-10)
          parse_source=$(printf '%s' "$cached" | cut -f11)
          info "Invoice metadata cache hit for PDF: $source_path"
        fi
      fi
    fi
  fi

  if [ -z "$parsed" ]; then
    extract_text_for_file "$source_path" "$text_file"
    text_extracted=1
    parsed=$(parse_invoice_metadata "$text_file" "$source_path")
    parse_source="pdf-text"
  else
    : >"$text_file"
  fi

  is_invoice=$(printf '%s' "$parsed" | cut -f1)
  invoice_year=$(printf '%s' "$parsed" | cut -f2)
  invoice_month=$(printf '%s' "$parsed" | cut -f3)
  invoice_day=$(printf '%s' "$parsed" | cut -f4)
  vendor_name=$(printf '%s' "$parsed" | cut -f5)
  vendor_tax=$(printf '%s' "$parsed" | cut -f6)
  invoice_no=$(printf '%s' "$parsed" | cut -f7)
  is_meta=$(printf '%s' "$parsed" | cut -f8)
  seller_is_ivar=$(printf '%s' "$parsed" | cut -f9)
  buyer_is_ivar=$(printf '%s' "$parsed" | cut -f10)

  if [ -z "$vendor_name" ] && [ -n "$vendor_tax" ]; then
    vendor_name=$(vendor_db_lookup_name "$vendor_tax")
  fi

  if printf '%s' "$file_lower" | grep -q '\.pdf$'; then
    metadata_cache_put "$sha" "$is_invoice" "$invoice_year" "$invoice_month" "$invoice_day" "$vendor_name" "$vendor_tax" "$invoice_no" "$is_meta" "$seller_is_ivar" "$buyer_is_ivar" "$parse_source"
  fi

  if printf '%s' "$file_lower" | grep -q '\.pdf$'; then
    needs_text_for_unc=0
    if printf '%s\n' "$file_name_upper" | grep -Eq '(^|[^A-Z])UNC([^A-Z]|$)|UY NHIEM CHI'; then
      needs_text_for_unc=1
    elif [ "$is_invoice" != "1" ] && [ "$text_extracted" -eq 0 ]; then
      needs_text_for_unc=1
    fi
    if [ "$needs_text_for_unc" -eq 1 ] && [ "$text_extracted" -eq 0 ]; then
      extract_text_for_file "$source_path" "$text_file"
      text_extracted=1
    fi

    if printf '%s\n%s\n' "$file_name_upper" "$(cat "$text_file" 2>/dev/null | tr '[:lower:]' '[:upper:]')" | grep -Eq '(^|[^A-Z])UNC([^A-Z]|$)|UY NHIEM CHI'; then
      unc_date=$(parse_unc_date_metadata "$text_file")
      unc_year=$(printf '%s' "$unc_date" | cut -f1)
      unc_month=$(printf '%s' "$unc_date" | cut -f2)
      if [ -n "$unc_year" ] && [ -n "$unc_month" ]; then
        invoice_year=$unc_year
        invoice_month=$unc_month
      fi
      process_unc_pdf "$source_path" "$invoice_year" "$invoice_month" "$file_mtime" "$file_size" "$sha" "$is_meta" "$text_file"
      return 0
    fi

      if [ "$is_invoice" = "1" ]; then
        if [ "$seller_is_ivar" = "1" ] && [ "$buyer_is_ivar" != "1" ] && [ "$is_meta" != "1" ]; then
          info "Skipping outgoing IVAR invoice: $source_path"
          return 0
        fi
        if [ -z "$invoice_year" ] || [ -z "$invoice_month" ]; then
          info "Invoice date not found in content; keeping at hoa don dau vao root: $source_path"
        fi
      process_invoice_pdf \
        "$source_path" "$invoice_year" "$invoice_month" "$vendor_name" "$vendor_tax" \
        "$invoice_no" "$is_meta" "$file_mtime" "$file_size" "$sha"
      return 0
    fi
  fi

  if printf '%s' "$(basename "$source_path")" | grep -Eq '[Pp][Oo][[:space:]_-]*[0-9]+'; then
    process_po_document "$source_path" "$file_mtime" "$file_size" "$sha"
    return 0
  fi

  if printf '%s' "$(basename "$source_path")" | grep -Eq '[Mm][Oo][[:space:]_-]*[0-9]+'; then
    process_mo_document "$source_path" "$file_mtime" "$file_size" "$sha"
    return 0
  fi

  source_dir=$(dirname "$source_path")
  if [ "$source_dir" = "$DOCS_ROOT" ] || [ "$source_dir" = "$DOWNLOADS_ROOT" ]; then
    process_year_inbox "$source_path" "$file_mtime" "$file_size" "$sha"
    return 0
  fi

  process_general_bucket "$source_path" "$file_mtime" "$file_size" "$sha"
}

scan_current_month() {
  tmp_root=$(tmp_dir_make)
  trap 'cleanup_dir "$tmp_root"' EXIT INT TERM

  if [ -d "$CURRENT_YEAR_ROOT" ]; then
    info "Scanning loose files in current year root: $CURRENT_YEAR_ROOT"
    find "$CURRENT_YEAR_ROOT" -maxdepth 1 -type f ! -name '.*' | while IFS= read -r source_path; do
      process_source_file "$source_path" "$tmp_root"
    done
  else
    warn "Current year folder not found: $CURRENT_YEAR_ROOT"
  fi

  if [ -d "$DOCS_ROOT" ]; then
    info "Scanning loose files in Documents root: $DOCS_ROOT"
    find "$DOCS_ROOT" -maxdepth 1 -type f ! -name '.*' | while IFS= read -r source_path; do
      process_source_file "$source_path" "$tmp_root"
    done
  fi

  if [ -d "$DOWNLOADS_ROOT" ]; then
    info "Scanning loose files in Downloads root: $DOWNLOADS_ROOT"
    find "$DOWNLOADS_ROOT" -maxdepth 1 -type f ! -name '.*' | while IFS= read -r source_path; do
      process_source_file "$source_path" "$tmp_root"
    done
  fi

  cleanup_dir "$tmp_root"
  trap - EXIT INT TERM
}

rescan_classified_file() {
  source_path=$1
  tmp_root=$2

  if [ ! -f "$source_path" ]; then
    info "Rescan skipping missing file: $source_path"
    return 0
  fi

  file_lower=$(printf '%s' "$source_path" | tr '[:upper:]' '[:lower:]')
  case "$file_lower" in
    *.pdf)
      ;;
    *)
      return 0
      ;;
  esac

  file_mtime=$(file_mtime_of "$source_path")
  file_size=$(file_size_of "$source_path")
  sha=$(sha256_file "$source_path")
  text_file="${tmp_root}/rescan-$(basename "$source_path").txt"
  parsed=""
  parse_source=""
  text_extracted=0

  cached=$(metadata_cache_get "$sha")
  if [ -n "$cached" ]; then
    cached_year=$(printf '%s' "$cached" | cut -f2)
    cached_vendor=$(printf '%s' "$cached" | cut -f5)
    if [ "$cached_vendor" != "Unknown Vendor" ] && { [ -z "$cached_year" ] || [ "$cached_year" -le 2027 ]; }; then
      parsed=$(printf '%s' "$cached" | cut -f1-10)
      parse_source=$(printf '%s' "$cached" | cut -f11)
      info "Invoice metadata cache hit during rescan: $source_path"
      : >"$text_file"
    fi
  fi
  if [ -z "$parsed" ]; then
    extract_text_for_file "$source_path" "$text_file"
    text_extracted=1
  fi

  file_name_upper=$(basename "$source_path" | tr '[:lower:]' '[:upper:]')
  if [ -z "$parsed" ]; then
    parsed=$(parse_invoice_metadata "$text_file" "$source_path")
    parse_source="pdf-text"
  fi
  is_invoice=$(printf '%s' "$parsed" | cut -f1)
  invoice_year=$(printf '%s' "$parsed" | cut -f2)
  invoice_month=$(printf '%s' "$parsed" | cut -f3)
  vendor_name=$(printf '%s' "$parsed" | cut -f5)
  vendor_tax=$(printf '%s' "$parsed" | cut -f6)
  invoice_no=$(printf '%s' "$parsed" | cut -f7)
  is_meta=$(printf '%s' "$parsed" | cut -f8)
  seller_is_ivar=$(printf '%s' "$parsed" | cut -f9)
  buyer_is_ivar=$(printf '%s' "$parsed" | cut -f10)

  if [ -z "$vendor_name" ] && [ -n "$vendor_tax" ]; then
    vendor_name=$(vendor_db_lookup_name "$vendor_tax")
  fi

  metadata_cache_put "$sha" "$is_invoice" "$invoice_year" "$invoice_month" "" "$vendor_name" "$vendor_tax" "$invoice_no" "$is_meta" "$seller_is_ivar" "$buyer_is_ivar" "$parse_source"

  if [ "$is_invoice" != "1" ] && [ "$text_extracted" -eq 0 ]; then
    extract_text_for_file "$source_path" "$text_file"
    text_extracted=1
  fi
  text_upper=$(cat "$text_file" 2>/dev/null | tr '[:lower:]' '[:upper:]')

  if printf '%s\n%s\n' "$file_name_upper" "$text_upper" | grep -Eq '(^|[^A-Z])UNC([^A-Z]|$)|UY NHIEM CHI'; then
    unc_date=$(parse_unc_date_metadata "$text_file")
    unc_year=$(printf '%s' "$unc_date" | cut -f1)
    unc_month=$(printf '%s' "$unc_date" | cut -f2)
    if [ -n "$unc_year" ] && [ -n "$unc_month" ]; then
      invoice_year=$unc_year
      invoice_month=$unc_month
    fi

    if [ -z "$invoice_year" ] || [ -z "$invoice_month" ]; then
      inferred=$(infer_year_month_from_path "$source_path" "${IVAR_DATA}/ngan hang/UNC")
      inferred_year=$(printf '%s' "$inferred" | cut -f1)
      inferred_month=$(printf '%s' "$inferred" | cut -f2)
      if [ -z "$invoice_year" ]; then
        invoice_year=$inferred_year
      fi
      if [ -z "$invoice_month" ]; then
        invoice_month=$inferred_month
      fi
    fi
    if [ -z "$invoice_year" ]; then
      invoice_year=$CURRENT_YEAR
    fi
    if [ -z "$invoice_month" ]; then
      invoice_month=$CURRENT_MONTH
    fi

    dest_dir="${IVAR_DATA}/ngan hang/UNC/${invoice_year}/${invoice_month}"
    dest_name=$(build_unc_filename "$text_file" "$source_path")
    target=$(unique_target_for_move "$source_path" "${dest_dir}/${dest_name}" "$sha")
    move_rescanned_file "$source_path" "$target" "UNC"
    return 0
  fi

  if [ "$is_invoice" = "1" ]; then
    if [ "$seller_is_ivar" = "1" ] && [ "$buyer_is_ivar" != "1" ] && [ "$is_meta" != "1" ]; then
      info "Rescan skipping outgoing IVAR invoice: $source_path"
      return 0
    fi

    if [ -z "$invoice_year" ] || [ -z "$invoice_month" ]; then
      inferred=$(infer_year_month_from_path "$source_path" "${IVAR_DATA}/hoa don dau vao")
      inferred_year=$(printf '%s' "$inferred" | cut -f1)
      inferred_month=$(printf '%s' "$inferred" | cut -f2)
      if [ -z "$invoice_year" ]; then
        invoice_year=$inferred_year
      fi
      if [ -z "$invoice_month" ]; then
        invoice_month=$inferred_month
      fi
    fi

    dest_dir=$(invoice_dest_dir_for_metadata "$invoice_year" "$invoice_month" "$vendor_name" "$vendor_tax")
    dest_name=$(build_invoice_filename "$vendor_tax" "$invoice_no" "$source_path")
    target=$(unique_target_for_move "$source_path" "${dest_dir}/${dest_name}" "$sha")
    old_source=$source_path
    move_rescanned_file "$source_path" "$target" "invoice"
    if [ "$old_source" != "$target" ]; then
      move_matching_invoice_xml "$old_source" "$dest_dir" "$invoice_year" "$invoice_month" "$vendor_name" "$vendor_tax" "$invoice_no"
    fi
    vendor_db_upsert "$vendor_name" "$vendor_tax" "$invoice_year" "$invoice_month" "$old_source" "$target"
    return 0
  fi

  info "Rescan found no matching classified rule: $source_path"
}

rescan_facebook_file() {
  source_path=$1
  tmp_root=$2
  facebook_kind=$3

  if [ ! -f "$source_path" ]; then
    info "Facebook rescan skipping missing file: $source_path"
    return 0
  fi

  file_lower=$(printf '%s' "$source_path" | tr '[:upper:]' '[:lower:]')
  case "$file_lower" in
    *.pdf)
      ;;
    *)
      return 0
      ;;
  esac

  sha=$(sha256_file "$source_path")
  text_file="${tmp_root}/facebook-$(basename "$source_path").txt"
  extract_text_for_file "$source_path" "$text_file"
  parsed=$(parse_invoice_metadata "$text_file" "$source_path")
  invoice_year=$(printf '%s' "$parsed" | cut -f2)
  vendor_tax=$(printf '%s' "$parsed" | cut -f6)
  invoice_no=$(printf '%s' "$parsed" | cut -f7)
  inferred_year=$(infer_year_from_path "$source_path" "${IVAR_DATA}/chi phi Facebook")

  if [ -z "$invoice_year" ]; then
    invoice_year=$inferred_year
  fi
  if [ -z "$invoice_year" ]; then
    invoice_year=$CURRENT_YEAR
  fi

  case "$facebook_kind" in
    unc)
      dest_dir="${IVAR_DATA}/chi phi Facebook/${invoice_year}/UNC"
      dest_name=$(build_unc_filename "$text_file" "$source_path")
      target=$(unique_target_for_move "$source_path" "${dest_dir}/${dest_name}" "$sha")
      move_rescanned_file "$source_path" "$target" "Facebook UNC"
      ;;
    invoice)
      dest_dir="${IVAR_DATA}/chi phi Facebook/${invoice_year}/hoa don"
      dest_name=$(build_invoice_filename "$vendor_tax" "$invoice_no" "$source_path")
      target=$(unique_target_for_move "$source_path" "${dest_dir}/${dest_name}" "$sha")
      move_rescanned_file "$source_path" "$target" "Facebook invoice"
      ;;
  esac
}

rescan_classified_dirs() {
  tmp_root=$(tmp_dir_make)
  trap 'cleanup_dir "$tmp_root"' EXIT INT TERM

  invoice_root="${IVAR_DATA}/hoa don dau vao"
  unc_root="${IVAR_DATA}/ngan hang/UNC"
  facebook_root="${IVAR_DATA}/chi phi Facebook"

  if [ -d "$invoice_root" ]; then
    info "Rescanning classified invoices: $invoice_root"
    find "$invoice_root" -type f ! -name '.*' | while IFS= read -r source_path; do
      source_lower=$(printf '%s' "$source_path" | tr '[:upper:]' '[:lower:]')
      case "$source_lower" in
        */xnk/*)
          info "Rescan skipping XNK document under invoice root: $source_path"
          continue
          ;;
      esac
      rescan_classified_file "$source_path" "$tmp_root"
    done
  else
    warn "Invoice destination folder not found: $invoice_root"
  fi

  if [ -d "$unc_root" ]; then
    info "Rescanning classified UNC files: $unc_root"
    find "$unc_root" -type f ! -name '.*' | while IFS= read -r source_path; do
      rescan_classified_file "$source_path" "$tmp_root"
    done
  else
    warn "UNC destination folder not found: $unc_root"
  fi

  if [ -d "$facebook_root" ]; then
    info "Rescanning Facebook expense files: $facebook_root"
    find "$facebook_root" -type f ! -name '.*' | while IFS= read -r source_path; do
      source_lower=$(printf '%s' "$source_path" | tr '[:upper:]' '[:lower:]')
      case "$source_lower" in
        */unc/*)
          rescan_facebook_file "$source_path" "$tmp_root" "unc"
          ;;
        */hoa\ don/*)
          rescan_facebook_file "$source_path" "$tmp_root" "invoice"
          ;;
      esac
    done
  else
    warn "Facebook expense destination folder not found: $facebook_root"
  fi

  cleanup_dir "$tmp_root"
  trap - EXIT INT TERM
}

run_rup_prj() {
  if [ "$NO_SYNC" -eq 1 ]; then
    info "Skipping rclone project sync because --no-sync was provided."
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    info "DRY-RUN (cd ${HOME_DIR} && rclone copy Documents/Drives/gDriveProjects/ beenorgone-gDrive: --drive-import-formats xlsx,docx,pptx,odt,ods,odp --drive-skip-gdocs --drive-auth-owner-only=true --filter-from=.rclone-filters-gprj --skip-links --stats=30s --stats-one-line --log-level ERROR)"
    return 0
  fi

  if [ ! -f "${HOME_DIR}/.rclone-filters-gprj" ]; then
    warn "rclone filter file not found: ${HOME_DIR}/.rclone-filters-gprj"
    return 0
  fi

  if ! command -v rclone >/dev/null 2>&1; then
    warn "rclone command not found."
    return 0
  fi

  if (
    cd "$HOME_DIR" &&
    rclone copy Documents/Drives/gDriveProjects/ beenorgone-gDrive: \
      --drive-import-formats xlsx,docx,pptx,odt,ods,odp \
      --drive-skip-gdocs \
      --drive-auth-owner-only=true \
      --filter-from='.rclone-filters-gprj' \
      --skip-links \
      --stats=30s \
      --stats-one-line \
      --log-level ERROR
  ); then
    info "Completed rclone project sync."
  else
    warn "rclone project sync failed."
  fi
}

main() {
  require_command find
  require_command sha256sum
  require_command stat

  ensure_dir "$STATE_DIR"

  info "=== IVAR Accounting File Sync ==="
  info "Date: $CURRENT_DATE"
  info "Source year root: $CURRENT_YEAR_ROOT"
  info "Destination root: $IVAR_DATA"
  info "State DB: $DB_PATH"
  info "Vendor DB: $VENDOR_DB_PATH"
  if [ "$DRY_RUN" -eq 1 ]; then
    info "Running in dry-run mode"
  fi
  if [ "$DEEP_MODE" -eq 1 ]; then
    info "Running in deep mode"
  fi
  if [ "$NO_SYNC" -eq 1 ]; then
    info "Running with rclone sync disabled"
  fi
  if [ "$RESCAN_MODE" -eq 1 ]; then
    info "Running in rescan mode"
  fi
  if [ "$VENDOR_LIST_MODE" -eq 1 ]; then
    info "Listing vendor reference DB"
  fi

  if [ "$VENDOR_LIST_MODE" -ne 1 ] && [ ! -d "$IVAR_DATA" ]; then
    error "Destination folder not found: $IVAR_DATA"
    exit 1
  fi

  vendor_db_import_processed
  metadata_cache_import_processed

  if [ "$VENDOR_LIST_MODE" -eq 1 ]; then
    vendor_db_list
    return 0
  fi

  if [ "$RESCAN_MODE" -eq 1 ]; then
    rescan_classified_dirs
  else
    maybe_rollover_previous_month
    scan_current_month
  fi

  info "Sync completed."
  run_rup_prj
}

main "$@"
