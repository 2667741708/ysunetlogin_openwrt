from types import SimpleNamespace
from unittest import mock

import desktop_gui as gui


def test_successful_desktop_login_binds_account_and_enables_heartbeat():
    host = {'id': 'local-windows', 'name': '本机 Windows', 'connection_type': 'local',
            'heartbeat_enabled': False, 'account_id': ''}
    account = {'id': 'account-new', 'name': 'selected', 'service': '1'}
    app = SimpleNamespace(busy=False, set_busy=mock.Mock(), set_output=mock.Mock(),
        store=mock.Mock(), load_heartbeat_form=mock.Mock(), heartbeat_engine=mock.Mock(),
        after=lambda delay, callback: callback())
    def immediate_thread(target, daemon):
        return SimpleNamespace(start=target)
    with mock.patch.object(gui, 'run_target', return_value={'ok': True}), \
            mock.patch.object(gui.threading, 'Thread', side_effect=immediate_thread):
        gui.DesktopApp.run_async(app, 'login', host, account)
    assert host['heartbeat_enabled'] is True
    assert host['account_id'] == account['id']
    app.store.save.assert_called_once()
    app.heartbeat_engine.check_now.assert_called_once_with(host['id'])


def test_manual_logout_disables_desktop_heartbeat_first():
    host = {'id': 'local-windows', 'name': '本机 Windows', 'heartbeat_enabled': True}
    app = SimpleNamespace(host_from_operation=lambda: host, store=mock.Mock(),
        heartbeat_engine=mock.Mock(), load_heartbeat_form=mock.Mock(), run_async=mock.Mock())
    with mock.patch.object(gui.messagebox, 'askyesno', return_value=True):
        gui.DesktopApp.logout_selected(app)
    assert host['heartbeat_enabled'] is False
    app.store.save.assert_called_once()
    app.run_async.assert_called_once_with('logout', host)


def test_cancelled_queued_heartbeat_cannot_reconnect_after_logout():
    host = {'id': 'local-windows', 'connection_type': 'local'}
    with mock.patch.object(gui, '_run_local_unlocked') as execute:
        result = gui.run_target(host, 'login', {}, is_current=lambda: False)
    assert result['cancelled']
    execute.assert_not_called()
