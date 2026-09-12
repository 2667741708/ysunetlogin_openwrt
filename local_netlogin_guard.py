#!/usr/bin/env python3
"""Keep one host logged in with the configured campus-network service."""

import argparse
import fcntl
import json
import subprocess
import sys
from pathlib import Path


SERVICE_IDS = {
    "校园网": "0",
    "中国移动": "1",
    "中国联通": "2",
    "中国电信": "3",
}


def run_netlogin(netlogin: Path, *args: str, capture: bool = False):
    return subprocess.run(
        [sys.executable, str(netlogin), *args],
        text=True,
        capture_output=capture,
        timeout=45,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts-file", required=True, type=Path)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--netlogin", required=True, type=Path)
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path.home() / ".local/state/ysu-netlogin-guard.lock",
    )
    args = parser.parse_args()

    args.lock_file.parent.mkdir(parents=True, exist_ok=True)
    with args.lock_file.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        with args.accounts_file.open(encoding="utf-8-sig") as src:
            accounts = json.load(src)["accounts"]
        account = next(
            item for item in accounts if item["name"] == args.account_name
        )
        expected = account["service"]
        service_id = str(account.get("service_type") or SERVICE_IDS[expected])

        status = run_netlogin(
            args.netlogin, "current-status", "--json", capture=True
        )
        summary = {}
        if status.returncode == 0:
            try:
                summary = json.loads(status.stdout).get("summary") or {}
            except json.JSONDecodeError:
                pass

        if summary.get("online") and summary.get("service") == expected:
            print(f"healthy: {expected}")
            return 0

        if summary.get("online"):
            print(
                f"correcting service: {summary.get('service') or 'unknown'}"
                f" -> {expected}"
            )
            run_netlogin(args.netlogin, "logout")

        login = run_netlogin(
            args.netlogin,
            account["username"],
            account["password"],
            service_id,
        )
        if login.returncode:
            print("netlogin failed", file=sys.stderr)
        return login.returncode


if __name__ == "__main__":
    raise SystemExit(main())
