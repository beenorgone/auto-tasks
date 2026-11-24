#!/bin/sh
# Install the archived release for the current Ubuntu user.
set -eu
umask 077

case "${1-}" in
    '') start_services=true ;;
    --no-start) start_services=false ;;
    *) echo "Cách dùng: $0 [--no-start]" >&2; exit 2 ;;
esac

if [ "$(id -u)" -eq 0 ]; then
    echo 'Chạy bộ cài bằng tài khoản Ubuntu thường.' >&2
    exit 1
fi
if ! /usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo 'Cần Python 3.10 trở lên tại /usr/bin/python3.' >&2
    exit 1
fi

bundle=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
data="$HOME/.local/share/docsync"
app="$data/app"
bin="$HOME/.local/bin"
applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
services='ivar-document-sync-app.service ivar-watcher.service'

for artifact in "$app" "$bin/docsync" "$bin/docsync-ubuntu" "$applications/DocSync.desktop" \
    "$units/ivar-document-sync-app.service" "$units/ivar-watcher.service"; do
    if [ -e "$artifact" ] || [ -L "$artifact" ]; then
        printf 'Đã có bản DocSync tại %s. Bộ cài dành cho máy cài mới.\n' "$artifact" >&2
        exit 1
    fi
done
if [ "$start_services" = true ]; then
    systemctl --user show-environment >/dev/null
fi

mkdir -p "$data" "$bin" "$applications" "$units" "$data/state"
stage=$(mktemp -d "$data/install.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT
cp -R -- "$bundle/app/." "$stage/"
mkdir "$stage/vendor"
for wheel in "$bundle"/wheels/*.whl; do
    /usr/bin/python3 -m zipfile -e "$wheel" "$stage/vendor"
done
PYTHONPATH="$stage/vendor" /usr/bin/python3 -c 'import xlrd'
PYTHONPATH="$stage" /usr/bin/python3 - <<'PY'
from pathlib import Path
from docsync_paths import workspace_config, workspace_defaults, save_workspace_paths

if not workspace_config().exists():
    defaults = workspace_defaults()
    local_accounting = Path.home() / 'Documents' / 'DocSync Accounting'
    for directory in (Path(defaults['DOCSYNC_ROOT']), local_accounting):
        directory.mkdir(parents=True, exist_ok=True)
    save_workspace_paths({
        'DOCSYNC_ROOT': defaults['DOCSYNC_ROOT'],
        'IVAR_DATA': str(local_accounting),
        'DOCSYNC_DOCUMENTS_SCAN': '',
        'DOCSYNC_DOWNLOADS_SCAN': '',
    })
PY

mv -T -- "$stage" "$app"
ln -s "$app/ubuntu-launch" "$bin/docsync"
ln -s "$app/ubuntu-launch" "$bin/docsync-ubuntu"
install -m 644 "$bundle/DocSync.desktop" "$applications/DocSync.desktop"
for unit in $services; do
    install -m 644 "$bundle/systemd/$unit" "$units/$unit"
done

if [ "$start_services" = true ]; then
    systemctl --user daemon-reload
    systemctl --user enable --now $services
fi
printf 'DocSync đã cài tại %s\nMở từ menu ứng dụng hoặc vào http://127.0.0.1:8787/\n' "$app"
