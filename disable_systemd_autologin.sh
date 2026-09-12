#!/usr/bin/env bash
set -euo pipefail

UNIT_PREFIX="ysu-netlogin"
KEEP_ENV=0
MASK_UNITS=1

usage() {
  cat <<'EOF'
Disable the legacy standalone YSU netlogin auto-recovery service.

This disables only ysu-netlogin.timer/service by default. It does not touch
zerotier-one or netlogin-mutual-watchdog.

Usage:
  sudo ./disable_systemd_autologin.sh [--keep-env] [--no-mask]

Options:
  --keep-env   Keep /etc/ysu-netlogin.env in place after backing it up.
  --no-mask    Stop and disable units, but do not mask them.
  --help       Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --keep-env)
      KEEP_ENV=1
      ;;
    --no-mask)
      MASK_UNITS=0
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo "$0" "$@"
  fi
  echo "This script must run as root." >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found; this script is for Linux hosts using systemd." >&2
  exit 1
fi

timer="${UNIT_PREFIX}.timer"
service="${UNIT_PREFIX}.service"
runner="/usr/local/sbin/${UNIT_PREFIX}-run"
env_file="/etc/${UNIT_PREFIX}.env"
opt_dir="/opt/${UNIT_PREFIX}"
stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="/root/${UNIT_PREFIX}-disabled-${stamp}"

mkdir -p "$backup_dir"

backup_if_exists() {
  local path="$1"
  if [ -e "$path" ] || [ -L "$path" ]; then
    cp -a "$path" "$backup_dir/"
  fi
}

echo "Backing up legacy auto-login files to: $backup_dir"
backup_if_exists "/etc/systemd/system/$timer"
backup_if_exists "/etc/systemd/system/$service"
backup_if_exists "$runner"
backup_if_exists "$env_file"
backup_if_exists "$opt_dir"

echo "Stopping and disabling $timer / $service"
systemctl disable --now "$timer" 2>/dev/null || true
systemctl stop "$service" 2>/dev/null || true

if [ "$MASK_UNITS" -eq 1 ]; then
  echo "Masking $timer / $service"
  if ! systemctl mask --force "$timer" "$service"; then
    echo "Direct mask failed; moving existing unit files aside and retrying."
    for unit in "$timer" "$service"; do
      unit_path="/etc/systemd/system/$unit"
      if [ -e "$unit_path" ] && [ "$(readlink "$unit_path" 2>/dev/null || true)" != "/dev/null" ]; then
        mv "$unit_path" "${unit_path}.disabled-${stamp}"
      fi
    done
    systemctl daemon-reload
    systemctl mask "$timer" "$service"
  fi
fi

if [ "$KEEP_ENV" -eq 0 ] && [ -f "$env_file" ]; then
  disabled_env="${env_file}.disabled-${stamp}"
  echo "Disabling credential env file: $disabled_env"
  mv "$env_file" "$disabled_env"
fi

systemctl daemon-reload

echo
echo "Current status:"
systemctl is-enabled "$timer" "$service" 2>/dev/null || true
systemctl is-active "$timer" "$service" 2>/dev/null || true
systemctl status "$timer" "$service" --no-pager -l 2>/dev/null || true

echo
echo "Done. Restore backup is in: $backup_dir"
