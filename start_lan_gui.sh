#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then
  echo "用法: $0 '至少10位GUI访问密码'" >&2
  exit 2
fi
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export YSU_GUI_PASSWORD=$1
exec python3 "$SCRIPT_DIR/lan_gui.py" --config "$SCRIPT_DIR/lan_gui_config.json"
