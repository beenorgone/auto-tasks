#!/usr/bin/env bash
set -euo pipefail

systemctl --user start ivar-unc.service
for attempt in {1..50}; do
    if curl --fail --silent --output /dev/null http://127.0.0.1:8788/api/stats; then
        exec /usr/bin/google-chrome --app=http://127.0.0.1:8788/
    fi
    sleep 0.1
done
printf 'UNC app did not start. Check: journalctl --user -u ivar-unc.service\n' >&2
exit 1
