# 2026 WIP Organizer

`wip-organizer.py` indexes proposed classifications for Documents/2026 and
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

The default scan indexes root-level files plus managed `AT. ` folders for GHTK,
B2, VAT, Shopee reports, SAOKE, QC reports, sourcing contracts, and invoice intake;
it also scans `WIP ke toan thue`, `Review Label`, the CUC dossier, and product
self-declaration dossier. The `AT. ` prefix identifies folders maintained by
automation rules. Personal book collections and PDF conversion artefacts are
excluded by policy.

Use a narrowly scoped inventory when refining rules:

```bash
python3 wip-organizer.py scan --no-root-files --source 'Review Label'
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

It binds only to `127.0.0.1:8787`. Use **Preview routing** first; it cannot
move, rename, or sync files. **Apply changes** runs the existing approved
managed routes. The rclone project sync remains off unless explicitly checked.

The included user service starts the app at login. Install it once from this
repository, then enable it:

```bash
cp systemd/ivar-document-sync-app.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ivar-document-sync-app.service
```
