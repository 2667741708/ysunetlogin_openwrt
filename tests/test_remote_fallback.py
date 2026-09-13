import json
import unittest
from unittest import mock

import desktop_gui


class RemoteFallbackTests(unittest.TestCase):
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
        retry.assert_called_once_with(host, 'status', None, 30)

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
