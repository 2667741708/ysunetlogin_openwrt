#!/usr/bin/env python3
"""Account-aware network self-healing heartbeat engine."""
from __future__ import annotations

import threading
import time


SERVICES = {'0': '校园网', '1': '中国移动', '2': '中国联通', '3': '中国电信'}
DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 15
MAX_INTERVAL_SECONDS = 3600
DEFAULT_FAILURE_THRESHOLD = 2
MAX_FAILURE_THRESHOLD = 5
MAX_BACKOFF_SECONDS = 900


def _clean(value):
    return str(value or '').strip().lower()


def _service_matches(actual, expected):
    actual_value = _clean(actual)
    expected_value = _clean(expected)
    expected_label = _clean(SERVICES.get(str(expected), expected))
    return not actual_value or actual_value in {expected_value, expected_label}


def assess_status(result, account):
    """Classify a current-status result without changing network state."""
    result = result if isinstance(result, dict) else {}
    summary = result.get('summary') or {}
    expected_user = _clean(account.get('username'))
    actual_user = _clean(summary.get('userId') or summary.get('userName'))
    expected_service = str(account.get('service', ''))
    actual_service = summary.get('service')

    if summary.get('online'):
        if actual_user and expected_user and actual_user != expected_user:
            return {
                'healthy': False,
                'needs_repair': True,
                'needs_logout': True,
                'reason': '当前在线账号与心跳账号不一致',
            }
        if actual_service and not _service_matches(actual_service, expected_service):
            return {
                'healthy': False,
                'needs_repair': True,
                'needs_logout': True,
                'reason': '当前运营商与心跳账号配置不一致',
            }
        return {
            'healthy': True,
            'needs_repair': False,
            'needs_logout': False,
            'reason': '账号和网络状态正常',
        }

    if result.get('internetOnline') is True:
        return {
            'healthy': True,
            'needs_repair': False,
            'needs_logout': False,
            'reason': '外网可达，认证账号暂时无法识别，保持现状',
        }

    return {
        'healthy': False,
        'needs_repair': True,
        'needs_logout': False,
        'reason': summary.get('message') or '检测到校园网离线',
    }


class HeartbeatEngine:
    """Schedule independent, thresholded repair checks for enabled hosts."""

    def __init__(self, profiles_provider, executor, callback=None, sleep_fn=time.sleep):
        self.profiles_provider = profiles_provider
        self.executor = executor
        self.callback = callback or (lambda event: None)
        self.sleep_fn = sleep_fn
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.states = {}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._loop, name='ysu-netlogin-heartbeat', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()

    def wake(self):
        self.wake_event.set()

    def check_now(self, host_id):
        with self.lock:
            state = self.states.setdefault(host_id, self._new_state())
            state['next_due'] = 0.0
        self.wake()

    @staticmethod
    def _new_state():
        return {
            'failures': 0,
            'repair_attempts': 0,
            'last_repair': 0.0,
            'next_due': 0.0,
            'active': False,
        }

    def _emit(self, level, host, message, **details):
        event = {
            'time': time.time(),
            'level': level,
            'host_id': host.get('id', ''),
            'host_name': host.get('name') or host.get('target') or '未知主机',
            'message': message,
        }
        event.update(details)
        try:
            self.callback(event)
        except Exception:
            pass

    def _profiles(self):
        try:
            return list(self.profiles_provider())
        except Exception as exc:
            self._emit('error', {}, '无法读取心跳配置：%s' % exc)
            return []

    def _loop(self):
        while not self.stop_event.is_set():
            now = time.monotonic()
            profiles = self._profiles()
            valid_ids = set()
            for host, account in profiles:
                host_id = host.get('id') or host.get('target')
                if not host_id:
                    continue
                valid_ids.add(host_id)
                with self.lock:
                    state = self.states.setdefault(host_id, self._new_state())
                    due = not state['active'] and now >= state['next_due']
                    if due:
                        state['active'] = True
                if due:
                    threading.Thread(
                        target=self._check_profile,
                        args=(dict(host), dict(account)),
                        name='ysu-heartbeat-%s' % host_id,
                        daemon=True,
                    ).start()
            with self.lock:
                for host_id in list(self.states):
                    if host_id not in valid_ids and not self.states[host_id]['active']:
                        self.states.pop(host_id, None)
            self.wake_event.wait(1.0)
            self.wake_event.clear()

    def _schedule(self, host_id, seconds):
        with self.lock:
            state = self.states.setdefault(host_id, self._new_state())
            state['next_due'] = time.monotonic() + max(1, seconds)
            state['active'] = False

    def _check_profile(self, host, account):
        host_id = host.get('id') or host.get('target')
        interval = int(host.get('heartbeat_interval_seconds') or DEFAULT_INTERVAL_SECONDS)
        threshold = int(host.get('heartbeat_failure_threshold') or DEFAULT_FAILURE_THRESHOLD)
        interval = min(MAX_INTERVAL_SECONDS, max(MIN_INTERVAL_SECONDS, interval))
        threshold = min(MAX_FAILURE_THRESHOLD, max(1, threshold))
        failure_recorded = False
        try:
            status = self.executor(host, 'status', None)
            assessment = assess_status(status, account)
            if assessment['healthy']:
                with self.lock:
                    state = self.states.setdefault(host_id, self._new_state())
                    state['failures'] = 0
                    state['repair_attempts'] = 0
                self._emit('healthy', host, assessment['reason'])
                self._schedule(host_id, interval)
                return

            with self.lock:
                state = self.states.setdefault(host_id, self._new_state())
                state['failures'] += 1
                failures = state['failures']
                failure_recorded = True
            if failures < threshold:
                self._emit(
                    'warning', host,
                    '%s；连续异常 %s/%s，暂不重连' % (
                        assessment['reason'], failures, threshold),
                )
                self._schedule(host_id, interval)
                return

            self._emit('repairing', host, '%s；开始按账号重连' % assessment['reason'])
            if assessment.get('needs_logout'):
                logout = self.executor(host, 'logout', None)
                if not logout.get('ok'):
                    self._emit('warning', host, '旧会话下线未确认，继续尝试账号登录')

            login = self.executor(host, 'login', account)
            if not login.get('ok'):
                raise RuntimeError(login.get('message') or '账号登录失败')

            self.sleep_fn(2)
            verified = self.executor(host, 'status', None)
            verified_assessment = assess_status(verified, account)
            if not verified_assessment['healthy']:
                raise RuntimeError('重连后验证失败：%s' % verified_assessment['reason'])

            with self.lock:
                state = self.states.setdefault(host_id, self._new_state())
                state['failures'] = 0
                state['repair_attempts'] = 0
                state['last_repair'] = time.monotonic()
            self._emit('repaired', host, '已使用账号“%s”恢复联网并验证成功' % account.get('name', ''))
            self._schedule(host_id, interval)
        except Exception as exc:
            with self.lock:
                state = self.states.setdefault(host_id, self._new_state())
                if not failure_recorded:
                    state['failures'] += 1
                state['repair_attempts'] += 1
                attempts = state['repair_attempts']
            backoff = min(MAX_BACKOFF_SECONDS, interval * (2 ** min(attempts, 4)))
            self._emit('error', host, '心跳或重连失败：%s；%s 秒后重试' % (exc, backoff))
            self._schedule(host_id, backoff)
