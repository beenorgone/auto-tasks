# Plan: shared managed WIP routing for IVAR organizer

## Outcome

Use one Python rule engine for both WIP indexing and the shell organizer's
controlled file moves. The shell remains the operational entry point; it asks
Python for a route and performs only approved, high-confidence moves.

The first auto-routed categories are deliberately narrow:

| Rule | WIP category | Managed destination |
| --- | --- | --- |
| `iv-mtt-batch` | `vat` | `AT.VAT` |
| `order\\.` | `shopee_reports` | `AT.Shopee reports` |
| `ghtk_` | `ghtk_reports` | `AT.GHTK reports` |
| `bien_ban_doi_soat` | `ghtk_reports` | `AT.GHTK reports` |

Rules for QC, sourcing, B2, packaging, legal, technical, and general finance
remain index/review-only in this change. Invoice PDF/XML and UNC processing
keep their existing higher-priority handlers.

## Design decisions

1. Keep `wip-organizer.py` as the canonical classifier. Add a small CLI route
   command that accepts a path and a rules file, then emits machine-readable
   category, confidence, reason, target, and route mode.
2. Keep the active, user-editable rules in `2026/.wip/rules.json`. Add an
   explicit `route_mode` field. Only `route_mode: managed_move` may be acted
   upon by the shell; all other categories remain informational.
3. Update `DEFAULT_RULES` in `wip-organizer.py` to seed the same category order
   and targets. Place `ghtk_reports` before `finance_ops`, and remove the four
   managed patterns from `finance_ops` so a later broad rule cannot override
   the intended route.
4. `ivar-documents-organizer.sh` calls the Python route command for loose files
   in the current year root only. It must never recurse through an `AT.*`
   destination or turn a WIP review into an automatic project filing.
5. Validate the returned destination before moving: it must be exactly one of
   `AT.VAT`, `AT.Shopee reports`, or `AT.GHTK reports`, must be relative, and
   must not contain `..`.

## Implementation steps

### 1. Extend Python WIP rules and CLI

- Change VAT and Shopee targets to their local managed `AT.*` folders.
- Add `ghtk_reports` with the two specified patterns and target
  `AT.GHTK reports`.
- Add `route_mode: managed_move` to the three approved categories; all other
  categories get `route_mode: review_only` or omit it (defaulting to review).
- Add a command such as:

  ```bash
  python3 wip-organizer.py route \
    --rules /home/beenorgone/Documents/2026/.wip/rules.json \
    --path 'GHTK_bao_cao_van_hanh_va_dong_tien_13_08_S12734516.xlsx' \
    --format tsv
  ```

  The TSV fields are: `category`, `confidence`, `target`, `route_mode`, and
  `reason`. Exit successfully for a deterministic match and return a distinct
  nonzero code when no approved managed move is available.

- Keep `scan`, `dashboard`, `review`, and `set-status` index-only. Do not add
  a move command to the WIP dashboard workflow.

### 2. Add a managed-route phase to the shell organizer

- Add `--plan-managed-rules` (read-only) and `--apply-managed-rules` (moves)
  flags. Existing default behavior must not start moving generic WIP files.
- In `process_source_file`, run duplicate detection first. Then, for a file
  directly in `CURRENT_YEAR_ROOT`, ask Python for its route before OCR or the
  general monthly bucket. This keeps `.xlsx` report handling fast.
- `--plan-managed-rules` logs the proposed source, category, reason, and final
  destination but performs no `mkdir`, database write, or move.
- `--apply-managed-rules` creates the approved `AT.*` folder, moves the file,
  and uses the existing SHA-256 prefix convention on name collisions.
- Record the result in `processed.db` with a status such as
  `managed-route` and a note containing the category. Do not treat it as an
  invoice, UNC, or duplicate.
- Preserve `--dry-run`: it must print exactly the same action plan without
  changing files or SQLite state. Preserve `--no-sync` for every validation
  and first production run.

### 3. Tests

- Extend `test_wip_organizer.py` to assert category, target, and route mode
  for all four rules above, plus a negative case proving unrelated GHTK text
  is not routed when it does not match either expression.
- Add shell integration checks using a temporary `HOME/Documents/2026` root:
  place one VAT XLSX, one Shopee `Order.*.xlsx`, one `GHTK_*.xlsx`, and one
  reconciliation XLSX in the root; run plan mode; assert no files moved.
- Run apply mode against the same temporary root; assert the four files are in
  their expected `AT.*` folders and the hash collision behavior is stable.
- Run `sh -n ivar-documents-organizer.sh`, `python3 -m py_compile
  wip-organizer.py`, and `python3 test_wip_organizer.py`.
- Run a real-root plan only:

  ```bash
  sh ivar-documents-organizer.sh --plan-managed-rules --no-sync
  ```

  Review the output before a supervised apply:

  ```bash
  sh ivar-documents-organizer.sh --apply-managed-rules --no-sync
  python3 wip-organizer.py scan
  python3 wip-organizer.py dashboard
  ```

## Acceptance criteria

- The four rule inputs route deterministically to the three specified `AT.*`
  folders in both the Python CLI and shell plan output.
- Shell plan mode is read-only; apply mode is the only mode that moves a
  managed report.
- No invoice XML/PDF, UNC, technical package, or review-only file is moved by
  the new route phase.
- A second run is idempotent: files already in `AT.*` are not reprocessed and
  collisions remain traceable by SHA-256 prefix.
- The WIP dashboard shows `VAT`, `Shopee reports`, and `GHTK reports` with the
  corresponding local managed targets.

## Commit plan

1. `feat(wip): expose managed routing rules for organizer reuse`
2. `feat(organizer): apply approved WIP report routes with plan mode`
3. `test(organizer): cover managed VAT Shopee and GHTK report routing`

Generated SQLite databases and `2026/.wip/` runtime state are not committed
unless explicitly approved.
