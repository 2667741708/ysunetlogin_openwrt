import subprocess
import sys
import zipfile

from build_cli_bundle import ASSETS, MODULES, build_bundle


def test_cli_bundle_contains_required_modules(tmp_path):
    bundle = build_bundle(tmp_path / "netlogin.pyz", "test-version")
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
    assert "__main__.py" in names
    for module in MODULES:
        assert module in names
    for asset in ASSETS:
        assert asset in names


def test_cli_bundle_version_and_help(tmp_path):
    bundle = build_bundle(tmp_path / "netlogin.pyz", "test-version")

    version = subprocess.run(
        [sys.executable, str(bundle), "--version"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert version.returncode == 0
    assert version.stdout.strip() == "netlogin test-version"

    help_result = subprocess.run(
        [sys.executable, str(bundle), "--help"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert help_result.returncode == 0
    assert "account-login" in help_result.stdout
    assert "netlogin.py userid" not in help_result.stdout
