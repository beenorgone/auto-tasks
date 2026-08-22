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

The default scan indexes root-level files plus `WIP ke toan thue`, `Review
Label`, the CUC dossier, and product self-declaration dossier. Personal book
collections and PDF conversion artefacts are excluded by policy.

Use a narrowly scoped inventory when refining rules:

```bash
python3 wip-organizer.py scan --no-root-files --source 'Review Label'
python3 wip-organizer.py dashboard
```

Update a dossier's WIP state without touching its documents:

```bash
python3 wip-organizer.py set-status FINANCE_OPS-2026-08-GHTK in_progress
```

Filing to project folders is intentionally a later phase requiring an
explicit approval workflow and copy/hash verification.
