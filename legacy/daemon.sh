#!/bin/sh
# Legacy cron calls are now read-only; login owns the automatic heartbeat lifecycle.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/../netlogin.py" heartbeat-status
