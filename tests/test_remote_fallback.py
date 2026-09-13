import json
import unittest
from unittest import mock

import desktop_gui


class RemoteFallbackTests(unittest.TestCase):
    def test_remote_login_stops_when_query_machine_preflight_fails(self):
        host = {'id': 'remote-1', 'target': 'example-host'}
        preflight = {
            'ok': False,
            'message': '当前机器不符合查询机器要求；需要先实现访问校园局域网',
        }
        with mock.patch.object(
                desktop_gui, '_run_remote_unlocked', return_value=preflight) as run:
            result = desktop_gui.run_target(host, 'login', {'username': 'demo'})

        self.assertFalse(result['ok'])
        run.assert_called_once_with(
            host, 'query-machine-status', None, 30)

    def test_remote_query_machine_check_always_uses_bundled_script(self):
        host = {
            'target': 'example-host',
            'script': '/old/netlogin.py',
        }
        identity = mock.Mock(returncode=0, stdout='example\n', stderr='')
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({'ok': True, 'message': 'qualified'}),
            stderr='',
        )
        with mock.patch.object(desktop_gui.subprocess, 'run', return_value=identity), \
                mock.patch.object(desktop_gui, 'detect_remote_python', return_value='python3'), \
                mock.patch.object(desktop_gui, 'run_remote_script_file') as old_script, \
                mock.patch.object(
                    desktop_gui, 'run_embedded_netlogin', return_value=completed) as embedded:
            result = desktop_gui._run_remote_unlocked(
                host, 'query-machine-status')

        self.assertTrue(result['ok'])
        old_script.assert_not_called()
        embedded.assert_called_once_with(
            host, 'query-machine-status', None, 30,
            remote_python='python3', online_user_uuids=None)

    def test_remote_account_query_uses_bundled_script(self):
        host = {
            'id': 'remote-1',
            'connection_type': 'ssh',
            'target': 'example-host',
            'script': '/custom/netlogin.py',
        }
        account = {'username': 'demo', 'password': 'encrypted', 'service': '1'}
        expected = {'summary': {'onlineDeviceCount': 1}}
        with mock.patch.object(
                desktop_gui, '_run_remote_unlocked', return_value=expected) as run:
            result = desktop_gui.run_account_device_operation(
                host, 'query', account, timeout=45)

        self.assertEqual(expected, result)
        embedded_host = dict(host)
        embedded_host['script'] = ''
        run.assert_called_once_with(
            embedded_host, 'account-status', account, 45,
            online_user_uuids=None)

    def test_missing_configured_script_retries_with_bundled_copy(self):
        host = {
            'target': 'example-host',
            'expected_hostname': '',
            'script': '/old/location/netlogin.py',
        }
        identity = mock.Mock(returncode=0, stdout='example\n', stderr='')
        missing = mock.Mock(
            returncode=2,
            stdout='',
            stderr="python3: can't open file '/old/location/netlogin.py': [Errno 2] No such file or directory",
        )
        bundled = mock.Mock(
            returncode=0,
            stdout=json.dumps({'ok': True, 'message': 'embedded'}),
            stderr='',
        )
        with mock.patch.object(desktop_gui.subprocess, 'run', return_value=identity), \
                mock.patch.object(desktop_gui, 'run_remote_script_file', return_value=missing), \
                mock.patch.object(desktop_gui, 'run_embedded_netlogin', return_value=bundled) as retry:
            result = desktop_gui._run_remote_unlocked(host, 'status')

        self.assertTrue(result['ok'])
        self.assertEqual('embedded', result['message'])
        retry.assert_called_once_with(
            host, 'status', None, 30, remote_python='python3')

    def test_windows_style_target_falls_back_from_python3_to_python(self):
        host = {'target': 'windows-host'}
        unavailable = mock.Mock(returncode=1, stdout='', stderr='not found')
        available = mock.Mock(
            returncode=0,
            stdout=r'C:\Python313\python.exe' + '\n',
            stderr='',
        )
        with mock.patch.object(
                desktop_gui.subprocess, 'run', side_effect=[unavailable, available]):
            selected = desktop_gui.detect_remote_python(host)

        self.assertEqual('python', selected)

    def test_other_remote_script_errors_are_not_retried(self):
        host = {
            'target': 'example-host',
            'expected_hostname': '',
            'script': '/opt/netlogin.py',
        }
        identity = mock.Mock(returncode=0, stdout='example\n', stderr='')
        failed = mock.Mock(returncode=1, stdout='login rejected', stderr='')
        with mock.patch.object(desktop_gui.subprocess, 'run', return_value=identity), \
                mock.patch.object(desktop_gui, 'run_remote_script_file', return_value=failed), \
                mock.patch.object(desktop_gui, 'run_embedded_netlogin') as retry:
            result = desktop_gui._run_remote_unlocked(host, 'status')

        self.assertFalse(result['ok'])
        retry.assert_not_called()


if __name__ == '__main__':
    unittest.main()
