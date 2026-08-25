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

VAT_FILE="iv-mtt-batch1-20260819-000000.xlsx"
SHOPEE_FILE="Order.all.20260701_20260731.xlsx"
GHTK_FILE="GHTK_bao_cao_test.xlsx"
RECON_FILE="bien_ban_doi_soat_test.xlsx"

printf 'vat report' >"${YEAR_ROOT}/${VAT_FILE}"
printf 'shopee report' >"${YEAR_ROOT}/${SHOPEE_FILE}"
printf 'ghtk report' >"${YEAR_ROOT}/${GHTK_FILE}"
printf 'reconciliation report' >"${YEAR_ROOT}/${RECON_FILE}"

mkdir -p "${YEAR_ROOT}/AT.GHTK reports"
printf 'pre-existing file with the same name' >"${YEAR_ROOT}/AT.GHTK reports/${GHTK_FILE}"

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
[ ! -d "${YEAR_ROOT}/AT.VAT" ] || fail "plan mode created AT.VAT"
[ ! -d "${YEAR_ROOT}/AT.Shopee reports" ] || fail "plan mode created AT.Shopee reports"
content=$(cat "${YEAR_ROOT}/AT.GHTK reports/${GHTK_FILE}")
[ "$content" = "pre-existing file with the same name" ] || fail "plan mode altered pre-existing collision file"
echo "OK: plan mode is read-only"

echo "== apply mode: must move the four files into their AT.* folders =="
run_organizer --apply-managed-rules

[ ! -e "${YEAR_ROOT}/${VAT_FILE}" ] || fail "VAT file still present in year root after apply"
[ ! -e "${YEAR_ROOT}/${SHOPEE_FILE}" ] || fail "Shopee file still present in year root after apply"
[ ! -e "${YEAR_ROOT}/${GHTK_FILE}" ] || fail "GHTK file still present in year root after apply"
[ ! -e "${YEAR_ROOT}/${RECON_FILE}" ] || fail "reconciliation file still present in year root after apply"

[ -f "${YEAR_ROOT}/AT.VAT/${VAT_FILE}" ] || fail "VAT file not routed to AT.VAT"
[ -f "${YEAR_ROOT}/AT.Shopee reports/${SHOPEE_FILE}" ] || fail "Shopee file not routed to AT.Shopee reports"
[ -f "${YEAR_ROOT}/AT.GHTK reports/${RECON_FILE}" ] || fail "reconciliation file not routed to AT.GHTK reports"

content=$(cat "${YEAR_ROOT}/AT.GHTK reports/${GHTK_FILE}")
[ "$content" = "pre-existing file with the same name" ] || fail "collision overwrote the pre-existing GHTK file"

sha_named=$(find "${YEAR_ROOT}/AT.GHTK reports" -maxdepth 1 -type f -name "GHTK_bao_cao_test_*.xlsx")
[ -n "$sha_named" ] || fail "colliding GHTK file was not renamed with a SHA-256 suffix"
moved_content=$(cat "$sha_named")
[ "$moved_content" = "ghtk report" ] || fail "SHA-suffixed file does not contain the moved source content"
echo "OK: apply mode routed all four files, collision handled with SHA-256 suffix"

echo "== idempotency: a second apply run must not error or reprocess moved files =="
run_organizer --apply-managed-rules
[ -f "${YEAR_ROOT}/AT.VAT/${VAT_FILE}" ] || fail "VAT file missing after second apply run"
echo "OK: second apply run is idempotent"

echo "ALL MANAGED ROUTE INTEGRATION CHECKS PASSED"
