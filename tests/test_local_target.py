import unittest
from unittest import mock

import desktop_gui


class LocalTargetTests(unittest.TestCase):
    def setUp(self):
        self.host = desktop_gui.default_local_host()
        self.account = {
            'id': 'account-1',
            'name': '本机移动账号',
            'username': '20260001',
            'password': 'dpapi-value',
            'service': '1',
        }

    @mock.patch('desktop_gui.Netlogin')
    def test_local_status_uses_current_windows_network(self, netlogin_class):
        netlogin_class.return_value.current_status.return_value = {
            'internetOnline': True,
            'summary': {'online': True, 'userId': '20260001'},
        }

        result = desktop_gui._run_local_unlocked(self.host, 'status')

        self.assertTrue(result['ok'])
        self.assertEqual('local', result['targetType'])
        netlogin_class.return_value.current_status.assert_called_once_with()

    @mock.patch('desktop_gui.unprotect_secret', return_value='plain-password')
    @mock.patch('desktop_gui.Netlogin')
    def test_local_login_uses_selected_account_and_operator(
            self, netlogin_class, unprotect_secret):
        netlogin_class.return_value.ensure_login.return_value = (True, '认证成功')

        result = desktop_gui._run_local_unlocked(
            self.host, 'login', self.account)

        self.assertTrue(result['ok'])
        self.assertEqual('认证成功', result['message'])
        netlogin_class.return_value.ensure_login.assert_called_once_with(
            user='20260001', pwd='plain-password', service_type='1')
        unprotect_secret.assert_called_once_with('dpapi-value')

    @mock.patch('desktop_gui.unprotect_secret', return_value='plain-password')
    @mock.patch('desktop_gui.Netlogin')
    def test_local_login_returns_automatic_switch_result(
            self, netlogin_class, unprotect_secret):
        netlogin_class.return_value.ensure_login.return_value = (
            True, '已自动下线旧会话；认证成功')

        result = desktop_gui._run_local_unlocked(
            self.host, 'login', self.account)

        self.assertTrue(result['ok'])
        self.assertIn('已自动下线旧会话', result['message'])
        netlogin_class.return_value.ensure_login.assert_called_once_with(
            user='20260001', pwd='plain-password', service_type='1')

    @mock.patch('desktop_gui.Netlogin')
    def test_local_logout_uses_local_session(self, netlogin_class):
        netlogin_class.return_value.logout.return_value = (True, '下线成功')

        result = desktop_gui._run_local_unlocked(self.host, 'logout')

        self.assertTrue(result['ok'])
        self.assertEqual('下线成功', result['message'])

    @mock.patch('desktop_gui._run_local_unlocked')
    def test_dispatcher_routes_local_profile_without_ssh(self, run_local):
        run_local.return_value = {'ok': True}

        result = desktop_gui.run_target(self.host, 'status')

        self.assertTrue(result['ok'])
        run_local.assert_called_once_with(self.host, 'status', None, 30)


if __name__ == '__main__':
    unittest.main()
