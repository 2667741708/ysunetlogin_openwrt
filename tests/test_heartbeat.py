import unittest

from heartbeat import HeartbeatEngine, assess_status


ACCOUNT = {
    'id': 'account-1',
    'name': '移动账号',
    'username': '20260001',
    'password': 'encrypted-for-gui',
    'service': '1',
}


def online_result(user='20260001', service='中国移动'):
    return {
        'ok': True,
        'internetOnline': True,
        'summary': {
            'online': True,
            'userId': user,
            'service': service,
            'message': 'ok',
        },
    }


def offline_result(internet=False):
    return {
        'ok': True,
        'internetOnline': internet,
        'summary': {'online': False, 'message': 'offline'},
    }


class StatusAssessmentTests(unittest.TestCase):
    def test_matching_account_and_service_is_healthy(self):
        result = assess_status(online_result(), ACCOUNT)
        self.assertTrue(result['healthy'])
        self.assertFalse(result['needs_repair'])

    def test_reachable_internet_with_unknown_portal_is_not_disrupted(self):
        result = assess_status(offline_result(internet=True), ACCOUNT)
        self.assertTrue(result['healthy'])
        self.assertIn('保持现状', result['reason'])

    def test_wrong_account_requires_logout_before_repair(self):
        result = assess_status(online_result(user='someone-else'), ACCOUNT)
        self.assertFalse(result['healthy'])
        self.assertTrue(result['needs_logout'])


class HeartbeatRepairTests(unittest.TestCase):
    def setUp(self):
        self.host = {
            'id': 'host-1',
            'name': '测试服务器',
            'target': 'test-host',
            'heartbeat_enabled': True,
            'heartbeat_interval_seconds': 60,
            'heartbeat_failure_threshold': 2,
        }
        self.events = []

    def test_two_failures_trigger_login_and_post_repair_verification(self):
        calls = []
        statuses = [offline_result(), offline_result(), online_result()]

        def executor(host, action, account):
            calls.append(action)
            if action == 'status':
                return statuses.pop(0)
            return {'ok': True, 'message': 'logged in'}

        engine = HeartbeatEngine(lambda: [], executor, self.events.append, sleep_fn=lambda _: None)
        engine._check_profile(self.host, ACCOUNT)
        self.assertEqual(['status'], calls)
        engine._check_profile(self.host, ACCOUNT)
        self.assertEqual(['status', 'status', 'login', 'status'], calls)
        self.assertEqual('repaired', self.events[-1]['level'])

    def test_wrong_account_logs_out_before_login(self):
        calls = []
        statuses = [online_result(user='wrong'), online_result()]
        host = dict(self.host, heartbeat_failure_threshold=1)

        def executor(current_host, action, account):
            calls.append(action)
            if action == 'status':
                return statuses.pop(0)
            return {'ok': True}

        engine = HeartbeatEngine(lambda: [], executor, self.events.append, sleep_fn=lambda _: None)
        engine._check_profile(host, ACCOUNT)
        self.assertEqual(['status', 'logout', 'login', 'status'], calls)
        self.assertEqual('repaired', self.events[-1]['level'])


if __name__ == '__main__':
    unittest.main()
