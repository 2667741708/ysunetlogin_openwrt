import contextlib
import io
from pathlib import Path
import runpy
import unittest
from unittest import mock

import netlogin as module
from netlogin import Netlogin as _Netlogin


class Netlogin(_Netlogin):
    def __init__(self):
        super().__init__(network_mode='system')


class ServiceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.client = Netlogin()

    def test_all_four_services_select_the_requested_wire_value(self):
        services = [dict(name=name, value='wire-' + code, order=code)
                    for code, name in self.client.services.items()]
        for code in self.client.services:
            with self.subTest(code=code):
                selected = self.client._choose_service(services, code)
                self.assertEqual('wire-' + code, self.client._service_value(selected))

    def test_missing_operator_never_falls_back_to_campus_or_first_entry(self):
        for services in ([{'value': '校园网'}], [{'value': '中国电信'}], [], None):
            self.assertIsNone(self.client._choose_service(services, '1'))

    def test_short_operator_names_are_supported(self):
        for code, name in [('1', '移动'), ('2', '联通'), ('3', '电信')]:
            entry = {'name': ' ' + name + ' ', 'value': 'server-specific-id'}
            self.assertIs(entry, self.client._choose_service([entry], code))

    def test_ambiguous_plans_are_rejected(self):
        self.assertIsNone(self.client._choose_service([
            {'name': '中国移动', 'value': 'plan-a'},
            {'name': '移动', 'value': 'plan-b'},
        ], '1'))

    def test_numeric_server_ids_are_not_assumed_to_be_cli_operator_codes(self):
        self.assertIsNone(self.client._choose_service([
            {'name': '校园网', 'value': '1'}], '1'))

    def test_invalid_input_is_rejected_before_network_or_logout(self):
        with mock.patch.object(self.client, 'query_machine_status') as probe:
            for user, password, service in [('', 'secret', '1'),
                                            ('student', '', '1'),
                                            ('student', 'secret', '4')]:
                self.assertFalse(self.client.ensure_login(user, password, service)[0])
            probe.assert_not_called()

    def test_existing_campus_session_is_not_operator_success(self):
        self.client.isLogined = True
        with mock.patch.object(self.client, 'current_status', return_value={
                'summary': {'online': True, 'userId': 'student', 'service': '校园网'}}):
            state, message = self.client.login('student', 'secret', '1')
        self.assertFalse(state)
        self.assertIn('实际服务：校园网', message)

    def test_invalid_positional_operator_does_not_probe_network(self):
        with mock.patch.object(self.client, 'tst_net') as probe:
            self.assertFalse(self.client.login('student', 'secret', '4')[0])
            probe.assert_not_called()

    def test_legacy_success_must_verify_actual_operator(self):
        for actual, expected in [('校园网', False), ('中国移动', True), ('', False)]:
            with self.subTest(actual=actual):
                self.client.isLogined = False
                with mock.patch.object(self.client, '_login_auth1', return_value=(None, '')), \
                        mock.patch.object(self.client, '_find_portal_query_string', return_value='wlanacname=test'), \
                        mock.patch.object(module, 'post'), \
                        mock.patch.object(self.client, '_parse_json', return_value={'result': 'success'}), \
                        mock.patch.object(self.client, '_get_online_user_info', return_value={
                            'result': 'success', 'userId': 'student', 'service': actual}):
                    state, _ = self.client.login('student', 'secret', '1')
                self.assertEqual(expected, state)

    def test_existing_legacy_session_can_be_verified(self):
        self.client.isLogined = True
        with mock.patch.object(self.client, 'current_status', return_value={}), \
                mock.patch.object(self.client, '_get_online_user_info', return_value={
                    'result': 'success', 'userId': 'student', 'service': '中国移动'}):
            self.assertTrue(self.client.login('student', 'secret', '1')[0])

    def test_internet_online_but_campus_offline_still_runs_login(self):
        self.client.isLogined = True
        status = {'internetOnline': True, 'summary': {'online': False},
                  'auth1Session': {'sessionId': 'campus'}}
        with mock.patch.object(self.client, 'current_status', return_value=status), \
                mock.patch.object(self.client, '_login_auth1', return_value=(True, '移动认证成功')) as login:
            self.assertTrue(self.client.login('student', 'secret', '1')[0])
        login.assert_called_once_with('student', 'secret', '1')

    def test_discovery_uses_auth1_before_public_internet_probes(self):
        response = mock.Mock()
        response.geturl.return_value = 'https://auth1.ysu.edu.cn/portal?sessionId=campus'
        with mock.patch.object(self.client, '_session_request', return_value=response) as request:
            self.assertEqual(response.geturl.return_value, self.client._find_auth1_portal_url({}))
        self.assertEqual(1, request.call_count)
        self.assertEqual('https://auth1.ysu.edu.cn/', request.call_args.args[1])


class Auth1LoginTests(unittest.TestCase):
    def run_login(self, services, actual='中国移动', node='serviceSelection',
                  actual_user='student', service_success=True, requested='1'):
        client = Netlogin()
        response = mock.Mock()
        response.geturl.return_value = 'https://auth1.ysu.edu.cn/portal?sessionId=test'
        response.read.return_value = b''
        response.code = 302
        response.headers = {'Location': '/auth-success'}

        def session_json(openers, path, *args, **kwargs):
            if path.endswith('getCurrentNode'):
                return {'data': {'currentNodePath': node}}
            if path.endswith('serviceSelection'):
                return {'data': services}
            if path.endswith('serviceLogin'):
                return {'data': {'authResult': 'success' if service_success else 'fail',
                                 'authMessage': 'operator denied'}}
            return {'data': {'portalOnlineUserInfo': {
                'result': 'success', 'userId': actual_user, 'service': actual}}}

        with contextlib.ExitStack() as stack:
            replacements = {
                '_new_cookie_openers': {},
                '_find_auth1_portal_url': 'https://auth1.ysu.edu.cn/portal',
                '_session_request': response,
                '_build_cas_url': ('https://auth1.ysu.edu.cn/cas', {'sessionId': 'test'}),
                '_parse_html_id': 'test-key',
                '_aes_encrypt_b64': 'encrypted',
            }
            for name, value in replacements.items():
                stack.enter_context(mock.patch.object(client, name, return_value=value))
            stack.enter_context(mock.patch.object(module.time, 'sleep'))
            requests = stack.enter_context(mock.patch.object(
                client, '_session_json', side_effect=session_json))
            result = client._login_auth1('student', 'secret', requested)
        submitted = [call.args[2] for call in requests.call_args_list
                     if call.args[1].endswith('serviceLogin')]
        return result, submitted

    def test_only_campus_offered_means_no_service_login_is_sent(self):
        (state, message), submitted = self.run_login([{'value': '校园网'}])
        self.assertFalse(state)
        self.assertIn('服务器返回：校园网', message)
        self.assertEqual([], submitted)

    def test_operator_wire_value_is_sent_and_actual_service_is_verified(self):
        (state, message), submitted = self.run_login([
            {'name': '移动', 'value': 'operator-mobile'}])
        self.assertTrue(state)
        self.assertIn('已核实服务：中国移动', message)
        self.assertEqual([{'sessionId': 'test', 'service': 'operator-mobile'}], submitted)

    def test_each_operator_is_submitted_without_selecting_campus(self):
        names = Netlogin().services
        services = [{'name': name, 'value': 'wire-' + code}
                    for code, name in names.items()]
        for code in ('1', '2', '3'):
            with self.subTest(operator=names[code]):
                (state, _), submitted = self.run_login(
                    services, actual=names[code], requested=code)
                self.assertTrue(state)
                self.assertEqual([{'sessionId': 'test', 'service': 'wire-' + code}], submitted)

    def test_each_missing_operator_stops_without_submitting_campus(self):
        for code in ('1', '2', '3'):
            with self.subTest(operator=code):
                (state, _), submitted = self.run_login(
                    [{'value': '校园网'}], actual='校园网', requested=code)
                self.assertFalse(state)
                self.assertEqual([], submitted)

    def test_success_response_with_campus_session_is_failure(self):
        (state, message), _ = self.run_login([{'value': '中国移动'}], actual='校园网')
        self.assertFalse(state)
        self.assertIn('实际服务：校园网', message)

    def test_auto_login_without_selection_still_verifies_operator(self):
        (state, message), submitted = self.run_login([], actual='校园网', node='success')
        self.assertFalse(state)
        self.assertIn('实际服务：校园网', message)
        self.assertEqual([], submitted)

    def test_missing_service_is_not_verified_success(self):
        (state, message), _ = self.run_login([{'value': '中国移动'}], actual='')
        self.assertFalse(state)
        self.assertIn('接口未返回', message)

    def test_wrong_account_is_not_verified_success(self):
        (state, _), _ = self.run_login([{'value': '中国移动'}], actual_user='other')
        self.assertFalse(state)

    def test_server_rejection_is_preserved(self):
        (state, message), _ = self.run_login([{'value': '中国移动'}], service_success=False)
        self.assertFalse(state)
        self.assertEqual('operator denied', message)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, args):
        # run_path defines its own class, so intercept __init__ to install mocks.
        client = mock.Mock()
        client.login.return_value = (False, '指定运营商不可用')
        original_build_class = __build_class__

        def build_class(func, name, *bases, **kwargs):
            cls = original_build_class(func, name, *bases, **kwargs)
            if name == 'Netlogin':
                return mock.Mock(return_value=client)
            return cls

        with mock.patch('builtins.__build_class__', side_effect=build_class), \
                mock.patch.object(module.sys, 'argv', ['netlogin.py'] + args), \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as stopped:
            runpy.run_path(str(Path(module.__file__)), run_name='__main__')
        return client, stopped.exception.code

    def test_status_aliases_only_query_status(self):
        for option in ['status', 'current-status', '--status', '--current-status']:
            with self.subTest(option=option):
                client, code = self.run_cli([option])
                self.assertEqual(0, code)
                client.current_status.assert_called_once_with()
                client.ensure_login.assert_not_called()
                client.login.assert_not_called()

    def test_positional_login_passes_operator_and_failure_exit_code(self):
        client, code = self.run_cli(['student', 'secret', '1'])
        self.assertEqual(1, code)
        client.login.assert_called_once_with(
            user='student', pwd='secret', type='1')

    def test_help_and_unknown_flags_do_not_touch_network(self):
        for args, expected in [(['--help'], 0), (['--unknown'], 2), ([], 0)]:
            client, code = self.run_cli(args)
            self.assertEqual(expected, code)
            client.login.assert_not_called()
            client.ensure_login.assert_not_called()


if __name__ == '__main__':
    unittest.main()
