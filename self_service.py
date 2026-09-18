"""YSU self-service commands, matched to the school's self frontend.

Keep current sessions, completed history and default service configuration separate.
No endpoint retry is performed for mutations.
"""
import argparse
import datetime
import getpass
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

from netlogin import AUTH1_HOST, get_header, response_code, load_account_credentials

BASE = '/sam/api/userself/'
COMMANDS = ('account-devices', 'account-history', 'device-offline', 'nosense-config',
            'nosense-enable', 'nosense-register', 'nosense-disable',
            'account-traffic', 'account-menu', 'account-login')


def flow_gb(value):
    """Readable approximation of a school GB/MB description; never guess blanks."""
    if not isinstance(value, str) or not value.strip():
        return None
    token = r'(\d+(?:\.\d+)?)\s*(TB|GB|MB|KB|B)'
    matches = list(re.finditer(token, value, re.I))
    if not matches or re.sub(token, '', value, flags=re.I).strip():
        return None
    powers = {'TB': 1, 'GB': 0, 'MB': -1, 'KB': -2, 'B': -3}
    try:
        total = sum(Decimal(m[1]) * Decimal(1024) ** powers[m[2].upper()] for m in matches)
        return float(total)
    except (InvalidOperation, OverflowError):
        return None


def traffic_summary(overview, flow):
    if not isinstance(overview, dict) or not isinstance(flow, dict):
        raise ValueError('学校未返回有效套餐流量数据；不能将其当作剩余 0 GB')
    policy = overview.get('policyInfo') or {}
    period = overview.get('periodInfo') or {}
    rows = []

    def collect(node, path, scope):
        if not isinstance(node, dict):
            return
        scope = node.get('netserviceName') or node.get('serviceName') or scope
        if 'totalFreeValueDesc' in node or 'leftFreeValueDesc' in node:
            total_text, left_text = node.get('totalFreeValueDesc'), node.get('leftFreeValueDesc')
            total, left = flow_gb(total_text), flow_gb(left_text)
            rows.append({'name': node.get('name') or '流量额度', 'scope': scope,
                         'sourcePath': path, 'totalText': total_text, 'remainingText': left_text,
                         'totalGBApprox': total, 'remainingGBApprox': left,
                         'usedGBApprox': total - left if total is not None and left is not None and total >= left else None,
                         'usedPercent': node.get('usingPercentages')})
        for key in ('availableFreeItems', 'chargeItems', 'netserviceDetailDescs'):
            children = node.get(key)
            if isinstance(children, list):
                for index, child in enumerate(children):
                    collect(child, '%s.%s[%s]' % (path, key, index), scope)

    collect(flow, 'package/flow', '学校账号套餐（未按运营商拆分）')
    return {'packageName': policy.get('packageName') or '未知套餐',
            'billingRule': policy.get('periodAndTimeOrFlowDetail') or '',
            'period': period.get('dateRangeDesc') or '周期未返回',
            'usedText': (overview.get('usedInfo') or {}).get('usedFreeDesc') or '',
            'items': rows, 'remainingKnown': any(r['remainingGBApprox'] is not None for r in rows),
            'conversion': '近似 GB 按 1 GB = 1024 MB 换算，学校原始描述为准；不同额度不相加。',
            'operatorTraffic': {'availability': 'not-returned',
                                'message': '学校接口未提供移动、联通、电信各自独立套餐的剩余流量；不能用学校 60GB 额度代替。'},
            'rawFlow': flow}


def mac_address(value):
    value = re.sub(r'[-:.]', '', value).upper()
    if not re.fullmatch(r'[0-9A-F]{12}', value) or int(value[:2], 16) & 1 or int(value, 16) == 0:
        raise ValueError('需要有效的单播设备 MAC 地址')
    return '-'.join(value[i:i + 2] for i in range(0, 12, 2))


def timestamp(value):
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return value
    return int(datetime.datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(
        tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp() * 1000)


def time_text(value):
    if not isinstance(value, (int, float)):
        return value or '未知'
    return datetime.datetime.fromtimestamp(value / 1000, datetime.timezone(
        datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


class SelfService:
    def __init__(self, client, user, password):
        self.client = client
        self.user = user
        ok, info, self.openers = client._cas_login_only(user, password)
        if not ok:
            raise ValueError('账号查询会话建立失败：' + info.get('message', '未知错误'))
        # Accept is required: without it the gateway loops through /login.
        headers = dict(client.header, Accept='text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
        url = AUTH1_HOST + '/self/my-devices?mode=online'
        for _ in range(16):
            parsed = urlsplit(url)
            # The deployed CAS emits one HTTP URL which immediately upgrades to
            # HTTPS. Upgrade locally so the ticket never travels over HTTP.
            if parsed.scheme == 'http' and parsed.netloc == urlsplit(AUTH1_HOST).netloc:
                url = parsed._replace(scheme='https').geturl()
                parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.netloc != urlsplit(AUTH1_HOST).netloc:
                raise ValueError('自助中心返回非预期登录地址，已停止')
            response = client._session_request(self.openers, url, headers=headers,
                                               allow_redirects=False)
            code = response_code(response)
            if code in (301, 302, 303, 307, 308):
                location = get_header(response, 'Location', '')
                if not location:
                    raise ValueError('自助中心跳转缺少地址')
                url = urljoin(url, location)
                continue
            if code == 200 and parsed.path.startswith('/self/'):
                return
            raise ValueError('自助中心未完成登录，请检查账号或学校登录要求')
        raise ValueError('自助中心登录跳转次数过多')

    def request(self, suffix, data=None, method='POST'):
        result = self.client._session_json(self.openers, BASE + suffix, data=data or {}, method=method)
        if result.get('code') != 200 or str(result.get('message', '')).lower() != 'ok':
            raise ValueError('自助中心接口失败：' + str(result.get('message') or result.get('code') or '未返回 JSON'))
        return result.get('data')

    def traffic(self):
        overview = self.request('package/overview', method='GET')
        flow = self.request('package/flow', method='GET')
        result = traffic_summary(overview, flow)
        result.update(ok=True, account=self.user, changed=False,
                      queriedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
        result['note'] = '额度来自学校计费快照，可能延迟更新；切换运营商不会重置学校套餐额度。'
        try:
            summary = self.client.current_status().get('summary') or {}
            result['currentSession'] = {k: summary.get(k) for k in ('online', 'userId', 'userIp', 'service')}
            result['currentSession']['matchesQueriedAccount'] = self.client._normalized_account(
                summary.get('userId')) == self.client._normalized_account(self.user)
        except Exception:
            result['currentSession'] = None
            result['warnings'] = ['当前出口状态查询失败，以上额度仍属于所查账号']
        return result

    def login_and_traffic(self, password, service_type):
        if service_type not in self.client.services:
            raise ValueError('必须明确选择 0、1、2 或 3，不会默认回退校园网')
        ok, message = self.client.ensure_login(self.user, password, service_type)
        result = {'ok': bool(ok), 'account': self.user, 'requestedService': self.client.services[service_type],
                  'loginVerified': bool(ok), 'message': message, 'traffic': None}
        if ok:
            try:
                # Login may invalidate an existing self-service cookie session.
                result['traffic'] = SelfService(self.client, self.user, password).traffic()
            except Exception:
                result['warnings'] = ['指定服务已登录并核实，但流量查询失败；稍后执行 account-traffic 重查。']
        return result

    def menu(self, password):
        print('账号验证通过：%s' % self.user)
        print('服务选择只改变本机当前校园连接。输入账号密码本身不会切换运营商。')
        try:
            print_result(self.traffic())
        except Exception:
            print('暂时无法查询流量，可稍后输入 t 重试；未改变当前连接。')
        while True:
            print('\n0 校园网 | 1 中国移动 | 2 中国联通 | 3 中国电信 | t 查询流量 | q 退出')
            try:
                choice = input('请选择：').strip().lower()
            except (EOFError, KeyboardInterrupt):
                print('\n已退出，保留当前连接。')
                return 0
            if choice == 'q':
                return 0
            try:
                if choice in self.client.services:
                    print_result(self.login_and_traffic(password, choice))
                elif choice == 't':
                    print_result(SelfService(self.client, self.user, password).traffic())
                else:
                    print('无效选择；未登录或切换任何服务。')
            except Exception:
                print('请求失败，请检查校园连接后重试；不会尝试其他运营商。')

    def devices(self, expected_service=None):
        data = self.request('devices')
        if not isinstance(data, dict) or not isinstance(data.get('onlineDevices'), list):
            raise ValueError('设备查询未返回有效在线列表，不能判断为全部离线')
        warnings = []
        try:
            defaults = self.request('devices/defaultServicesList') or []
        except Exception:
            defaults = []
            warnings.append('缺省服务名称查询失败，不影响在线设备列表')
        names = {item['value']: item.get('label', '') for item in defaults if isinstance(item, dict) and 'value' in item}
        try:
            current = self.client.current_status().get('summary') or {}
        except Exception:
            current = {}
            warnings.append('当前出口会话读取失败；设备在线状态仍来自自助中心')
        rows = []
        for key, online in [('onlineDevices', True), ('offlineDevices', False)]:
            items = self.client._brief_devices(data.get(key) or [], online=online)
            if online:
                self.client._enrich_current_device(items, current, self.user)
            for item in items:
                item['defaultService'] = names.get(item['defaultServiceId'], '')
                item['serviceKnown'] = bool(item.get('service'))
                if expected_service is not None:
                    item['serviceMatch'] = ('offline' if not online else
                                            'unknown' if not item['serviceKnown'] else
                                            'match' if item['service'] == expected_service else 'mismatch')
            rows.extend(items)
        return {'ok': True, 'account': self.user, 'changed': False,
                'onlineDeviceCount': len(data['onlineDevices']),
                'offlineDeviceCount': len(data.get('offlineDevices') or []),
                'expectedService': expected_service, 'devices': rows, 'warnings': warnings,
                'note': '在线状态来自学校设备列表；实际服务未知不表示离线。缺省服务和历史服务不代表当前服务。'}

    def history(self, page=1, size=20, start=None, end=None, ip=None):
        if page < 1 or not 1 <= size <= 200:
            raise ValueError('页码必须大于 0，每页条数为 1–200')
        start, end = timestamp(start), timestamp(end)
        if start is not None and end is not None and not 0 <= end - start <= 92 * 86400000:
            raise ValueError('时间顺序错误或范围超过学校页面的 92 天限制')
        payload = {'pageIndex': page, 'pageSize': size}
        if start is not None:
            payload['loginTime'] = start
        if end is not None:
            payload['logoutTime'] = end
        data = self.request('business/internet/records', payload)
        if not isinstance(data, dict) or not isinstance(data.get('results'), list):
            raise ValueError('历史查询未返回有效记录列表')
        rows = []
        for raw in data['results']:
            if ip and ip not in (raw.get('userIpv4'), raw.get('userIpv6')):
                continue
            rows.append({'recordId': raw.get('onlineDetailUuid'), 'ip': raw.get('userIpv4') or raw.get('userIpv6'),
                         'mac': raw.get('userMac'), 'deviceName': raw.get('selfLoginDeviceName'),
                         'loginTime': raw.get('loginTime'), 'logoutTime': raw.get('logoutTime'),
                         'service': raw.get('serviceSuffix') or '', 'serviceSource': 'history-record',
                         'recordState': 'completed' if raw.get('logoutTime') else 'logout-not-recorded',
                         'onlineDuration': raw.get('onlineTime'), 'terminateCause': raw.get('terminateCause')})
        return {'ok': True, 'account': self.user, 'changed': False, 'pageIndex': page,
                'pageSize': size, 'total': data.get('total'), 'records': rows,
                'ipFilterScope': 'current-page' if ip else None,
                'note': '历史服务仅适用于这次历史会话，不能据此判断设备现在在线或正在使用该服务。'}

    def action(self, action, uuids=None, all_devices=False, mac=None, expire=None, dry_run=False):
        if action in ('nosense-enable', 'nosense-register'):
            address = mac_address(mac or '')
            if action == 'nosense-enable':
                devices = self.request('devices') or {}
                online = devices.get('onlineDevices') or []
                if not any(re.sub(r'[-:.]', '', str(item.get('userMac') or '')).upper() == address.replace('-', '') for item in online):
                    raise ValueError('开启无感需要本账号当前在线设备的 MAC；手工登记使用 nosense-register')
            payload = {'userMac': address}
            if action == 'nosense-register':
                config = self.request('package/duration') or {}
                unit, limit = config.get('unit'), config.get('availableDuration')
                if unit not in ('minute', 'hour', 'day', 'month', 'year', 'daily') or not isinstance(limit, (int, float)):
                    raise ValueError('未取得可验证的无感有效期配置')
                if expire is not None and (unit == 'daily' or limit <= 0 or not 0 < expire <= limit):
                    raise ValueError('有效期须在学校上限内；每日到期或长期有效配置不能指定天数')
                payload.update(expireTime=expire if expire is not None else limit, expireUnit=unit)
            suffix = 'nosense/register'
        else:
            if bool(uuids) == bool(all_devices):
                raise ValueError('必须指定一个或多个 --uuid，或明确使用 --all')
            data = self.request('devices')
            if not isinstance(data, dict) or not isinstance(data.get('onlineDevices'), list):
                raise ValueError('设备列表无效，已停止操作')
            rows = data['onlineDevices']
            if action == 'nosense-disable':
                rows = rows + (data.get('offlineDevices') or [])
                field, key, suffix = 'noSenseUuid', 'noSenseUuids', 'nosense/unbind/batch'
                rows = [item for item in rows if item.get('bindNoSense')]
            elif action == 'device-offline':
                field, key, suffix = 'onlineUserUuid', 'onlineUserUuids', 'devices/kick-offline/batch'
            else:
                raise ValueError('未知设备操作')
            available = {item[field] for item in rows if item.get(field)}
            targets = available if all_devices else set(uuids)
            if not targets or not targets <= available:
                raise ValueError('目标为空、已失效或不属于当前账号；请重新查询设备 UUID')
            payload = {key: sorted(targets)}
        result = {'ok': True, 'account': self.user, 'action': action, 'changed': False,
                  'dryRun': dry_run, 'request': {'path': BASE + suffix, 'data': payload}}
        if not dry_run:
            result['response'] = self.request(suffix, payload)
            result['changed'] = True
            result['note'] = '学校接口已接受操作；使用 account-devices 再次查询设备状态。'
        return result


def main(client, argv):
    parser = argparse.ArgumentParser(prog='netlogin.py ' + argv[0], description='学校自助中心：账号、服务选择、设备、流量与历史')
    parser.add_argument('username', nargs='?')
    parser.add_argument('password', nargs='?', help='省略时交互输入；也可使用 --stdin 或账号文件')
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument('--accounts-file', '--account-file')
    auth.add_argument('--stdin', action='store_true', help='从标准输入读取 username/password JSON')
    parser.add_argument('--account-name', '--name', default='')
    parser.add_argument('--json', action='store_true')
    command = argv[0]
    if command == 'account-login':
        parser.add_argument('--service', choices=sorted(client.services), required=True, help='明确选择 0 校园网、1 移动、2 联通、3 电信')
    elif command == 'account-devices':
        parser.add_argument('--service', choices=sorted(client.services), help='核对指定运营商；仍列出未知设备')
    elif command == 'account-history':
        parser.add_argument('--page', type=int, default=1)
        parser.add_argument('--page-size', type=int, default=20)
        parser.add_argument('--start', help='北京时间 YYYY-MM-DD HH:MM:SS，登录时间下界')
        parser.add_argument('--end', help='北京时间 YYYY-MM-DD HH:MM:SS，登出时间上界')
        parser.add_argument('--ip', help='仅筛选本页返回的 IP，翻页需 --page')
    elif command in ('device-offline', 'nosense-disable'):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--uuid', action='append', help='可重复；下线用 onlineUserUuid，关闭无感用 noSenseUuid')
        group.add_argument('--all', action='store_true', dest='all_devices')
        parser.add_argument('--dry-run', action='store_true', help='只预览请求，不改变设备')
    elif command in ('nosense-enable', 'nosense-register'):
        parser.add_argument('--mac', required=True)
        if command == 'nosense-register':
            parser.add_argument('--expire', type=int, help='有效期数值，单位取自 nosense-config；省略使用学校上限')
        parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv[1:])
    if command == 'account-menu' and (args.stdin or args.json):
        parser.error('交互菜单不支持 --stdin / --json；自动调用请用 account-login 或 account-traffic')
    try:
        if (args.stdin or args.accounts_file) and (args.username or args.password):
            raise ValueError('账号文件/标准输入不能与位置账号密码混用')
        if args.stdin:
            payload = json.load(sys.stdin)
            user, password = payload.get('username', ''), payload.get('password', '')
        elif args.accounts_file:
            user, password = load_account_credentials(args.accounts_file, args.account_name)
        else:
            user = args.username or ''
            if not user and command == 'account-menu':
                user = input('校园账号：').strip()
            if not user:
                raise ValueError('请指定账号，或使用 --accounts-file / --stdin')
            password = args.password if args.password is not None else getpass.getpass('账号密码：')
        service = SelfService(client, user, password)
        if command == 'account-menu':
            return service.menu(password)
        if command == 'account-login':
            result = service.login_and_traffic(password, args.service)
        elif command == 'account-traffic':
            result = service.traffic()
        elif command == 'account-devices':
            result = service.devices(client.services.get(args.service))
        elif command == 'account-history':
            result = service.history(args.page, args.page_size, args.start, args.end, args.ip)
        elif command == 'nosense-config':
            config = dict(service.request('package/duration') or {})
            config.pop('userId', None)
            result = {'ok': True, 'changed': False, 'config': config}
        else:
            result = service.action(command, getattr(args, 'uuid', None), getattr(args, 'all_devices', False),
                                    getattr(args, 'mac', None), getattr(args, 'expire', None), args.dry_run)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_result(result)
        return 0 if result.get('ok') else 1
    except Exception as error:
        # Do not print transport URLs which can contain OAuth tickets or tokens.
        message = str(error) if isinstance(error, ValueError) else '自助中心请求失败（%s）；请检查校园直连和账号会话' % type(error).__name__
        print(json.dumps({'ok': False, 'message': message}, ensure_ascii=False))
        return 1


def print_result(result):
    if 'loginVerified' in result:
        print('指定服务：%s；%s' % (result['requestedService'], '登录已核实' if result['loginVerified'] else '登录失败或未核实'))
        print(result['message'])
        if result.get('traffic'):
            print_result(result['traffic'])
    elif 'items' in result:
        print('学校账号：%s | 套餐：%s | 周期：%s' % (result['account'], result['packageName'], result['period']))
        current = result.get('currentSession')
        if current:
            print('本机当前账号：%s | 实际服务：%s | 状态：%s' % (
                current.get('userId') or '未知', current.get('service') or '未知',
                '在线' if current.get('online') else '离线/未核实'))
            if not current.get('matchesQueriedAccount'):
                print('本机当前账号与查询账号不同；下面额度属于查询账号。')
        for row in result['items']:
            value = row['remainingGBApprox']
            print('%s [%s]：剩余 %s%s / 总额 %s' % (
                row['name'], row['scope'], row['remainingText'] or '未知',
                '（约 %.2f GB）' % value if value is not None else '', row['totalText'] or '未知'))
            if row['usedGBApprox'] is not None:
                print('  已用约 %.2f GB' % row['usedGBApprox'])
        if not result['items']:
            print('学校未返回可识别的流量额度，不表示剩余 0 GB 或无限流量。')
        print(result['conversion'])
        print(result['operatorTraffic']['message'])
    elif 'devices' in result:
        print('在线：%s 台；离线登记：%s 台' % (result['onlineDeviceCount'], result['offlineDeviceCount']))
        for item in result['devices']:
            print('%s | %s | %s | 实际服务=%s | 缺省服务=%s' % (
                '在线' if item['online'] else '离线', item['ip'], item['deviceName'] or '未命名',
                item['service'] or '未知（接口未返回）', item['defaultService'] or '未设置/未知'))
            print('  MAC=%s | 主机名=%s | 无感=%s | 核对=%s' % (
                item['mac'] or '未知', item['hostname'] or '未知', bool(item['bindNoSense']), item.get('serviceMatch', '未指定服务')))
            print('  onlineUserUuid=%s | noSenseUuid=%s' % (item['onlineUserUuid'], item['noSenseUuid']))
    elif 'records' in result:
        print('历史共 %s 条；第 %s 页（每页 %s 条）' % (result['total'], result['pageIndex'], result['pageSize']))
        for item in result['records']:
            print('%s | %s → %s | 当次服务=%s | %s' % (item['ip'], time_text(item['loginTime']),
                  time_text(item['logoutTime']), item['service'] or '未知', item['terminateCause'] or ''))
        if result.get('ipFilterScope'):
            print('IP 筛选仅作用于本页；查看更多请调整 --page / --page-size。')
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('note'):
        print(result['note'])
    for warning in result.get('warnings', []):
        print('提示：' + warning)
