import unittest
from unittest import mock

import desktop_gui
from netlogin import Netlogin


class DeviceDataTests(unittest.TestCase):
    def setUp(self):
        self.client = Netlogin(network_mode='system')
        self.client.network_report = {'selected': {'source_ip': '10.0.0.1'}}

    def status(self, user='student@cmcc', target='student'):
        return {'account': target, 'online': {'data': {'portalOnlineUserInfo': {
            'result': 'success', 'userId': user, 'userIp': '10.0.0.1', 'service': '中国移动'}}},
            'devices': {'data': {'onlineDevices': [
                {'userIpv4': '10.0.0.1', 'deviceName': '我的设备'},
                {'userIpv4': '10.0.0.2', 'deviceName': '别的设备'},
            ]}}}

    def test_current_device_gets_verified_hostname_and_actual_service(self):
        with mock.patch('netlogin.socket.gethostname', return_value='real-workstation'):
            result = self.client._account_status_summary(self.status())
        current, other = result['devices']
        self.assertEqual('real-workstation', current['hostname'])
        self.assertEqual('local-machine', current['hostnameSource'])
        self.assertEqual('中国移动', current['service'])
        self.assertEqual('current-session', current['serviceSource'])
        self.assertTrue(current['currentDevice'])
        self.assertEqual('', other['hostname'])
        self.assertEqual('', other['service'])
        self.assertEqual(1, result['hostnameUnknownCount'])
        self.assertEqual(1, result['serviceUnknownCount'])

    def test_other_account_does_not_inherit_query_machine_identity(self):
        result = self.client._account_status_summary(self.status(target='other-account'))
        self.assertTrue(all(not item['hostname'] and not item['service'] for item in result['devices']))

    def test_ip_mismatch_never_assigns_local_hostname(self):
        self.client.network_report = {'selected': {'source_ip': '10.9.9.9'}}
        result = self.client._account_status_summary(self.status())
        self.assertEqual('', result['devices'][0]['hostname'])

    def test_server_hostname_and_service_are_preserved(self):
        status = self.status()
        status['devices']['data']['onlineDevices'][0].update(
            hostName='server-host', serviceName='中国联通')
        result = self.client._account_status_summary(status)
        self.assertEqual('server-host', result['devices'][0]['hostname'])
        self.assertEqual('中国联通', result['devices'][0]['service'])
        self.assertEqual('findDevice', result['devices'][0]['serviceSource'])

    def test_device_display_name_is_not_mistaken_for_hostname(self):
        result = self.client._brief_devices([{'deviceName': '我的设备'}])[0]
        self.assertEqual('', result['hostname'])
        self.assertEqual('我的设备', result['deviceName'])

    def test_hostname_failure_does_not_block_device_query(self):
        with mock.patch('netlogin.socket.gethostname', side_effect=OSError('unavailable')):
            result = self.client._account_status_summary(self.status())
        self.assertEqual(2, result['onlineDeviceCount'])


class DevicePresentationTests(unittest.TestCase):
    def test_long_values_are_preserved_in_columns_and_details(self):
        long_name = 'long-host-' * 40
        item = {'ip': '2001:db8:1234:5678:abcd:ef01:2345:6789', 'hostname': long_name,
                'hostnameSource': 'findDevice', 'service': '中国移动', 'serviceSource': 'current-session'}
        values = desktop_gui.DesktopApp.account_device_values(item)
        details = desktop_gui.DesktopApp.account_device_details(item)
        self.assertEqual(long_name, values[1])
        self.assertIn(long_name, details)
        self.assertIn(item['ip'], details)
        self.assertIn('当前出口认证会话', details)

    def test_missing_operator_is_explicit_in_both_views(self):
        values = desktop_gui.DesktopApp.account_device_values({})
        details = desktop_gui.DesktopApp.account_device_details({})
        self.assertIn('未知', values[2])
        self.assertIn('实际运营商 / 服务：未知', details)


if __name__ == '__main__':
    unittest.main()
