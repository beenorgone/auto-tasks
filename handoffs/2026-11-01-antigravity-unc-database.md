# Handoff: implement a searchable UNC database

The user requests implementation, not just a proposal. They have delegated this work to the newly opened Antigravity agent. Plan first, inspect local AGENTS.md and the working tree, then implement and verify.

## Workspace and source

- Code workspace: `/home/beenorgone/Documents/.auto-tasks`
- Read-only document source: `/home/beenorgone/Documents/Drives/gDriveProjects/Working/Projects/IVAR/Accounting - Ke toan/Data - Du lieu/ngan hang/UNC/`
- Existing organizer: `ivar-documents-organizer.sh`
- Existing tracking DB: `ivar-documents-organizer/processed.db`
- Existing supplier DB: `ivar-documents-organizer/vendors.db`

## Verified findings on 2026-11-01

- Source contains 319 PDF files: 2020: 1, 2023: 1, 2024: 41, 2025: 81, 2026: 195. Recount before importing.
- No database or index file exists inside the UNC source directory.
- 102 filenames contain `PO`; this is only a filename heuristic, not verified PO extraction.
- `processed.db` has `processed_files` and `invoice_metadata_cache`. There are no dedicated beneficiary-account, payment-category or PO columns. `processed_files` stores source/final paths, file metadata, SHA-256, status, invoice/vendor fields, notes and timestamps.
- 171 tracking rows reference paths below the UNC root; only 143 current source files have an exact matching tracked final path. Do not treat this tracking DB as a complete UNC inventory.
- `vendors.db` stores supplier tax/name and routing metadata, not recipient bank accounts.

## Required outcome

Create and populate a local database with a usable lookup mechanism for UNC by:

1. Payment classification (such as supplier payments, payroll, advertising, electricity, social insurance, internal transfers, refunds, other/needs review).
2. Beneficiary account number, beneficiary name and bank.
3. PO when present; one UNC may reference multiple POs.

Store document date, UNC/reference number, amount, currency, transfer details and original file path when reliably available. Preserve account numbers and identifiers as text, including leading zeros. Distinguish sender and beneficiary accounts. Missing values must remain unknown rather than guessed. Keep extraction evidence, extraction method and review state so uncertain records are recognizable.

## Implementation boundaries

- Preserve all source PDFs: do not rename, move, delete or modify them.
- Keep existing DocSync tracking and jobs working. Prefer an independent SQLite index and reusable commands after inspecting the current design.
- Inventory every source file, including unreadable/scanned files, with explicit parse/review states. Use text extraction first; inspect representative PDF layouts and use available local OCR when needed. Read relevant PDF skills before processing.
- Use SHA-256 and size for duplicate detection; preserve all source-path associations. Re-import must be idempotent and incremental, with cached extraction keyed by content and parser version.
- Do not upload financial documents to external services without explicit authorization.
- Use indexed queries, bound SQL parameters, safe file-path handling and local access by default.
- Provide actual search commands or a local interface and instructions with working examples for each requested filter, combined filters and opening the original document.
- If adding UI, default to Material, clear processing/review states and visible next actions; keep Kanban mode in mind. Do not expand into an unrelated ERP project.
- Preserve unrelated dirty work; do not commit/push without user instruction. Provide a detailed English commit message to copy at completion.

## Validation and delivery

- Populate the DB from the entire source inventory and report unique documents versus file paths, parsed/partial/review/error counts, duplicates and missing metadata.
- Verify real beneficiary accounts and PO references against representative PDFs from different banks/layouts/years; test sender/beneficiary separation, leading zeros, multiple POs and uncertain fields.
- Verify idempotent reruns and search results for category, beneficiary account and PO. Report coverage limits honestly.
- Deliver exact database path, lookup entry point/commands, update instructions and remaining review items. Complete the implementation rather than stopping at a plan.
