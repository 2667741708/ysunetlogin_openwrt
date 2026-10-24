"""Per-user detached heartbeat, automatically armed after a successful CLI login."""
from contextlib import contextmanager
import base64
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

from heartbeat import HeartbeatEngine


def state_directory():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'YSUNetlogin' / 'heartbeat'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'ysu-netlogin'


def prepare_directory(directory):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('心跳目录不能是符号链接')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != 'nt':
        if directory.stat().st_uid != os.getuid():
            raise ValueError('心跳目录不属于当前用户')
        directory.chmod(0o700)
    return directory


def write_private(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.heartbeat-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    if path.is_symlink():
        raise ValueError('心跳状态文件不能是符号链接')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


class FileLock:
    """OS-owned lock: stale PID files can never create a second worker."""
    def __init__(self, path, timeout=90):
        self.path, self.timeout, self.stream = path, timeout, None

    def __enter__(self):
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        self.stream = os.fdopen(os.open(self.path, flags, 0o600), 'r+b', buffering=0)
        if not self.path.stat().st_size:
            self.stream.write(b'0')
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    raise TimeoutError('心跳操作正在进行，请稍后重试')
                time.sleep(0.05)

    def __exit__(self, *unused):
        if os.name == 'nt':
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()


@contextmanager
def operation(directory=None):
    directory = prepare_directory(directory or state_directory())
    with FileLock(directory / 'operation.lock'):
        yield directory


def _dpapi(data, decrypt=False):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.windll.crypt32
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise OSError('Windows 无法保护或读取心跳凭据')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def encode_account(account):
    if os.name == 'nt':
        raw = json.dumps(account).encode('utf-8')
        return {'dpapi': base64.b64encode(_dpapi(raw)).decode('ascii')}
    return {'account': account}


def decode_account(config):
    if 'dpapi' in config:
        return json.loads(_dpapi(base64.b64decode(config['dpapi']), True).decode('utf-8'))
    return config['account']


def runtime_bundle(directory):
    # Copy sources out of zipapps / temporary SSH uploads so the worker survives cleanup.
    names = ('auto_heartbeat', 'heartbeat', 'netlogin', 'campus_network', 'wifi_scan')
    sources = {}
    for name in names:
        spec = importlib.util.find_spec(name)
        source = spec.loader.get_source(name) if spec and spec.loader else None
        if source is None:
            raise RuntimeError('缺少心跳运行模块：' + name)
        sources[name + '.py'] = source
    adapter_spec = importlib.util.find_spec('campus_network')
    adapter_path = Path(adapter_spec.origin).parent / 'campus_adapters.ps1'
    if adapter_path.is_file():
        sources['campus_adapters.ps1'] = adapter_path.read_text(encoding='utf-8-sig')
    else:
        loader = adapter_spec.loader
        try:
            sources['campus_adapters.ps1'] = loader.get_data(str(adapter_path)).decode('utf-8-sig')
        except (OSError, AttributeError):
            if os.name == 'nt':
                raise RuntimeError('缺少 Windows 网卡检测模块')
    sources['__main__.py'] = 'from auto_heartbeat import worker_main\nworker_main()\n'
    digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()[:20]
    destination = directory / ('runtime-' + digest + '.pyz')
    if not destination.exists():
        fd, temporary = tempfile.mkstemp(dir=directory, prefix='.runtime-')
        os.close(fd)
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, source in sources.items():
                    archive.writestr(name, source)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return destination


def status(directory=None):
    directory = prepare_directory(directory or state_directory())
    configured = bool(read_json(directory / 'config.json'))
    try:
        with FileLock(directory / 'worker.lock', timeout=0):
            running = False
    except TimeoutError:
        running = True
    report = read_json(directory / 'status.json') if configured else {}
    return {'enabled': configured, 'running': running and configured,
            'interval_seconds': 60, 'failure_threshold': 2, **report}


def _start_locked(directory, user, password, service):
    bundle = runtime_bundle(directory)
    generation = uuid.uuid4().hex
    config = encode_account({'username': user, 'password': password,
                             'service': str(service), 'name': '自动登录账号'})
    config['generation'] = generation
    # A new worker reads the exact bundle version needed by this login.
    config['runtime'] = str(bundle)
    write_private(directory / 'config.json', config)
    # Always attempt launch; worker.lock elects exactly one persistent owner.
    options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL,
               'stderr': subprocess.DEVNULL, 'close_fds': True}
    if os.name == 'nt':
        options['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options['start_new_session'] = True
    loader = 'import runpy,sys;sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name="__main__")'
    process = subprocess.Popen([sys.executable, '-c', loader, str(bundle), str(directory)], **options)
    return generation, process


def wait_started(directory, generation, process):
    # Caller has released operation.lock; the worker can acknowledge configuration.
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        report = status(directory)
        if report['running'] and report.get('generation') == generation:
            return {'ok': True, 'message': '已自动启动后台自愈心跳（60 秒检查，连续 2 次异常后重连）'}
        if process.poll() not in (None, 0):
            break
        time.sleep(0.1)
    return {'ok': False, 'message': '登录成功，但未能确认后台心跳启动；请检查 heartbeat-status'}


def login(client, user, password, service='0', ensure=False, once=False, directory=None):
    if once:
        method = client.ensure_login if ensure else client.login
        return (*method(user=user, pwd=password, **({'service_type': service} if ensure else {'type': service})), None)
    with operation(directory) as directory:
        method = client.ensure_login if ensure else client.login
        ok, message = method(user=user, pwd=password, **({'service_type': service} if ensure else {'type': service}))
        if not ok:
            return ok, message, None
        try:
            generation, process = _start_locked(directory, user, password, service)
        except Exception:
            return ok, message, {'ok': False, 'message': '登录成功，但后台心跳启动失败；请检查源码模块及状态目录权限'}
    return ok, message, wait_started(directory, generation, process)


def stop(directory=None):
    with operation(directory) as directory:
        (directory / 'config.json').unlink(missing_ok=True)
        (directory / 'status.json').unlink(missing_ok=True)


def logout(client, once=False, directory=None):
    if once:
        return client.logout()
    with operation(directory) as directory:
        (directory / 'config.json').unlink(missing_ok=True)
        (directory / 'status.json').unlink(missing_ok=True)
        return client.logout()


def run_worker(directory, client_factory=None):
    if client_factory is None:
        from netlogin import Netlogin
        client_factory = Netlogin
    directory = prepare_directory(directory)
    try:
        ownership = FileLock(directory / 'worker.lock', timeout=0)
        ownership.__enter__()
    except TimeoutError:
        return
    try:
        generation, engine = None, None
        host = {'id': 'local', 'name': '当前设备', 'heartbeat_interval_seconds': 60,
                'heartbeat_failure_threshold': 2}
        while True:
            with operation(directory):
                config = read_json(directory / 'config.json')
                if not config:
                    return
                if config['generation'] != generation:
                    account = decode_account(config)
                    generation = config['generation']
                    client = client_factory()

                    def execute(host, action, credentials):
                        if action == 'status':
                            return client.current_status()
                        if action == 'logout':
                            ok, _ = client.logout()
                        else:
                            ok, _ = client.ensure_login(user=credentials['username'],
                                pwd=credentials['password'], service_type=credentials['service'])
                        # Authentication responses may contain personal information.
                        return {'ok': bool(ok), 'message': '认证成功' if ok else '认证失败'}

                    def report(event):
                        # Store operational state only, never credentials or raw portal responses.
                        write_private(directory / 'status.json', {
                            'generation': generation, 'pid': os.getpid(), 'updated_at': time.time(),
                            'state': event['level']})

                    engine = HeartbeatEngine(lambda: [], execute, report)
                    report({'level': 'started'})
                next_due = engine.states.get('local', {}).get('next_due', 0)
                if time.monotonic() >= next_due:
                    engine._check_profile(host, account)
            time.sleep(1)
    finally:
        ownership.__exit__()


def worker_main():
    run_worker(Path(sys.argv[1]))
