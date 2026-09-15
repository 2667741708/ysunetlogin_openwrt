import contextlib
import http.server
import threading
import unittest
from unittest import mock
import urllib.request

import netlogin as netlogin_module
from netlogin import Netlogin as _Netlogin


class Netlogin(_Netlogin):
    def __init__(self):
        super().__init__(network_mode='system')


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

    def test_real_get_post_and_cas_requests_ignore_unreachable_proxy(self):
        received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.command, self.path, b''))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'ok')

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                received.append((self.command, self.path, body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'ok')

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'http://127.0.0.1:%s' % server.server_port
        try:
            # If urllib installs the configured proxy by accident, requests fail.
            with mock.patch.object(urllib.request, 'getproxies', return_value={
                    'http': 'http://127.0.0.1:1', 'https': 'http://127.0.0.1:1'}), \
                    mock.patch.object(urllib.request, 'proxy_bypass', return_value=False):
                client = Netlogin()
                openers = client._new_cookie_openers()
                responses = [
                    netlogin_module.get(url + '/probe', timeout=2),
                    netlogin_module.get(url + '/no-redirect', timeout=2, allow_redirects=False),
                    netlogin_module.post(url + '/legacy', data={'service': '中国联通'}, timeout=2),
                    client._session_request(openers, url + '/cas',
                                            data={'username': 'test-student'},
                                            allow_redirects=False, timeout=2),
                    client._session_request(openers, url + '/serviceLogin',
                                            json_data={'service': '中国电信'}, timeout=2),
                ]
                for response in responses:
                    with contextlib.closing(response):
                        self.assertEqual(b'ok', response.read())
            self.assertEqual(['/probe', '/no-redirect', '/legacy', '/cas', '/serviceLogin'],
                             [path for _, path, _ in received])
            self.assertEqual(['GET', 'GET', 'POST', 'POST', 'POST'],
                             [method for method, _, _ in received])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


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
