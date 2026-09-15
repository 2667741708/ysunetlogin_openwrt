#!/usr/bin/env python3
"""Windows desktop manager for YSU campus network logins over SSH."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import uuid
from heartbeat import (
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_INTERVAL_SECONDS,
    HeartbeatEngine,
    MAX_FAILURE_THRESHOLD,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    assess_status,
)
from netlogin import Netlogin

APP_NAME = 'YSUNetloginManager'
APP_VERSION = '2.1.4'
CONFIG_VERSION = 6
LOCAL_HOST_ID = 'local-windows'
SERVICES = {'0': '校园网', '1': '中国移动', '2': '中国联通', '3': '中国电信'}
SERVICE_IDS = {value: key for key, value in SERVICES.items()}
TARGET_RE = re.compile(r'^[A-Za-z0-9_.@:\-]+$')
HOSTNAME_RE = re.compile(r'^[A-Za-z0-9_.:\-\[\]]+$')
SCRIPT_RE = re.compile(r'^/[A-Za-z0-9_./\-\u3400-\u9fff]+$')
SSH_ALIAS_RE = re.compile(r'^[^\s*?!]+$')
SSH_VALUE_RE = re.compile(r'^[^\r\n]+$')
_REMOTE_LOCKS = {}
_REMOTE_LOCKS_GUARD = threading.Lock()

LEGACY_SEEDED_REMOTE_DEFAULTS = {
    'c201-4090': {
        'expected_hostname': 'a-MS-7E06',
        'script': '/home/a/网络登录服务器管理/ysunetlogin_openwrt/netlogin.py',
    },
    'c201-5080': {
        'expected_hostname': 'c201-MS-7E06',
        'script': '/home/c201/网络登录服务器管理/ysunetlogin_openwrt/netlogin.py',
    },
}


def default_local_host():
    return {
        'id': LOCAL_HOST_ID,
        'name': '本机 Windows',
        'connection_type': 'local',
        'target': 'local',
        'expected_hostname': '',
        'script': '',
        'account_id': '',
        'heartbeat_enabled': False,
        'heartbeat_interval_seconds': DEFAULT_INTERVAL_SECONDS,
        'heartbeat_failure_threshold': DEFAULT_FAILURE_THRESHOLD,
    }

REMOTE_BOOTSTRAP = r"""
import base64, json, os, subprocess, sys, tempfile
payload = json.loads(base64.b64decode(PAYLOAD_B64).decode('utf-8'))
script = base64.b64decode(payload['script_b64']).decode('utf-8')
handle = tempfile.NamedTemporaryFile('w', suffix='.py', prefix='ysu-netlogin-', delete=False, encoding='utf-8')
try:
    handle.write(script)
    handle.close()
    if payload['action'] == 'status':
        args = [sys.executable, handle.name, 'current-status', '--json']
        child_input = None
    elif payload['action'] == 'query-machine-status':
        args = [sys.executable, handle.name, 'query-machine-status']
        child_input = None
    elif payload['action'] == 'logout':
        args = [sys.executable, handle.name, 'logout']
        child_input = None
    elif payload['action'] == 'account-status':
        args = [sys.executable, handle.name, 'account-status-stdin']
        child_input = json.dumps(payload['account'], ensure_ascii=False)
    elif payload['action'] == 'account-offline-devices':
        args = [sys.executable, handle.name, 'account-offline-devices-stdin']
        child_input = json.dumps(dict(payload['account'],
            onlineUserUuids=payload.get('online_user_uuids')), ensure_ascii=False)
    else:
        args = [sys.executable, handle.name, 'login-stdin']
        child_input = json.dumps(payload['account'], ensure_ascii=False)
    completed = subprocess.run(args, input=child_input, text=True, capture_output=True,
                               encoding='utf-8', errors='replace', timeout=payload.get('timeout', 30))
    sys.stdout.write((completed.stdout or completed.stderr or '').strip())
    sys.exit(completed.returncode)
finally:
    try:
        os.unlink(handle.name)
    except Exception:
        pass
""".strip()


class DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD),
                ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data):
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def protect_secret(value):
    if not value:
        return ''
    raw, raw_buffer = _blob(value.encode('utf-8'))
    entropy, entropy_buffer = _blob(b'YSUNetloginManager-v1')
    output = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(raw), APP_NAME, ctypes.byref(entropy), None, None, 0,
        ctypes.byref(output))
    if not ok:
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return base64.b64encode(encrypted).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def unprotect_secret(value):
    if not value:
        return ''
    encrypted = base64.b64decode(value)
    raw, raw_buffer = _blob(encrypted)
    entropy, entropy_buffer = _blob(b'YSUNetloginManager-v1')
    output = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(raw), None, ctypes.byref(entropy), None, None, 0,
        ctypes.byref(output))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode('utf-8')
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


class ConfigStore:
    def __init__(self):
        appdata = os.environ.get('APPDATA') or str(Path.home())
        self.directory = Path(appdata) / APP_NAME
        self.path = self.directory / 'config.json'
        self.data = {'version': CONFIG_VERSION, 'accounts': [], 'hosts': []}
        self.load()

    def load(self):
        if self.path.exists():
            with self.path.open('r', encoding='utf-8-sig') as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        self._migrate()

    def _migrate(self):
        """Migrate profiles without retaining SSH secrets or losing heartbeat data."""
        changed = self.data.get('version') != CONFIG_VERSION
        forbidden = {
            'identity_file', 'identityfile', 'private_key', 'private_key_path',
            'ssh_private_key', 'ssh_key_data', 'key_data',
        }
        for host in self.data.get('hosts') or []:
            for key in list(host):
                if key.lower() in forbidden:
                    host.pop(key, None)
                    changed = True
            connection_type = 'local' if host.get('id') == LOCAL_HOST_ID else 'ssh'
            if host.get('connection_type') not in ('local', 'ssh'):
                host['connection_type'] = connection_type
                changed = True
            legacy = LEGACY_SEEDED_REMOTE_DEFAULTS.get(host.get('target'))
            if legacy:
                for key in ('expected_hostname', 'script'):
                    if host.get(key) == legacy[key]:
                        host[key] = ''
                        changed = True
            defaults = {
                'heartbeat_enabled': False,
                'heartbeat_interval_seconds': DEFAULT_INTERVAL_SECONDS,
                'heartbeat_failure_threshold': DEFAULT_FAILURE_THRESHOLD,
            }
            for key, value in defaults.items():
                if key not in host:
                    host[key] = value
                    changed = True
        if not any(host.get('id') == LOCAL_HOST_ID for host in self.data.get('hosts') or []):
            self.data.setdefault('hosts', []).insert(0, default_local_host())
            changed = True
        valid_host_ids = {host.get('id') for host in self.data.get('hosts') or []}
        if self.data.get('account_query_host_id') not in valid_host_ids:
            preferred = next((host for host in self.data.get('hosts') or []
                              if host.get('target') in ('c201-4090', 'c201-4090-wg')), None)
            self.data['account_query_host_id'] = (
                preferred.get('id') if preferred else LOCAL_HOST_ID)
            changed = True
        self.data['version'] = CONFIG_VERSION
        if changed and self.path.exists():
            self.save()

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8', newline='\n') as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)

    def account(self, account_id):
        return next((item for item in self.data['accounts'] if item['id'] == account_id), None)

    def host(self, host_id):
        return next((item for item in self.data['hosts'] if item['id'] == host_id), None)


class SSHConfigManager:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path.home() / '.ssh' / 'config'

    def read_lines(self):
        if not self.path.exists():
            return []
        return self.path.read_text(encoding='utf-8-sig', errors='replace').splitlines()

    def parse(self):
        lines = self.read_lines()
        blocks = []
        active = None
        for index, line in enumerate(lines):
            stripped = line.strip()
            lower = stripped.lower()
            is_host = bool(stripped) and not stripped.startswith('#') and lower.startswith('host ')
            is_match = bool(stripped) and not stripped.startswith('#') and lower.startswith('match ')
            if is_host or is_match:
                if active:
                    active['end'] = index
                    blocks.append(self._finalize_block(lines, active))
                    active = None
                if is_host:
                    aliases = stripped.split()[1:]
                    active = {'start': index, 'end': len(lines), 'aliases': aliases}
        if active:
            blocks.append(self._finalize_block(lines, active))
        return blocks

    def _finalize_block(self, lines, block):
        raw = lines[block['start']:block['end']]
        options = {}
        for line in raw[1:]:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                options[parts[0].lower()] = parts[1].strip()
        block['raw'] = raw
        block['options'] = options
        block['key'] = ' '.join(block['aliases'])
        block['editable'] = all(SSH_ALIAS_RE.fullmatch(alias) for alias in block['aliases'])
        return block

    def find_block(self, key='', aliases=None):
        aliases = aliases or []
        for block in self.parse():
            if key and block['key'] == key:
                return block
        for block in self.parse():
            if any(alias in block['aliases'] for alias in aliases):
                return block
        return None

def ssh_command(host, remote_args):
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
            '-o', 'StrictHostKeyChecking=yes', '--', host['target'], *remote_args]


def describe_ssh_failure(host, stderr):
    detail = (stderr or '').strip()
    lowered = detail.lower()
    target = host.get('target') or host.get('ssh_hostname') or '未知目标'
    if 'could not resolve hostname' in lowered or 'name or service not known' in lowered:
        reason = 'SSH 主机名或 Host 别名无法解析，请检查 HostName/别名。'
    elif ('connection timed out' in lowered or 'operation timed out' in lowered or
          'no route to host' in lowered or 'network is unreachable' in lowered):
        reason = '目标 IP 地址不可达或 SSH 端口超时，请检查 IP、网络和端口。'
    elif 'connection refused' in lowered:
        reason = '目标可以到达，但 SSH 端口拒绝连接，请检查端口和 sshd 服务。'
    elif 'permission denied' in lowered:
        reason = '已连接到 SSH 服务，但身份认证失败，请检查 User、密钥或密码配置。'
    elif ('host key verification failed' in lowered or
          'remote host identification has changed' in lowered):
        reason = 'SSH 主机密钥校验失败，请核对服务器身份及本机 known_hosts。'
    else:
        reason = 'SSH 连接失败。'
    if detail:
        return '%s\nSSH 目标：%s\n详细信息：%s' % (reason, target, detail)
    return '%s\nSSH 目标：%s' % (reason, target)


def bundled_path(name):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return root / name


def startup_command():
    if getattr(sys, 'frozen', False):
        return '"%s" --minimized' % sys.executable
    return '"%s" "%s" --minimized' % (sys.executable, Path(__file__).resolve())


def startup_enabled():
    try:
        import winreg
        path = r'Software\Microsoft\Windows\CurrentVersion\Run'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
        return value == startup_command()
    except OSError:
        return False


def set_startup_enabled(enabled):
    import winreg
    path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def load_bundled_netlogin_source():
    path = bundled_path('netlogin.py')
    if not path.exists():
        raise RuntimeError('EXE 内未找到内置 netlogin.py，请重新构建程序。')
    return path.read_text(encoding='utf-8-sig')


def find_bundled_vnc_viewer():
    relative = Path('tools') / 'TigerVNC' / 'vncviewer64-1.16.2.exe'
    candidates = [
        bundled_path(relative),
        Path(__file__).resolve().parent.parent / relative,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError('程序内未找到 TigerVNC Viewer，请重新构建管理器。')


def reserve_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def local_port_ready(port, timeout=0.2):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False


def run_remote_script_file(host, action, account=None, timeout=30,
                           remote_python='python3'):
    if action == 'status':
        args = [remote_python, host['script'], 'current-status', '--json']
        stdin = None
    elif action == 'query-machine-status':
        args = [remote_python, host['script'], 'query-machine-status']
        stdin = None
    elif action == 'logout':
        args = [remote_python, host['script'], 'logout']
        stdin = None
    else:
        args = [remote_python, host['script'], 'login-stdin']
        stdin = json.dumps({
            'username': account['username'],
            'password': unprotect_secret(account['password']),
            'service': account['service'],
        }, ensure_ascii=False)
    return subprocess.run(
        ssh_command(host, args), input=stdin, text=True, capture_output=True,
        timeout=timeout, encoding='utf-8', errors='replace',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def run_embedded_netlogin(host, action, account=None, timeout=30,
                          remote_python='python3', online_user_uuids=None):
    payload = {
        'action': action,
        'script_b64': base64.b64encode(load_bundled_netlogin_source().encode('utf-8')).decode('ascii'),
        'timeout': timeout,
        'account': None,
        'online_user_uuids': online_user_uuids,
    }
    if action not in ('status', 'logout', 'query-machine-status'):
        payload['account'] = {
            'username': account['username'],
            'password': unprotect_secret(account['password']),
            'service': account['service'],
        }
    payload_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode('utf-8')).decode('ascii')
    remote_program = "PAYLOAD_B64 = %r\n%s\n" % (payload_b64, REMOTE_BOOTSTRAP)
    return subprocess.run(
        ssh_command(host, [remote_python, '-']),
        input=remote_program, text=True, capture_output=True,
        timeout=timeout + 15, encoding='utf-8', errors='replace',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def remote_script_is_missing(completed):
    """Detect only Python's missing-script failure, where an embedded retry is safe."""
    detail = '%s\n%s' % (completed.stdout or '', completed.stderr or '')
    lowered = detail.lower()
    return ("can't open file" in lowered or 'cannot open file' in lowered) and (
        'no such file or directory' in lowered or '[errno 2]' in lowered)


def detect_remote_python(host):
    candidates = []
    if host.get('remote_python'):
        candidates.append(host['remote_python'])
    for value in ('python3', 'python', 'python.exe'):
        if value not in candidates:
            candidates.append(value)
    failures = []
    for candidate in candidates:
        try:
            completed = subprocess.run(
                ssh_command(host, [candidate, '-c', 'import sys; print(sys.executable)']),
                text=True, capture_output=True, timeout=12, encoding='utf-8',
                errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            failures.append('%s 超时' % candidate)
            continue
        if completed.returncode == 0 and completed.stdout.strip():
            return candidate
        failures.append('%s 不可用' % candidate)
    raise RuntimeError('远端未找到可用 Python（%s）' % '；'.join(failures))


def _run_remote_unlocked(host, action, account=None, timeout=30,
                         online_user_uuids=None):
    try:
        identity = subprocess.run(
            ssh_command(host, ['hostname']), text=True, capture_output=True,
            timeout=12, encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            '目标 IP 地址或 SSH 端口在 12 秒内没有响应。\nSSH 目标：%s' % host['target'])
    if identity.returncode != 0:
        raise RuntimeError(describe_ssh_failure(host, identity.stderr))
    if host.get('expected_hostname') and identity.stdout.strip() != host['expected_hostname']:
        raise RuntimeError('主机名不匹配：收到 %s' % identity.stdout.strip())
    if action == 'ssh-test':
        return {
            'ok': True,
            'message': 'SSH 连接成功',
            'hostname': identity.stdout.strip(),
            'target': host['target'],
        }
    remote_python = detect_remote_python(host)
    if host.get('script') and action != 'query-machine-status':
        completed = run_remote_script_file(
            host, action, account, timeout, remote_python=remote_python)
        if remote_script_is_missing(completed):
            completed = run_embedded_netlogin(
                host, action, account, timeout, remote_python=remote_python)
    else:
        completed = run_embedded_netlogin(
            host, action, account, timeout, remote_python=remote_python,
            online_user_uuids=online_user_uuids)
    output = (completed.stdout or completed.stderr).strip()
    try:
        result = json.loads(output)
    except ValueError:
        result = {'ok': completed.returncode == 0, 'message': output or '远端未返回内容'}
    result.setdefault('ok', completed.returncode == 0)
    return result


def _run_local_unlocked(host, action, account=None, timeout=30):
    """Run the same netlogin core directly against the Windows host network."""
    if action == 'ssh-test':
        return {
            'ok': True,
            'message': '本机网络功能可用',
            'hostname': socket.gethostname(),
            'target': '本机 Windows',
        }
    netlogin = Netlogin()
    if action == 'query-machine-status':
        result = netlogin.query_machine_status()
        result['targetType'] = 'local'
        return result
    if action == 'status':
        result = netlogin.current_status()
        result['ok'] = True
        result['targetType'] = 'local'
        return result
    if action == 'logout':
        state, message = netlogin.logout()
        return {'ok': bool(state), 'message': message, 'targetType': 'local'}
    if not account:
        return {'ok': False, 'message': '未选择用于本机联网的账号'}
    password = unprotect_secret(account.get('password', ''))
    state, message = netlogin.ensure_login(
        user=account.get('username', ''),
        pwd=password,
        service_type=str(account.get('service', '')),
    )
    return {
        'ok': bool(state),
        'message': message,
        'targetType': 'local',
        'service': str(account.get('service', '')),
    }


def run_target(host, action, account=None, timeout=30):
    """Serialize manual and heartbeat operations for one local or SSH target."""
    lock_key = host.get('id') or host.get('target') or 'unknown'
    with _REMOTE_LOCKS_GUARD:
        lock = _REMOTE_LOCKS.setdefault(lock_key, threading.Lock())
    with lock:
        if host.get('connection_type') == 'local' or host.get('id') == LOCAL_HOST_ID:
            if action == 'login':
                preflight = _run_local_unlocked(
                    host, 'query-machine-status', None, timeout)
                if not preflight.get('ok'):
                    return preflight
            return _run_local_unlocked(host, action, account, timeout)
        if action == 'login':
            preflight = _run_remote_unlocked(
                host, 'query-machine-status', None, timeout)
            if not preflight.get('ok'):
                return preflight
        return _run_remote_unlocked(host, action, account, timeout)


def run_account_device_operation(host, action, account, online_user_uuids=None,
                                 timeout=60):
    """Run account device queries where the chosen network session exists."""
    lock_key = host.get('id') or host.get('target') or 'account-query'
    with _REMOTE_LOCKS_GUARD:
        lock = _REMOTE_LOCKS.setdefault(lock_key, threading.Lock())
    with lock:
        if host.get('connection_type') == 'local' or host.get('id') == LOCAL_HOST_ID:
            netlogin = Netlogin()
            password = unprotect_secret(account['password'])
            if action == 'query':
                return netlogin.account_status(account['username'], password)
            return netlogin.account_offline_devices(
                account['username'], password, online_user_uuids)
        embedded_host = dict(host)
        embedded_host['script'] = ''
        remote_action = ('account-status' if action == 'query'
                         else 'account-offline-devices')
        return _run_remote_unlocked(
            embedded_host, remote_action, account, timeout,
            online_user_uuids=online_user_uuids)


def run_remote(host, action, account=None, timeout=30):
    """Backward-compatible SSH/local operation entry point."""
    return run_target(host, action, account, timeout)


class DesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.store = ConfigStore()
        self.ssh_config = SSHConfigManager()
        self.title('YSU 校园网 SSH 登录管理器 v%s' % APP_VERSION)
        self.geometry('1120x760')
        self.minsize(920, 700)
        self.configure(bg='#f4f6f3')
        self.option_add('*Font', ('Microsoft YaHei UI', 10))
        self.style = ttk.Style(self)
        self.style.theme_use('clam')
        self.style.configure('TFrame', background='#f4f6f3')
        self.style.configure('Panel.TFrame', background='#ffffff')
        self.style.configure('Title.TLabel', background='#f4f6f3', foreground='#13251e', font=('Microsoft YaHei UI', 24, 'bold'))
        self.style.configure('Muted.TLabel', background='#f4f6f3', foreground='#627069')
        self.style.configure('Security.TLabel', background='#e4f3ea', foreground='#175c3b', font=('Microsoft YaHei UI', 9, 'bold'))
        self.style.configure('Primary.TButton', font=('Microsoft YaHei UI', 10, 'bold'), foreground='white', background='#176b49')
        self.style.map('Primary.TButton', background=[('active', '#10583b'), ('disabled', '#91aa9f')])
        self.busy = False
        self.vnc_connecting = False
        self.vnc_sessions = {}
        self.account_device_busy = False
        self.account_devices = []
        self.heartbeat_log_path = self.create_heartbeat_log_path()
        self.sync_ssh_hosts()
        self.build_ui()
        self.refresh_all()
        self.heartbeat_engine = HeartbeatEngine(
            self.heartbeat_profiles, run_target, self.on_heartbeat_event)
        self.heartbeat_engine.start()
        self.protocol('WM_DELETE_WINDOW', self.close_app)

    def build_ui(self):
        header = ttk.Frame(self, padding=(28, 22, 28, 14))
        header.pack(fill='x')
        ttk.Label(header, text='校园网登录管理器', style='Title.TLabel').pack(anchor='w')
        ttk.Label(header, text='选择本机或服务器与校园网账号，一次操作完成认证。', style='Muted.TLabel').pack(anchor='w', pady=(4, 10))
        ttk.Label(
            header,
            text='✓ 本机可直接联网；远端模式不读取、不保存、不打包 SSH 私钥',
            style='Security.TLabel',
            padding=(10, 6),
        ).pack(anchor='w')
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=24, pady=(4, 24))
        self.operation_tab = ttk.Frame(self.tabs, style='Panel.TFrame', padding=24)
        self.accounts_tab = ttk.Frame(self.tabs, style='Panel.TFrame', padding=20)
        self.hosts_tab = ttk.Frame(self.tabs, style='Panel.TFrame', padding=20)
        self.heartbeat_tab = ttk.Frame(self.tabs, style='Panel.TFrame', padding=20)
        self.tabs.add(self.operation_tab, text=' 一键连接 ')
        self.tabs.add(self.heartbeat_tab, text=' 网络自愈心跳 ')
        self.tabs.add(self.accounts_tab, text=' 校园网账号 ')
        self.tabs.add(self.hosts_tab, text=' 连接目标 ')
        self.build_operation_tab()
        self.build_heartbeat_tab()
        self.build_accounts_tab()
        self.build_hosts_tab()

    def build_operation_tab(self):
        frame = self.operation_tab
        ttk.Label(
            frame, text='让哪个目标上线？', background='#ffffff',
            foreground='#13251e', font=('Microsoft YaHei UI', 16, 'bold')
        ).grid(row=0, column=0, sticky='w', pady=(0, 18))
        ttk.Label(frame, text='目标设备', background='#ffffff').grid(row=1, column=0, sticky='w')
        self.op_host = ttk.Combobox(frame, state='readonly', width=48)
        self.op_host.grid(row=2, column=0, sticky='ew', pady=(6, 18))
        self.op_host.bind('<<ComboboxSelected>>', self.apply_host_default)
        ttk.Label(frame, text='使用校园网账号').grid(row=3, column=0, sticky='w')
        self.op_account = ttk.Combobox(frame, state='readonly', width=48)
        self.op_account.grid(row=4, column=0, sticky='ew', pady=(6, 8))
        self.mapping_hint = ttk.Label(frame, text='', foreground='#66736d', background='#ffffff')
        self.mapping_hint.grid(row=5, column=0, sticky='w', pady=(0, 20))
        actions = ttk.Frame(frame, style='Panel.TFrame')
        actions.grid(row=6, column=0, sticky='w')
        self.connect_button = ttk.Button(actions, text='一键连接校园网', style='Primary.TButton', command=self.connect_selected)
        self.connect_button.pack(side='left', ipadx=12, ipady=6)
        self.status_button = ttk.Button(actions, text='查询当前状态', command=self.status_selected)
        self.status_button.pack(side='left', padx=10, ipadx=8, ipady=6)
        self.logout_button = ttk.Button(actions, text='下线当前服务器', command=self.logout_selected)
        self.logout_button.pack(side='left', ipadx=8, ipady=6)
        self.vnc_button = ttk.Button(actions, text='打开 VNC 桌面', command=self.open_vnc_selected)
        self.vnc_button.pack(side='left', padx=(10, 0), ipadx=8, ipady=6)
        ttk.Separator(frame).grid(row=7, column=0, sticky='ew', pady=24)
        ttk.Label(frame, text='执行结果', background='#ffffff', font=('Microsoft YaHei UI', 11, 'bold')).grid(row=8, column=0, sticky='w')
        output_frame = ttk.Frame(frame, style='Panel.TFrame')
        output_frame.grid(row=9, column=0, sticky='nsew', pady=(8, 0))
        self.output = tk.Text(output_frame, height=13, wrap='word', relief='flat', bg='#edf3ee', fg='#17251f', padx=14, pady=12)
        output_scroll = ttk.Scrollbar(output_frame, orient='vertical', command=self.output.yview)
        self.output.configure(yscrollcommand=output_scroll.set, state='disabled')
        self.output.grid(row=0, column=0, sticky='nsew')
        output_scroll.grid(row=0, column=1, sticky='ns')
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(9, weight=1)

    def build_heartbeat_tab(self):
        frame = self.heartbeat_tab
        ttk.Label(
            frame, text='掉线后自动按账号恢复', background='#ffffff',
            foreground='#13251e', font=('Microsoft YaHei UI', 16, 'bold')
        ).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(
            frame,
            text='每个目标使用“连接目标”页设置的默认账号。本机直接检测，远端通过 SSH；连续异常达到阈值后才会重连。',
            foreground='#66736d', background='#ffffff', wraplength=820,
        ).grid(row=1, column=0, columnspan=3, sticky='w', pady=(5, 18))

        ttk.Label(frame, text='服务器', background='#ffffff').grid(row=2, column=0, sticky='w')
        self.heartbeat_host = ttk.Combobox(frame, state='readonly', width=36)
        self.heartbeat_host.grid(row=3, column=0, sticky='ew', padx=(0, 12), pady=(5, 12))
        self.heartbeat_host.bind('<<ComboboxSelected>>', self.load_heartbeat_form)

        ttk.Label(frame, text='检查间隔（秒）', background='#ffffff').grid(row=2, column=1, sticky='w')
        self.heartbeat_interval = tk.StringVar(value=str(DEFAULT_INTERVAL_SECONDS))
        ttk.Entry(frame, textvariable=self.heartbeat_interval, width=14).grid(
            row=3, column=1, sticky='ew', padx=(0, 12), pady=(5, 12))

        ttk.Label(frame, text='连续异常阈值', background='#ffffff').grid(row=2, column=2, sticky='w')
        self.heartbeat_threshold = ttk.Combobox(
            frame, state='readonly', values=[str(value) for value in range(1, MAX_FAILURE_THRESHOLD + 1)],
            width=12)
        self.heartbeat_threshold.set(str(DEFAULT_FAILURE_THRESHOLD))
        self.heartbeat_threshold.grid(row=3, column=2, sticky='ew', pady=(5, 12))

        self.heartbeat_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text='启用这台服务器的自动检测与账号重连',
            variable=self.heartbeat_enabled,
        ).grid(row=4, column=0, columnspan=3, sticky='w')
        self.heartbeat_account_hint = ttk.Label(
            frame, text='默认账号：—', foreground='#66736d', background='#ffffff', wraplength=820)
        self.heartbeat_account_hint.grid(row=5, column=0, columnspan=3, sticky='w', pady=(5, 12))

        buttons = ttk.Frame(frame, style='Panel.TFrame')
        buttons.grid(row=6, column=0, columnspan=3, sticky='w')
        ttk.Button(
            buttons, text='保存并应用', style='Primary.TButton',
            command=self.save_heartbeat_settings,
        ).pack(side='left')
        ttk.Button(buttons, text='立即检查一次', command=self.check_heartbeat_now).pack(side='left', padx=8)
        self.startup_var = tk.BooleanVar(value=startup_enabled())
        ttk.Checkbutton(
            buttons, text='随 Windows 登录启动（最小化）', variable=self.startup_var,
            command=self.toggle_startup,
        ).pack(side='left', padx=(10, 0))

        self.heartbeat_status = ttk.Label(
            frame, text='心跳引擎已启动，等待已启用的主机。', foreground='#175c3b',
            background='#e4f3ea', padding=(10, 7), wraplength=820)
        self.heartbeat_status.grid(row=7, column=0, columnspan=3, sticky='ew', pady=(18, 8))

        ttk.Label(frame, text='本次运行记录', background='#ffffff', font=('Microsoft YaHei UI', 10, 'bold')).grid(
            row=8, column=0, columnspan=3, sticky='w')
        log_frame = ttk.Frame(frame, style='Panel.TFrame')
        log_frame.grid(row=9, column=0, columnspan=3, sticky='nsew', pady=(6, 0))
        self.heartbeat_log = tk.Text(
            log_frame, height=12, wrap='word', relief='flat', bg='#edf3ee',
            fg='#17251f', padx=10, pady=8)
        log_scroll = ttk.Scrollbar(log_frame, orient='vertical', command=self.heartbeat_log.yview)
        self.heartbeat_log.configure(yscrollcommand=log_scroll.set, state='disabled')
        self.heartbeat_log.grid(row=0, column=0, sticky='nsew')
        log_scroll.grid(row=0, column=1, sticky='ns')
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)
        frame.rowconfigure(9, weight=1)

    def build_accounts_tab(self):
        left = ttk.Frame(self.accounts_tab, style='Panel.TFrame')
        left.pack(side='left', fill='y', padx=(0, 20))
        self.account_list = tk.Listbox(left, width=28, height=16, exportselection=False)
        self.account_list.pack(fill='y', expand=True)
        self.account_list.bind('<<ListboxSelect>>', self.load_account_form)
        ttk.Button(left, text='新建账号', command=self.new_account).pack(fill='x', pady=(10, 0))
        right = ttk.Frame(self.accounts_tab, style='Panel.TFrame')
        right.pack(side='left', fill='both', expand=True)
        self.account_vars = {key: tk.StringVar() for key in ('id', 'name', 'username', 'password', 'service')}
        self.form_entry(right, 0, '配置名称', self.account_vars['name'])
        self.form_entry(right, 1, '校园网账号', self.account_vars['username'])
        self.form_entry(right, 2, '校园网密码', self.account_vars['password'], show='•')
        ttk.Label(right, text='默认运营商', background='#fffdf7').grid(row=6, column=0, sticky='w', pady=(10, 5))
        self.account_service = ttk.Combobox(right, state='readonly', values=list(SERVICES.values()), textvariable=self.account_vars['service'])
        self.account_service.grid(row=7, column=0, sticky='ew')
        buttons = ttk.Frame(right, style='Panel.TFrame')
        buttons.grid(row=8, column=0, sticky='w', pady=20)
        ttk.Button(buttons, text='保存账号', style='Primary.TButton', command=self.save_account).pack(side='left')
        ttk.Button(buttons, text='删除', command=self.delete_account).pack(side='left', padx=8)
        ttk.Label(right, text='密码使用 Windows DPAPI 加密，只能由当前 Windows 用户解密。', foreground='#66736d', background='#fffdf7').grid(row=9, column=0, sticky='w')
        query_location = ttk.Frame(right, style='Panel.TFrame')
        query_location.grid(row=10, column=0, sticky='ew', pady=(14, 2))
        ttk.Label(query_location, text='在线设备查询位置', background='#ffffff').pack(
            side='left', padx=(0, 10))
        self.account_query_host_var = tk.StringVar()
        self.account_query_host = ttk.Combobox(
            query_location, state='readonly', width=42,
            textvariable=self.account_query_host_var)
        self.account_query_host.pack(side='left', fill='x', expand=True)
        self.account_query_host.bind('<<ComboboxSelected>>', self.save_account_query_host)
        self.account_machine_check_button = ttk.Button(
            query_location, text='检查资格', command=self.check_query_machine)
        self.account_machine_check_button.pack(side='left', padx=(8, 0))
        device_buttons = ttk.Frame(right, style='Panel.TFrame')
        device_buttons.grid(row=11, column=0, sticky='w', pady=(12, 8))
        self.account_query_button = ttk.Button(device_buttons, text='查询在线设备', command=self.query_account_devices)
        self.account_query_button.pack(side='left')
        self.account_kick_selected_button = ttk.Button(device_buttons, text='踢选中设备', command=self.kick_selected_account_devices)
        self.account_kick_selected_button.pack(side='left', padx=8)
        self.account_kick_all_button = ttk.Button(device_buttons, text='踢全部设备', command=self.kick_all_account_devices)
        self.account_kick_all_button.pack(side='left')
        ttk.Button(device_buttons, text='查看完整详情', command=self.show_account_device_details).pack(
            side='left', padx=(8, 0))
        self.account_device_hint = ttk.Label(
            right, text='先查询该账号在线设备，再按需下线。',
            foreground='#66736d', background='#fffdf7', wraplength=920,
            justify='left')
        self.account_device_hint.grid(row=12, column=0, sticky='w')
        device_list_frame = ttk.Frame(right, style='Panel.TFrame')
        device_list_frame.grid(row=13, column=0, sticky='nsew', pady=(8, 0))
        columns = ('ip', 'hostname', 'service', 'deviceName', 'deviceType', 'onlineDuration', 'accessTime')
        self.account_device_list = ttk.Treeview(
            device_list_frame, columns=columns, show='headings', height=7, selectmode='extended')
        for key, title, width in (
                ('ip', '在线 IP', 150), ('hostname', '主机名', 200),
                ('service', '实际运营商 / 服务', 180), ('deviceName', '设备名称', 160),
                ('deviceType', '类型', 85), ('onlineDuration', '在线时长', 140),
                ('accessTime', '上线时间', 180)):
            self.account_device_list.heading(key, text=title)
            self.account_device_list.column(key, width=width, minwidth=60, stretch=False)
        device_y_scroll = ttk.Scrollbar(device_list_frame, orient='vertical', command=self.account_device_list.yview)
        device_x_scroll = ttk.Scrollbar(device_list_frame, orient='horizontal', command=self.account_device_list.xview)
        self.account_device_list.configure(yscrollcommand=device_y_scroll.set, xscrollcommand=device_x_scroll.set)
        self.account_device_list.grid(row=0, column=0, sticky='nsew')
        device_y_scroll.grid(row=0, column=1, sticky='ns')
        device_x_scroll.grid(row=1, column=0, sticky='ew')
        device_list_frame.columnconfigure(0, weight=1)
        device_list_frame.rowconfigure(0, weight=1)
        self.account_device_list.bind('<MouseWheel>', self.scroll_account_devices)
        self.account_device_list.bind('<Shift-MouseWheel>', self.scroll_account_devices_horizontal)
        self.account_device_list.bind('<Double-1>', self.show_account_device_details)
        self.account_device_list.bind('<Return>', self.show_account_device_details)
        right.bind('<Configure>', lambda event: self.account_device_hint.configure(
            wraplength=max(220, event.width - 20)))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(13, weight=1)

    def build_hosts_tab(self):
        left = ttk.Frame(self.hosts_tab, style='Panel.TFrame')
        left.pack(side='left', fill='y', padx=(0, 20))
        host_list_frame = ttk.Frame(left, style='Panel.TFrame')
        host_list_frame.pack(fill='both', expand=True)
        self.host_list = tk.Listbox(host_list_frame, width=28, height=16, exportselection=False)
        host_list_scroll = ttk.Scrollbar(host_list_frame, orient='vertical', command=self.host_list.yview)
        self.host_list.configure(yscrollcommand=host_list_scroll.set)
        self.host_list.grid(row=0, column=0, sticky='nsew')
        host_list_scroll.grid(row=0, column=1, sticky='ns')
        host_list_frame.columnconfigure(0, weight=1)
        host_list_frame.rowconfigure(0, weight=1)
        self.host_list.bind('<<ListboxSelect>>', self.load_host_form)
        ttk.Button(left, text='新建远端连接', command=self.new_host).pack(fill='x', pady=(10, 0))
        ttk.Button(left, text='导入 SSH Config 别名', command=self.reload_ssh_config).pack(fill='x', pady=(8, 0))
        right = ttk.Frame(self.hosts_tab, style='Panel.TFrame')
        right.pack(side='left', fill='both', expand=True)
        self.host_vars = {key: tk.StringVar() for key in (
            'id', 'ssh_key', 'name', 'aliases', 'hostname', 'script', 'account')}
        ttk.Label(
            right,
            text='本机直接联网  ·  远端只保存 SSH Host 别名，不保存密钥',
            foreground='#175c3b', background='#e4f3ea',
            font=('Microsoft YaHei UI', 12, 'bold'), padding=(10, 7), wraplength=650,
        ).grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        ttk.Label(
            right, text='读取来源：%s' % self.ssh_config.path,
            foreground='#66736d', background='#ffffff', wraplength=650
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 4))
        self.host_name_entry = self.form_entry_at(right, 2, 0, '显示名称', self.host_vars['name'])
        self.host_alias_entry = self.form_entry_at(right, 2, 1, 'SSH Host 别名（本机目标无需填写）', self.host_vars['aliases'])
        self.host_hostname_entry = self.form_entry_at(right, 4, 0, '预期主机名（远端 hostname，可选）', self.host_vars['hostname'], columnspan=2)
        self.host_script_entry = self.form_entry_at(right, 6, 0, '远端 netlogin.py 绝对路径（可选；留空使用内置脚本）', self.host_vars['script'], columnspan=2)
        ttk.Label(right, text='该目标默认账号', background='#ffffff').grid(row=8, column=0, sticky='w', pady=(8, 4))
        self.host_account = ttk.Combobox(right, state='readonly', textvariable=self.host_vars['account'])
        self.host_account.grid(row=9, column=0, columnspan=2, sticky='ew', padx=(0, 14))
        buttons = ttk.Frame(right, style='Panel.TFrame')
        buttons.grid(row=10, column=0, columnspan=2, sticky='w', pady=(12, 10))
        ttk.Button(buttons, text='保存连接档案', style='Primary.TButton', command=self.save_host).pack(side='left')
        self.host_test_button = ttk.Button(buttons, text='测试连接', command=self.test_host_form)
        self.host_test_button.pack(side='left', padx=8)
        self.host_delete_button = ttk.Button(buttons, text='删除连接档案', command=self.delete_host)
        self.host_delete_button.pack(side='left')
        ttk.Label(right, text='连接测试结果', background='#ffffff', font=('Microsoft YaHei UI', 10, 'bold')).grid(
            row=11, column=0, columnspan=2, sticky='w', pady=(2, 5))
        host_test_frame = ttk.Frame(right, style='Panel.TFrame')
        host_test_frame.grid(row=12, column=0, columnspan=2, sticky='nsew', padx=(0, 14))
        self.host_test_output = tk.Text(
            host_test_frame, height=5, wrap='word', relief='flat', bg='#edf3ee',
            fg='#17251f', padx=10, pady=8)
        host_test_scroll = ttk.Scrollbar(host_test_frame, orient='vertical', command=self.host_test_output.yview)
        self.host_test_output.configure(yscrollcommand=host_test_scroll.set, state='disabled')
        self.host_test_output.grid(row=0, column=0, sticky='nsew')
        host_test_scroll.grid(row=0, column=1, sticky='ns')
        host_test_frame.columnconfigure(0, weight=1)
        host_test_frame.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(12, weight=1)

    def form_entry(self, parent, row, label, variable, show=None):
        ttk.Label(parent, text=label, background='#fffdf7').grid(row=row * 2, column=0, sticky='w', pady=(6, 3))
        entry = ttk.Entry(parent, textvariable=variable, show=show)
        entry.grid(row=row * 2 + 1, column=0, sticky='ew')
        return entry

    def form_entry_at(self, parent, row, column, label, variable, show=None, columnspan=1):
        ttk.Label(parent, text=label, background='#fffdf7').grid(row=row, column=column, columnspan=columnspan, sticky='w', pady=(5, 3), padx=(0, 14))
        entry = ttk.Entry(parent, textvariable=variable, show=show)
        entry.grid(row=row + 1, column=column, columnspan=columnspan, sticky='ew', padx=(0, 14))
        return entry

    def account_display(self, item):
        return '%s  ·  %s' % (item['name'], SERVICES.get(item['service'], item['service']))

    def create_heartbeat_log_path(self):
        try:
            base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or str(Path.home())
            directory = Path(base) / APP_NAME / 'logs'
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            return directory / ('heartbeat-%s-%s.log' % (stamp, os.getpid()))
        except OSError:
            return None

    def refresh_all(self):
        self.accounts = list(self.store.data['accounts'])
        self.hosts = list(self.store.data['hosts'])
        account_names = [self.account_display(item) for item in self.accounts]
        host_names = [item['name'] for item in self.hosts]
        self.account_list.delete(0, 'end')
        self.host_list.delete(0, 'end')
        for value in account_names:
            self.account_list.insert('end', value)
        for value in host_names:
            self.host_list.insert('end', value)
        self.op_account['values'] = account_names
        self.host_account['values'] = ['（不设置）'] + account_names
        self.op_host['values'] = host_names
        self.heartbeat_host['values'] = host_names
        self.account_query_hosts = [
            ('%s · %s' % (host['name'], host.get('target') or 'local'), host)
            for host in self.hosts
        ]
        self.account_query_host['values'] = [item[0] for item in self.account_query_hosts]
        selected_query_id = self.store.data.get('account_query_host_id')
        selected_query = next((item[0] for item in self.account_query_hosts
                               if item[1].get('id') == selected_query_id), '')
        if selected_query:
            self.account_query_host_var.set(selected_query)
        if host_names and not self.op_host.get():
            self.op_host.current(0)
            self.apply_host_default()
        if account_names and not self.op_account.get():
            self.op_account.current(0)
        if host_names and not self.heartbeat_host.get():
            self.heartbeat_host.current(0)
            self.load_heartbeat_form()

    def heartbeat_host_from_form(self):
        name = self.heartbeat_host.get()
        return next((item for item in self.hosts if item['name'] == name), None)

    def load_heartbeat_form(self, event=None):
        host = self.heartbeat_host_from_form()
        if not host:
            self.heartbeat_enabled.set(False)
            self.heartbeat_account_hint.configure(text='默认账号：—')
            return
        self.heartbeat_enabled.set(bool(host.get('heartbeat_enabled', False)))
        self.heartbeat_interval.set(str(
            host.get('heartbeat_interval_seconds') or DEFAULT_INTERVAL_SECONDS))
        self.heartbeat_threshold.set(str(
            host.get('heartbeat_failure_threshold') or DEFAULT_FAILURE_THRESHOLD))
        account = self.store.account(host.get('account_id', ''))
        if account:
            text = '默认账号：%s（%s）' % (
                account.get('name'), SERVICES.get(account.get('service'), account.get('service')))
        else:
            text = '默认账号：未设置。请先到“连接目标”页为该目标选择默认账号。'
        self.heartbeat_account_hint.configure(text=text)

    def save_heartbeat_settings(self, notify=True):
        host = self.heartbeat_host_from_form()
        if not host:
            if notify:
                messagebox.showerror('缺少配置', '请选择要配置心跳的服务器。')
            return False
        try:
            interval = int(self.heartbeat_interval.get().strip())
            threshold = int(self.heartbeat_threshold.get().strip())
        except ValueError:
            if notify:
                messagebox.showerror('配置错误', '检查间隔和异常阈值必须是整数。')
            return False
        if not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
            if notify:
                messagebox.showerror(
                    '配置错误', '检查间隔必须在 %s 到 %s 秒之间。' % (
                        MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS))
            return False
        if not 1 <= threshold <= MAX_FAILURE_THRESHOLD:
            if notify:
                messagebox.showerror('配置错误', '连续异常阈值必须在 1 到 %s 之间。' % MAX_FAILURE_THRESHOLD)
            return False
        account = self.store.account(host.get('account_id', ''))
        if self.heartbeat_enabled.get() and not account:
            if notify:
                messagebox.showerror('缺少账号', '启用心跳前，请在“连接目标”页为该目标设置默认账号。')
            return False
        host['heartbeat_enabled'] = bool(self.heartbeat_enabled.get())
        host['heartbeat_interval_seconds'] = interval
        host['heartbeat_failure_threshold'] = threshold
        self.store.save()
        if hasattr(self, 'heartbeat_engine'):
            self.heartbeat_engine.wake()
        state = '已启用' if host['heartbeat_enabled'] else '已停用'
        self.heartbeat_status.configure(text='%s：%s自动自愈心跳。' % (host['name'], state))
        if notify:
            messagebox.showinfo('心跳配置已保存', '%s：%s自动自愈心跳。' % (host['name'], state))
        return True

    def heartbeat_profiles(self):
        profiles = []
        for source_host in self.store.data.get('hosts') or []:
            if not source_host.get('heartbeat_enabled'):
                continue
            account = self.store.account(source_host.get('account_id', ''))
            if account:
                profiles.append((dict(source_host), dict(account)))
        return profiles

    def check_heartbeat_now(self):
        host = self.heartbeat_host_from_form()
        if not host:
            return messagebox.showerror('缺少配置', '请选择要检查的服务器。')
        if not self.save_heartbeat_settings(notify=False):
            return
        if not host.get('heartbeat_enabled'):
            return messagebox.showinfo('心跳未启用', '请先勾选启用并保存，再执行立即检查。')
        self.heartbeat_status.configure(text='%s：已请求立即检查。' % host['name'])
        self.heartbeat_engine.check_now(host.get('id') or host.get('target'))

    def toggle_startup(self):
        desired = bool(self.startup_var.get())
        try:
            set_startup_enabled(desired)
        except OSError as exc:
            self.startup_var.set(startup_enabled())
            return messagebox.showerror('开机启动设置失败', str(exc))
        text = '已启用，程序将在 Windows 登录后最小化启动。' if desired else '已关闭随 Windows 登录启动。'
        self.heartbeat_status.configure(text=text)

    def on_heartbeat_event(self, event):
        self.after(0, lambda: self.append_heartbeat_event(event))

    def append_heartbeat_event(self, event):
        stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(event.get('time') or time.time()))
        line = '[%s] [%s] %s：%s' % (
            stamp, event.get('level', 'info').upper(),
            event.get('host_name', '未知主机'), event.get('message', ''))
        self.heartbeat_log.configure(state='normal')
        self.heartbeat_log.insert('end', line + '\n')
        self.heartbeat_log.see('end')
        self.heartbeat_log.configure(state='disabled')
        self.heartbeat_status.configure(text=line)
        if self.heartbeat_log_path:
            try:
                with self.heartbeat_log_path.open('a', encoding='utf-8', newline='\n') as handle:
                    handle.write(line + '\n')
            except OSError:
                self.heartbeat_log_path = None

    def sync_ssh_hosts(self):
        changed = False
        blocks = [block for block in self.ssh_config.parse() if block['editable']]
        for block in blocks:
            aliases = block['aliases']
            if not aliases:
                continue
            existing = next((host for host in self.store.data['hosts']
                             if host.get('connection_type') != 'local' and (
                                 host.get('ssh_config_key') == block['key']
                                 or host.get('target') in aliases
                                 or any(alias in host.get('ssh_aliases', []) for alias in aliases))), None)
            options = block['options']
            updates = {
                'ssh_config_key': block['key'],
                'ssh_aliases': aliases,
                'target': aliases[0],
            }
            if existing:
                for key, value in updates.items():
                    if existing.get(key) != value:
                        existing[key] = value
                        changed = True
                if not existing.get('name'):
                    existing['name'] = aliases[0]
                    changed = True
            else:
                self.store.data['hosts'].append({
                    'id': str(uuid.uuid4()),
                    'name': aliases[0],
                    'connection_type': 'ssh',
                    'target': aliases[0],
                    'expected_hostname': '',
                    'script': '',
                    'account_id': '',
                    **updates,
                })
                changed = True
        if changed:
            self.store.save()

    def reload_ssh_config(self):
        try:
            self.sync_ssh_hosts()
        except Exception as exc:
            return messagebox.showerror('读取失败', '无法读取 SSH config：%s' % exc)
        self.refresh_all()
        messagebox.showinfo(
            '已导入',
            '已经从 %s 导入 Host 别名。\n\n未读取或保存 IdentityFile 与任何私钥内容。' %
            self.ssh_config.path)

    def new_account(self):
        for var in self.account_vars.values(): var.set('')
        self.account_vars['id'].set(str(uuid.uuid4()))
        self.account_vars['service'].set('中国移动')
        self.account_list.selection_clear(0, 'end')
        self.clear_account_devices('保存账号后可查询在线设备。')

    def load_account_form(self, event=None):
        selected = self.account_list.curselection()
        if not selected: return
        item = self.accounts[selected[0]]
        self.account_vars['id'].set(item['id'])
        self.account_vars['name'].set(item['name'])
        self.account_vars['username'].set(item['username'])
        try: self.account_vars['password'].set(unprotect_secret(item['password']))
        except Exception: self.account_vars['password'].set('')
        self.account_vars['service'].set(SERVICES.get(item['service'], '校园网'))
        self.clear_account_devices('先查询该账号在线设备，再按需下线。')

    def save_account(self):
        name = self.account_vars['name'].get().strip()
        username = self.account_vars['username'].get().strip()
        password = self.account_vars['password'].get()
        service = SERVICE_IDS.get(self.account_vars['service'].get())
        if not name or not username or not password or service is None:
            return messagebox.showerror('无法保存', '请完整填写配置名称、账号、密码和运营商。')
        account_id = self.account_vars['id'].get() or str(uuid.uuid4())
        item = {'id': account_id, 'name': name, 'username': username,
                'password': protect_secret(password), 'service': service}
        existing = self.store.account(account_id)
        if existing: existing.update(item)
        else: self.store.data['accounts'].append(item)
        self.store.save(); self.account_vars['password'].set(''); self.refresh_all()
        messagebox.showinfo('已保存', '账号配置已加密保存。')

    def delete_account(self):
        account_id = self.account_vars['id'].get()
        if not account_id or not messagebox.askyesno('确认删除', '删除这个账号配置？'): return
        self.store.data['accounts'] = [a for a in self.store.data['accounts'] if a['id'] != account_id]
        for host in self.store.data['hosts']:
            if host.get('account_id') == account_id: host['account_id'] = ''
        self.store.save(); self.new_account(); self.refresh_all()

    def clear_account_devices(self, hint):
        self.account_devices = []
        if hasattr(self, 'account_device_list'):
            self.account_device_list.delete(*self.account_device_list.get_children())
            self.account_device_hint.configure(text=hint)

    def selected_saved_account(self):
        account = self.store.account(self.account_vars['id'].get())
        if not account:
            raise ValueError('请先选择一个已保存的校园网账号。')
        return account

    def selected_account_query_host(self):
        display = self.account_query_host_var.get()
        host = next((item[1] for item in self.account_query_hosts
                     if item[0] == display), None)
        if not host:
            raise ValueError('请选择在线设备查询位置。')
        return host

    def save_account_query_host(self, event=None):
        try:
            host = self.selected_account_query_host()
        except ValueError as exc:
            return messagebox.showerror('查询位置不可用', str(exc))
        self.store.data['account_query_host_id'] = host['id']
        self.store.save()
        self.clear_account_devices(
            '查询位置已改为 %s；请重新查询在线设备。' % host['name'])

    def account_device_display(self, item):
        parts = [
            '当前设备' if item.get('currentDevice') else '',
            item.get('ip') or '无 IP',
            '主机名：' + (item.get('hostname') or '未知（接口未返回）'),
            '设备名：' + (item.get('deviceName') or '未命名设备'),
            item.get('deviceType') or '未知类型',
            '实际服务：' + (item.get('service') or '未知（接口未返回）'),
            item.get('onlineDuration') or '',
            item.get('accessTime') or '',
        ]
        return '  ·  '.join([part for part in parts if part])

    @staticmethod
    def account_device_values(item):
        return (item.get('ip') or '未知', item.get('hostname') or '未知（接口未返回）',
                item.get('service') or '未知（接口未返回）', item.get('deviceName') or '未命名设备',
                item.get('deviceType') or '未知', item.get('onlineDuration') or '未知',
                item.get('accessTime') or '未知')

    @staticmethod
    def account_device_details(item):
        sources = {'findDevice': '学校设备接口', 'current-session': '当前出口认证会话',
                   'local-machine': '查询机器的系统主机名（本机 IP 已核对）'}
        rows = [('在线 IP', item.get('ip')), ('主机名', item.get('hostname')),
                ('主机名来源', sources.get(item.get('hostnameSource'), '接口未返回')),
                ('实际运营商 / 服务', item.get('service')),
                ('服务来源', sources.get(item.get('serviceSource'), '接口未返回')),
                ('设备名称（不等于主机名）', item.get('deviceName')),
                ('设备类型', item.get('deviceType')), ('MAC', item.get('mac')),
                ('上线时间', item.get('accessTime')), ('在线时长', item.get('onlineDuration')),
                ('当前出口设备', '是' if item.get('currentDevice') else '未确认'),
                ('设备标识', item.get('onlineUserUuid'))]
        return '\n'.join('%s：%s' % (label, value or '未知（接口未返回）') for label, value in rows)

    def show_account_device_details(self, event=None):
        selected = self.account_device_list.selection()
        if not selected:
            return
        text = '\n\n'.join(self.account_device_details(self.account_devices[int(index)]) for index in selected)
        dialog = tk.Toplevel(self)
        dialog.title('在线设备完整详情')
        dialog.geometry('760x480')
        dialog.minsize(420, 280)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill='both', expand=True)
        content = tk.Text(frame, wrap='word', padx=8, pady=8)
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=content.yview)
        content.configure(yscrollcommand=scrollbar.set)
        content.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        content.insert('1.0', text)
        content.configure(state='disabled')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        def copy_details():
            self.clipboard_clear()
            self.clipboard_append(text)

        ttk.Button(frame, text='复制全部详情', command=copy_details).grid(row=1, column=0, sticky='w', pady=(8, 0))

    def scroll_account_devices(self, event):
        self.account_device_list.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        return 'break'

    def scroll_account_devices_horizontal(self, event):
        self.account_device_list.xview_scroll(int(-1 * (event.delta / 120)), 'units')
        return 'break'

    def set_account_device_busy(self, busy):
        self.account_device_busy = busy
        state = 'disabled' if busy else 'normal'
        self.account_query_button.configure(state=state)
        self.account_machine_check_button.configure(state=state)
        self.account_kick_selected_button.configure(state=state)
        self.account_kick_all_button.configure(state=state)

    def check_query_machine(self):
        if self.account_device_busy:
            return
        try:
            query_host = self.selected_account_query_host()
        except Exception as exc:
            return messagebox.showerror('查询位置不可用', str(exc))
        self.set_account_device_busy(True)
        self.account_device_hint.configure(
            text='正在检查 %s 是否具备校园网查询条件……' % query_host['name'])

        def worker():
            try:
                result = run_target(query_host, 'query-machine-status', timeout=45)
                state = '符合要求' if result.get('ok') else '不符合要求'
                text = '执行位置：%s；查询机器资格：%s；%s' % (
                    query_host['name'], state,
                    result.get('message') or '未返回检查说明')
                self.after(0, lambda: self.account_device_hint.configure(text=text))
            except Exception as exc:
                error_text = str(exc).strip() or type(exc).__name__
                self.after(0, lambda value=error_text: self.account_device_hint.configure(
                    text='查询机器检查失败：%s' % value))
            finally:
                self.after(0, lambda: self.set_account_device_busy(False))
        threading.Thread(target=worker, daemon=True).start()

    def run_account_device_async(self, action, uuids=None):
        if self.account_device_busy:
            return
        try:
            account = self.selected_saved_account()
            password = unprotect_secret(account['password'])
            query_host = self.selected_account_query_host()
        except Exception as exc:
            return messagebox.showerror('账号不可用', str(exc))
        self.set_account_device_busy(True)
        self.account_device_hint.configure(
            text='正在通过 %s 处理 %s，请稍候……' % (query_host['name'], account['name']))

        def worker():
            try:
                if action == 'query':
                    result = run_account_device_operation(query_host, 'query', account)
                    query_machine = result.get('queryMachine') or {}
                    if query_machine and not query_machine.get('ok'):
                        raise RuntimeError(
                            query_machine.get('message') or '需要先实现访问校园局域网')
                    summary = result.get('summary') or {}
                    devices = summary.get('devices') or []
                    text = '执行位置：%s；在线设备数：%s' % (
                        query_host['name'], summary.get('onlineDeviceCount', 0))
                    if summary.get('querySource') == 'current-session':
                        text += '；已复用该位置的当前认证会话'
                    if summary.get('serviceUnknownCount'):
                        text += '；%s 台设备的实际运营商接口未返回' % summary['serviceUnknownCount']
                    if summary.get('hostnameUnknownCount'):
                        text += '；%s 台设备的主机名未知' % summary['hostnameUnknownCount']
                    errors = result.get('errors') or []
                    if errors:
                        text += '；警告：%s' % '；'.join(errors)
                    elif not devices:
                        text += '；学校接口未返回其他在线设备'
                    elif (len(devices) == 1 and
                          summary.get('querySource') == 'current-session'):
                        text += '；学校接口本次只返回 1 台设备，未返回其他会话'
                    self.after(0, lambda: self.show_account_devices(devices, text))
                else:
                    result = run_account_device_operation(
                        query_host, 'kick', account, uuids)
                    summary = result.get('summary') or {}
                    text = '执行位置：%s；已请求下线设备数：%s；结果：%s' % (
                        query_host['name'], summary.get('targetCount', 0),
                        summary.get('message') or '—')
                    if summary.get('errors'):
                        text += '；错误：%s' % '；'.join(summary.get('errors'))
                    refreshed = run_account_device_operation(
                        query_host, 'query', account)
                    devices = (refreshed.get('summary') or {}).get('devices') or []
                    self.after(0, lambda: self.show_account_devices(devices, text))
            except Exception as exc:
                error_text = str(exc).strip() or type(exc).__name__
                self.after(0, lambda value=error_text: self.account_device_hint.configure(
                    text='操作失败：%s' % value))
            finally:
                self.after(0, lambda: self.set_account_device_busy(False))
        threading.Thread(target=worker, daemon=True).start()

    def show_account_devices(self, devices, hint):
        self.account_devices = list(devices)
        self.account_device_list.delete(*self.account_device_list.get_children())
        for index, item in enumerate(self.account_devices):
            self.account_device_list.insert('', 'end', iid=str(index), values=self.account_device_values(item))
        self.account_device_hint.configure(text=hint)

    def query_account_devices(self):
        self.run_account_device_async('query')

    def kick_selected_account_devices(self):
        selected = self.account_device_list.selection()
        if not selected:
            return messagebox.showerror('缺少选择', '请先查询并选择要下线的设备。')
        devices = [self.account_devices[int(index)] for index in selected]
        uuids = [item.get('onlineUserUuid') for item in devices if item.get('onlineUserUuid')]
        if not uuids:
            return messagebox.showerror('无法下线', '选中的设备没有 onlineUserUuid。')
        detail = '\n'.join(self.account_device_display(item) for item in devices)
        if not messagebox.askyesno('确认踢设备', '下线选中的 %s 台设备？\n\n%s' % (len(uuids), detail)):
            return
        self.run_account_device_async('kick', uuids)

    def kick_all_account_devices(self):
        try:
            account = self.selected_saved_account()
        except ValueError as exc:
            return messagebox.showerror('账号不可用', str(exc))
        if not messagebox.askyesno('确认踢全部设备', '下线账号 %s 名下所有在线设备？' % account['name']):
            return
        self.run_account_device_async('kick', None)

    def new_host(self):
        for var in self.host_vars.values(): var.set('')
        self.host_vars['id'].set(str(uuid.uuid4()))
        self.host_vars['account'].set('（不设置）')
        self.host_list.selection_clear(0, 'end')
        self.set_host_form_local(False)

    def set_host_form_local(self, is_local):
        state = 'disabled' if is_local else 'normal'
        for entry in (
                self.host_name_entry, self.host_alias_entry,
                self.host_hostname_entry, self.host_script_entry):
            entry.configure(state=state)
        self.host_delete_button.configure(state='disabled' if is_local else 'normal')
        self.host_test_button.configure(text='检查本机功能' if is_local else '测试 SSH')

    def load_host_form(self, event=None):
        selected = self.host_list.curselection()
        if not selected: return
        item = self.hosts[selected[0]]
        for var in self.host_vars.values(): var.set('')
        self.host_vars['id'].set(item['id'])
        self.host_vars['ssh_key'].set(item.get('ssh_config_key', ''))
        self.host_vars['name'].set(item['name'])
        self.host_vars['aliases'].set(item.get('target', ''))
        self.host_vars['hostname'].set(item.get('expected_hostname', ''))
        self.host_vars['script'].set(item.get('script', ''))
        account = self.store.account(item.get('account_id', ''))
        self.host_vars['account'].set(self.account_display(account) if account else '（不设置）')
        self.set_host_form_local(item.get('connection_type') == 'local')

    def account_id_from_display(self, display):
        for item in self.accounts:
            if self.account_display(item) == display: return item['id']
        return ''

    def validated_host_form(self):
        host_id = self.host_vars['id'].get()
        existing = self.store.host(host_id)
        if existing and existing.get('connection_type') == 'local':
            item = dict(existing)
            item['account_id'] = self.account_id_from_display(self.host_vars['account'].get())
            return item
        target = self.host_vars['aliases'].get().strip()
        item = {
            'id': host_id or str(uuid.uuid4()),
            'connection_type': 'ssh',
            'ssh_config_key': self.host_vars['ssh_key'].get(),
            'name': self.host_vars['name'].get().strip(),
            'ssh_aliases': [target] if target else [],
            'target': target,
            'expected_hostname': self.host_vars['hostname'].get().strip(),
            'script': self.host_vars['script'].get().strip(),
            'account_id': self.account_id_from_display(self.host_vars['account'].get()),
        }
        if not item['name'] or not target or not SSH_ALIAS_RE.fullmatch(target):
            raise ValueError('显示名称和 SSH Host 别名不能为空；别名不能包含空格、通配符或 !。')
        if item['expected_hostname'] and not HOSTNAME_RE.fullmatch(item['expected_hostname']):
            raise ValueError('预期主机名格式无效。')
        if item['script'] and not SCRIPT_RE.fullmatch(item['script']):
            raise ValueError('脚本必须是无空格、无 shell 特殊字符的 Linux 绝对路径。')
        return item

    def save_host(self):
        try: item = self.validated_host_form()
        except ValueError as exc: return messagebox.showerror('无法保存', str(exc))
        existing = self.store.host(item['id'])
        if existing: existing.update(item)
        else: self.store.data['hosts'].append(item)
        self.store.save(); self.sync_ssh_hosts(); self.refresh_all()
        if item.get('connection_type') == 'local':
            message = '本机默认账号映射已保存。'
        else:
            message = '连接档案和默认账号映射已保存。\nSSH config 与私钥均未被修改。'
        messagebox.showinfo('已保存', message)

    def delete_host(self):
        host_id = self.host_vars['id'].get()
        host = self.store.host(host_id)
        if host and host.get('connection_type') == 'local':
            return messagebox.showerror('不能删除', '“本机 Windows”是内置目标，不能删除。')
        if not host_id or not host or not messagebox.askyesno(
                '确认删除', '只删除管理器中的连接档案？\n\nSSH config 不会被修改。'):
            return
        self.store.data['hosts'] = [h for h in self.store.data['hosts'] if h['id'] != host_id]
        self.store.save(); self.new_host(); self.op_host.set(''); self.refresh_all()

    def host_from_operation(self):
        name = self.op_host.get()
        return next((item for item in self.hosts if item['name'] == name), None)

    def account_from_operation(self):
        return self.store.account(self.account_id_from_display(self.op_account.get()))

    def apply_host_default(self, event=None):
        host = self.host_from_operation()
        account = self.store.account(host.get('account_id', '')) if host else None
        is_local = bool(host and host.get('connection_type') == 'local')
        self.connect_button.configure(
            text='使用此账号连接本机' if is_local else '一键连接校园网')
        self.status_button.configure(
            text='检查本机网络状态' if is_local else '查询当前状态')
        self.logout_button.configure(
            text='下线本机' if is_local else '下线当前服务器')
        self.vnc_button.configure(state='disabled' if is_local else 'normal')
        if account:
            self.op_account.set(self.account_display(account))
            self.mapping_hint.configure(text='已自动选择该目标的默认账号。')
        else:
            self.mapping_hint.configure(text='该目标尚未设置默认账号，可在“连接目标”页配置。')

    def set_output(self, text):
        self.output.configure(state='normal'); self.output.delete('1.0', 'end')
        self.output.insert('1.0', text); self.output.configure(state='disabled')

    def set_host_test_output(self, text):
        self.host_test_output.configure(state='normal')
        self.host_test_output.delete('1.0', 'end')
        self.host_test_output.insert('1.0', text)
        self.host_test_output.configure(state='disabled')

    def set_busy(self, busy):
        self.busy = busy
        state = 'disabled' if busy else 'normal'
        self.connect_button.configure(state=state)
        self.status_button.configure(state=state)
        self.logout_button.configure(state=state)
        self.host_test_button.configure(state=state)
        host = self.host_from_operation()
        is_local = bool(host and host.get('connection_type') == 'local')
        self.vnc_button.configure(
            state='disabled' if busy or self.vnc_connecting or is_local else 'normal')

    def set_vnc_connecting(self, busy):
        self.vnc_connecting = busy
        host = self.host_from_operation()
        is_local = bool(host and host.get('connection_type') == 'local')
        self.vnc_button.configure(
            state='disabled' if busy or self.busy or is_local else 'normal')

    def vnc_targets(self, host):
        primary = host.get('target', '')
        targets = [primary] if primary else []
        prefix = primary + '-'
        preferred_suffixes = ('-ZO', '-ZN', '-wg', '-zt')
        alternatives = [item.get('target', '') for item in self.hosts
                        if item.get('target', '').startswith(prefix)]
        alternatives.sort(key=lambda value: next(
            (index for index, suffix in enumerate(preferred_suffixes) if value.endswith(suffix)),
            len(preferred_suffixes)))
        for target in alternatives:
            if target and target not in targets:
                targets.append(target)
        return targets

    def open_vnc_selected(self):
        host = self.host_from_operation()
        if not host:
            return messagebox.showerror('缺少配置', '请先选择要访问的 SSH 主机。')
        if host.get('connection_type') == 'local':
            return messagebox.showinfo('本机无需 VNC', '“本机 Windows”目标直接在当前电脑操作，不需要 VNC。')
        session_key = host.get('id') or host.get('target')
        existing = self.vnc_sessions.get(session_key)
        if existing and existing['viewer'].poll() is None:
            return messagebox.showinfo('VNC 已打开', '%s 的 VNC 窗口已经在运行。' % host['name'])
        self.set_vnc_connecting(True)
        self.set_output('正在为 %s 建立安全 SSH 隧道并启动内置 VNC Viewer……' % host['name'])

        def worker():
            tunnel = None
            viewer = None
            errors = []
            local_port = reserve_local_port()
            try:
                viewer_path = find_bundled_vnc_viewer()
                ssh_path = shutil.which('ssh.exe') or shutil.which('ssh')
                if not ssh_path:
                    raise RuntimeError('Windows 未找到 ssh.exe，请安装或启用 OpenSSH Client。')
                used_target = ''
                for target in self.vnc_targets(host):
                    command = [
                        ssh_path,
                        '-o', 'BatchMode=yes',
                        '-o', 'StrictHostKeyChecking=yes',
                        '-o', 'ConnectTimeout=5',
                        '-o', 'ExitOnForwardFailure=yes',
                        '-o', 'ServerAliveInterval=30',
                        '-o', 'ServerAliveCountMax=3',
                        '-N', '-L', '127.0.0.1:%s:127.0.0.1:5902' % local_port,
                        target,
                    ]
                    candidate = subprocess.Popen(
                        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    for _ in range(24):
                        if candidate.poll() is not None or local_port_ready(local_port):
                            break
                        time.sleep(0.25)
                    if local_port_ready(local_port):
                        tunnel = candidate
                        used_target = target
                        break
                    try:
                        detail = candidate.communicate(timeout=1)[1].strip()
                    except subprocess.TimeoutExpired:
                        candidate.terminate()
                        detail = candidate.communicate(timeout=2)[1].strip()
                    errors.append('%s：%s' % (target, detail or '连接超时'))
                if not tunnel:
                    raise RuntimeError('所有 SSH 路径均失败：\n' + '\n'.join(errors))
                viewer = subprocess.Popen(
                    [str(viewer_path), '127.0.0.1::%s' % local_port],
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.vnc_sessions[session_key] = {'tunnel': tunnel, 'viewer': viewer}
                self.after(0, lambda: self.set_output(
                    'VNC 已启动\n服务器：%s\nSSH 路径：%s\n本机隧道：127.0.0.1:%s\nVNC 密码：请输入该服务器设置的密码。' %
                    (host['name'], used_target, local_port)))
                self.after(0, lambda: self.set_vnc_connecting(False))
                viewer.wait()
            except Exception as exc:
                message = 'VNC 启动失败：%s' % exc
                self.after(0, lambda value=message: self.set_output(value))
                self.after(0, lambda value=message: messagebox.showerror('VNC 启动失败', value))
            finally:
                if tunnel and tunnel.poll() is None:
                    tunnel.terminate()
                    try:
                        tunnel.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        tunnel.kill()
                self.vnc_sessions.pop(session_key, None)
                self.after(0, lambda: self.set_vnc_connecting(False))
        threading.Thread(target=worker, daemon=True).start()

    def close_app(self):
        if hasattr(self, 'heartbeat_engine'):
            self.heartbeat_engine.stop()
        for session in list(self.vnc_sessions.values()):
            for process in (session.get('viewer'), session.get('tunnel')):
                if process and process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
        self.destroy()

    def run_async(self, action, host, account=None):
        if self.busy: return
        is_local = host.get('connection_type') == 'local'
        progress = ('正在检查本机网络，请稍候……' if is_local else
                    '正在通过 SSH 连接 %s，请稍候……' % host['name'])
        self.set_busy(True); self.set_output(progress)
        if action == 'ssh-test':
            test_progress = ('正在检查本机网络功能，请稍候……' if is_local else
                             '正在测试 SSH 连接：%s\n请稍候……' % host['target'])
            self.set_host_test_output(test_progress)
        def worker():
            succeeded = False
            try:
                result = run_target(host, action, account)
                succeeded = bool(result.get('ok'))
                if action == 'status':
                    summary = result.get('summary') or {}
                    text = '\n'.join([
                        '状态：%s' % ('在线' if summary.get('online') else '离线'),
                        '目标：%s' % host['name'],
                        '账号：%s' % (summary.get('userId') or '—'),
                        '运营商：%s' % (summary.get('service') or '—'),
                        'IP：%s' % (summary.get('userIp') or '—'),
                        '说明：%s' % (summary.get('message') or '—')])
                elif action == 'ssh-test':
                    if is_local:
                        text = '\n'.join([
                            '本机网络功能检查通过',
                            '目标：本机 Windows',
                            '计算机名：%s' % (result.get('hostname') or '—')])
                    else:
                        text = '\n'.join([
                            'SSH 连接成功',
                            '服务器：%s' % host['name'],
                            'SSH 目标：%s' % result.get('target', host['target']),
                            '远端 hostname：%s' % (result.get('hostname') or '—')])
                elif action == 'logout':
                    text = '\n'.join([
                        result.get('message', '下线操作完成'),
                        '目标：%s' % host['name'],
                        '说明：只让该目标当前校园网会话下线，不会删除保存的账号配置。'])
                else:
                    text = '%s\n目标：%s\n账号配置：%s\n指定运营商：%s' % (
                        result.get('message', '操作完成'), host['name'], account['name'],
                        SERVICES.get(account['service'], account['service']))
                if not result.get('ok'): text = '操作失败\n' + text
            except Exception as exc:
                text = '执行失败：%s' % exc
            def finish():
                self.set_output(text)
                if action == 'ssh-test':
                    self.set_host_test_output(text)
                    if succeeded:
                        messagebox.showinfo('连接测试成功', text)
                    else:
                        messagebox.showerror('连接测试失败', text)
                self.set_busy(False)
            self.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    def connect_selected(self):
        host, account = self.host_from_operation(), self.account_from_operation()
        if not host or not account:
            return messagebox.showerror('缺少配置', '请选择目标设备和校园网账号。')
        self.run_async('login', host, account)

    def status_selected(self):
        host = self.host_from_operation()
        if not host: return messagebox.showerror('缺少配置', '请选择目标设备。')
        self.run_async('status', host)

    def logout_selected(self):
        host = self.host_from_operation()
        if not host:
            return messagebox.showerror('缺少配置', '请选择目标设备。')
        heartbeat_note = ''
        if host.get('heartbeat_enabled'):
            heartbeat_note = '\n\n这台主机已启用自愈心跳，稍后可能会按默认账号自动重新连接。'
        if not messagebox.askyesno(
                '确认下线', '让 %s 当前校园网会话下线？%s' % (host['name'], heartbeat_note)):
            return
        self.run_async('logout', host)

    def test_host_form(self):
        try: host = self.validated_host_form()
        except ValueError as exc: return messagebox.showerror('配置错误', str(exc))
        self.run_async('ssh-test', host)


def seed_known_hosts(store):
    if any(host.get('connection_type') == 'ssh' for host in store.data.get('hosts') or []):
        return
    store.data['hosts'].extend([
        {'id': str(uuid.uuid4()), 'name': '4090 服务器', 'connection_type': 'ssh', 'target': 'c201-4090',
         'expected_hostname': '', 'script': '', 'account_id': ''},
        {'id': str(uuid.uuid4()), 'name': '5080 服务器', 'connection_type': 'ssh', 'target': 'c201-5080',
         'expected_hostname': '', 'script': '', 'account_id': ''},
    ])
    store.save()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.name != 'nt':
        raise SystemExit('桌面版当前仅支持 Windows。')
    if '--self-test' in argv or '--self-test-file' in argv:
        source = load_bundled_netlogin_source()
        viewer = find_bundled_vnc_viewer()
        aes_key = base64.b64encode(b'Thats my Kung Fu').decode('ascii')
        aes_value = Netlogin()._aes_encrypt_b64(aes_key, 'Two One Nine Two')
        aes_ok = base64.b64decode(aes_value)[:16].hex() == '29c3505f571420f6402299b31a02d73a'
        result = json.dumps({
            'ok': bool(source) and viewer.is_file() and aes_ok,
            'app': APP_NAME,
            'version': APP_VERSION,
            'heartbeat': True,
            'localTarget': default_local_host().get('connection_type') == 'local',
            'bundledViewer': viewer.is_file(),
            'aesBackend': aes_ok,
        }, ensure_ascii=False)
        if '--self-test-file' in argv:
            index = argv.index('--self-test-file')
            if index + 1 >= len(argv):
                raise SystemExit(2)
            Path(argv[index + 1]).write_text(result, encoding='utf-8')
        else:
            print(result)
        return
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\YSUNetloginManager.SingleInstance')
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(None, '校园网登录管理器已经在运行。', APP_NAME, 0x40)
        return
    store = ConfigStore(); seed_known_hosts(store)
    app = DesktopApp()
    if '--minimized' in argv:
        app.after(250, app.iconify)
    app.mainloop()
    ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == '__main__':
    main()
