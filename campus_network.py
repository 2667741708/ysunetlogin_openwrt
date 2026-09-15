"""Windows campus transport: physical NIC binding plus adapter-specific DNS.

No global proxy, DNS, route, or Wi-Fi connection settings are modified.
"""
import concurrent.futures
import http.client
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULTS = {'scan_wifi': True, 'preferred_ssid': 'iYanDa',
            'interface': 'auto', 'timeout': 4}


def load_config():
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    path = Path(os.environ.get('YSUNETLOGIN_NETWORK_CONFIG') or
                base / 'campus_network.json')
    config = dict(DEFAULTS)
    if path.exists():
        with path.open(encoding='utf-8-sig') as handle:
            values = json.load(handle)
        if not isinstance(values, dict):
            raise ValueError('campus_network.json 必须是对象')
        config.update(values)
    if not isinstance(config['scan_wifi'], bool):
        raise ValueError('scan_wifi 必须是 true 或 false')
    if not isinstance(config['preferred_ssid'], str) or not isinstance(config['interface'], str):
        raise ValueError('preferred_ssid 和 interface 必须是字符串')
    if not isinstance(config['timeout'], (int, float)) or not 1 <= config['timeout'] <= 10:
        raise ValueError('timeout 必须在 1 到 10 秒之间')
    return config


def adapter_inventory():
    script = Path(__file__).with_name('campus_adapters.ps1')
    shell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run(
        [str(shell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
        timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('无法读取物理网卡：' + result.stderr.strip())
    return json.loads(result.stdout.lstrip('\ufeff'))


def usable_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
        return not (address.is_loopback or address.is_link_local or address.is_unspecified
                    or address.is_multicast or address in ipaddress.ip_network('198.18.0.0/15'))
    except ValueError:
        return False


def dns_name(packet, offset, visited=None):
    visited = set() if visited is None else set(visited)
    labels = []
    end = None
    while True:
        if offset in visited or offset >= len(packet):
            raise ValueError('DNS 名称损坏')
        visited.add(offset)
        length = packet[offset]
        offset += 1
        if length & 0xc0 == 0xc0:
            if offset >= len(packet):
                raise ValueError('DNS 指针损坏')
            target = ((length & 0x3f) << 8) | packet[offset]
            end = offset + 1 if end is None else end
            suffix, _ = dns_name(packet, target, visited)
            labels.append(suffix)
            break
        if length == 0:
            break
        if length > 63 or offset + length > len(packet):
            raise ValueError('DNS 标签损坏')
        labels.append(packet[offset:offset + length].decode('ascii').lower())
        offset += length
    return '.'.join(labels), end if end is not None else offset


def dns_answers(packet, transaction, hostname):
    if len(packet) < 12:
        raise ValueError('DNS 响应过短')
    ident, flags, questions, answers, _, _ = struct.unpack('!6H', packet[:12])
    if ident != transaction or not flags & 0x8000 or flags & 0x020f or questions != 1:
        raise ValueError('DNS 响应失败、截断或事务不匹配')
    question, offset = dns_name(packet, 12)
    if question != hostname or packet[offset:offset + 4] != b'\x00\x01\x00\x01':
        raise ValueError('DNS 问题不匹配')
    offset += 4
    records = []
    for _ in range(answers):
        owner, offset = dns_name(packet, offset)
        kind, cls, ttl, length = struct.unpack_from('!HHIH', packet, offset)
        offset += 10
        if offset + length > len(packet):
            raise ValueError('DNS 记录损坏')
        value = None
        if cls == 1 and kind == 1 and length == 4:
            value = socket.inet_ntoa(packet[offset:offset + 4])
        elif cls == 1 and kind == 5:
            value, _ = dns_name(packet, offset)
        records.append((owner, kind, value))
        offset += length
    names = {hostname}
    for _ in records:
        names.update(value for owner, kind, value in records if owner in names and kind == 5 and value)
    ips = list(dict.fromkeys(value for owner, kind, value in records
                            if owner in names and kind == 1 and value and usable_ipv4(value)))
    if not ips:
        raise ValueError('DNS 未返回真实 IPv4 地址（可能为代理虚拟地址）')
    return ips


class BoundTransport:
    def __init__(self, adapter, address, timeout=4):
        self.adapter = adapter
        self.address = address
        self.timeout = timeout
        self.cache = {}

    def new_socket(self, kind, timeout):
        sock = socket.socket(socket.AF_INET, kind)
        try:
            sock.settimeout(timeout)
            # IP_UNICAST_IF is a network-byte-order interface index on Windows.
            sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', self.adapter['index']))
            sock.bind((self.address, 0))
            return sock
        except Exception:
            sock.close()
            raise

    def resolve(self, hostname):
        try:
            return [str(ipaddress.IPv4Address(hostname))]
        except ValueError:
            pass
        hostname = hostname.rstrip('.').encode('idna').decode('ascii').lower()
        cached = self.cache.get(hostname)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        transaction = secrets.randbits(16)
        question = b''.join(bytes([len(part)]) + part.encode('ascii') for part in hostname.split('.'))
        packet = struct.pack('!6H', transaction, 0x0100, 1, 0, 0, 0) + question + b'\x00\x00\x01\x00\x01'
        failures = []
        for server in self.adapter.get('dns_servers', []):
            if not usable_ipv4(server):
                continue
            try:
                with self.new_socket(socket.SOCK_DGRAM, self.timeout) as sock:
                    sock.connect((server, 53))
                    sock.send(packet)
                    response = sock.recv(4096)
                values = dns_answers(response, transaction, hostname)
                self.cache[hostname] = (time.monotonic() + 30, values)
                return values
            except (OSError, ValueError, struct.error) as error:
                failures.append('%s: %s' % (server, error))
        raise OSError('网卡 %s 的 DNS 无法解析 %s：%s' % (
            self.adapter['name'], hostname, '; '.join(failures) or '未配置可用 DNS'))

    def connect(self, hostname, port, timeout):
        timeout = self.timeout if timeout is socket._GLOBAL_DEFAULT_TIMEOUT or timeout is None else timeout
        errors = []
        for address in self.resolve(hostname):
            sock = self.new_socket(socket.SOCK_STREAM, timeout)
            try:
                sock.connect((address, port))
                return sock
            except OSError as error:
                sock.close()
                errors.append(str(error))
        raise OSError('校园网卡 %s 直连 %s:%s 失败：%s' % (
            self.adapter['name'], hostname, port, '; '.join(errors)))

    def handlers(self, context=None):
        transport = self

        class HTTPConnection(http.client.HTTPConnection):
            def connect(self):
                self.sock = transport.connect(self.host, self.port, self.timeout)

        class HTTPSConnection(http.client.HTTPSConnection):
            def connect(self):
                sock = transport.connect(self.host, self.port, self.timeout)
                try:
                    self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                except Exception:
                    sock.close()
                    raise

        class HTTPHandler(urllib.request.HTTPHandler):
            def http_open(self, request):
                return self.do_open(HTTPConnection, request)

        class HTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self, request):
                return self.do_open(HTTPSConnection, request, context=self._context)

        return [HTTPHandler(), HTTPSHandler(context=context)]

    def probe(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             *self.handlers(ssl.create_default_context()))
        # A valid campus session is stronger evidence than public internet reachability.
        with opener.open('https://auth1.ysu.edu.cn/', timeout=self.timeout) as response:
            url = urllib.parse.urlsplit(response.geturl())
            params = urllib.parse.parse_qs(url.query)
            if url.hostname != 'auth1.ysu.edu.cn' or not params.get('sessionId'):
                raise OSError('认证入口可达，但未获得本机校园 sessionId')
        return {'portal_reachable': True, 'campus_session_available': True,
                'auth1_addresses': self.resolve('auth1.ysu.edu.cn')}


def inspect_network(config=None, scan=None):
    config = load_config() if config is None else config
    report = {'ok': False, 'proxy_bypassed': True, 'physical_interface_bound': False,
              'adapters': [], 'wifi': None, 'selected': None}
    if sys.platform != 'win32':
        report['message'] = '网卡绑定检测仅适用于 Windows；Linux 使用本机直连请求'
        return report, None
    should_scan = config['scan_wifi'] if scan is None else scan
    if should_scan:
        try:
            from wifi_scan import scan_wifi
            report['wifi'] = scan_wifi()
            report['preferred_wifi_visible'] = any(
                item['ssid'].casefold() == config['preferred_ssid'].casefold()
                for interface in report['wifi']['interfaces'] if interface['fresh_scan_confirmed']
                for item in interface['networks'])
        except Exception as error:
            # Wi-Fi diagnostics must not prevent a working wired connection.
            report['wifi'] = {'error': str(error)}
    adapters = adapter_inventory()
    candidates = []
    for adapter in adapters:
        detail = dict(adapter, checked=False, campus_direct=False)
        report['adapters'].append(detail)
        if not adapter['up'] or (config['interface'] != 'auto' and
                                adapter['name'] != config['interface']):
            continue
        for address in adapter.get('addresses', []):
            if usable_ipv4(address):
                candidates.append((detail, BoundTransport(adapter, address, config['timeout'])))

    def check_candidate(pair):
        detail, transport = pair
        try:
            return pair, transport.probe(), None
        except Exception as error:
            return pair, None, str(error)

    successes = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(len(candidates), 4))) as pool:
        for (detail, transport), probe, error in pool.map(check_candidate, candidates):
            detail['checked'] = True
            if error:
                detail.setdefault('errors', []).append(error)
            else:
                detail.update(probe, campus_direct=True, source_ip=transport.address)
                successes.append(transport)
    if successes:
        successes.sort(key=lambda item: (item.adapter['kind'] != 'ethernet', item.adapter['index']))
        transport = successes[0]
        report.update(ok=True, physical_interface_bound=True,
                      selected={'name': transport.adapter['name'], 'kind': transport.adapter['kind'],
                                'index': transport.adapter['index'], 'source_ip': transport.address,
                                'dns_servers': transport.adapter['dns_servers']},
                      message='校园网卡、DNS 和认证入口直连检查通过')
        return report, transport
    report['message'] = '未找到可直连校园认证的物理网卡；请连接 iYanDa 或校园网线，并检查网卡 DNS / 代理 TUN 过滤'
    return report, None


def print_report(report):
    print(report['message'])
    if report.get('selected'):
        selected = report['selected']
        print('认证出口：%s；本机 IP：%s；DNS：%s' % (
            selected['name'], selected['source_ip'], ', '.join(selected['dns_servers'])))
    for adapter in report['adapters']:
        print('%s [%s]：%s；校园直连：%s' % (
            adapter['name'], adapter['kind'], '已接入' if adapter['up'] else '未连接',
            '通过' if adapter['campus_direct'] else '未通过' if adapter['checked'] else '未检测'))
        for error in adapter.get('errors', []):
            print('  ' + error)
    wifi = report.get('wifi') or {}
    if wifi.get('error'):
        print('Wi-Fi 扫描：' + wifi['error'])
    for interface in wifi.get('interfaces', []):
        print('Wi-Fi 主动扫描：' + interface['scan_status'])
        for network in interface['networks']:
            print('  %s | %s%% | %s' % (network['ssid'] or '(隐藏 SSID)',
                                       network['signal_percent'], '已连接' if network['connected'] else '未连接'))
