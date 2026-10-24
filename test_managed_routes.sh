#!/bin/sh
# Shell integration test for the managed WIP route phase (--plan-managed-rules /
# --apply-managed-rules) in ivar-documents-organizer.sh. Runs entirely inside a
# temporary HOME so it never touches the real Documents tree or its databases.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ORGANIZER="${SCRIPT_DIR}/ivar-documents-organizer.sh"
WIP_PY="${SCRIPT_DIR}/wip-organizer.py"

TMP_HOME=$(mktemp -d "${TMPDIR:-/tmp}/ivar-managed-routes-test.XXXXXX")
cleanup() { rm -rf "$TMP_HOME"; }
trap cleanup EXIT INT TERM

YEAR_ROOT="${TMP_HOME}/Documents/2026"
IVAR_DATA_DIR="${TMP_HOME}/ivar-data"
STATE_DIR_TEST="${TMP_HOME}/state"

mkdir -p "$YEAR_ROOT" "$IVAR_DATA_DIR"

python3 "$WIP_PY" --year-root "$YEAR_ROOT" init >/dev/null

VAT_FILE="iv-company-batch1-20260819-000000.xlsx"
SHOPEE_FILE="orders_2026-07-01_2026-07-31.xlsx"
GHTK_FILE="GHTK_bao_cao_test.xlsx"
RECON_FILE="bien_ban_doi_soat_test.xlsx"
SAOKE_FILE="11090087_SAOKE_TK_202608.pdf"
PLAN_JUNK_LOG="preview-must-not-delete.log"
PLAN_PO_FILE="PO-9988 preview must not move.txt"
PURCHASE_ZIP="ho so PO9989 - 2026-08-25 - NCC Test.zip"

printf 'vat report' >"${YEAR_ROOT}/${VAT_FILE}"
printf 'shopee report' >"${YEAR_ROOT}/${SHOPEE_FILE}"
printf 'ghtk report' >"${YEAR_ROOT}/${GHTK_FILE}"
printf 'reconciliation report' >"${YEAR_ROOT}/${RECON_FILE}"
printf 'bank statement' >"${YEAR_ROOT}/${SAOKE_FILE}"
printf 'preview junk log' >"${YEAR_ROOT}/${PLAN_JUNK_LOG}"
printf 'preview purchase order' >"${YEAR_ROOT}/${PLAN_PO_FILE}"
python3 - "$YEAR_ROOT/$PURCHASE_ZIP" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'w') as archive:
    archive.writestr('invoice.txt', 'purchase dossier attachment')
PY
PURCHASE_SHA=$(sha256sum "$YEAR_ROOT/$PURCHASE_ZIP" | cut -d' ' -f1)

mkdir -p "${YEAR_ROOT}/20_BAO_CAO/GHTK"
printf 'pre-existing file with the same name' >"${YEAR_ROOT}/20_BAO_CAO/GHTK/${GHTK_FILE}"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

run_organizer() {
  HOME="$TMP_HOME" IVAR_DATA="$IVAR_DATA_DIR" STATE_DIR="$STATE_DIR_TEST" \
    CURRENT_DATE="2026-08-25" \
    sh "$ORGANIZER" --no-sync --quiet "$@"
}

echo "== plan mode: must not move any file =="
run_organizer --plan-managed-rules

[ -f "${YEAR_ROOT}/${VAT_FILE}" ] || fail "plan mode moved VAT file"
[ -f "${YEAR_ROOT}/${SHOPEE_FILE}" ] || fail "plan mode moved Shopee file"
[ -f "${YEAR_ROOT}/${GHTK_FILE}" ] || fail "plan mode moved GHTK file"
[ -f "${YEAR_ROOT}/${RECON_FILE}" ] || fail "plan mode moved reconciliation file"
[ -f "${YEAR_ROOT}/${SAOKE_FILE}" ] || fail "plan mode moved SAOKE file"
[ -f "${YEAR_ROOT}/${PLAN_JUNK_LOG}" ] || fail "plan mode deleted a loose log"
[ -f "${YEAR_ROOT}/${PLAN_PO_FILE}" ] || fail "plan mode moved a non-managed PO file"
[ -d "${YEAR_ROOT}/20_BAO_CAO/GHTK" ] || fail "plan mode renamed a managed folder"
[ -d "${YEAR_ROOT}/20_BAO_CAO/VAT" ] || fail "initialized VAT destination missing"
[ -d "${YEAR_ROOT}/20_BAO_CAO/Shopee" ] || fail "initialized Shopee destination missing"
content=$(cat "${YEAR_ROOT}/20_BAO_CAO/GHTK/${GHTK_FILE}")
[ "$content" = "pre-existing file with the same name" ] || fail "plan mode altered pre-existing collision file"
echo "OK: plan mode is read-only"

echo "== apply mode: must move the four files into their AT.* folders =="
run_organizer

[ ! -e "${YEAR_ROOT}/${VAT_FILE}" ] || fail "VAT file still present in year root after apply"
[ ! -e "${YEAR_ROOT}/${SHOPEE_FILE}" ] || fail "Shopee file still present in year root after apply"
[ ! -e "${YEAR_ROOT}/${GHTK_FILE}" ] || fail "GHTK file still present in year root after apply"
[ ! -e "${YEAR_ROOT}/${RECON_FILE}" ] || fail "reconciliation file still present in year root after apply"

[ -d "${YEAR_ROOT}/20_BAO_CAO/GHTK" ] || fail "report destination missing"
[ -f "${YEAR_ROOT}/20_BAO_CAO/VAT/${VAT_FILE}" ] || fail "VAT file not routed to 20_BAO_CAO/VAT"
[ -f "${YEAR_ROOT}/20_BAO_CAO/Shopee/${SHOPEE_FILE}" ] || fail "Shopee file not routed to 20_BAO_CAO/Shopee"
[ -f "${YEAR_ROOT}/20_BAO_CAO/GHTK/${RECON_FILE}" ] || fail "reconciliation file not routed to 20_BAO_CAO/GHTK"
[ -f "${YEAR_ROOT}/20_BAO_CAO/Sao ke/${SAOKE_FILE}" ] || fail "SAOKE file not routed to 20_BAO_CAO/Sao ke"

content=$(cat "${YEAR_ROOT}/20_BAO_CAO/GHTK/${GHTK_FILE}")
[ "$content" = "pre-existing file with the same name" ] || fail "collision overwrote the pre-existing GHTK file"

sha_named=$(find "${YEAR_ROOT}/20_BAO_CAO/GHTK" -maxdepth 1 -type f -name "GHTK_bao_cao_test_*.xlsx")
[ -n "$sha_named" ] || fail "colliding GHTK file was not renamed with a SHA-256 suffix"
moved_content=$(cat "$sha_named")
[ "$moved_content" = "ghtk report" ] || fail "SHA-suffixed file does not contain the moved source content"
echo "OK: apply mode routed all four files, collision handled with SHA-256 suffix"

echo "== idempotency: a second apply run must not error or reprocess moved files =="
run_organizer --apply-managed-rules
[ -f "${YEAR_ROOT}/20_BAO_CAO/VAT/${VAT_FILE}" ] || fail "VAT file missing after second apply run"
echo "OK: second apply run is idempotent"

[ -f "$YEAR_ROOT/$PURCHASE_ZIP" ] || fail "purchase dossier ZIP was moved out of intake"
[ "$(sha256sum "$YEAR_ROOT/$PURCHASE_ZIP" | cut -d' ' -f1)" = "$PURCHASE_SHA" ] || fail "purchase dossier ZIP changed"
[ ! -e "$YEAR_ROOT/20_BAO_CAO/Nhap hang/$PURCHASE_ZIP" ] || fail "purchase dossier ZIP was treated as a PO report"
[ -f "$YEAR_ROOT/20_BAO_CAO/Nhap hang/$PLAN_PO_FILE" ] || fail "standalone PO document was not filed as a report"
echo "OK: purchase dossier ZIP held for review; standalone PO report still routed"

echo "== junk .log cleanup: dry-run preserves, a normal run deletes =="
JUNK_LOG="somesite.com-1755999999999.log"
printf 'junk crawl log' >"${YEAR_ROOT}/${JUNK_LOG}"

run_organizer --dry-run
[ -f "${YEAR_ROOT}/${JUNK_LOG}" ] || fail "dry-run deleted the junk log file"
echo "OK: --dry-run preserves junk .log files"

run_organizer
[ ! -e "${YEAR_ROOT}/${JUNK_LOG}" ] || fail "junk .log file was not deleted on a normal run"
echo "OK: a normal run deletes loose .log files from the year root"

echo "== junk .log cleanup: byte-identical logs must both be deleted, never routed to duplicates/ =="
DUP_LOG_A="sitea.com-1111111111111.log"
DUP_LOG_B="siteb.com-2222222222222.log"
printf 'identical crawl payload' >"${YEAR_ROOT}/${DUP_LOG_A}"
printf 'identical crawl payload' >"${YEAR_ROOT}/${DUP_LOG_B}"

run_organizer
[ ! -e "${YEAR_ROOT}/${DUP_LOG_A}" ] || fail "first byte-identical junk log was not deleted"
[ ! -e "${YEAR_ROOT}/${DUP_LOG_B}" ] || fail "second byte-identical junk log was not deleted"
stray=$(find "${YEAR_ROOT}" -type f -path "*/duplicates/site*.log" 2>/dev/null)
[ -z "$stray" ] || fail "byte-identical junk log was diverted to duplicates/: $stray"
echo "OK: byte-identical junk logs are both deleted outright, none diverted to duplicates/"

echo "ALL MANAGED ROUTE INTEGRATION CHECKS PASSED"

echo "== inbox routing: nested intake reports use canonical destinations =="
mkdir -p "${YEAR_ROOT}/00_INBOX/import-batch"
printf 'new inbox report' >"${YEAR_ROOT}/00_INBOX/import-batch/GHTK_inbox.xlsx"
run_organizer --plan-managed-rules
[ -f "${YEAR_ROOT}/00_INBOX/import-batch/GHTK_inbox.xlsx" ] || fail "preview moved inbox source"
run_organizer
[ -f "${YEAR_ROOT}/20_BAO_CAO/GHTK/GHTK_inbox.xlsx" ] || fail "inbox report not routed"
[ ! -e "${YEAR_ROOT}/00_INBOX/import-batch/GHTK_inbox.xlsx" ] || fail "inbox source still present"
echo "OK: nested inbox report routed to canonical target"
