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
CURRENT_DATE="${CURRENT_DATE:-$(date '+%Y-%m-%d')}"
CURRENT_YEAR=$(printf '%s' "$CURRENT_DATE" | cut -d- -f1)
CURRENT_MONTH=$(printf '%s' "$CURRENT_DATE" | cut -d- -f2)
CURRENT_DAY=$(printf '%s' "$CURRENT_DATE" | cut -d- -f3)
CURRENT_YEAR_ROOT="${DOCS_ROOT}/${CURRENT_YEAR}"
CURRENT_MONTH_ROOT="${DOCS_ROOT}/${CURRENT_YEAR}/${CURRENT_MONTH}"
DRY_RUN=0
QUIET=0
DEEP_MODE=0

usage() {
  cat <<'EOF'
Usage: sh ivar-documents-organizer.sh [--dry-run] [--deep] [--quiet]

Options:
  --dry-run   Print planned actions without moving/copying files or writing DB.
  --deep      Force month-end rollover logic as if today were the first day of next month.
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
    pdftotext "$pdf_path" - 2>/dev/null >"$out_file" || true
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
          tesseract "$image_path" stdout -l vie+eng 2>/dev/null >>"$out_file" || \
            tesseract "$image_path" stdout -l eng 2>/dev/null >>"$out_file" || true
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

def normalize_ascii(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    return normalize_spaces(value)

norm = normalize_ascii(text).upper()
filename = os.path.basename(file_path)
filename_norm = normalize_ascii(filename).upper()

has_vat_title = "HOA DON GIA TRI GIA TANG" in norm or "VAT INVOICE" in norm
has_invoice_markers = (
    ("MA CQT" in norm or "MA CQT (CODE)" in norm)
    or "KY HIEU" in norm
    or "SO (NO.)" in norm
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
seller_is_ivar = 0

lines = [normalize_spaces(line) for line in norm.splitlines()]

for idx, line in enumerate(lines):
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
    seller_patterns = [
        r"DON VI BAN HANG\s*\(SELLER\)\s*:\s*([^\n]+)",
        r"TEN DON VI\s*:\s*([^\n]+)",
        r"NGUOI BAN\s*:\s*([^\n]+)",
    ]
    for pattern in seller_patterns:
        match = re.search(pattern, norm)
        if match:
            vendor_name = normalize_spaces(match.group(1))
            break

if not vendor_name:
    signed_by_match = re.search(r"KY BOI\s*\(SIGNED BY\)\s*:\s*(.+?)\s*KY NGAY\s*\(SIGNING DATE\)\s*:", norm, re.S)
    if signed_by_match:
        signed_name = normalize_spaces(signed_by_match.group(1))
        if signed_name:
            vendor_name = signed_name

if not vendor_name:
    top_lines = []
    for line in lines:
        if not line:
            continue
        if "MA SO THUE" in line or "TAX CODE" in line:
            break
        if "HOA DON GIA TRI GIA TANG" in line or "VAT INVOICE" in line:
            break
        top_lines.append(line)
    if top_lines:
        vendor_name = normalize_spaces(" ".join(top_lines[-2:]))

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

invoice_no_patterns = [
    r"INVOICE #\s*([A-Z0-9\-_./]+)",
    r"\bSO\s*:\s*\n+\s*([A-Z0-9\-_./]+)",
    r"\bSO\s*:\s*([A-Z0-9\-_./]+)",
    r"\bSO\s*\(NO\.\)\s*:\s*([A-Z0-9\-_./]+)",
]
for pattern in invoice_no_patterns:
    match = re.search(pattern, norm)
    if match:
        invoice_no = normalize_spaces(match.group(1))
        break

if is_meta:
    if not vendor_name:
        vendor_name = "Meta Platforms Ireland"
    if not vendor_tax:
        vendor_tax = "9000000327"

if "IVAR VIET NAM" in vendor_name or vendor_tax == "0109555754":
    seller_is_ivar = 1

if not invoice_no:
    file_no = re.search(r"(FBADS[-_A-Z0-9]+)", filename_norm)
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
]))
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

process_unc_pdf() {
  source_path=$1
  invoice_year=$2
  invoice_month=$3
  file_mtime=$4
  file_size=$5
  sha=$6
  is_meta=$7
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
  target="${move_dir}/$(basename "$source_path")"
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

  vendor_folder=$vendor_name
  if [ -n "$vendor_folder" ] && [ -n "$vendor_tax" ]; then
    vendor_folder="${vendor_folder} ${vendor_tax}"
  fi
  vendor_folder=$(printf '%s' "$vendor_folder" | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')

  if [ -n "$invoice_year" ] && [ -n "$invoice_month" ] && [ -n "$vendor_folder" ]; then
    dest_dir="${IVAR_DATA}/hoa don dau vao/${invoice_year}/${invoice_month}/${vendor_folder}"
  elif [ -n "$invoice_year" ] && [ -n "$invoice_month" ]; then
    dest_dir="${IVAR_DATA}/hoa don dau vao/${invoice_year}/${invoice_month}"
  else
    dest_dir="${IVAR_DATA}/hoa don dau vao"
  fi
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
  record_processed "$source_path" "$file_mtime" "$file_size" "$sha" "$target" "invoice" "$invoice_year" "$invoice_month" "$vendor_name" "$vendor_tax" "$invoice_no" ""
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
  extract_text_for_file "$source_path" "$text_file"

  file_lower=$(printf '%s' "$source_path" | tr '[:upper:]' '[:lower:]')
  file_name_upper=$(basename "$source_path" | tr '[:lower:]' '[:upper:]')
  parsed=$(parse_invoice_metadata "$text_file" "$source_path")
  is_invoice=$(printf '%s' "$parsed" | cut -f1)
  invoice_year=$(printf '%s' "$parsed" | cut -f2)
  invoice_month=$(printf '%s' "$parsed" | cut -f3)
  invoice_day=$(printf '%s' "$parsed" | cut -f4)
  vendor_name=$(printf '%s' "$parsed" | cut -f5)
  vendor_tax=$(printf '%s' "$parsed" | cut -f6)
  invoice_no=$(printf '%s' "$parsed" | cut -f7)
  is_meta=$(printf '%s' "$parsed" | cut -f8)
  seller_is_ivar=$(printf '%s' "$parsed" | cut -f9)

  if printf '%s' "$file_lower" | grep -q '\.pdf$'; then
    if printf '%s\n%s\n' "$file_name_upper" "$(cat "$text_file" 2>/dev/null | tr '[:lower:]' '[:upper:]')" | grep -Eq '(^|[^A-Z])UNC([^A-Z]|$)|UY NHIEM CHI'; then
      process_unc_pdf "$source_path" "$invoice_year" "$invoice_month" "$file_mtime" "$file_size" "$sha" "$is_meta"
      return 0
    fi

      if [ "$is_invoice" = "1" ]; then
        if [ "$seller_is_ivar" = "1" ] && [ "$is_meta" != "1" ]; then
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

run_rup_prj() {
  if [ "$DRY_RUN" -eq 1 ]; then
    info "DRY-RUN bash -ic 'rup-prj'"
    return 0
  fi

  if bash -ic 'rup-prj' >/dev/null 2>&1; then
    info "Completed rup-prj."
  else
    warn "rup-prj failed."
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
  if [ "$DRY_RUN" -eq 1 ]; then
    info "Running in dry-run mode"
  fi
  if [ "$DEEP_MODE" -eq 1 ]; then
    info "Running in deep mode"
  fi

  if [ ! -d "$IVAR_DATA" ]; then
    error "Destination folder not found: $IVAR_DATA"
    exit 1
  fi

  maybe_rollover_previous_month
  scan_current_month

  info "Sync completed."
  run_rup_prj
}

main "$@"
