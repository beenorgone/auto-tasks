# 2026 WIP Organizer

Hướng dẫn sử dụng tiếng Việt: [GUIDE-DOCSYNC.md](GUIDE-DOCSYNC.md).

`wip-organizer.py` indexes proposed classifications for Documents/WIP & Reference and
renders a local Kanban dashboard. It has no command that moves, copies,
renames, deletes, or files a source document.

## Safe first-use flow

```bash
python3 wip-organizer.py init
python3 wip-organizer.py scan
python3 wip-organizer.py dashboard
```

The generated state is outside this repository at:

- `../2026/.wip/index.sqlite` — local index and audit events
- `../2026/.wip/rules.json` — editable deterministic classification rules
- `../2026/.wip/dashboard.html` — Material-style Kanban board

The default scan indexes root files, `00_INBOX`, `10_HO_SO`, `20_BAO_CAO`,
`80_THAM_CHIEU`, and `90_CACH_LY_KY_THUAT`. Legacy scopes remain readable during
transition. Folder names describe document content; workflow state lives in Kanban. Personal book collections and PDF conversion artefacts are
excluded by policy.

Use a narrowly scoped inventory when refining rules:

```bash
python3 wip-organizer.py scan --no-root-files --source '10_HO_SO/Bao bi'
python3 wip-organizer.py dashboard
```

Update a dossier's WIP state without touching its documents:

```bash
python3 wip-organizer.py set-status FINANCE_OPS-2026-08-GHTK in_progress
```

Review the manual-routing queue, then record a human decision without filing
the source document:

```bash
python3 wip-organizer.py review-queue
python3 wip-organizer.py review NEEDS_REVIEW-2026-08-EXAMPLE legal_corporate \
  --status in_progress --note 'confirmed legal authorization dossier'
python3 wip-organizer.py dashboard
```

Filing to project folders is intentionally a later phase requiring an
explicit approval workflow and copy/hash verification.

## Local Document Sync app

Start the Material-style local control panel with:

```bash
python3 doc-sync-app.py --open
```

It binds only to `127.0.0.1:8787` and reuses `wip-organizer.py` as the single
source of classification rules. **Preview routing** forces organizer dry-run,
disables rclone, then refreshes the WIP index and Kanban dashboard. Source
documents are not moved, renamed, copied, or deleted during preview.

Use **Refresh Kanban** to index root-level files and the configured WIP scopes
without running the accounting organizer. **Open Kanban** serves the generated
dashboard from the local app. Categories marked `review_only` are displayed for
human review and are never moved by DocSync.

**Apply changes** runs approved `managed_move` routes and existing accounting handlers, refreshes the
WIP index and dashboard afterward, and keeps the rclone project sync off unless
it is explicitly checked.

The included user service starts the app at login. Install it once from this
repository, then enable it:

```bash
cp systemd/ivar-document-sync-app.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ivar-document-sync-app.service
```

## DocSync CLI and service health

Install the small XLS reader environment and CLI entry point:

```bash
uv venv .venv-docsync
uv pip install --python .venv-docsync/bin/python -r requirements-docsync.txt
ln -s "$PWD/docsync" "$HOME/.local/bin/docsync"
```

If an older `docsync` shell alias exists, point it at this checkout's `docsync`
launcher and reload that alias. Bare `docsync` now displays the Kanban board.

```bash
docsync                 # same as docsync board; reads the current queue
docsync board --stage ready --limit 0  # all cards ready for managed routing
docsync status          # watcher, timer, last completion, next run, recent log
docsync rules           # category IDs and automatic/review-only routing modes
docsync scan            # refresh index and HTML Kanban without moving documents
docsync board --json    # machine-readable cards, reasons, targets and next actions
docsync review DOSSIER_ID legal_corporate --note 'Confirmed legal dossier'
docsync set-status DOSSIER_ID waiting_external
docsync preview         # preview routing; no external sync
docsync apply           # apply managed routes and existing accounting handlers
docsync apply --sync-project  # explicitly include external rclone sync
```

Board columns: **Chờ duyệt → Đang xử lý → Chờ bên ngoài → Sẵn sàng chuyển →
Đã lưu hồ sơ → Lưu trữ**. Each card shows its reason, proposed destination,
dossier ID and next action. `review_only` decisions remain manual filing work;
marking a dossier approved does not enable automatic moves. Manual decisions
survive subsequent scans while the file content remains unchanged. Changed
content is classified again. The board reflects indexed scopes plus live direct
files in the WIP & Reference root. Run `scan` to discover new nested WIP files.

The shell organizer enables managed rules by default for normal runs, including
legacy invocations through the Tools symlink. UI, CLI, timer and watcher use the
same organizer and a shared `flock`; a competing run exits 75 (busy) before any
processing. Watcher busy/error results remain queued for retry.

The watcher inspects direct intake files in the WIP & Reference root,
`00_INBOX`, `00_INBOX/Hoa don`, Documents and Downloads every 5 seconds. It waits for 20 seconds of stability,
uses a 60-second cooldown and ignores temporary downloads. It supports all
file types handled by the organizer, not only PDFs. Existing files at watcher
startup wait for the regular timer or an explicit apply. Watcher runs use
`--no-sync`; the existing six-hour timer retains its project-sync behavior.

Deploy service definitions with:

```bash
cp systemd/ivar-watcher.service systemd/ivar-documents-organizer.service systemd/ivar-document-sync-app.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user reset-failed ivar-watcher.service
systemctl --user enable ivar-watcher.service
systemctl --user restart ivar-watcher.service
systemctl --user restart ivar-document-sync-app.service
```

The web overview caches health and queue reads for 30 seconds. Source filenames
are rendered as text. No browser request can select a systemd unit or shell
command. Watcher output goes to the journal with bounded restart attempts.

Rules v8 add company VAT batches and dated `orders_` exports. Generic transaction
workbooks require bank/account/balance header evidence before automatic routing.
Unreadable, oversized or unsupported workbooks remain for review. XLS verification
requires the pinned `xlrd` dependency; XLSX inspection uses bounded ZIP/XML reads.
Personal education programs no longer match Commerce on “Chương trình” alone.

Validation:

```bash
.venv-docsync/bin/python -m unittest -q test_doc_sync_app.py test_wip_organizer.py test_docsync.py
sh test_managed_routes.sh
.venv-docsync/bin/python -m unittest -q test_docsync_layout.py
```

## Folder layout migration (v9)

Canonical paths and category associations live in `docsync_layout.py`.
Four automatic report routes now target `20_BAO_CAO/{VAT,Shopee,GHTK,Sao ke}`.
B2B uses only `10_HO_SO/Kinh doanh/B2B/{Bang gia,Doi soat cong no}` and stays
review-only. Folder context wins over incidental filename keywords. Custom rule
targets are retained; only recognized old defaults are migrated.

`00_INBOX` is scanned for processing during organizer runs, including nested
invoice packages. `10_HO_SO` holds working dossiers, while existing official
invoice/UNC destinations in the IVAR project stay in place. Existing month
folders are retained. New report date/account subfolders are not guessed from
filesystem timestamps.

For an explicit structural migration, stop the watcher, timer and web app, check
that the organizer is idle, then plan and review mappings:

```bash
.venv-docsync/bin/python docsync_migrate_layout.py --year-root "$HOME/Documents/WIP & Reference" > /tmp/docsync-layout-plan.json
```

After reviewing the mappings, run the authorized migration:

```bash
.venv-docsync/bin/python docsync_migrate_layout.py --year-root "$HOME/Documents/WIP & Reference" --apply
./docsync scan
```

The migration holds the shared organizer lock, rejects symlinks and destination
collisions, preserves file names/hash/inodes/mtime, backs up SQLite databases and
rules, remaps indexed paths and processed final destinations, and rolls back its
moves on failure. No project/rclone sync is run. Source directories are removed
only when empty. Unknown B2B files remain at their old location and are listed in
the plan. Plan mode does not modify source files or metadata.

Review the audit in `99_NHAT_KY`, check `./docsync board`, then restore the services
that were active before migration. Do not run the legacy broad organizer Apply
as a substitute for this folder-only migration.
