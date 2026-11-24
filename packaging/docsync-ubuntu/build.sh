#!/bin/sh
# Package the runnable DocSync source and its pinned XLS dependency.
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 /path/DocSync-Ubuntu.tar.gz" >&2
    exit 2
fi

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
packaging="$source_dir/packaging/docsync-ubuntu"
archive=$(realpath -m -- "$1")
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
bundle="$stage/DocSync-Ubuntu"
mkdir -p "$bundle/app" "$bundle/systemd" "$bundle/wheels"

for file in docsync_telegram_inbox.py docsync-telegram-inbox.html docsync-telegram-inbox.js docsync-telegram-inbox.css docsync docsync.py doc-sync-app.py docsync-app.css docsync-day.html docsync-day.js \
    docsync-history.html docsync-history.js docsync-workflows.html docsync-settings.html docsync-settings.js \
    docsync-file-actions.html docsync-file-actions.js docsync_file_actions.py \
    docsync_carrier.py docsync_core.py docsync_daily.py docsync_history.py docsync_intake.py docsync_layout.py \
    docsync_paths.py docsync_purchase.py docsync_topics.py docsync_migrate_layout.py docsync_relocate.py \
    docsync_watcher.py docsync_workflows.py ivar-documents-organizer.sh wip-organizer.py; do
    cp -- "$source_dir/$file" "$bundle/app/"
done

install -m 755 "$packaging/ubuntu-launch" "$bundle/app/ubuntu-launch"
install -m 755 "$packaging/install.sh" "$bundle/install.sh"
install -m 644 "$packaging/README.md" "$packaging/DocSync.desktop" "$bundle/"

for spec in ivar-document-sync-app:app ivar-watcher:watcher; do
    unit="${spec%:*}.service"
    entry="${spec#*:}"
    sed -e 's|^WorkingDirectory=.*|WorkingDirectory=%h/.local/share/docsync/app|' \
        -e "s|^ExecStart=.*|ExecStart=%h/.local/share/docsync/app/ubuntu-launch $entry|" \
        "$source_dir/systemd/$unit" > "$bundle/systemd/$unit"
done

python3 -m pip download --disable-pip-version-check --no-deps --only-binary=:all: \
    -r "$source_dir/requirements-docsync.txt" --dest "$bundle/wheels"
mkdir -p "$(dirname -- "$archive")"
tar -czf "$stage/bundle.tar.gz" -C "$stage" DocSync-Ubuntu
install -m 644 "$stage/bundle.tar.gz" "$archive"
printf '%s\n' "$archive"
