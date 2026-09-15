"""Actively scan Windows Wi-Fi without connecting or changing network settings."""
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime
import json
import sys
import threading


class GUID(C.Structure):
    _fields_ = [('data1', W.DWORD), ('data2', W.WORD), ('data3', W.WORD),
                ('data4', W.BYTE * 8)]


class Interface(C.Structure):
    _fields_ = [('guid', GUID), ('description', W.WCHAR * 256), ('state', W.DWORD)]


class SSID(C.Structure):
    _fields_ = [('length', W.ULONG), ('value', W.BYTE * 32)]


class Network(C.Structure):
    _fields_ = [
        ('profile', W.WCHAR * 256), ('ssid', SSID), ('bss_type', W.DWORD),
        ('bssid_count', W.ULONG), ('connectable', W.BOOL), ('reason', W.DWORD),
        ('phy_count', W.ULONG), ('phy_types', W.DWORD * 8), ('more_phy', W.BOOL),
        ('signal', W.ULONG), ('secure', W.BOOL), ('auth', W.DWORD),
        ('cipher', W.DWORD), ('flags', W.DWORD), ('reserved', W.DWORD),
    ]


class Notification(C.Structure):
    _fields_ = [('source', W.DWORD), ('code', W.DWORD), ('guid', GUID),
                ('size', W.DWORD), ('data', C.c_void_p)]


def check(code, operation):
    if code:
        detail = C.FormatError(code).strip()
        if code == 5:
            detail += '；请检查 Windows 定位服务及桌面应用的位置访问权限'
        raise OSError(code, '%s: %s' % (operation, detail))


def copy_list(pointer, item_type):
    count = C.cast(pointer, C.POINTER(W.DWORD))[0]
    start = pointer.value + 2 * C.sizeof(W.DWORD)
    return [item_type.from_buffer_copy(C.string_at(
        start + index * C.sizeof(item_type), C.sizeof(item_type)))
        for index in range(count)]


def decode_ssid(raw):
    for encoding in ('utf-8', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode('utf-8', errors='backslashreplace')


def scan_wifi(timeout=6):
    if sys.platform != 'win32':
        raise RuntimeError('此扫描程序需要 Windows')
    api = C.WinDLL('wlanapi.dll')
    callback_type = C.WINFUNCTYPE(None, C.POINTER(Notification), C.c_void_p)
    signatures = {
        'WlanOpenHandle': ([W.DWORD, C.c_void_p, C.POINTER(W.DWORD), C.POINTER(W.HANDLE)], W.DWORD),
        'WlanEnumInterfaces': ([W.HANDLE, C.c_void_p, C.POINTER(C.c_void_p)], W.DWORD),
        'WlanRegisterNotification': ([W.HANDLE, W.DWORD, W.BOOL, callback_type,
                                      C.c_void_p, C.c_void_p, C.POINTER(W.DWORD)], W.DWORD),
        'WlanScan': ([W.HANDLE, C.POINTER(GUID), C.c_void_p, C.c_void_p, C.c_void_p], W.DWORD),
        'WlanGetAvailableNetworkList': ([W.HANDLE, C.POINTER(GUID), W.DWORD,
                                         C.c_void_p, C.POINTER(C.c_void_p)], W.DWORD),
        'WlanFreeMemory': ([C.c_void_p], None),
        'WlanCloseHandle': ([W.HANDLE, C.c_void_p], W.DWORD),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes = args
        function.restype = result

    handle, negotiated = W.HANDLE(), W.DWORD()
    check(api.WlanOpenHandle(2, None, C.byref(negotiated), C.byref(handle)), 'WlanOpenHandle')
    pending = {}

    @callback_type
    def callback(pointer, context):
        note = pointer.contents
        entry = pending.get(bytes(note.guid))
        if entry is not None and note.source == 8 and note.code in (7, 8):
            entry['state'] = 'completed' if note.code == 7 else 'failed'
            if note.code == 8 and note.data and note.size >= C.sizeof(W.DWORD):
                entry['reason'] = C.cast(note.data, C.POINTER(W.DWORD))[0]
            entry['event'].set()

    registered = False
    try:
        pointer = C.c_void_p()
        check(api.WlanEnumInterfaces(handle, None, C.byref(pointer)), 'WlanEnumInterfaces')
        try:
            interfaces = copy_list(pointer, Interface)
        finally:
            api.WlanFreeMemory(pointer)
        previous = W.DWORD()
        check(api.WlanRegisterNotification(handle, 8, False, callback, None, None,
                                          C.byref(previous)), 'WlanRegisterNotification')
        registered = True
        results = []
        for interface in interfaces:
            entry = {'event': threading.Event(), 'state': 'timeout'}
            pending[bytes(interface.guid)] = entry
            result = {'adapter': interface.description, 'networks': []}
            try:
                check(api.WlanScan(handle, C.byref(interface.guid), None, None, None), 'WlanScan')
                entry['event'].wait(timeout)
                result['scan_status'] = entry['state']
                result['fresh_scan_confirmed'] = entry['state'] == 'completed'
                if 'reason' in entry:
                    result['scan_failure_reason'] = entry['reason']
                pointer = C.c_void_p()
                check(api.WlanGetAvailableNetworkList(handle, C.byref(interface.guid),
                                                     0, None, C.byref(pointer)),
                      'WlanGetAvailableNetworkList')
                try:
                    networks = copy_list(pointer, Network)
                finally:
                    api.WlanFreeMemory(pointer)
                unique = {}
                for network in networks:
                    if not network.bssid_count:
                        continue
                    raw = bytes(network.ssid.value[:min(network.ssid.length, 32)])
                    key = (raw, network.auth, network.cipher, network.bss_type)
                    item = {
                        'ssid': decode_ssid(raw), 'signal_percent': network.signal,
                        'connected': bool(network.flags & 1),
                        'connectable': bool(network.connectable),
                        'security_enabled': bool(network.secure),
                        'access_point_count': network.bssid_count,
                    }
                    if key in unique:
                        item['connected'] |= unique[key]['connected']
                        item['signal_percent'] = max(item['signal_percent'], unique[key]['signal_percent'])
                    unique[key] = item
                result['networks'] = sorted(unique.values(), key=lambda item:
                                             (not item['connected'], -item['signal_percent'], item['ssid']))
            except OSError as error:
                result.update(scan_status='error', fresh_scan_confirmed=False, error=str(error))
            results.append(result)
        return {'scanned_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                'method': 'Windows Native Wi-Fi: WlanScan + WlanGetAvailableNetworkList',
                'interfaces': results}
    finally:
        if registered:
            api.WlanRegisterNotification(handle, 0, False, callback_type(), None, None, None)
        api.WlanCloseHandle(handle, None)


def main():
    parser = argparse.ArgumentParser(description='主动扫描附近 Wi-Fi，不切换当前连接')
    parser.add_argument('--json', action='store_true', help='输出完整 JSON')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    try:
        result = scan_wifi()
    except (OSError, RuntimeError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('扫描时间：' + result['scanned_at'])
        for interface in result['interfaces']:
            print('\n网卡：' + interface['adapter'])
            print('主动扫描：' + interface['scan_status'])
            if not interface['fresh_scan_confirmed']:
                print('未确认扫描完成，列表可能为缓存。' + interface.get('error', ''))
            for item in interface['networks']:
                print('%s | 信号 %s%% | %s | %s' % (
                    item['ssid'] or '(隐藏 SSID)', item['signal_percent'],
                    '已连接' if item['connected'] else '未连接',
                    '加密' if item['security_enabled'] else '开放'))
    return 0 if result['interfaces'] and all(
        item['fresh_scan_confirmed'] for item in result['interfaces']) else 1


if __name__ == '__main__':
    sys.exit(main())
