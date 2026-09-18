import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from netlogin import Netlogin
from self_service import SelfService, mac_address, main


class SelfServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = Netlogin(network_mode='system')
        self.client.network_report = {'selected': {'source_ip': '10.0.0.1'}}
        self.client.current_status = Mock(return_value={'summary': {
            'online': True, 'userId': 'student', 'userIp': '10.0.0.1', 'service': '中国移动'}})
        self.api = SelfService.__new__(SelfService)
        self.api.client, self.api.user = self.client, 'student'
        self.data = {'onlineDevices': [
            {'userIpv4': '10.0.0.1', 'userMac': '02-00-00-00-00-01', 'onlineUserUuid': 'one'},
            {'userIpv4': '10.0.0.2', 'userMac': '02-00-00-00-00-02', 'onlineUserUuid': 'two',
             'defaultServiceOperaporsConfigUuid': 'cmcc', 'bindNoSense': True, 'noSenseUuid': 'mab2'},
        ], 'offlineDevices': [{'userMac': '02-00-00-00-00-03', 'bindNoSense': True, 'noSenseUuid': 'mab3'}]}
        self.api.request = Mock(side_effect=lambda path, *args: self.data if path == 'devices' else
                                [{'value': 'cmcc', 'label': '中国移动'}] if path == 'devices/defaultServicesList' else None)

    def test_states_and_unknown_service_do_not_inherit_defaults(self):
        result = self.api.devices('中国移动')
        one, two, three = result['devices']
        self.assertEqual(['match', 'unknown', 'offline'], [d['serviceMatch'] for d in result['devices']])
        self.assertTrue(two['online'])
        self.assertFalse(three['online'])
        self.assertEqual('', two['service'])
        self.assertEqual('中国移动', two['defaultService'])
        self.assertEqual('current-session', one['serviceSource'])

    def test_other_account_does_not_get_current_service(self):
        self.api.user = 'other'
        self.assertFalse(any(d['serviceKnown'] for d in self.api.devices()['devices']))

    def test_device_query_failure_is_not_empty_offline_result(self):
        self.api.request.return_value = {}
        self.api.request.side_effect = None
        with self.assertRaises(ValueError):
            self.api.devices()

    def test_history_retains_service_but_does_not_claim_current_online(self):
        self.api.request.return_value = {'total': 2, 'results': [
            {'userIpv4': '10.0.0.1', 'serviceSuffix': '校园网', 'loginTime': 1000, 'logoutTime': 2000},
            {'userIpv4': '10.0.0.2', 'serviceSuffix': '中国移动', 'loginTime': 3000}]}
        self.api.request.side_effect = None
        result = self.api.history(ip='10.0.0.1')
        self.assertEqual('completed', result['records'][0]['recordState'])
        self.assertNotIn('online', result['records'][0])
        self.assertEqual(2, result['total'])
        self.assertEqual('current-page', result['ipFilterScope'])

    def test_invalid_history_range_does_not_call_api(self):
        for kwargs in [{'page': 0}, {'size': 201}, {'start': 10, 'end': 1}, {'start': 0, 'end': 93 * 86400000}]:
            with self.assertRaises(ValueError):
                self.api.history(**kwargs)
        self.api.request.assert_not_called()

    def test_offline_dry_run_and_stale_uuid(self):
        result = self.api.action('device-offline', uuids=['two'], dry_run=True)
        self.assertFalse(result['changed'])
        self.assertEqual({'onlineUserUuids': ['two']}, result['request']['data'])
        self.api.request.assert_called_once_with('devices')
        with self.assertRaises(ValueError):
            self.api.action('device-offline', uuids=['not-owned'])
        self.assertTrue(all(call.args == ('devices',) for call in self.api.request.call_args_list))

    def test_batch_offline_requires_explicit_all(self):
        with self.assertRaises(ValueError):
            self.api.action('device-offline')
        self.api.request.assert_not_called()
        result = self.api.action('device-offline', all_devices=True, dry_run=True)
        self.assertEqual(['one', 'two'], result['request']['data']['onlineUserUuids'])

    def test_unbind_uses_nosense_ids_including_offline_registrations(self):
        result = self.api.action('nosense-disable', all_devices=True, dry_run=True)
        self.assertEqual({'noSenseUuids': ['mab2', 'mab3']}, result['request']['data'])

    def test_enable_checks_owned_online_mac(self):
        result = self.api.action('nosense-enable', mac='02:00:00:00:00:01', dry_run=True)
        self.assertEqual({'userMac': '02-00-00-00-00-01'}, result['request']['data'])
        with self.assertRaises(ValueError):
            self.api.action('nosense-enable', mac='02:00:00:00:00:03', dry_run=True)

    def test_register_expiry_and_dry_run(self):
        self.api.request.side_effect = None
        self.api.request.return_value = {'unit': 'day', 'availableDuration': 7}
        result = self.api.action('nosense-register', mac='02:00:00:00:00:04', expire=3, dry_run=True)
        self.assertEqual({'userMac': '02-00-00-00-00-04', 'expireTime': 3, 'expireUnit': 'day'}, result['request']['data'])
        self.api.request.assert_called_once_with('package/duration')
        with self.assertRaises(ValueError):
            self.api.action('nosense-register', mac='02:00:00:00:00:04', expire=8, dry_run=True)

    def test_mutation_submits_once_and_propagates_error(self):
        self.api.request.side_effect = [self.data, ValueError('server rejected')]
        with self.assertRaises(ValueError):
            self.api.action('device-offline', uuids=['one'])
        self.assertEqual(2, self.api.request.call_count)
        self.assertEqual(('devices/kick-offline/batch', {'onlineUserUuids': ['one']}), self.api.request.call_args.args)

    def test_invalid_mac(self):
        for value in ['', 'FF:FF:FF:FF:FF:FF', '01:00:00:00:00:01', '00:00:00:00:00:00']:
            with self.assertRaises(ValueError):
                mac_address(value)

    def test_cli_stdin_dispatch_and_json(self):
        with patch('self_service.SelfService', return_value=self.api), patch('sys.stdin', io.StringIO(
                '{"username":"student","password":"test-only"}')), contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(self.client, ['account-devices', '--stdin', '--service', '1', '--json'])
        self.assertEqual(0, code)
        self.assertEqual('unknown', json.loads(output.getvalue())['devices'][1]['serviceMatch'])


class SelfLoginTests(unittest.TestCase):
    def test_same_host_http_redirect_is_upgraded_locally(self):
        client = Mock()
        client.header = {}
        client._cas_login_only.return_value = (True, {}, {})
        with patch('self_service.response_code', side_effect=[302, 200]), patch(
                'self_service.get_header', return_value='http://auth1.ysu.edu.cn/self/my-devices'):
            SelfService(client, 'student', 'test-only')
        self.assertTrue(all(call.args[1].startswith('https://') for call in client._session_request.call_args_list))

    def test_oauth_redirects_and_accept_header(self):
        client = Mock()
        client.header = {}
        client._cas_login_only.return_value = (True, {}, {})
        first, final = Mock(), Mock()
        client._session_request.side_effect = [first, final]
        with patch('self_service.response_code', side_effect=[302, 200]), patch(
                'self_service.get_header', return_value='/self/my-devices'):
            SelfService(client, 'student', 'test-only')
        self.assertIn('text/html', client._session_request.call_args.kwargs['headers']['Accept'])
        self.assertFalse(client._session_request.call_args.kwargs['allow_redirects'])

    def test_foreign_redirect_is_not_followed(self):
        client = Mock()
        client.header = {}
        client._cas_login_only.return_value = (True, {}, {})
        with patch('self_service.response_code', return_value=302), patch(
                'self_service.get_header', return_value='https://example.com/steal'):
            with self.assertRaises(ValueError):
                SelfService(client, 'student', 'test-only')
        self.assertEqual(1, client._session_request.call_count)


if __name__ == '__main__':
    unittest.main()
