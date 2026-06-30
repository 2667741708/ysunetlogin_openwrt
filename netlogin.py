#!/bin/python
# coding:utf-8
import re
import json
import sys
import base64
import binascii
import subprocess
import time
import ssl


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


# 封装post请求
def post(url, headers=None, data=None, timeout=DEFAULT_TIMEOUT):
    headers = headers or {}
    data = data or {}
    data = urlencode(data)
    if VERSION == 3:
        data = data.encode('utf-8')
        request = urllib.request.Request(url, headers=headers, data=data)
        response = urllib.request.urlopen(request, timeout=timeout)
    else:
        request = urllib2.Request(url, headers=headers, data=data)
        response = urllib2.urlopen(request, timeout=timeout)

    return response


# 封装get请求
def get(url, headers=None, timeout=DEFAULT_TIMEOUT, allow_redirects=True):
    headers = headers or {}
    if VERSION == 3:
        request = urllib.request.Request(url, headers=headers)
        if allow_redirects:
            response = urllib.request.urlopen(request, timeout=timeout)
        else:
            opener = urllib.request.build_opener(NoRedirectHandler)
            response = opener.open(request, timeout=timeout)
    else:
        request = urllib2.Request(url, headers=headers)
        if allow_redirects:
            response = urllib2.urlopen(request, timeout=timeout)
        else:
            opener = urllib2.build_opener(NoRedirectHandler)
            response = opener.open(request, timeout=timeout)

    return response


class Netlogin():
    def __init__(self):
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
            res = get(url, headers=self.header, allow_redirects=False)
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
            res = get(self.url + 'getOnlineUserInfo', headers=self.header)
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
        if VERSION == 3:
            jar = http.cookiejar.CookieJar()

            def build(no_redirect=False):
                handlers = [urllib.request.HTTPCookieProcessor(jar)]
                https_handler = self._https_handler()
                if https_handler:
                    handlers.append(https_handler)
                if no_redirect:
                    handlers.append(NoRedirectHandler())
                return urllib.request.build_opener(*handlers)
        else:
            jar = cookielib.CookieJar()

            def build(no_redirect=False):
                handlers = [urllib2.HTTPCookieProcessor(jar)]
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

    def _choose_service(self, service_list, service_type):
        if not isinstance(service_list, list) or not service_list:
            return None

        preferred = text_value(self.services.get(service_type, service_type))

        def item_order(item):
            if isinstance(item, dict) and item.get('order') is not None:
                return item.get('order')
            return 10 ** 9

        ordered = sorted(service_list, key=item_order)
        for item in ordered:
            if not isinstance(item, dict):
                continue
            values = [text_value(item.get(k)) for k in ('value', 'key', 'name', 'serviceName')]
            if preferred and preferred in values:
                return item

        campus_text = text_value('校园')
        for item in ordered:
            if isinstance(item, dict):
                summary = json.dumps(item, ensure_ascii=False)
                if campus_text in text_value(summary):
                    return item
        return ordered[0]

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
                return (False, 'auth1 未获取到可用服务')
            result = self._session_json(openers, '/eportal/network/serviceLogin',
                                        {'sessionId': session_id,
                                         'service': service_value})
            data = result.get('data') or {}
            if data.get('authResult') != 'success':
                return (False, data.get('authMessage') or result.get('message') or '服务认证失败')

        for _ in range(5):
            online = self._session_json(openers,
                                        '/eportal/adaptor/getOnlineUserInfo?sessionId=' + session_id,
                                        method='GET')
            portal_info = (online.get('data') or {}).get('portalOnlineUserInfo') or {}
            if portal_info.get('result') == 'success':
                self.isLogined = True
                return (True, '认证成功')
            time.sleep(0.5)

        self.tst_net()
        if self.isLogined:
            return (True, '认证成功')
        return (False, 'auth1 已提交认证，但在线状态仍为未登录')

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
            res = get(self.portal, headers=self.header, allow_redirects=False)
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


    def login(self,user,pwd,type,code=''):
        '''
        输入参数登入校园网，自动检测当前网络是否认证。
        :param user:登入id
        :param pwd:登入密码
        :param type:认证服务
        :param code:验证码
        :return:元祖第一项：是否认证状态；第二项：详细信息
        '''
        if self.isLogined == None:
            self.tst_net()
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
	    
            res = post(self.url+'login',headers = self.header,data = self.data)
            login_json = self._parse_json(res)
            self.userindex = login_json.get('userIndex')
            #self.info = login_json
            self.info = login_json.get('message', '认证接口返回异常')
            if login_json.get('result') == 'success':
                return (True,'认证成功')
            else:
                return (False,self.info)

        return (True,'已经在线')
    def get_alldata(self):
        '''
        获取当前认证账号全部信息
        #！！！注意！！！#此操作会获得账号alldata['userId']姓名alldata['userName']以及密码alldata['password']
        :return:全部数据的字典格式
        '''
        res = get('http://auth.ysu.edu.cn/eportal/InterFace.do?method=getOnlineUserInfo',headers = self.header)
        try:
            self.alldata = self._parse_json(res)
        except ValueError as e:
            print('数据解析失败，请稍后重试。')

        return self.alldata


    def logout(self):
        '''
        登出，操作内会自动获取特征码
        :return:元祖第一项：是否操作成功；第二项：详细信息
        '''
        if self.alldata==None:
            self.get_alldata()

        res = get(self.url+'logout',headers = self.header)
        logout_json = self._parse_json(res)
        #self.info = logout_json
        self.info = logout_json.get('message', '认证接口返回异常')

        if logout_json.get('result') == 'success':
            return (True,'下线成功')
        else:
            return (False,self.info)

if __name__ == '__main__':
    loger = Netlogin()
    l = len(sys.argv)
    name = sys.argv[0]
    if l==2 and sys.argv[1]=='logout':
        state,info = loger.logout()
        if state:
            print(info)
        else:
            print('出现错误!')
            print(info)
        sys.exit(0)
    elif l==3:
        state, info = loger.login(user=sys.argv[1], pwd=sys.argv[2], type='0')
    elif l==4:
        state, info = loger.login(user=sys.argv[1], pwd=sys.argv[2], type=sys.argv[3])
    else:
        print('登陆服务： 0.校园网 1.中国移动 2.中国联通 3.中国电信')
        print('格式：')
        print('登入：%s userid password [service_type=校园网] ' % name)
        print('注销：%s logout ' % name)
        state, info = loger.login(user="", pwd="", type='3')
        print(state, info)
        sys.exit(0)
    if state:
        print(info)
    else:
        print('出现错误!')
        print(info)


