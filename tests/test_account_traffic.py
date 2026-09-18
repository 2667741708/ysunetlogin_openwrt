import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from netlogin import Netlogin
from self_service import SelfService, flow_gb, traffic_summary, main, print_result


class TrafficTests(unittest.TestCase):
    def sample(self, total='60 GB 0.00 MB ', left='53 GB 862.62 MB '):
        return {'policyInfo': {'packageName': '测试60G'}, 'periodInfo': {'dateRangeDesc': '本月'}}, {
            'availableFreeItems': [{'chargeItems': [{'name': '国内下行流量',
             'totalFreeValueDesc': total, 'leftFreeValueDesc': left, 'usingPercentages': 10.3}]}]}

    def test_mixed_units_keep_mb_precision_and_original(self):
        row = traffic_summary(*self.sample())['items'][0]
        self.assertAlmostEqual(53 + 862.62 / 1024, row['remainingGBApprox'])
        self.assertAlmostEqual(60 - row['remainingGBApprox'], row['usedGBApprox'])
        self.assertEqual('53 GB 862.62 MB ', row['remainingText'])

    def test_zero_unknown_unlimited_and_unexpected_text_are_distinct(self):
        self.assertEqual(0, flow_gb('0 GB 0.00 MB'))
        for value in [None, '', '--', '不限量', '60 GB 已过期', '-1 GB', 'NaN GB']:
            self.assertIsNone(flow_gb(value))
        self.assertEqual(1, flow_gb('1024 MB'))

    def test_no_hardcoded_60_gb(self):
        row = traffic_summary(*self.sample('100 GB', '98 GB'))['items'][0]
        self.assertEqual(100, row['totalGBApprox'])
        self.assertEqual(2, row['usedGBApprox'])

    def test_absent_data_never_claims_zero(self):
        result = traffic_summary({}, {})
        self.assertFalse(result['remainingKnown'])
        self.assertEqual([], result['items'])
        with self.assertRaises(ValueError):
            traffic_summary({}, None)

    def test_multiple_area_quotas_are_not_added(self):
        overview, flow = self.sample()
        flow['netserviceDetailDescs'] = [{'serviceName': '测试区域', 'availableFreeItems': [
            {'chargeItems': [{'name': '另一额度', 'totalFreeValueDesc': '10 GB', 'leftFreeValueDesc': '8 GB'}]}]}]
        result = traffic_summary(overview, flow)
        self.assertEqual(2, len(result['items']))
        self.assertEqual('测试区域', result['items'][1]['scope'])
        self.assertNotIn('remainingGBApprox', result)
        self.assertEqual('not-returned', result['operatorTraffic']['availability'])

    def test_get_endpoints_and_different_current_account(self):
        client = Netlogin(network_mode='system')
        client.current_status = Mock(return_value={'summary': {'online': True, 'userId': 'another', 'service': '中国移动'}})
        api = SelfService.__new__(SelfService)
        api.client, api.user = client, 'student'
        api.request = Mock(side_effect=self.sample())
        result = api.traffic()
        self.assertFalse(result['currentSession']['matchesQueriedAccount'])
        self.assertEqual([('package/overview',), ('package/flow',)], [c.args for c in api.request.call_args_list])
        self.assertTrue(all(c.kwargs == {'method': 'GET'} for c in api.request.call_args_list))

    def test_status_failure_does_not_hide_quota(self):
        api = SelfService.__new__(SelfService)
        api.client, api.user = Mock(), 'student'
        api.client.current_status.side_effect = OSError('offline')
        api.request = Mock(side_effect=self.sample())
        result = api.traffic()
        self.assertTrue(result['ok'])
        self.assertTrue(result['remainingKnown'])
        self.assertIsNone(result['currentSession'])


class AccountConsoleTests(unittest.TestCase):
    def setUp(self):
        self.api = SelfService.__new__(SelfService)
        self.api.client = Netlogin(network_mode='system')
        self.api.client.ensure_login = Mock(return_value=(True, 'verified'))
        self.api.user = 'student'
        self.api.traffic = Mock(return_value={'ok': True})

    def test_login_failure_never_retries_campus_or_fetches_quota(self):
        self.api.client.ensure_login.return_value = (False, '移动核验失败')
        with patch('self_service.SelfService') as factory:
            result = self.api.login_and_traffic('test-only', '1')
        self.assertFalse(result['ok'])
        self.api.client.ensure_login.assert_called_once_with('student', 'test-only', '1')
        factory.assert_not_called()

    def test_quota_failure_does_not_turn_successful_login_into_failure(self):
        with patch('self_service.SelfService', side_effect=OSError('quota unavailable')):
            result = self.api.login_and_traffic('test-only', '0')
        self.assertTrue(result['ok'])
        self.assertTrue(result['loginVerified'])
        self.assertIsNone(result['traffic'])
        self.assertTrue(result['warnings'])

    def test_invalid_service_does_not_login(self):
        for choice in ['', '4', None]:
            with self.assertRaises(ValueError):
                self.api.login_and_traffic('test-only', choice)
        self.api.client.ensure_login.assert_not_called()

    def test_menu_exit_and_bad_input_never_default_to_campus(self):
        with patch('builtins.input', side_effect=['', 'bad', 'q']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, self.api.menu('test-only'))
        self.api.client.ensure_login.assert_not_called()

    def test_menu_selects_requested_service(self):
        self.api.login_and_traffic = Mock(return_value={'ok': True})
        with patch('builtins.input', side_effect=['3', 'q']), contextlib.redirect_stdout(io.StringIO()):
            self.api.menu('test-only')
        self.api.login_and_traffic.assert_called_once_with('test-only', '3')

    def test_menu_prompts_account_and_password_once(self):
        with patch('self_service.SelfService', return_value=self.api) as factory, patch(
                'builtins.input', side_effect=['student', 'q']), patch(
                'self_service.getpass.getpass', return_value='test-only') as secret, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(self.api.client, ['account-menu']))
        self.assertEqual('student', factory.call_args.args[1])
        secret.assert_called_once()
        self.api.client.ensure_login.assert_not_called()

    def test_login_command_requires_explicit_service_before_auth(self):
        with patch('self_service.SelfService') as factory, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                main(self.api.client, ['account-login', 'student'])
        self.assertEqual(2, exc.exception.code)
        factory.assert_not_called()

    def test_login_failure_exit_code(self):
        self.api.login_and_traffic = Mock(return_value={'ok': False})
        with patch('self_service.SelfService', return_value=self.api), contextlib.redirect_stdout(io.StringIO()):
            code = main(self.api.client, ['account-login', 'student', 'test-only', '--service', '1', '--json'])
        self.assertEqual(1, code)


if __name__ == '__main__':
    unittest.main()
