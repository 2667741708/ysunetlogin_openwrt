import unittest
from unittest import mock
import urllib.request

import netlogin as netlogin_module
from netlogin import Netlogin


class DirectNetworkRoutingTests(unittest.TestCase):
    def test_plain_get_explicitly_disables_system_proxy(self):
        opener = mock.Mock()
        opener.open.return_value = object()
        with mock.patch.object(
                urllib.request, 'build_opener', return_value=opener) as build:
            result = netlogin_module.get('http://example.test/', timeout=1)

        self.assertIs(result, opener.open.return_value)
        proxy_handler = build.call_args.args[0]
        self.assertIsInstance(proxy_handler, urllib.request.ProxyHandler)
        self.assertEqual({}, proxy_handler.proxies)

    def test_cookie_sessions_explicitly_disable_system_proxy(self):
        netlogin = Netlogin()
        with mock.patch.object(urllib.request, 'build_opener') as build:
            netlogin._new_cookie_openers()

        self.assertEqual(2, build.call_count)
        for call in build.call_args_list:
            proxy_handlers = [
                item for item in call.args
                if isinstance(item, urllib.request.ProxyHandler)]
            self.assertEqual(1, len(proxy_handlers))
            self.assertEqual({}, proxy_handlers[0].proxies)


class ExplicitLoginTests(unittest.TestCase):
    def test_explicit_login_automatically_replaces_wrong_session(self):
        netlogin = Netlogin()
        current = {
            'summary': {
                'online': True,
                'userId': 'someone-else',
                'service': '校园网',
            },
        }
        with mock.patch.object(netlogin, 'query_machine_status', return_value={
                'ok': True}), \
                mock.patch.object(netlogin, 'current_status', return_value=current), \
                mock.patch.object(netlogin, 'logout', return_value=(True, '下线成功')) as logout, \
                mock.patch.object(netlogin, 'login', return_value=(True, '认证成功')) as login:
            state, message = netlogin.ensure_login('20260001', 'secret', '1')

        self.assertTrue(state)
        self.assertIn('已自动下线旧会话', message)
        logout.assert_called_once_with()
        login.assert_called_once_with(
            user='20260001', pwd='secret', type='1', code='')

    def test_explicit_login_does_not_accept_unidentified_internet_as_success(self):
        netlogin = Netlogin()
        current = {
            'internetOnline': True,
            'summary': {'online': False, 'message': '账号无法识别'},
        }
        with mock.patch.object(netlogin, 'query_machine_status', return_value={
                'ok': True}), \
                mock.patch.object(netlogin, 'current_status', return_value=current), \
                mock.patch.object(netlogin, 'login', return_value=(True, '认证成功')) as login:
            state, message = netlogin.ensure_login('20260001', 'secret', '1')

        self.assertTrue(state)
        self.assertEqual('认证成功', message)
        login.assert_called_once_with(
            user='20260001', pwd='secret', type='1', code='')

    def test_explicit_login_stops_without_campus_network(self):
        netlogin = Netlogin()
        with mock.patch.object(netlogin, 'query_machine_status', return_value={
                'ok': False,
                'message': '需要先实现访问校园局域网',
        }), mock.patch.object(netlogin, 'current_status') as current:
            state, message = netlogin.ensure_login('20260001', 'secret', '1')

        self.assertFalse(state)
        self.assertIn('需要先实现访问校园局域网', message)
        current.assert_not_called()


if __name__ == '__main__':
    unittest.main()
