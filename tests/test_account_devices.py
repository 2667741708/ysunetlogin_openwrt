import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from netlogin import Netlogin


def current_status(user='20260001', count=2):
    devices = [
        {'onlineUserUuid': 'device-%s' % index, 'userIpv4': '10.0.0.%s' % index}
        for index in range(1, count + 1)
    ]
    return {
        'auth1Session': {'sessionId': 'session-current'},
        'online': {'data': {'portalOnlineUserInfo': {
            'result': 'success', 'userId': user, 'userName': user}}},
        'devices': {'data': {'onlineDevices': devices, 'offlineDevices': []}},
        'offlineAccount': {},
        'errors': [],
        'summary': {'online': True, 'userId': user, 'userName': user},
    }


class AccountDeviceStatusTests(unittest.TestCase):
    def test_reuses_matching_current_session_and_returns_all_devices(self):
        netlogin = Netlogin()
        with patch.object(netlogin, 'current_status', return_value=current_status()), \
                patch.object(netlogin, '_cas_login_only') as cas_login:
            result = netlogin.account_status('20260001', 'secret')

        summary = result['summary']
        self.assertTrue(summary['casLoginOk'])
        self.assertEqual('current-session', summary['querySource'])
        self.assertEqual(2, summary['onlineDeviceCount'])
        cas_login.assert_not_called()

    def test_account_realm_suffix_matches_current_session(self):
        netlogin = Netlogin()
        with patch.object(netlogin, 'current_status',
                          return_value=current_status('20260001@cmcc')), \
                patch.object(netlogin, '_cas_login_only') as cas_login:
            result = netlogin.account_status('20260001', 'secret')

        self.assertEqual('current-session', result['summary']['querySource'])
        cas_login.assert_not_called()

    def test_http_401_becomes_actionable_result_instead_of_exception(self):
        netlogin = Netlogin()
        error = HTTPError('https://auth1.example/login', 401, None, {}, None)
        with patch.object(netlogin, 'current_status',
                          return_value=current_status('different-user')), \
                patch.object(netlogin, '_cas_login_only', side_effect=error):
            result = netlogin.account_status('20260001', 'secret')

        self.assertFalse(result['summary']['casLoginOk'])
        self.assertIn('HTTP 401', result['errors'][0])
        self.assertNotIn('None', result['errors'][0])

    def test_kick_reuses_matching_current_session(self):
        netlogin = Netlogin()
        status = netlogin._account_status_from_current(current_status(), '20260001')
        with patch.object(netlogin, 'account_status', return_value=status), \
                patch.object(netlogin, '_cas_login_only') as cas_login, \
                patch.object(netlogin, '_session_json', return_value={
                    'code': 200, 'message': 'OK'
                }) as request:
            result = netlogin.account_offline_devices(
                '20260001', 'secret', ['device-1'])

        self.assertTrue(result['summary']['changed'])
        self.assertEqual(1, result['summary']['targetCount'])
        cas_login.assert_not_called()
        self.assertEqual('session-current', request.call_args.args[2]['sessionId'])


if __name__ == '__main__':
    unittest.main()
