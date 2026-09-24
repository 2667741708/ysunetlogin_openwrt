#!/usr/bin/env python3
"""Build the self-contained netlogin command bundle."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipapp


ROOT = Path(__file__).resolve().parent
MODULES = (
    "netlogin.py",
    "self_service.py",
    "campus_network.py",
    "wifi_scan.py",
)


def source_version() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def launcher(version: str) -> str:
    return f'''import runpy
import sys

BUNDLE_VERSION = {version!r}

if len(sys.argv) == 2 and sys.argv[1] in ("--version", "-V", "version"):
    print("netlogin " + BUNDLE_VERSION)
    raise SystemExit(0)

runpy.run_module("netlogin", run_name="__main__")
'''


def build_bundle(output: Path, version: str | None = None) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    version = version or source_version()

    with tempfile.TemporaryDirectory(
            prefix="netlogin-cli-", dir=output.parent) as temp_name:
        stage = Path(temp_name)
        for module in MODULES:
            shutil.copy2(ROOT / module, stage / module)
        (stage / "__main__.py").write_text(
            launcher(version), encoding="utf-8", newline="\n"
        )
        zipapp.create_archive(
            stage,
            target=output,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
    return output


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "release" / "netlogin.pyz",
    )
    parser.add_argument("--version", default=None)
    args = parser.parse_args()

    output = build_bundle(args.output, args.version)
    print(output)
    print("sha256=" + sha256(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
