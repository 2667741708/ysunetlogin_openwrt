#!/bin/python
# coding:utf-8
import re
import json
import os
import sys
import base64
import binascii
import subprocess
import time
import ssl
import io
import socket


if sys.version_info < (3, 0):
    VERSION = 2
    import urllib
    import urllib2
    import urlparse
    import cookielib
    HTTPError = urllib2.HTTPError
    URLError = urllib2.URLError
else:
    VERSION = 3
    import urllib.request
    import urllib.parse
    import urllib.error
    import http.cookiejar
    HTTPError = urllib.error.HTTPError
    URLError = urllib.error.URLError

DEFAULT_TIMEOUT = 8
AUTH1_HOST = 'https://auth1.ysu.edu.cn'
REDIRECT_CODES = (301, 302, 303, 307, 308)


class NoRedirectHandler(
        urllib.request.HTTPRedirectHandler if VERSION == 3
        else urllib2.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def direct_proxy_handler():
    '''Disable OS, PAC and environment proxies for campus-network traffic.'''
    if VERSION == 3:
        return urllib.request.ProxyHandler({})
    return urllib2.ProxyHandler({})


def build_direct_opener(*handlers):
    all_handlers = [direct_proxy_handler()]
    all_handlers.extend(handlers)
    if VERSION == 3:
        return urllib.request.build_opener(*all_handlers)
    return urllib2.build_opener(*all_handlers)


def configured_proxy_schemes():
    try:
        proxies = urllib.request.getproxies() if VERSION == 3 else urllib.getproxies()
    except Exception:
        proxies = {}
    return sorted(text_value(key) for key, value in proxies.items() if value)


def urlencode(data):
    if VERSION == 2:
        data = utf8_form_data(data)
    if VERSION == 3:
        return urllib.parse.urlencode(data)
    return urllib.urlencode(data)


def urljoin(base, url):
    if VERSION == 3:
        return urllib.parse.urljoin(base, url)
    return urlparse.urljoin(base, url)


def unquote_plus(value):
    if VERSION == 3:
        return urllib.parse.unquote_plus(value)
    return urllib.unquote_plus(value)


def quote(value, safe=''):
    if value is None:
        value = ''
    if VERSION == 3:
        return urllib.parse.quote(str(value), safe=safe)
    if not isinstance(value, (str, unicode)):
        value = str(value)
    if isinstance(value, unicode):
        value = value.encode('utf-8')
    return urllib.quote(value, safe=safe)


def urlsplit(url):
    if VERSION == 3:
        return urllib.parse.urlsplit(url)
    return urlparse.urlsplit(url)


def urlunsplit(parts):
    if VERSION == 3:
        return urllib.parse.urlunsplit(parts)
    return urlparse.urlunsplit(parts)


def parse_qsl(query, keep_blank_values=True):
    if VERSION == 3:
        return urllib.parse.parse_qsl(query, keep_blank_values=keep_blank_values)
    return urlparse.parse_qsl(query, keep_blank_values=keep_blank_values)


def utf8_bytes(value):
    if VERSION == 3:
        if isinstance(value, bytes):
            return value
        if not isinstance(value, str):
            value = str(value)
        return value.encode('utf-8')
    if isinstance(value, unicode):
        return value.encode('utf-8')
    if not isinstance(value, str):
        return str(value)
    return value


def text_value(value):
    if value is None:
        return ''
    if VERSION == 3:
        return str(value)
    if isinstance(value, unicode):
        return value
    try:
        return str(value).decode('utf-8')
    except Exception:
        return str(value).decode('utf-8', 'replace')


def utf8_form_data(data):
    def convert(value):
        if isinstance(value, unicode):
            return value.encode('utf-8')
        return value

    if hasattr(data, 'items'):
        return dict((convert(k), convert(v)) for k, v in data.items())
    return [(convert(k), convert(v)) for k, v in data]


def get_header(response, name, default=None):
    try:
        return response.headers.get(name, default)
    except AttributeError:
        return response.info().get(name, default)


def response_code(response):
    if hasattr(response, 'code'):
        return response.code
    return response.getcode()


def read_text(response):
    body = response.read()
    if VERSION == 3 and isinstance(body, str):
        return body
    if VERSION == 2 and isinstance(body, unicode):
        return body
    for encoding in ('utf-8', 'gbk'):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            pass
    return body.decode('utf-8', 'replace')


def load_account_credentials(path, account_name):
    with io.open(path, 'r', encoding='utf-8-sig') as handle:
        config = json.load(handle)

    candidates = []
    if isinstance(config, dict):
        if isinstance(config.get('accounts'), list):
            candidates.extend(config.get('accounts'))
        elif account_name and isinstance(config.get(account_name), dict):
            item = dict(config.get(account_name))
            item.setdefault('name', account_name)
            candidates.append(item)

    for item in candidates:
        if not isinstance(item, dict):
            continue
        if item.get('name') == account_name:
            user = item.get('username') or item.get('user') or item.get('userId')
            pwd = item.get('password') or item.get('pwd')
            if not user or not pwd:
                raise ValueError('账号配置缺少 username/password 字段')
            return text_value(user), text_value(pwd)

    raise ValueError('未找到账号配置：%s' % account_name)


# 封装post请求
def post(url, headers=None, data=None, timeout=DEFAULT_TIMEOUT, transport=None):
    headers = headers or {}
    data = data or {}
    data = urlencode(data)
    if VERSION == 3:
        data = data.encode('utf-8')
        request = urllib.request.Request(url, headers=headers, data=data)
        response = build_direct_opener(*(transport.handlers() if transport else [])).open(request, timeout=timeout)
    else:
        request = urllib2.Request(url, headers=headers, data=data)
        response = build_direct_opener().open(request, timeout=timeout)

    return response


# 封装get请求
def get(url, headers=None, timeout=DEFAULT_TIMEOUT, allow_redirects=True, transport=None):
    headers = headers or {}
    if VERSION == 3:
        request = urllib.request.Request(url, headers=headers)
        handlers = transport.handlers() if transport else []
        if allow_redirects:
            response = build_direct_opener(*handlers).open(request, timeout=timeout)
        else:
            opener = build_direct_opener(NoRedirectHandler(), *handlers)
            response = opener.open(request, timeout=timeout)
    else:
        request = urllib2.Request(url, headers=headers)
        if allow_redirects:
            response = build_direct_opener().open(request, timeout=timeout)
        else:
            opener = build_direct_opener(NoRedirectHandler())
            response = opener.open(request, timeout=timeout)

    return response


class Netlogin():
    def __init__(self, network_mode='auto'):
        '''
        登陆服务
        0：校园网
        1：中国移动
        2：中国联通
        3：中国电信
        '''
        self.services = {
            '0': '校园网',
            '1': '中国移动',
            '2': '中国联通',
            '3': '中国电信',
        }
        self.portal = 'http://auth.ysu.edu.cn'
        self.url = 'http://auth.ysu.edu.cn/eportal/InterFace.do?method='
        self.header = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.140 Safari/537.36 Edge/17.17134',
            'Accept-Encoding': 'identity'
        }
        self.check_urls = [
            'http://www.baidu.com/',
            'http://www.msftconnecttest.com/connecttest.txt',
            'http://connectivitycheck.gstatic.com/generate_204',
        ]
        self.isLogined = None
        self.alldata = None
        self.queryString = None
        self.network_mode = network_mode
        self.network_report = None
        self._transport = None
        self._transport_ready = False

    def _network_transport(self):
        if sys.platform != 'win32' or self.network_mode == 'system':
            return None
        if not self._transport_ready:
            from campus_network import inspect_network
            self.network_report, self._transport = inspect_network()
            self._transport_ready = True
        if self._transport is None:
            raise OSError(self.network_report.get('message') or '校园网卡直连检查失败')
        return self._transport

    def _get(self, url, **kwargs):
        return get(url, transport=self._network_transport(), **kwargs)

    def _post(self, url, **kwargs):
        return post(url, transport=self._network_transport(), **kwargs)

    def _parse_json(self, response):
        try:
            return json.loads(read_text(response))
        except ValueError:
            return {}

    def _normalize_query_string(self, value):
        if not value:
            return None
        value = value.strip().replace('&amp;', '&')
        if '?' in value:
            value = value.split('?', 1)[1]
        value = value.split('#', 1)[0]
        if 'wlanacname' in value or 'wlanuserip' in value:
            return value
        decoded = unquote_plus(value)
        if 'wlanacname' in decoded or 'wlanuserip' in decoded:
            return decoded
        return None

    def _extract_query_string(self, text_or_url):
        query = self._normalize_query_string(text_or_url)
        if query:
            return query

        patterns = [
            r'''(?:href|location\.href)\s*=\s*['"]([^'"]+)['"]''',
            r'''window\.location\s*=\s*['"]([^'"]+)['"]''',
        ]
        for pattern in patterns:
            for match in re.findall(pattern, text_or_url, re.I):
                query = self._normalize_query_string(match)
                if query:
                    return query
        return None

    def _probe_url(self, url):
        '''
        探测外网地址，不自动跟随重定向，避免被 123.123.123.123 卡死。
        :return: (是否已联网, 捕获到的认证 queryString)
        '''
        try:
            res = self._get(url, headers=self.header, allow_redirects=False)
            text = read_text(res)
            query = self._extract_query_string(res.geturl()) or self._extract_query_string(text)
            if query:
                return (False, query)
            if 'auth.ysu.edu.cn' in text:
                return (False, None)
            return (True, None)
        except HTTPError as e:
            location = e.headers.get('Location') if e.headers else None
            if location:
                full_location = urljoin(url, location)
                query = self._extract_query_string(full_location)
                if query:
                    return (False, query)
                if 'auth.ysu.edu.cn' in full_location:
                    return (False, None)
                return (True, None)

            try:
                text = read_text(e)
            except Exception:
                text = ''
            query = self._extract_query_string(text)
            if query:
                return (False, query)
            if 'auth.ysu.edu.cn' in text:
                return (False, None)
        except (URLError, IOError):
            pass
        return (False, None)

    def _get_online_user_info(self):
        try:
            res = self._get(self.url + 'getOnlineUserInfo', headers=self.header)
            return self._parse_json(res)
        except Exception:
            return {}

    def _https_handler(self):
        try:
            context = ssl._create_unverified_context()
            if VERSION == 3:
                return urllib.request.HTTPSHandler(context=context)
            return urllib2.HTTPSHandler(context=context)
        except Exception:
            return None

    def _new_cookie_openers(self):
        transport = self._network_transport()
        if VERSION == 3:
            jar = http.cookiejar.CookieJar()

            def build(no_redirect=False):
                handlers = [direct_proxy_handler(), urllib.request.HTTPCookieProcessor(jar)]
                if transport:
                    handlers.extend(transport.handlers())
                else:
                    https_handler = self._https_handler()
                    if https_handler:
                        handlers.append(https_handler)
                if no_redirect:
                    handlers.append(NoRedirectHandler())
                return urllib.request.build_opener(*handlers)
        else:
            jar = cookielib.CookieJar()

            def build(no_redirect=False):
                handlers = [direct_proxy_handler(), urllib2.HTTPCookieProcessor(jar)]
                https_handler = self._https_handler()
                if https_handler:
                    handlers.append(https_handler)
                if no_redirect:
                    handlers.append(NoRedirectHandler())
                return urllib2.build_opener(*handlers)

        return {
            'default': build(False),
            'no_redirect': build(True),
        }

    def _session_request(self, openers, url, headers=None, data=None,
                         json_data=None, allow_redirects=True,
                         timeout=DEFAULT_TIMEOUT):
        headers = dict(headers or {})
        body = None
        if json_data is not None:
            body = utf8_bytes(json.dumps(json_data, ensure_ascii=False))
            headers.setdefault('Content-Type', 'application/json')
            headers.setdefault('Accept', 'application/json, text/plain, */*')
        elif data is not None:
            body = utf8_bytes(urlencode(data))
            headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')

        if VERSION == 3:
            request = urllib.request.Request(url, headers=headers, data=body)
        else:
            request = urllib2.Request(url, headers=headers, data=body)

        opener = openers['default'] if allow_redirects else openers['no_redirect']
        try:
            return opener.open(request, timeout=timeout)
        except HTTPError as e:
            if (not allow_redirects) and e.code in REDIRECT_CODES:
                return e
            raise

    def _session_json(self, openers, path, data=None, method='POST'):
        url = AUTH1_HOST + path
        headers = dict(self.header)
        headers.update({
            'Origin': AUTH1_HOST,
            'Referer': AUTH1_HOST + '/portal/',
        })
        if method == 'GET':
            response = self._session_request(openers, url, headers=headers)
        else:
            response = self._session_request(openers, url, headers=headers,
                                             json_data=data or {})
        try:
            return json.loads(read_text(response))
        except ValueError:
            return {}

    def _auth1_session_info(self, openers):
        info = {
            'sessionId': '',
            'source': '',
            'url': '',
            'portalUrl': '',
            'errors': [],
        }

        try:
            response = self._session_request(openers, AUTH1_HOST + '/',
                                             headers=self.header,
                                             timeout=DEFAULT_TIMEOUT * 2)
            url = response.geturl()
            info['url'] = url
            info['sessionId'] = self._query_dict(url).get('sessionId', '')
            if info['sessionId']:
                info['source'] = 'auth1-root'
                return info
        except Exception as e:
            info['errors'].append('auth1-root: %s' % text_value(e))

        try:
            portal_url = self._find_auth1_portal_url(openers)
            info['portalUrl'] = portal_url or ''
            if portal_url:
                response = self._session_request(openers, portal_url,
                                                 headers=self.header,
                                                 timeout=DEFAULT_TIMEOUT * 2)
                url = response.geturl()
                info['url'] = url
                info['sessionId'] = self._query_dict(url).get('sessionId', '')
                if info['sessionId']:
                    info['source'] = 'portal-redirect'
        except Exception as e:
            info['errors'].append('portal-redirect: %s' % text_value(e))

        return info

    def _brief_devices(self, online_devices, online=True):
        brief_devices = []
        for item in online_devices:
            if not isinstance(item, dict):
                continue
            service = (item.get('service') or item.get('serviceName') or
                       item.get('realServiceName') or item.get('operatorName') or
                       item.get('ispName') or item.get('productName') or '')
            hostname = item.get('hostName') or item.get('hostname') or item.get('computerName') or ''
            brief_devices.append({
                'online': online,
                'state': 'online' if online else 'offline',
                'stateSource': 'onlineDevices' if online else 'offlineDevices',
                'ip': item.get('userIpv4') or item.get('nodeIp') or item.get('userIpv6') or '',
                'mac': item.get('userMac') or item.get('nodeMac') or '',
                'deviceName': item.get('deviceName') or '',
                'hostname': hostname,
                'hostnameSource': 'findDevice' if hostname else 'not-returned-by-findDevice',
                'deviceType': item.get('deviceType') or item.get('nodeType') or '',
                'accessTime': item.get('accessTime') or item.get('authenticationTime') or '',
                'onlineDuration': item.get('onlineDuration') or '',
                'currentDevice': item.get('currentDevice'),
                'onlineUserUuid': item.get('onlineUserUuid') or item.get('userObjectId') or '',
                'service': service,
                'serviceSource': 'findDevice' if service else 'not-returned-by-findDevice',
                'bindNoSense': item.get('bindNoSense'),
                'noSenseUuid': item.get('noSenseUuid') or '',
                'noSenseEndTime': item.get('noSenseEndTime'),
                'defaultServiceId': item.get('defaultServiceOperaporsConfigUuid') or '',
                'authType': item.get('authType') or '',
                'location': item.get('location') or '',
            })
        return brief_devices

    def _enrich_current_device(self, devices, summary, target_user=''):
        current_ip = summary.get('userIp') or ''
        current_user = summary.get('userId') or summary.get('userName') or ''
        if not summary.get('online') or not current_ip or not current_user:
            return devices
        if target_user and self._normalized_account(current_user) != self._normalized_account(target_user):
            return devices
        # Only label the query machine's hostname when its actual source IP matches.
        local_ip = ((self.network_report or {}).get('selected') or {}).get('source_ip')
        if not local_ip and sys.platform != 'win32':
            try:
                probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    probe.connect(('10.11.0.1', 80))
                    local_ip = probe.getsockname()[0]
                finally:
                    probe.close()
            except OSError:
                pass
        for item in devices:
            if item.get('ip') != current_ip:
                continue
            item['currentDevice'] = True
            if not item.get('service') and summary.get('service'):
                item['service'] = summary['service']
                item['serviceSource'] = 'current-session'
            if not item.get('hostname') and local_ip == current_ip:
                try:
                    item['hostname'] = socket.gethostname()
                    item['hostnameSource'] = 'local-machine'
                except OSError:
                    pass
        return devices

    def _auth1_status_summary(self, status):
        online_info = status.get('online') or {}
        data = online_info.get('data') or {}
        portal_info = data.get('portalOnlineUserInfo') or {}
        online_user = data.get('onlineUser') or {}
        devices_data = (status.get('devices') or {}).get('data') or {}
        online_devices = devices_data.get('onlineDevices') or []

        result = portal_info.get('result')
        is_online = result == 'success'
        if result == 'fail':
            state = 'offline'
        elif is_online:
            state = 'online'
        elif status.get('internetOnline') is True and not status.get('auth1Session', {}).get('sessionId'):
            state = 'internet-online-auth-unknown'
        else:
            state = 'unknown'

        brief_devices = self._brief_devices(online_devices)

        summary = {
            'state': state,
            'online': is_online,
            'message': portal_info.get('message') or online_info.get('message') or '',
            'userId': portal_info.get('userId') or portal_info.get('userName') or online_user.get('userName') or '',
            'userName': portal_info.get('userName') or online_user.get('userName') or '',
            'service': portal_info.get('service') or portal_info.get('realServiceName') or '',
            'userIp': portal_info.get('userIp') or online_user.get('nodeIp') or '',
            'ssid': portal_info.get('ssid') or '',
            'location': online_user.get('nodePhysicalLocation') or '',
            'authenticationTime': online_user.get('authenticationTime') or '',
            'deviceCount': len(brief_devices),
            'devices': brief_devices,
        }
        self._enrich_current_device(brief_devices, summary)
        return summary

    def _account_status_summary(self, status):
        devices_data = (status.get('devices') or {}).get('data') or {}
        online_devices = devices_data.get('onlineDevices') or []
        offline_devices = devices_data.get('offlineDevices') or []
        brief_devices = self._brief_devices(online_devices)

        online_summary = self._auth1_status_summary({
            'online': status.get('online') or {},
            'devices': status.get('devices') or {},
            'internetOnline': None,
            'auth1Session': status.get('auth1Session') or {},
        })
        target_user = status.get('account') or ''
        self._enrich_current_device(brief_devices, online_summary, target_user)

        return {
            'account': target_user,
            'queryMachineQualified': bool(
                (status.get('queryMachine') or {}).get('ok')),
            'casLoginOk': bool((status.get('casLogin') or {}).get('ok')),
            'querySource': (status.get('casLogin') or {}).get('source') or '',
            'onlineDeviceCount': len(brief_devices),
            'offlineDeviceCount': len(offline_devices),
            'devices': brief_devices,
            'offlineDevices': self._brief_devices(offline_devices, online=False),
            'currentSession': online_summary,
            'serviceUnknownCount': len([item for item in brief_devices
                                        if not item.get('service')]),
            'hostnameUnknownCount': len([item for item in brief_devices if not item.get('hostname')]),
            'note': ('设备主机名与实际运营商优先使用接口返回值；'
                     '当前出口设备可补齐会话服务，确认属于查询机器时可补齐本机主机名。'
                     '其他设备未返回的字段显示“未知”，不会用账号配置的默认运营商代替。'),
        }

    def _normalized_account(self, value):
        value = text_value(value).strip().lower()
        return value.split('@', 1)[0]

    def _current_session_matches_account(self, current, user):
        summary = (current or {}).get('summary') or {}
        target = self._normalized_account(user)
        candidates = [summary.get('userId'), summary.get('userName')]
        return bool(target and summary.get('online') and any(
            self._normalized_account(item) == target for item in candidates if item))

    def _query_machine_result(self, session_info):
        session_info = session_info or {}
        session_id = session_info.get('sessionId') or ''
        portal_reachable = bool(
            session_id or session_info.get('url') or session_info.get('portalUrl'))
        if session_id:
            message = '符合查询机器要求：可以访问校园网认证环境并获得 auth1 会话'
        elif portal_reachable:
            message = ('当前机器可以访问认证入口，但未获得有效 auth1 sessionId；'
                       '需要先实现访问校园局域网')
        else:
            message = ('当前机器不符合查询机器要求；需要先实现访问校园局域网，'
                       '并确认 auth1.ysu.edu.cn 与学校 CAS 可达')
        return {
            'ok': bool(session_id),
            'qualifiesAsQueryMachine': bool(session_id),
            'campusPortalReachable': portal_reachable,
            'sessionIdAvailable': bool(session_id),
            'sessionSource': session_info.get('source') or '',
            'message': message,
            'errors': list(session_info.get('errors') or []),
        }

    def query_machine_status(self):
        '''
        只读检查当前机器是否具备校园网查询上下文，不登录或下线任何账号。
        '''
        try:
            openers = self._new_cookie_openers()
            session_info = self._auth1_session_info(openers)
        except Exception as error:
            session_info = {'errors': [text_value(error)]}
        result = self._query_machine_result(session_info)
        result['network'] = self.network_report
        if self.network_report and not self.network_report.get('ok'):
            result['message'] = self.network_report.get('message')
        proxy_schemes = configured_proxy_schemes()
        result['proxyDetected'] = bool(proxy_schemes)
        result['proxySchemes'] = proxy_schemes
        result['proxyBypassed'] = True
        if result.get('ok') and proxy_schemes:
            result['message'] += '；已绕过本机代理设置'
        return result

    def _account_status_from_current(self, current, user):
        status = {
            'ok': True,
            'changed': False,
            'account': user,
            'queryMachine': self._query_machine_result(
                current.get('auth1Session') or {}),
            'auth1Session': current.get('auth1Session') or {},
            'casLogin': {
                'ok': True,
                'message': '已复用本机当前认证会话',
                'sessionId': (current.get('auth1Session') or {}).get('sessionId') or '',
                'source': 'current-session',
            },
            'online': current.get('online') or {},
            'devices': current.get('devices') or {},
            'offlineAccount': current.get('offlineAccount') or {},
            'errors': list(current.get('errors') or []),
        }
        status['summary'] = self._account_status_summary(status)
        return status

    def current_status(self):
        '''
        只读查询当前出口的认证状态，不执行登录、下线或踢设备。
        '''
        status = {
            'changed': False,
            'internetOnline': None,
            'queryStringFound': False,
            'auth1Session': {},
            'online': {},
            'devices': {},
            'offlineAccount': {},
            'errors': [],
        }

        try:
            status['internetOnline'] = self.tst_net()
            status['queryStringFound'] = bool(self.queryString)
        except Exception as e:
            status['errors'].append('tst_net: %s' % text_value(e))

        try:
            openers = self._new_cookie_openers()
            session_info = self._auth1_session_info(openers)
        except Exception as error:
            session_info = {'errors': [text_value(error)]}
            status['errors'].append(text_value(error))
        status['network'] = self.network_report
        status['auth1Session'] = session_info
        session_id = session_info.get('sessionId') or ''

        if session_id:
            quoted_session = quote(session_id)
            probes = [
                ('online',
                 '/eportal/adaptor/getOnlineUserInfo?sessionId=' + quoted_session,
                 None,
                 'GET'),
                ('devices',
                 '/eportal/adaptor/devices/findDevice',
                 {'sessionId': session_id},
                 'POST'),
                ('offlineAccount',
                 '/eportal/operator/offlineAccountData',
                 {'sessionId': session_id},
                 'POST'),
            ]
            for key, path, data, method in probes:
                try:
                    status[key] = self._session_json(openers, path,
                                                     data=data,
                                                     method=method)
                except Exception as e:
                    status['errors'].append('%s: %s' % (key, text_value(e)))

        status['summary'] = self._auth1_status_summary(status)
        return status

    def print_current_status(self, status):
        summary = status.get('summary') or {}
        state_map = {
            'online': '在线',
            'offline': '未登录/离线',
            'unknown': '未知',
            'internet-online-auth-unknown': '外网可达，但未拿到 auth1 认证会话',
        }
        print('状态：%s' % state_map.get(summary.get('state'), summary.get('state') or '未知'))
        network = status.get('network') or {}
        selected = network.get('selected') or {}
        if selected:
            print('认证直连网卡：%s（%s）' % (selected.get('name'), selected.get('source_ip')))
        elif network and not network.get('ok'):
            print('直连检查：%s' % network.get('message'))
        if summary.get('userId'):
            print('账号：%s' % summary.get('userId'))
        if summary.get('service'):
            print('服务：%s' % summary.get('service'))
        if summary.get('userIp'):
            print('IP：%s' % summary.get('userIp'))
        if summary.get('ssid'):
            print('SSID：%s' % summary.get('ssid'))
        if summary.get('location'):
            print('位置：%s' % summary.get('location'))
        if summary.get('authenticationTime'):
            print('上线时间：%s' % summary.get('authenticationTime'))
        if summary.get('message') and not summary.get('online'):
            print('认证信息：%s' % summary.get('message'))

        print('在线设备数：%s' % summary.get('deviceCount', 0))
        for index, item in enumerate(summary.get('devices') or [], 1):
            parts = []
            for key in ('ip', 'hostname', 'deviceName', 'deviceType', 'service', 'accessTime', 'onlineDuration'):
                if item.get(key):
                    parts.append('%s=%s' % (key, item.get(key)))
            print('  %s. %s' % (index, ', '.join(parts) if parts else item))

        if status.get('errors'):
            print('查询警告：%s' % '；'.join(status.get('errors')))

    def account_status(self, user, pwd):
        '''
        只读查询指定账号的在线设备列表。
        只执行 CAS 登录以获得账号会话，不执行 serviceLogin、注销或踢设备。
        '''
        current = self.current_status()
        query_machine = self._query_machine_result(
            current.get('auth1Session') or {})
        if not query_machine.get('ok'):
            message = query_machine.get('message') or '需要先实现访问校园局域网'
            status = {
                'ok': False,
                'changed': False,
                'account': user,
                'queryMachine': query_machine,
                'auth1Session': current.get('auth1Session') or {},
                'casLogin': {
                    'ok': False,
                    'message': message,
                    'sessionId': '',
                    'source': 'query-machine-preflight',
                },
                'online': {},
                'devices': {},
                'offlineAccount': {},
                'errors': [message],
            }
            status['summary'] = self._account_status_summary(status)
            return status
        if self._current_session_matches_account(current, user):
            return self._account_status_from_current(current, user)

        status = {
            'ok': False,
            'changed': False,
            'account': user,
            'queryMachine': query_machine,
            'auth1Session': {},
            'casLogin': {},
            'online': {},
            'devices': {},
            'offlineAccount': {},
            'errors': [],
        }

        try:
            ok, cas_info, openers = self._cas_login_only(user, pwd)
        except HTTPError as error:
            code = getattr(error, 'code', None)
            if code == 401:
                message = ('CAS 登录请求被拒绝（HTTP 401）。请检查保存的密码；'
                           '若本机正在使用该账号，请先确认当前登录账号与所选配置一致。')
            else:
                message = 'CAS 登录请求失败（HTTP %s）' % (code or '未知')
            status['casLogin'] = {
                'ok': False,
                'message': message,
                'sessionId': '',
                'source': 'cas-http-error',
            }
            status['errors'].append(message)
            status['summary'] = self._account_status_summary(status)
            return status
        status['casLogin'] = cas_info
        session_id = cas_info.get('sessionId') or ''
        status['auth1Session'] = {
            'sessionId': session_id,
            'source': cas_info.get('source') or '',
            'url': cas_info.get('portalUrl') or '',
        }
        if not ok:
            status['errors'].append(cas_info.get('message') or 'CAS 登录失败')
            status['summary'] = self._account_status_summary(status)
            return status

        status['ok'] = True

        quoted_session = quote(session_id)
        probes = [
            ('online',
             '/eportal/adaptor/getOnlineUserInfo?sessionId=' + quoted_session,
             None,
             'GET'),
            ('devices',
             '/eportal/adaptor/devices/findDevice',
             {'sessionId': session_id},
             'POST'),
            ('offlineAccount',
             '/eportal/operator/offlineAccountData',
             {'sessionId': session_id},
             'POST'),
        ]
        for key, path, data, method in probes:
            try:
                status[key] = self._session_json(openers, path,
                                                 data=data,
                                                 method=method)
            except Exception as e:
                status['errors'].append('%s: %s' % (key, text_value(e)))

        status['summary'] = self._account_status_summary(status)
        return status

    def account_offline_devices(self, user, pwd, online_user_uuids=None):
        '''
        使用账号会话踢掉指定在线设备。online_user_uuids 为空时踢掉该账号全部在线设备。
        '''
        result = {
            'changed': False,
            'account': user,
            'requestedUuids': online_user_uuids or [],
            'targetUuids': [],
            'kick': {},
            'errors': [],
        }
        status = self.account_status(user, pwd)
        result['before'] = status
        if not (status.get('summary') or {}).get('casLoginOk'):
            result['errors'].append((status.get('casLogin') or {}).get('message') or 'CAS 登录失败')
            result['summary'] = self._account_offline_summary(result)
            return result

        online_devices = (((status.get('devices') or {}).get('data') or {}).get('onlineDevices') or [])
        requested = set(online_user_uuids or [])
        target_uuids = []
        for item in online_devices:
            uuid_value = item.get('onlineUserUuid') or item.get('userObjectId') or ''
            if uuid_value and (not requested or uuid_value in requested):
                target_uuids.append(uuid_value)
        result['targetUuids'] = target_uuids
        if not target_uuids:
            result['summary'] = self._account_offline_summary(result)
            return result

        summary = status.get('summary') or {}
        if summary.get('querySource') == 'current-session':
            openers = self._new_cookie_openers()
            session_id = (status.get('auth1Session') or {}).get('sessionId') or ''
        else:
            ok, cas_info, openers = self._cas_login_only(user, pwd)
            if not ok:
                result['errors'].append(cas_info.get('message') or 'CAS 登录失败')
                result['summary'] = self._account_offline_summary(result)
                return result
            session_id = cas_info.get('sessionId') or ''
        if not session_id:
            result['errors'].append('未获得 auth1 sessionId')
            result['summary'] = self._account_offline_summary(result)
            return result
        try:
            result['kick'] = self._session_json(
                openers,
                '/eportal/adaptor/kick-offline/batch',
                {'sessionId': session_id, 'onlineUserUuids': target_uuids},
                method='POST')
            result['changed'] = (result['kick'].get('code') == 200 and
                                 text_value(result['kick'].get('message')).lower() == 'ok')
            if not result['changed']:
                result['errors'].append(result['kick'].get('message') or '踢设备接口返回失败')
        except Exception as e:
            result['errors'].append(text_value(e))
        result['summary'] = self._account_offline_summary(result)
        return result

    def _account_offline_summary(self, result):
        return {
            'account': result.get('account') or '',
            'requestedCount': len(result.get('requestedUuids') or []),
            'targetCount': len(result.get('targetUuids') or []),
            'changed': bool(result.get('changed')),
            'message': ((result.get('kick') or {}).get('message') or
                        ('未找到需要下线的在线设备' if not result.get('targetUuids') else '下线失败')),
            'errors': result.get('errors') or [],
        }

    def print_account_status(self, status):
        summary = status.get('summary') or {}
        print('账号：%s' % (summary.get('account') or status.get('account') or ''))
        if not summary.get('casLoginOk'):
            message = (status.get('casLogin') or {}).get('message') or 'CAS 登录失败'
            print('账号查询登录：失败（%s）' % message)
        else:
            print('账号查询登录：成功（只读 CAS 会话，未执行运营商登录/下线）')

        print('在线设备数：%s' % summary.get('onlineDeviceCount', 0))
        for index, item in enumerate(summary.get('devices') or [], 1):
            parts = ['state=online']
            for key in ('ip', 'hostname', 'deviceName', 'deviceType', 'accessTime', 'onlineDuration'):
                if item.get(key):
                    parts.append('%s=%s' % (key, item.get(key)))
            service = item.get('service')
            if service:
                if item.get('serviceSource') == 'current-session':
                    parts.append('service=%s(当前出口补齐)' % service)
                else:
                    parts.append('service=%s' % service)
            else:
                parts.append('service=接口未返回')
            print('  %s. %s' % (index, ', '.join(parts) if parts else item))

        if summary.get('offlineDeviceCount'):
            print('离线设备记录数：%s' % summary.get('offlineDeviceCount'))
        if summary.get('serviceUnknownCount'):
            print('提示：%s' % summary.get('note'))
        if status.get('errors'):
            print('查询警告：%s' % '；'.join(status.get('errors')))

    def _extract_auth1_url(self, text_or_url):
        if not text_or_url:
            return None
        text_or_url = text_or_url.replace('&amp;', '&')
        patterns = [
            r'''https?://auth1\.ysu\.edu\.cn[^\s'"<>\\]+''',
            r'''https?://auth\.ysu\.edu\.cn[^\s'"<>\\]+''',
        ]
        for pattern in patterns:
            match = re.search(pattern, text_or_url)
            if match:
                return match.group(0)
        return None

    def _find_auth1_portal_url(self, openers):
        # Go straight to the campus portal before probing public web sites.
        # Public internet can be reachable via a proxy before campus login.
        try:
            response = self._session_request(openers, AUTH1_HOST + '/',
                                             headers=self.header)
            portal_url = response.geturl()
            if (urlsplit(portal_url).hostname == 'auth1.ysu.edu.cn' and
                    self._query_dict(portal_url).get('sessionId')):
                return portal_url
        except (HTTPError, URLError, IOError):
            pass
        for url in self.check_urls:
            try:
                response = self._session_request(openers, url, headers=self.header,
                                                 allow_redirects=False)
                location = get_header(response, 'Location', '')
                portal_url = self._extract_auth1_url(location)
                if portal_url:
                    return portal_url
                portal_url = self._extract_auth1_url(read_text(response))
                if portal_url:
                    return portal_url
            except (HTTPError, URLError, IOError):
                pass

        try:
            response = self._session_request(openers, self.portal,
                                             headers=self.header,
                                             allow_redirects=False)
            location = get_header(response, 'Location', '')
            return self._extract_auth1_url(location) or self._extract_auth1_url(read_text(response))
        except (HTTPError, URLError, IOError):
            return None

    def _query_dict(self, url):
        return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))

    def _build_cas_url(self, portal_main_url):
        params = self._query_dict(portal_main_url)
        cas_params = {
            'flowSessionId': params.get('sessionId', ''),
            'customPageId': params.get('customPageId', ''),
            'preview': 'false',
            'appType': 'normal',
            'language': 'zh-CN',
            'showIdentitySwitch': 'false',
            'timer': str(int(time.time() * 1000)),
            'nasIp': params.get('nasIp', ''),
            'userIp': params.get('userIp', ''),
            'ssid': params.get('ssid', ''),
            'nodeMac': params.get('nodeMac', ''),
        }
        if params.get('mode'):
            cas_params['mode'] = params.get('mode')
        return AUTH1_HOST + '/cas-sso/login?' + urlencode(cas_params), params

    def _cas_login_only(self, user, pwd):
        if not user or not pwd:
            return (False, {
                'ok': False,
                'message': '用户名或密码为空',
                'sessionId': '',
            }, None)

        openers = self._new_cookie_openers()
        session_info = self._auth1_session_info(openers)
        portal_main_url = session_info.get('url') or ''
        source = session_info.get('source') or ''

        if not session_info.get('sessionId'):
            portal_url = self._find_auth1_portal_url(openers)
            if not portal_url:
                return (False, {
                    'ok': False,
                    'message': '未检测到 auth1 新认证页面',
                    'sessionId': '',
                    'source': source,
                    'errors': session_info.get('errors') or [],
                }, openers)
            response = self._session_request(openers, portal_url,
                                             headers=self.header,
                                             timeout=DEFAULT_TIMEOUT * 2)
            portal_main_url = response.geturl()
            source = 'portal-redirect'

        cas_url, portal_params = self._build_cas_url(portal_main_url)
        session_id = portal_params.get('sessionId') or session_info.get('sessionId') or ''
        if not session_id:
            return (False, {
                'ok': False,
                'message': 'auth1 新认证未返回 sessionId',
                'sessionId': '',
                'source': source,
                'portalUrl': portal_main_url,
            }, openers)

        cas_response = self._session_request(openers, cas_url, headers=self.header)
        cas_html = read_text(cas_response)
        key = self._parse_html_id(cas_html, 'login-croypto')
        execution = self._parse_html_id(cas_html, 'login-page-flowkey')
        if not key or not execution:
            return (False, {
                'ok': False,
                'message': 'auth1 CAS 页面缺少加密参数',
                'sessionId': session_id,
                'source': source,
                'portalUrl': portal_main_url,
            }, openers)

        post_url = self._set_query_param(cas_response.geturl(),
                                         'accept-language', 'zh-CN')
        login_data = {
            'username': user,
            'type': 'UsernamePassword',
            '_eventId': 'submit',
            'geolocation': '',
            'execution': execution,
            'captcha_code': '',
            'rememberMe': 'false',
            'croypto': key,
            'password': self._aes_encrypt_b64(key, pwd),
            'captcha_payload': self._aes_encrypt_b64(key, '{}'),
        }
        headers = dict(self.header)
        headers.update({
            'Origin': AUTH1_HOST,
            'Referer': cas_response.geturl(),
        })
        login_response = self._session_request(openers, post_url, headers=headers,
                                               data=login_data,
                                               allow_redirects=False,
                                               timeout=DEFAULT_TIMEOUT * 2)
        location = get_header(login_response, 'Location', '')
        if response_code(login_response) not in REDIRECT_CODES or 'auth-success' not in location:
            detail = read_text(login_response)
            return (False, {
                'ok': False,
                'message': 'auth1 CAS 登录失败：%s' % (detail[:160] or '未返回成功跳转'),
                'sessionId': session_id,
                'source': source,
                'portalUrl': portal_main_url,
            }, openers)

        self._session_request(openers, urljoin(post_url, location),
                              headers=self.header,
                              timeout=DEFAULT_TIMEOUT * 2)
        return (True, {
            'ok': True,
            'message': 'CAS 登录成功，仅建立账号查询会话',
            'sessionId': session_id,
            'source': source,
            'portalUrl': portal_main_url,
        }, openers)

    def _set_query_param(self, url, key, value):
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if k != key]
        query.append((key, value))
        return urlunsplit((parts.scheme, parts.netloc, '/cas-sso/login',
                           urlencode(query), parts.fragment))

    def _parse_html_id(self, html, element_id):
        match = re.search(r'''id=["']%s["'][^>]*>(.*?)<''' % re.escape(element_id),
                          html, re.S)
        if not match:
            return ''
        return re.sub(r'<[^>]+>', '', match.group(1)).strip()

    def _aes_encrypt_b64(self, key_b64, value):
        key = base64.b64decode(utf8_bytes(key_b64))
        data = utf8_bytes(value)
        pad_len = 16 - (len(data) % 16)
        if VERSION == 3:
            data += bytes([pad_len]) * pad_len
        else:
            data += chr(pad_len) * pad_len

        try:
            from Crypto.Cipher import AES
            encrypted = AES.new(key, AES.MODE_ECB).encrypt(data)
            encoded = base64.b64encode(encrypted)
            return encoded.decode('ascii') if VERSION == 3 else encoded
        except Exception:
            pass

        # The Windows desktop bundle already includes cryptography. Reuse it
        # when PyCryptodome or the OpenSSL command-line tool is unavailable.
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            pass
        else:
            encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
            encrypted = encryptor.update(data) + encryptor.finalize()
            encoded = base64.b64encode(encrypted)
            return encoded.decode('ascii') if VERSION == 3 else encoded

        if os.name == 'nt':
            try:
                encrypted = self._aes_encrypt_windows_cng(key, data)
                encoded = base64.b64encode(encrypted)
                return encoded.decode('ascii') if VERSION == 3 else encoded
            except Exception:
                pass

        proc = subprocess.Popen(
            ['openssl', 'enc', '-aes-128-ecb', '-base64',
             '-K', binascii.hexlify(key).decode('ascii'),
             '-nosalt', '-nopad'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate(data)
        if proc.returncode != 0:
            raise RuntimeError('openssl AES failed: %s' % text_value(stderr))
        if VERSION == 3:
            return stdout.decode('ascii').strip().replace('\n', '')
        return stdout.strip().replace('\n', '')

    def _aes_encrypt_windows_cng(self, key, data):
        """AES-ECB through Windows CNG, requiring no Python package or OpenSSL."""
        import ctypes
        from ctypes import wintypes

        bcrypt = ctypes.WinDLL('bcrypt.dll')
        bcrypt.BCryptOpenAlgorithmProvider.restype = wintypes.LONG
        bcrypt.BCryptSetProperty.restype = wintypes.LONG
        bcrypt.BCryptGetProperty.restype = wintypes.LONG
        bcrypt.BCryptGenerateSymmetricKey.restype = wintypes.LONG
        bcrypt.BCryptEncrypt.restype = wintypes.LONG
        bcrypt.BCryptDestroyKey.restype = wintypes.LONG
        bcrypt.BCryptCloseAlgorithmProvider.restype = wintypes.LONG

        algorithm = wintypes.HANDLE()
        secret = wintypes.HANDLE()
        try:
            status = bcrypt.BCryptOpenAlgorithmProvider(
                ctypes.byref(algorithm), 'AES', None, 0)
            if status != 0:
                raise OSError('BCryptOpenAlgorithmProvider failed: 0x%08x' %
                              (status & 0xffffffff))

            chaining = 'ChainingModeECB'.encode('utf-16-le') + b'\x00\x00'
            chaining_buffer = ctypes.create_string_buffer(chaining)
            status = bcrypt.BCryptSetProperty(
                algorithm, 'ChainingMode', chaining_buffer, len(chaining), 0)
            if status != 0:
                raise OSError('BCryptSetProperty failed: 0x%08x' %
                              (status & 0xffffffff))

            object_length = wintypes.ULONG()
            returned = wintypes.ULONG()
            status = bcrypt.BCryptGetProperty(
                algorithm, 'ObjectLength', ctypes.byref(object_length),
                ctypes.sizeof(object_length), ctypes.byref(returned), 0)
            if status != 0:
                raise OSError('BCryptGetProperty failed: 0x%08x' %
                              (status & 0xffffffff))

            key_object = ctypes.create_string_buffer(object_length.value)
            key_buffer = ctypes.create_string_buffer(key)
            status = bcrypt.BCryptGenerateSymmetricKey(
                algorithm, ctypes.byref(secret), key_object, object_length.value,
                key_buffer, len(key), 0)
            if status != 0:
                raise OSError('BCryptGenerateSymmetricKey failed: 0x%08x' %
                              (status & 0xffffffff))

            source = ctypes.create_string_buffer(data)
            output = ctypes.create_string_buffer(len(data))
            written = wintypes.ULONG()
            status = bcrypt.BCryptEncrypt(
                secret, source, len(data), None, None, 0,
                output, len(output), ctypes.byref(written), 0)
            if status != 0:
                raise OSError('BCryptEncrypt failed: 0x%08x' %
                              (status & 0xffffffff))
            return output.raw[:written.value]
        finally:
            if secret:
                bcrypt.BCryptDestroyKey(secret)
            if algorithm:
                bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)

    def _choose_service(self, service_list, service_type):
        if not isinstance(service_list, list) or not service_list:
            return None
        matches = []
        for item in service_list:
            if not isinstance(item, dict):
                continue
            values = [text_value(item.get(k)) for k in ('value', 'key', 'name', 'serviceName')]
            if any(self._service_matches(value, service_type) for value in values):
                matches.append(item)
        # Missing or ambiguous operators must never silently select another plan.
        return matches[0] if len(matches) == 1 else None

    def _service_matches(self, actual, requested):
        expected = self.services.get(text_value(requested), text_value(requested))
        aliases = {
            '校园网': ('校园网',),
            '中国移动': ('中国移动', '移动'),
            '中国联通': ('中国联通', '联通'),
            '中国电信': ('中国电信', '电信'),
        }
        actual = text_value(actual).strip()
        return bool(actual and actual in aliases.get(expected, (expected,)))

    def _verified_login_result(self, summary, user, service_type):
        summary = summary or {}
        if self._manual_target_matches(summary, user, service_type):
            return (True, '认证成功；已核实服务：%s' % summary.get('service'))
        requested = self.services.get(text_value(service_type), service_type)
        if summary.get('online'):
            return (False, '当前会话已在线，但未达到指定账号和运营商要求；'
                    '请求服务：%s；实际服务：%s；账号匹配：%s。'
                    '如需切换，请先 logout；并检查学校网页的可选服务、账号绑定和接入区域。' % (
                        requested, summary.get('service') or '接口未返回',
                        '是' if self._normalized_account(
                            summary.get('userId') or summary.get('userName')) ==
                        self._normalized_account(user) else '否或无法确认'))
        return (False, '已提交认证，但无法核实指定账号和运营商在线：%s' % requested)

    def _service_value(self, service):
        if not isinstance(service, dict):
            return None
        for key in ('value', 'serviceName', 'name', 'key'):
            if service.get(key):
                return service.get(key)
        return None

    def _login_auth1(self, user, pwd, service_type):
        openers = self._new_cookie_openers()
        portal_url = self._find_auth1_portal_url(openers)
        if not portal_url:
            return (None, '未检测到 auth1 新认证页面')

        response = self._session_request(openers, portal_url, headers=self.header)
        portal_main_url = response.geturl()
        cas_url, portal_params = self._build_cas_url(portal_main_url)
        session_id = portal_params.get('sessionId')
        if not session_id:
            return (False, 'auth1 新认证未返回 sessionId')

        cas_response = self._session_request(openers, cas_url, headers=self.header)
        cas_html = read_text(cas_response)
        key = self._parse_html_id(cas_html, 'login-croypto')
        execution = self._parse_html_id(cas_html, 'login-page-flowkey')
        if not key or not execution:
            return (False, 'auth1 CAS 页面缺少加密参数')

        post_url = self._set_query_param(cas_response.geturl(),
                                         'accept-language', 'zh-CN')
        login_data = {
            'username': user,
            'type': 'UsernamePassword',
            '_eventId': 'submit',
            'geolocation': '',
            'execution': execution,
            'captcha_code': '',
            'rememberMe': 'false',
            'croypto': key,
            'password': self._aes_encrypt_b64(key, pwd),
            'captcha_payload': self._aes_encrypt_b64(key, '{}'),
        }
        headers = dict(self.header)
        headers.update({
            'Origin': AUTH1_HOST,
            'Referer': cas_response.geturl(),
        })
        login_response = self._session_request(openers, post_url, headers=headers,
                                               data=login_data,
                                               allow_redirects=False,
                                               timeout=DEFAULT_TIMEOUT * 2)
        location = get_header(login_response, 'Location', '')
        if response_code(login_response) not in REDIRECT_CODES or 'auth-success' not in location:
            detail = read_text(login_response)
            return (False, 'auth1 CAS 登录失败：%s' % (detail[:160] or '未返回成功跳转'))

        self._session_request(openers, urljoin(post_url, location), headers=self.header)
        time.sleep(0.5)

        node = self._session_json(openers, '/eportal/workFlow/getCurrentNode',
                                  {'sessionId': session_id, 'flowKey': 'portal_auth'})
        current_node = (node.get('data') or {}).get('currentNodePath')
        if current_node == 'serviceSelection':
            services = self._session_json(openers, '/eportal/network/serviceSelection',
                                          {'sessionId': session_id})
            selected = self._choose_service(services.get('data'), service_type)
            service_value = self._service_value(selected)
            if not service_value:
                available = []
                for item in services.get('data') or []:
                    if isinstance(item, dict):
                        labels = [text_value(item.get(key)) for key in
                                  ('name', 'serviceName', 'value', 'key') if item.get(key)]
                        available.append(' / '.join(labels))
                return (False, 'auth1 未找到唯一匹配的指定服务：%s；服务器返回：%s；'
                        '未改选其他服务' % (
                            self.services.get(text_value(service_type), service_type),
                            '、'.join(available) or '无可用服务'))
            result = self._session_json(openers, '/eportal/network/serviceLogin',
                                        {'sessionId': session_id,
                                         'service': service_value})
            data = result.get('data') or {}
            if data.get('authResult') != 'success':
                return (False, data.get('authMessage') or result.get('message') or '服务认证失败')

        summary = {}
        for _ in range(5):
            online = self._session_json(openers,
                                        '/eportal/adaptor/getOnlineUserInfo?sessionId=' + session_id,
                                        method='GET')
            portal_info = (online.get('data') or {}).get('portalOnlineUserInfo') or {}
            summary = self._auth1_status_summary({'online': online})
            if portal_info.get('result') == 'success':
                self.isLogined = True
                if self._manual_target_matches(summary, user, service_type):
                    return self._verified_login_result(summary, user, service_type)
            time.sleep(0.5)
        return self._verified_login_result(summary, user, service_type)

    def _find_portal_query_string(self):
        if self.queryString:
            return self.queryString

        for url in self.check_urls:
            online, query = self._probe_url(url)
            if query:
                self.queryString = query
                return query
            if online:
                return None

        try:
            res = self._get(self.portal, headers=self.header, allow_redirects=False)
            query = self._extract_query_string(read_text(res))
            if query:
                self.queryString = query
                return query
        except HTTPError as e:
            location = e.headers.get('Location') if e.headers else None
            query = self._extract_query_string(location or '')
            if query:
                self.queryString = query
                return query
        except (URLError, IOError):
            pass

        info = self._get_online_user_info()
        query = self._extract_query_string(info.get('redirectUrl') or '')
        if query:
            self.queryString = query
            return query
        return None


    def tst_net(self):
        '''
        测试网络是否认证
        :return: 是否已经认证
        '''
        for url in self.check_urls:
            online, query = self._probe_url(url)
            if query:
                self.queryString = query
                self.isLogined = False
                return self.isLogined
            if online:
                self.isLogined = True
                return self.isLogined

        info = self._get_online_user_info()
        if info.get('result') == 'success':
            self.isLogined = True
        else:
            query = self._extract_query_string(info.get('redirectUrl') or '')
            if query:
                self.queryString = query
            self.isLogined = False
        return self.isLogined


    def isCode(self):
        '''
        检测是否需要输入验证码
        未开放
        :return:是否需要验证码
        '''
        pass

        return False

    def _manual_target_matches(self, summary, user, service_type):
        summary = summary or {}
        actual_user = self._normalized_account(
            summary.get('userId') or summary.get('userName'))
        expected_user = self._normalized_account(user)
        service_matches = self._service_matches(summary.get('service'), service_type)
        return bool(summary.get('online') and actual_user == expected_user
                    and service_matches)

    def ensure_login(self, user, pwd, service_type, code=''):
        '''
        Explicitly ensure this machine uses the requested account and operator.
        Unlike heartbeat assessment, an unidentifiable existing session is not
        treated as success after the user explicitly requests a connection.
        '''
        if not user or not pwd:
            return (False, '用户名或密码为空')
        service_type = text_value(service_type).strip()
        if service_type not in self.services:
            return (False, '运营商编号必须是 0、1、2 或 3')
        qualification = self.query_machine_status()
        if not qualification.get('ok'):
            return (False, qualification.get('message') or
                    '需要先实现访问校园局域网')

        current = self.current_status()
        summary = current.get('summary') or {}
        if self._manual_target_matches(summary, user, service_type):
            return (True, '本机已使用指定账号和运营商在线')

        switched = False
        if summary.get('online'):
            logout_state, logout_message = self.logout()
            if not logout_state:
                return (False, '切换到指定账号前无法下线旧会话：%s' % logout_message)
            switched = True

        self.isLogined = False
        self.queryString = None
        state, message = self.login(user=user, pwd=pwd, type=service_type, code=code)
        if state and switched:
            message = '已自动下线旧会话；%s' % message
        return (state, message)


    def login(self,user,pwd,type,code=''):
        '''
        输入参数登入校园网，自动检测当前网络是否认证。
        :param user:登入id
        :param pwd:登入密码
        :param type:认证服务
        :param code:验证码
        :return:元祖第一项：是否认证状态；第二项：详细信息
        '''
        if not user or not pwd:
            return (False, '用户名或密码为空')
        type = text_value(type).strip()
        if type not in self.services:
            return (False, '运营商编号必须是 0、1、2 或 3')
        if self.isLogined is not False:
            current = self.current_status()
            summary = current.get('summary') or {}
            if not summary.get('online') and not current.get('auth1Session', {}).get('sessionId'):
                online_info = self._get_online_user_info()
                summary = self._auth1_status_summary({'online': {
                    'data': {'portalOnlineUserInfo': online_info}}})
            if summary.get('online'):
                return self._verified_login_result(summary, user, type)
            # Internet reachability is never evidence of campus authentication.
            self.isLogined = False
        if self.isLogined == False:
            if user == '' or pwd == '':
                return (False,'用户名或密码为空')
            try:
                auth1_state, auth1_info = self._login_auth1(user, pwd, type)
            except Exception as e:
                return (False, 'auth1 新认证流程失败：%s' % text_value(e))
            if auth1_state is not None:
                return (auth1_state, auth1_info)

            qs = self._find_portal_query_string()
            if not qs:
                return (False, '无法获取校园网认证参数 queryString，请确认当前网络已跳转到认证页面')
            login_query = qs[qs.index("wlanacname"):] if "wlanacname" in qs else qs
            self.data = {
                'userId': user,
                'password': pwd,
                'service': self.services.get(type, type),
                'queryString' : login_query,
                'operatorPwd': '',
                'operatorUserId': '',
                'validcode': code,
                'passwordEncrypt':'false'
            }
	    
            res = self._post(self.url+'login',headers = self.header,data = self.data)
            login_json = self._parse_json(res)
            self.userindex = login_json.get('userIndex')
            #self.info = login_json
            self.info = login_json.get('message', '认证接口返回异常')
            if login_json.get('result') == 'success':
                online_info = self._get_online_user_info()
                summary = self._auth1_status_summary({'online': {
                    'data': {'portalOnlineUserInfo': online_info}}})
                return self._verified_login_result(summary, user, type)
            else:
                return (False,self.info)

        return (False, '无法确认校园认证状态')
    def get_alldata(self):
        '''
        获取当前认证账号全部信息
        #！！！注意！！！#此操作会获得账号alldata['userId']姓名alldata['userName']以及密码alldata['password']
        :return:全部数据的字典格式
        '''
        res = self._get('http://auth.ysu.edu.cn/eportal/InterFace.do?method=getOnlineUserInfo',headers = self.header)
        try:
            self.alldata = self._parse_json(res)
        except ValueError as e:
            print('数据解析失败，请稍后重试。')

        return self.alldata

    def _logout_auth1(self):
        openers = self._new_cookie_openers()
        try:
            response = self._session_request(openers, AUTH1_HOST + '/',
                                             headers=self.header,
                                             timeout=DEFAULT_TIMEOUT * 2)
            session_id = self._query_dict(response.geturl()).get('sessionId', '')
        except Exception as e:
            return (None, 'auth1 获取 sessionId 失败：%s' % text_value(e))

        if not session_id:
            try:
                online = self._session_json(openers,
                                            '/eportal/adaptor/getOnlineUserInfo?sessionId=',
                                            method='GET')
                portal_info = (online.get('data') or {}).get('portalOnlineUserInfo') or {}
                if portal_info.get('result') != 'success':
                    return (True, '已经下线')
            except Exception:
                pass
            return (None, 'auth1 未返回 sessionId')

        try:
            online = self._session_json(openers,
                                        '/eportal/adaptor/getOnlineUserInfo?sessionId=' + quote(session_id),
                                        method='GET')
            portal_info = (online.get('data') or {}).get('portalOnlineUserInfo') or {}
            if portal_info.get('result') != 'success':
                return (True, '已经下线')
        except Exception:
            pass

        try:
            result = self._session_json(openers, '/eportal/network/offline',
                                        {'sessionId': session_id})
        except Exception as e:
            return (None, 'auth1 下线请求失败：%s' % text_value(e))

        if result.get('code') == 200:
            return (True, '下线成功')
        return (False, result.get('message') or 'auth1 下线失败')


    def logout(self):
        '''
        登出，操作内会自动获取特征码
        :return:元祖第一项：是否操作成功；第二项：详细信息
        '''
        auth1_state, auth1_info = self._logout_auth1()
        if auth1_state is not None:
            return (auth1_state, auth1_info)

        try:
            if self.alldata==None:
                self.get_alldata()

            res = self._get(self.url+'logout',headers = self.header)
            logout_json = self._parse_json(res)
            #self.info = logout_json
            self.info = logout_json.get('message', '认证接口返回异常')
        except Exception as e:
            return (False, auth1_info + '；旧认证接口下线也失败：%s' % text_value(e))

        if logout_json.get('result') == 'success':
            return (True,'下线成功')
        else:
            return (False,self.info)

if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    loger = Netlogin()
    l = len(sys.argv)
    name = sys.argv[0]
    if l >= 2 and sys.argv[1] in (
            'account-devices', 'account-history', 'device-offline',
            'nosense-config', 'nosense-enable', 'nosense-register', 'nosense-disable',
            'account-traffic', 'account-menu', 'account-login'):
        from self_service import main as self_service_main
        sys.exit(self_service_main(loger, sys.argv[1:]))
    if l >= 2 and sys.argv[1] == 'wifi-scan':
        from wifi_scan import main as wifi_scan_main
        sys.argv = [name] + sys.argv[2:]
        sys.exit(wifi_scan_main())
    if l >= 2 and sys.argv[1] == 'campus-check':
        try:
            from campus_network import inspect_network, print_report
            report, _ = inspect_network()
            if '--json' in sys.argv[2:]:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print_report(report)
            sys.exit(0 if report.get('ok') else 1)
        except Exception as error:
            print(json.dumps({'ok': False, 'message': text_value(error)}, ensure_ascii=False))
            sys.exit(1)
    if l >= 2 and sys.argv[1] == 'query-machine-status':
        result = loger.query_machine_status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get('ok') else 1)
    if l >= 2 and sys.argv[1] in ('account-status-stdin', 'account-offline-devices-stdin'):
        try:
            payload = json.load(sys.stdin)
            user = text_value(payload.get('username', '')).strip()
            pwd = text_value(payload.get('password', ''))
            if not user or not pwd:
                raise ValueError('账号或密码为空')
            if sys.argv[1] == 'account-status-stdin':
                result = loger.account_status(user, pwd)
                success = bool((result.get('summary') or {}).get('casLoginOk'))
            else:
                uuids = payload.get('onlineUserUuids')
                if uuids is not None and not isinstance(uuids, list):
                    raise ValueError('onlineUserUuids 必须是数组或 null')
                result = loger.account_offline_devices(user, pwd, uuids)
                success = not bool((result.get('summary') or {}).get('errors'))
            print(json.dumps(result, ensure_ascii=False))
            sys.exit(0 if success else 1)
        except Exception as e:
            print(json.dumps({'ok': False, 'message': text_value(e)}, ensure_ascii=False))
            sys.exit(2)
    if l >= 2 and sys.argv[1] == 'login-stdin':
        try:
            payload = json.load(sys.stdin)
            user = text_value(payload.get('username', '')).strip()
            pwd = text_value(payload.get('password', ''))
            service_type = text_value(payload.get('service', '')).strip()
            if service_type not in loger.services:
                raise ValueError('运营商编号必须是 0、1、2 或 3')
            state, info = loger.ensure_login(
                user=user, pwd=pwd, service_type=service_type)
            print(json.dumps({
                'ok': bool(state),
                'message': text_value(info),
                'service': loger.services.get(service_type, service_type),
            }, ensure_ascii=False))
            sys.exit(0 if state else 1)
        except Exception as e:
            print(json.dumps({
                'ok': False,
                'message': text_value(e),
            }, ensure_ascii=False))
            sys.exit(2)
    if l>=2 and sys.argv[1] in ('status', 'current-status', '--status', '--current-status'):
        status = loger.current_status()
        if '--json' in sys.argv[2:]:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            loger.print_current_status(status)
        sys.exit(0)
    if l>=2 and sys.argv[1] == 'account-status':
        args = sys.argv[2:]
        output_json = '--json' in args
        args = [item for item in args if item != '--json']
        accounts_file = ''
        account_name = ''
        positional = []
        error = ''
        index = 0
        while index < len(args):
            item = args[index]
            if item in ('--accounts-file', '--account-file'):
                if index + 1 >= len(args):
                    error = '%s 缺少路径参数' % item
                    break
                accounts_file = args[index + 1]
                index += 2
            elif item in ('--account-name', '--name'):
                if index + 1 >= len(args):
                    error = '%s 缺少账号配置名' % item
                    break
                account_name = args[index + 1]
                index += 2
            elif item.startswith('--'):
                error = '未知参数：%s' % item
                break
            else:
                positional.append(item)
                index += 1

        if not error and accounts_file:
            if not account_name and positional:
                account_name = positional.pop(0)
            try:
                user, pwd = load_account_credentials(accounts_file, account_name)
            except Exception as e:
                error = text_value(e)
        elif not error and len(positional) >= 2:
            user, pwd = positional[0], positional[1]
        elif not error:
            error = ('格式：%s account-status userid password [--json]；'
                     '或 %s account-status --accounts-file path --account-name name [--json]'
                     % (name, name))

        if error:
            print(error)
            sys.exit(2)

        status = loger.account_status(user, pwd)
        if output_json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            loger.print_account_status(status)
        if not (status.get('summary') or {}).get('casLoginOk'):
            sys.exit(1)
        sys.exit(0)
    if l==2 and sys.argv[1]=='logout':
        state,info = loger.logout()
        if state:
            print(info)
        else:
            print('出现错误!')
            print(info)
        sys.exit(0)
    elif l in (3, 4) and not sys.argv[1].startswith('-'):
        state, info = loger.login(
            user=sys.argv[1], pwd=sys.argv[2],
            type=sys.argv[3] if l == 4 else '0')
    else:
        print('登陆服务： 0.校园网 1.中国移动 2.中国联通 3.中国电信')
        print('格式：')
        print('登入：%s userid password [service_type=校园网] ' % name)
        print('安全登入：向标准输入传入 JSON，然后执行 %s login-stdin' % name)
        print('注销：%s logout ' % name)
        print('状态：%s current-status [--json] ' % name)
        print('附近 Wi-Fi：%s wifi-scan [--json]' % name)
        print('校园网卡直连检测：%s campus-check [--json]' % name)
        print('账号设备：%s account-status userid password [--json] ' % name)
        print('账号设备配置：%s account-status --accounts-file path --account-name name [--json] ' % name)
        print('自助中心：account-devices / account-history / device-offline / nosense-config / nosense-enable / nosense-register / nosense-disable')
        print('查看各命令参数：%s 命令 --help' % name)
        print('账号菜单：%s account-menu；剩余流量：%s account-traffic 学号' % (name, name))
        print('选择服务并查看流量：%s account-login 学号 --service 0/1/2/3' % name)
        sys.exit(0 if l == 1 or sys.argv[1] in ('--help', '-h') else 2)
    if state:
        print(info)
    else:
        print('出现错误!')
        print(info)
    sys.exit(0 if state else 1)


