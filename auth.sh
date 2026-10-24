#!/bin/sh
# Compatibility login entry point; a successful login automatically starts heartbeat.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/netlogin.py" "你的学号" "你的校园网密码" "1"
