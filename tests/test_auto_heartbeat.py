import builtins
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from unittest import mock

import auto_heartbeat as auto
from build_cli_bundle import build_bundle


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError('background worker did not reach expected state')


def worker_released(directory):
    try:
        with auto.FileLock(directory / 'worker.lock', timeout=0):
            return True
    except TimeoutError:
        return False


def fake_runtime(tmp_path):
    runner = tmp_path / 'fake_worker.py'
    root = str(Path(auto.__file__).resolve().parent)
    runner.write_text(f'''
import sys, json, time
from pathlib import Path
sys.path.insert(0, {root!r})
import auto_heartbeat as auto
from heartbeat import HeartbeatEngine
directory = Path(sys.argv[1])
original_schedule = HeartbeatEngine._schedule
HeartbeatEngine._schedule = lambda self, host, seconds: original_schedule(self, host, 1)
class Client:
    def current_status(self):
        if (directory / 'offline').exists():
            return {{'summary': {{'online': False}}}}
        return {{'summary': {{'online': True, 'userId': 'student', 'service': '中国移动'}}}}
    def ensure_login(self, **credentials):
        (directory / 'repaired').write_text('verified', encoding='utf-8')
        (directory / 'offline').unlink(missing_ok=True)
        return True, 'ok'
    def logout(self):
        return True, 'ok'
auto.run_worker(directory, Client)
''', encoding='utf-8')
    return runner


def test_login_starts_worker_reuses_pid_repairs_and_logout_stops(tmp_path):
    directory = tmp_path / 'state'
    runner = fake_runtime(tmp_path)
    client = mock.Mock()
    client.login.return_value = (True, 'authenticated')
    client.logout.return_value = (True, 'logged out')
    try:
        with mock.patch.object(auto, 'runtime_bundle', return_value=runner):
            ok, _, heartbeat = auto.login(client, 'student', 'test-secret', '1', directory=directory)
            assert ok and heartbeat['ok']
            first_pid = auto.status(directory)['pid']
            ok, _, heartbeat = auto.login(client, 'student', 'test-secret', '1', directory=directory)
            assert ok and heartbeat['ok']
            assert auto.status(directory)['pid'] == first_pid
        config_text = (directory / 'config.json').read_text(encoding='utf-8')
        if os.name == 'nt':
            assert 'test-secret' not in config_text
        else:
            assert directory.stat().st_mode & 0o777 == 0o700
            assert (directory / 'config.json').stat().st_mode & 0o777 == 0o600
        (directory / 'offline').touch()
        wait_for(lambda: (directory / 'repaired').exists())
        wait_for(lambda: auto.status(directory).get('state') == 'repaired')
        assert 'test-secret' not in (directory / 'status.json').read_text()
        assert auto.logout(client, directory=directory)[0]
        assert not (directory / 'config.json').exists()
        wait_for(lambda: worker_released(directory))
        assert not auto.status(directory)['running']
    finally:
        auto.stop(directory)
        wait_for(lambda: worker_released(directory))


def test_failed_login_does_not_start_or_save_credentials(tmp_path):
    client = mock.Mock()
    client.login.return_value = (False, 'rejected')
    with mock.patch.object(auto, '_start_locked') as start:
        ok, _, heartbeat = auto.login(client, 'student', 'secret', directory=tmp_path)
    assert not ok and heartbeat is None
    start.assert_not_called()
    assert not (tmp_path / 'config.json').exists()


def test_once_login_does_not_create_state(tmp_path):
    client = mock.Mock()
    client.ensure_login.return_value = (True, 'verified')
    state = tmp_path / 'unused'
    ok, _, heartbeat = auto.login(client, 'student', 'secret', '1', ensure=True,
                                 once=True, directory=state)
    assert ok and heartbeat is None and not state.exists()


def test_runtime_snapshot_can_be_built_inside_zipapp(tmp_path):
    bundle = build_bundle(tmp_path / 'netlogin.pyz', 'test')
    code = ('import sys;sys.path.insert(0,sys.argv[1]);'
            'import auto_heartbeat as a;from pathlib import Path;'
            'print(a.runtime_bundle(a.prepare_directory(Path(sys.argv[2]))))')
    result = subprocess.run([sys.executable, '-c', code, str(bundle), str(tmp_path / 'state')],
                            capture_output=True, text=True, check=True)
    snapshot = Path(result.stdout.strip())
    assert snapshot.is_file()
    # No config means the real worker exits without performing any network operation.
    completed = subprocess.run([sys.executable, str(snapshot), str(tmp_path / 'state')],
                               capture_output=True, timeout=10)
    assert completed.returncode == 0


def test_cli_success_automatically_arms_heartbeat_and_preserves_json():
    original_build_class = builtins.__build_class__
    client = mock.Mock()
    client.services = {'1': '中国移动'}
    def build_class(func, name, *bases, **kwargs):
        cls = original_build_class(func, name, *bases, **kwargs)
        return mock.Mock(return_value=client) if name == 'Netlogin' else cls
    output = io.StringIO()
    with mock.patch('builtins.__build_class__', side_effect=build_class), \
            mock.patch.object(sys, 'argv', ['netlogin.py', 'login-stdin']), \
            mock.patch.object(sys, 'stdin', io.StringIO(json.dumps({
                'username': 'student', 'password': 'test-only', 'service': '1'}))), \
            mock.patch.object(auto, 'login', return_value=(True, 'ok', {'ok': True})) as start, \
            contextlib.redirect_stdout(output):
        try:
            runpy.run_path(str(Path(auto.__file__).with_name('netlogin.py')), run_name='__main__')
        except SystemExit as stopped:
            assert stopped.code == 0
    start.assert_called_once_with(client, 'student', 'test-only', '1', ensure=True)
    assert json.loads(output.getvalue())['heartbeat']['ok']
