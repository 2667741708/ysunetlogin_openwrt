#!/usr/bin/env python3
"""Privacy-safe UI fixture used to capture the documented workflow."""
from __future__ import annotations

import desktop_gui


DEMO_ACCOUNT = {
    'id': 'demo-account',
    'name': '本机移动账号（演示）',
    'username': '2026****',
    'password': 'demo-not-a-real-secret',
    'service': '1',
}


class DemoConfigStore:
    def __init__(self):
        local = desktop_gui.default_local_host()
        local['account_id'] = DEMO_ACCOUNT['id']
        self.data = {
            'version': desktop_gui.CONFIG_VERSION,
            'accounts': [dict(DEMO_ACCOUNT)],
            'hosts': [
                local,
                {
                    'id': 'demo-remote',
                    'name': '远端服务器（演示）',
                    'connection_type': 'ssh',
                    'target': 'demo-server',
                    'expected_hostname': 'demo-host',
                    'script': '',
                    'account_id': DEMO_ACCOUNT['id'],
                    'heartbeat_enabled': False,
                    'heartbeat_interval_seconds': 60,
                    'heartbeat_failure_threshold': 2,
                },
            ],
        }

    def save(self):
        return None

    def account(self, account_id):
        return next((item for item in self.data['accounts'] if item['id'] == account_id), None)

    def host(self, host_id):
        return next((item for item in self.data['hosts'] if item['id'] == host_id), None)


def demo_run_target(host, action, account=None, timeout=30):
    if action == 'status':
        return {
            'ok': True,
            'internetOnline': True,
            'summary': {
                'state': 'online',
                'online': True,
                'userId': '2026****',
                'service': '中国移动',
                'userIp': '10.0.*.*',
                'message': '本机网络状态正常（演示结果）',
            },
        }
    if action == 'login':
        return {'ok': True, 'message': '已使用指定账号连接本机（演示结果）'}
    if action == 'logout':
        return {'ok': True, 'message': '本机会话已下线（演示结果）'}
    return {
        'ok': True,
        'message': '本机网络功能可用',
        'hostname': 'DEMO-PC',
        'target': '本机 Windows',
    }


def main():
    desktop_gui.ConfigStore = DemoConfigStore
    desktop_gui.DesktopApp.sync_ssh_hosts = lambda self: None
    desktop_gui.run_target = demo_run_target
    desktop_gui.run_remote = demo_run_target
    desktop_gui.startup_enabled = lambda: False
    desktop_gui.seed_known_hosts = lambda store: None
    desktop_gui.main()


if __name__ == '__main__':
    main()
