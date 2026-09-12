#!/usr/bin/env python3
"""LAN-only web GUI for running the fixed YSU netlogin script over SSH."""
import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import ssl
import subprocess
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

SERVICES = {'0': '校园网', '1': '中国移动', '2': '中国联通', '3': '中国电信'}
SESSIONS = {}
ATTEMPTS = {}
LOCK = threading.Lock()

STYLE = """
:root{color-scheme:light;--ink:#17251f;--muted:#66736d;--paper:#f3f5ef;--panel:#fffdf7;
--line:#d8ded7;--green:#12634b;--lime:#d9f28b;--red:#a43f35;--shadow:0 18px 55px #18352a18}
*{box-sizing:border-box}html,body{max-width:100%;overflow-x:hidden}body{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.shell{max-width:1040px;margin:auto;padding:32px 22px 56px}.top{display:flex;justify-content:space-between;
align-items:center;margin-bottom:42px}.brand{font-weight:800;letter-spacing:.04em}.secure{color:var(--green);font-size:13px}
.hero{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,440px);gap:54px;align-items:start}.hero>*{min-width:0}
h1{font-size:clamp(36px,6vw,72px);line-height:.98;letter-spacing:-.055em;margin:0 0 22px;max-width:680px}
.lead{max-width:560px;color:var(--muted);font-size:17px}.facts{display:flex;gap:22px;flex-wrap:wrap;margin-top:32px}
.fact b{display:block;font-size:22px}.fact span{color:var(--muted);font-size:12px}
.panel{background:var(--panel);border:1px solid var(--line);box-shadow:var(--shadow);padding:28px}
.panel h2{margin:0 0 20px;font-size:20px}label{display:block;font-weight:700;font-size:13px;margin:15px 0 6px}
input,select,button{width:100%;font:inherit;border-radius:0}input,select{border:1px solid #bdc7c0;background:white;
padding:12px 13px;color:var(--ink)}input:focus,select:focus,button:focus{outline:3px solid #9ccfbc;outline-offset:2px}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:22px}button{border:0;padding:13px;
font-weight:800;cursor:pointer}.primary{background:var(--green);color:white}.secondary{background:var(--lime);color:var(--ink)}
button:disabled{opacity:.55;cursor:wait}.result{margin-top:18px;padding:14px;border-left:4px solid var(--green);
background:#edf6f0;white-space:pre-wrap;overflow-wrap:anywhere}.result.error{border-color:var(--red);background:#faefed}
.note{margin-top:18px;color:var(--muted);font-size:12px}.login{max-width:420px;margin:10vh auto}.logout{width:auto;
background:transparent;color:var(--muted);padding:6px}.statusdot{display:inline-block;width:8px;height:8px;border-radius:50%;
background:#27a66f;margin-right:6px}@media(max-width:760px){.shell{padding:22px 16px}.top{margin-bottom:28px}
.top .secure{display:none}.hero{grid-template-columns:minmax(0,1fr);gap:28px}h1{font-size:44px}.facts{margin-top:22px}
.panel{width:100%;min-width:0;padding:22px}input,select{min-width:0}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
"""

def load_config(path):
    config_path = Path(path).resolve()
    with open(config_path, 'r', encoding='utf-8-sig') as handle:
        config = json.load(handle)
    for key in ('tls_cert', 'tls_key'):
        if config.get(key) and not Path(config[key]).is_absolute():
            config[key] = str(config_path.parent / config[key])
    required = {'id', 'name', 'ssh_target', 'expected_hostname', 'script'}
    for server in config.get('servers', []):
        if not required.issubset(server) or not server['ssh_target'] or not server['script'].startswith('/'):
            raise ValueError('服务器配置缺少字段或脚本路径不是绝对路径')
    if not config.get('servers'):
        raise ValueError('至少配置一台服务器')
    return config

def encode_session(user, csrf, expires, secret):
    raw = json.dumps([user, csrf, expires], separators=(',', ':')).encode()
    sig = hmac.new(secret, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + sig).decode()

def decode_session(value, secret):
    try:
        blob = base64.urlsafe_b64decode(value.encode())
        raw, sig = blob[:-32], blob[-32:]
        if not hmac.compare_digest(sig, hmac.new(secret, raw, hashlib.sha256).digest()):
            return None
        user, csrf, expires = json.loads(raw)
        return (user, csrf) if time.time() < expires else None
    except Exception:
        return None

def ssh_run(server, action, payload, timeout):
    identity = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
         '-o', 'StrictHostKeyChecking=yes', '--',
         server['ssh_target'], 'hostname'],
        text=True, capture_output=True, timeout=min(timeout, 10),
        encoding='utf-8', errors='replace')
    if identity.returncode != 0 or identity.stdout.strip() != server['expected_hostname']:
        return {'ok': False, 'message': 'SSH 主机身份校验失败'}
    if action == 'status':
        remote_args = ['python3', server['script'], 'current-status', '--json']
        stdin = None
    else:
        remote_args = ['python3', server['script'], 'login-stdin']
        stdin = json.dumps(payload, ensure_ascii=False)
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
               '-o', 'StrictHostKeyChecking=yes', '--',
               server['ssh_target'], *remote_args]
    completed = subprocess.run(command, input=stdin, text=True, capture_output=True,
                               timeout=timeout, encoding='utf-8', errors='replace')
    output = (completed.stdout or completed.stderr).strip()
    try:
        data = json.loads(output)
    except ValueError:
        data = {'ok': completed.returncode == 0, 'message': output or '远端没有返回内容'}
    data.setdefault('ok', completed.returncode == 0)
    return data

class Handler(BaseHTTPRequestHandler):
    server_version = 'YSUNetloginLAN/1.0'

    def log_message(self, fmt, *args):
        print('%s - %s' % (self.client_address[0], fmt % args))

    def security_headers(self):
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                         "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')

    def send_html(self, body, status=200, cookie=None):
        raw = body.encode('utf-8')
        self.send_response(status)
        self.security_headers()
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.security_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def session(self):
        jar = cookies.SimpleCookie(self.headers.get('Cookie', ''))
        item = jar.get('ysu_session')
        return decode_session(item.value, self.server.secret) if item else None

    def body(self):
        length = min(int(self.headers.get('Content-Length', '0')), 16384)
        return parse_qs(self.rfile.read(length).decode('utf-8', 'replace'))

    def do_GET(self):
        if self.path == '/health':
            return self.send_json({'ok': True})
        session = self.session()
        if self.path == '/logout':
            secure = '; Secure' if self.server.is_tls else ''
            return self.send_html(self.login_page('已退出'),
                                  cookie='ysu_session=; Path=/; HttpOnly; SameSite=Strict%s; Max-Age=0' % secure)
        if not session:
            return self.send_html(self.login_page())
        if self.path != '/':
            return self.send_json({'ok': False, 'message': 'Not found'}, 404)
        return self.send_html(self.dashboard(session[1]))

    def do_POST(self):
        form = self.body()
        if self.path == '/login':
            return self.handle_login(form)
        session = self.session()
        if not session:
            return self.send_json({'ok': False, 'message': '会话已过期'}, 401)
        if not hmac.compare_digest(form.get('csrf', [''])[0], session[1]):
            return self.send_json({'ok': False, 'message': '请求校验失败'}, 403)
        if self.path != '/api/run':
            return self.send_json({'ok': False, 'message': 'Not found'}, 404)
        action = form.get('action', [''])[0]
        server_id = form.get('server', [''])[0]
        server = next((s for s in self.server.config['servers'] if s['id'] == server_id), None)
        if not server or action not in ('login', 'status'):
            return self.send_json({'ok': False, 'message': '服务器或操作无效'}, 400)
        payload = {}
        if action == 'login':
            payload = {
                'username': form.get('username', [''])[0].strip(),
                'password': form.get('password', [''])[0],
                'service': form.get('service', [''])[0],
            }
            if not payload['username'] or not payload['password'] or payload['service'] not in SERVICES:
                return self.send_json({'ok': False, 'message': '请填写账号、密码并选择运营商'}, 400)
        try:
            data = ssh_run(server, action, payload, self.server.config.get('ssh_timeout', 20))
            data['server'] = server['name']
            return self.send_json(data, 200 if data.get('ok') else 502)
        except subprocess.TimeoutExpired:
            return self.send_json({'ok': False, 'message': 'SSH 或校园网认证超时'}, 504)
        except Exception as exc:
            return self.send_json({'ok': False, 'message': '执行失败：%s' % exc}, 500)

    def handle_login(self, form):
        ip = self.client_address[0]
        now = time.time()
        with LOCK:
            recent = [t for t in ATTEMPTS.get(ip, []) if now - t < 300]
            ATTEMPTS[ip] = recent
            if len(recent) >= 10:
                return self.send_html(self.login_page('尝试次数过多，请稍后再试'), 429)
        password = form.get('password', [''])[0]
        if not hmac.compare_digest(password, self.server.gui_password):
            with LOCK:
                ATTEMPTS[ip].append(now)
            return self.send_html(self.login_page('访问密码不正确'), 401)
        csrf = secrets.token_urlsafe(24)
        expires = int(now + self.server.config.get('session_minutes', 480) * 60)
        token = encode_session('lan-user', csrf, expires, self.server.secret)
        secure = '; Secure' if self.server.is_tls else ''
        cookie = 'ysu_session=%s; Path=/; HttpOnly; SameSite=Strict%s; Max-Age=%d' % (
            token, secure, expires - int(now))
        self.send_response(303)
        self.security_headers()
        self.send_header('Set-Cookie', cookie)
        self.send_header('Location', '/')
        self.end_headers()

    def login_page(self, message=''):
        notice = '<div class="result error">%s</div>' % html.escape(message) if message else ''
        return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>校园网控制台</title>
<style>%s</style><main class="shell login"><div class="top"><div class="brand">YSU / LAN</div>
<div class="secure"><span class="statusdot"></span>本地网络工具</div></div>
<section class="panel"><h1 style="font-size:38px">进入控制台</h1>
<p class="lead">请输入管理员提供的 GUI 访问密码。</p>%s
<form method="post" action="/login"><label for="password">访问密码</label>
<input id="password" name="password" type="password" required autocomplete="current-password">
<button class="primary" style="margin-top:20px">登录</button></form></section></main></html>""" % (STYLE, notice)

    def dashboard(self, csrf):
        options = ''.join('<option value="%s">%s</option>' % (
            html.escape(s['id']), html.escape(s['name'])) for s in self.server.config['servers'])
        return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>校园网控制台</title>
<style>%s</style><main class="shell"><header class="top"><div class="brand">YSU / NETLOGIN</div>
<div><span class="secure"><span class="statusdot"></span>SSH 白名单已启用</span>
<a href="/logout"><button class="logout">退出</button></a></div></header>
<div class="hero"><section><h1>让服务器<br>回到线上。</h1>
<p class="lead">从校园 LAN 选择目标服务器，提交本次使用的校园网账号和运营商。凭据只通过 SSH 标准输入传递，不会保存在控制台。</p>
<div class="facts"><div class="fact"><b>%d</b><span>白名单服务器</span></div>
<div class="fact"><b>4</b><span>认证服务</span></div><div class="fact"><b>0</b><span>持久化密码</span></div></div></section>
<section class="panel"><h2>连接校园网</h2><form id="runForm">
<input type="hidden" name="csrf" value="%s"><label for="server">目标服务器</label>
<select id="server" name="server">%s</select><label for="username">校园网账号</label>
<input id="username" name="username" autocomplete="username" placeholder="学号 / 账号">
<label for="password">校园网密码</label><input id="password" name="password" type="password"
autocomplete="current-password" placeholder="仅本次请求使用"><label for="service">运营商</label>
<select id="service" name="service"><option value="0">校园网</option><option value="1">中国移动</option>
<option value="2">中国联通</option><option value="3">中国电信</option></select>
<div class="actions"><button name="action" value="status" class="secondary">查询状态</button>
<button name="action" value="login" class="primary">连接</button></div></form>
<div id="result" class="result" hidden aria-live="polite"></div>
<p class="note">仅可访问配置文件中的服务器。状态查询不需要填写校园网账号密码。</p></section></div></main>
<script>
const form=document.getElementById('runForm'),out=document.getElementById('result');
form.addEventListener('submit',async e=>{e.preventDefault();const button=e.submitter;
const data=new FormData(form);data.set('action',button.value);document.querySelectorAll('button').forEach(b=>b.disabled=true);
out.hidden=false;out.className='result';out.textContent=button.value==='status'?'正在查询…':'正在通过 SSH 认证…';
try{const r=await fetch('/api/run',{method:'POST',body:new URLSearchParams(data)});
const j=await r.json();out.classList.toggle('error',!j.ok);
const s=j.summary||{};out.textContent=j.ok?(j.message||['状态：'+(s.online?'在线':'离线'),'服务器：'+(j.server||''),'账号：'+(s.userId||'—'),'服务：'+(s.service||'—'),'IP：'+(s.userIp||'—')].join('\\n')):(j.message||'操作失败');
if(button.value==='login')document.getElementById('password').value='';}catch(err){out.className='result error';out.textContent='网络请求失败：'+err}
finally{document.querySelectorAll('button').forEach(b=>b.disabled=false)}})
</script></html>""" % (STYLE, len(self.server.config['servers']), html.escape(csrf), options)

def main():
    parser = argparse.ArgumentParser(description='YSU netlogin LAN GUI')
    parser.add_argument('--config', default=str(Path(__file__).with_name('lan_gui_config.json')))
    args = parser.parse_args()
    config = load_config(args.config)
    password = os.environ.get('YSU_GUI_PASSWORD', '')
    if len(password) < 10:
        raise SystemExit('请设置至少 10 位的环境变量 YSU_GUI_PASSWORD')
    server = ThreadingHTTPServer((config.get('listen', '0.0.0.0'), int(config.get('port', 8765))), Handler)
    server.config = config
    server.gui_password = password
    server.secret = secrets.token_bytes(32)
    cert = config.get('tls_cert')
    key = config.get('tls_key')
    if cert and key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = 'https'
    else:
        scheme = 'http'
        if config.get('listen', '0.0.0.0') not in ('127.0.0.1', '::1', 'localhost') and not config.get('allow_insecure_http'):
            raise SystemExit('LAN 监听必须配置 tls_cert/tls_key；仅测试时可显式设置 allow_insecure_http=true')
    server.is_tls = scheme == 'https'
    print('LAN GUI: %s://%s:%s' % (scheme, *server.server_address))
    server.serve_forever()

if __name__ == '__main__':
    main()
