#!/usr/bin/env python3
"""Fail closed when a desktop build could contain SSH private key material."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys


PRIVATE_KEY_BOUNDARY = re.compile(
    rb"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----"
)
FORBIDDEN_NAME = re.compile(
    r"^(?:id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pem)?|.*\.(?:ppk|pem))$",
    re.IGNORECASE,
)
FORBIDDEN_PATH = re.compile(
    rb"(?:^|[\\/])\.ssh[\\/](?:id_(?:rsa|dsa|ecdsa|ed25519)|[^ \r\n]+\.ppk)",
    re.IGNORECASE,
)


def audit_file(path: Path) -> list[str]:
    failures: list[str] = []
    if FORBIDDEN_NAME.fullmatch(path.name):
        failures.append(f"禁止打包疑似私钥文件：{path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"无法审计文件 {path}：{exc}"]
    if PRIVATE_KEY_BOUNDARY.search(data):
        failures.append(f"发现私钥正文边界：{path}")
    if FORBIDDEN_PATH.search(data):
        failures.append(f"发现本机 .ssh 私钥路径引用：{path}")
    return failures


def audit_paths(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        if not path.exists():
            failures.append(f"审计目标不存在：{path}")
            continue
        if path.is_file():
            failures.extend(audit_file(path))
            continue
        for child in path.rglob("*"):
            if child.is_file():
                failures.extend(audit_file(child))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    failures = audit_paths([path.resolve() for path in args.paths])
    if failures:
        print("安全审计失败：", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 2
    print("安全审计通过：未发现 SSH 私钥正文、私钥文件名或本机 .ssh 私钥路径。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
