#!/usr/bin/env python3
"""
Yahoo Pro Trial 一推到底 — 道法自然·万法归宗
=============================================
虚拟卡需求 = 0. Pro Trial = 免费(2周100积分, 零支付).

底层解构:
  ┌─────────────────────────────────────────────────────┐
  │  Windsurf Pro Trial 不需要任何信用卡/虚拟卡         │
  │  注册只需: email + password + 邮箱验证             │
  │  Pro Trial 自动激活: 2周 100积分 零支付             │
  └─────────────────────────────────────────────────────┘

SMS三源降级(零成本优先):
  S0: OnePlus手机SIM (ADB读短信, 完全零成本!)  ← 首选
  S1: SMS-Activate API (~$0.10/号)
  S2: 5sim.net API (~$0.05/号)
  S3: 手动输入 (人工)

五阶段一推到底:
  Phase 1: Yahoo邮箱创建 (DrissionPage + OnePlus SMS)
  Phase 2: Windsurf注册 (DrissionPage + turnstilePatch)
  Phase 3: Yahoo IMAP取验证码
  Phase 4: Firebase signIn → RegisterUser → apiKey
  Phase 5: Pro Trial确认 + 号池注入

用法:
  python _yahoo_pro_trial.py                # 交互式注册1个(OnePlus优先)
  python _yahoo_pro_trial.py --batch 5      # 批量注册5个
  python _yahoo_pro_trial.py --status       # 查看服务状态
  python _yahoo_pro_trial.py --harvest      # 收割已有Yahoo账号
  python _yahoo_pro_trial.py --probe        # 全链路可达性探测

OnePlus手机准备:
  1. USB连接电脑 + 开启USB调试
  2. adb devices 可看到设备
  3. 引擎自动通过ADB读取短信验证码

环境变量 (secrets.env, 可选):
  SMS_API_KEY         — SMS-Activate/5sim API Key (OnePlus不可用时降级)
  SMS_SERVICE         — smsactivate | 5sim (默认: smsactivate)
  CAPTCHA_API_KEY     — 2Captcha/CapSolver API Key (可选, ~$0.001/次)
  CAPTCHA_SERVICE     — 2captcha | capsolver (默认: 2captcha)

  注意: 不需要任何虚拟卡/信用卡相关配置!
"""

import json, os, sys, time, random, string, re, ssl, socket, struct
import subprocess, traceback, imaplib
import email as email_lib
import html as html_mod

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen, ProxyHandler, build_opener, HTTPSHandler
from urllib.error import HTTPError, URLError

VERSION = '2.0.0'  # OnePlus integrated
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT.parent / 'data'
CST = timezone(timedelta(hours=8))

SECRETS_ENV = Path(r'e:\道\道生一\一生二\secrets.env')
RESULTS_FILE = SCRIPT_DIR / '_yahoo_pro_trial_results.json'
LOG_FILE = SCRIPT_DIR / '_yahoo_pro_trial.log'

# ═══════════════════════════════════════════════════════
# §1  常量 — 逆向提取
# ═══════════════════════════════════════════════════════

FIREBASE_KEYS = [
    'AIzaSyDsOl-1XpT5err0Tcnx8FFod1H8gVGIycY',
    'AIzaSyDKm6GGxMJfCbNf-k0kPytiGLaqFJpeSac',
]

REGISTER_URLS = [
    'https://register.windsurf.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
    'https://server.codeium.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
    'https://web-backend.windsurf.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
]

PLAN_STATUS_URLS = [
    'https://server.codeium.com/exa.seat_management_pb.SeatManagementService/GetPlanStatus',
    'https://web-backend.windsurf.com/exa.seat_management_pb.SeatManagementService/GetPlanStatus',
]

WINDSURF_REGISTER_URL = 'https://windsurf.com/account/register'
YAHOO_SIGNUP_URL = 'https://login.yahoo.com/account/create'
YAHOO_FUNCAPTCHA_KEY = 'B5B07C8C-2A0F-4202-8D2F-0DBBB25BA498'
PROXY_PORTS = [7890, 7897, 7891, 10808, 1080]

FIRST_NAMES = [
    "Alex","Jordan","Taylor","Morgan","Casey","Riley","Quinn","Avery",
    "Charlie","Dakota","Emerson","Finley","Harper","Jamie","Kendall","Logan",
    "Madison","Parker","Reese","Skyler","Blake","Drew","Eden","Gray","Sam",
]
LAST_NAMES = [
    "Anderson","Brooks","Carter","Davis","Edwards","Fisher","Garcia",
    "Hughes","Irving","Jensen","Kim","Lee","Mitchell","Nelson","Ortiz",
    "Park","Quinn","Rivera","Smith","Turner","Walker","Young","Zhang",
]

WS_APPDATA = Path(os.environ.get('APPDATA', '')) / 'Windsurf'
ACCT_FILE_PATHS = [
    WS_APPDATA / 'User' / 'globalStorage' / 'zhouyoukang.windsurf-assistant' / 'windsurf-assistant-accounts.json',
    WS_APPDATA / 'User' / 'globalStorage' / 'windsurf-login-accounts.json',
    WS_APPDATA / 'User' / 'globalStorage' / 'undefined_publisher.windsurf-login-helper' / 'windsurf-login-accounts.json',
]
YAHOO_FILE = DATA_DIR / '账号.txt'

# ADB — OnePlus手机直连
ONEPLUS_SERIAL = '158377ff'  # OnePlus NE2210
ADB_EXE = None
for _p in [Path(r'E:\道\道生一\一生二\scrcpy\adb.exe'),
           Path(os.environ.get('LOCALAPPDATA', '')) / 'Android' / 'Sdk' / 'platform-tools' / 'adb.exe']:
    if _p.exists():
        ADB_EXE = str(_p)
        break


# ═══════════════════════════════════════════════════════
# §2  工具层
# ═══════════════════════════════════════════════════════

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE
_proxy_cache = None


def log(msg, ok=None, to_file=True):
    icon = "+" if ok is True else ("-" if ok is False else "*")
    ts = datetime.now(CST).strftime("%H:%M:%S")
    line = f"  [{ts}][{icon}] {msg}"
    print(line)
    if to_file:
        try:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now(CST).strftime('%Y-%m-%d')} {line}\n")
        except Exception:
            pass


def detect_proxy():
    global _proxy_cache
    if _proxy_cache is not None:
        return _proxy_cache
    for port in PROXY_PORTS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(('127.0.0.1', port))
            s.close()
            _proxy_cache = port
            return port
        except Exception:
            continue
    _proxy_cache = 0
    return 0


def proxy_str():
    port = detect_proxy()
    return f"http://127.0.0.1:{port}" if port else None


CLASH_API = 'http://127.0.0.1:39798'
_node_blacklist = set()

def _clash_switch_node():
    """Auto-switch Clash proxy node when current node is dead."""
    global _node_blacklist
    try:
        req = Request(f'{CLASH_API}/proxies')
        data = json.loads(urlopen(req, timeout=3).read())
        proxies = data.get('proxies', {})
        # Find current node
        selector = proxies.get('\u8282\u70b9\u9009\u62e9', {})
        current = selector.get('now', '')
        _node_blacklist.add(current)
        # Find alive nodes sorted by latency
        alive = []
        for name, info in proxies.items():
            if info.get('type') in ('Trojan', 'Shadowsocks', 'Vmess', 'Vless'):
                if name in _node_blacklist:
                    continue
                hist = info.get('history', [])
                if hist and hist[-1].get('delay', 0) > 0:
                    delay = hist[-1]['delay']
                    if delay < 2000:
                        alive.append((delay, name))
        alive.sort()
        if not alive:
            _node_blacklist.clear()
            return False
        _, best = alive[0]
        # Switch
        body = json.dumps({'name': best}).encode()
        switch_url = f'{CLASH_API}/proxies/%E8%8A%82%E7%82%B9%E9%80%89%E6%8B%A9'
        req2 = Request(switch_url, data=body, method='PUT')
        req2.add_header('Content-Type', 'application/json')
        urlopen(req2, timeout=3)
        log(f"Proxy node switched: {current} -> {best}", True)
        return True
    except Exception:
        return False


def http_json(url, data=None, method='POST', use_proxy=True, timeout=15, headers=None):
    body = json.dumps(data).encode('utf-8') if data else None
    req = Request(url, data=body, method=method)
    req.add_header('Content-Type', 'application/json')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    port = detect_proxy() if use_proxy else 0
    try:
        if port > 0:
            handler = ProxyHandler({'https': f'http://127.0.0.1:{port}', 'http': f'http://127.0.0.1:{port}'})
            resp = build_opener(handler, HTTPSHandler(context=_ssl_ctx)).open(req, timeout=timeout)
        else:
            resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read()
        try:
            return json.loads(body)
        except Exception:
            raise RuntimeError(f"HTTP {e.code}: {body[:300]}")


def grpc_web_wrap(proto_bytes: bytes) -> bytes:
    """Wrap raw protobuf in gRPC-web frame (5-byte header: compression_flag + uint32_length)"""
    return struct.pack('>?I', False, len(proto_bytes)) + proto_bytes


def grpc_web_unwrap(data: bytes) -> bytes:
    """Strip gRPC-web frame header, return raw protobuf payload"""
    if len(data) >= 5 and data[0] in (0x00, 0x01):
        length = struct.unpack('>I', data[1:5])[0]
        if 5 + length <= len(data):
            return data[5:5 + length]
    return data


def http_bin(url, bin_data, use_proxy=True, timeout=15, grpc_web=True):
    if grpc_web:
        payload = grpc_web_wrap(bin_data)
    else:
        payload = bin_data
    req = Request(url, data=payload, method='POST')
    ct = 'application/grpc-web+proto' if grpc_web else 'application/proto'
    req.add_header('Content-Type', ct)
    req.add_header('Accept', ct)
    if grpc_web:
        req.add_header('X-Requested-With', 'XmlHttpRequest')
    port = detect_proxy() if use_proxy else 0
    if port > 0:
        handler = ProxyHandler({'https': f'http://127.0.0.1:{port}', 'http': f'http://127.0.0.1:{port}'})
        resp = build_opener(handler, HTTPSHandler(context=_ssl_ctx)).open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    raw = resp.read()
    if grpc_web:
        return grpc_web_unwrap(raw)
    return raw


def gen_password():
    chars = string.ascii_letters + string.digits + "!@#$"
    pw = (random.choice(string.ascii_uppercase) + random.choice(string.ascii_lowercase) +
          random.choice(string.digits) + random.choice("!@#$") +
          ''.join(random.choices(chars, k=12)))
    return ''.join(random.sample(pw, len(pw)))


def gen_username():
    return ''.join(random.choices(string.ascii_lowercase, k=random.randint(5, 8))) + \
           ''.join(random.choices(string.digits, k=random.randint(4, 7)))


def load_secrets():
    d = {}
    for p in [SECRETS_ENV, PROJECT_ROOT / 'secrets.env', Path('secrets.env')]:
        if p.exists():
            for line in open(p, 'r', encoding='utf-8'):
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    d[k.strip()] = v.strip().strip('"').strip("'")
            break
    for key in ['CAPTCHA_API_KEY', 'CAPTCHA_SERVICE', 'SMS_API_KEY', 'SMS_SERVICE']:
        if key in os.environ:
            d[key] = os.environ[key]
    return d


def load_results():
    if RESULTS_FILE.exists():
        try:
            return json.loads(RESULTS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return []


def save_result(result):
    results = load_results()
    results.append(result)
    RESULTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')


# ═══════════════════════════════════════════════════════
# §2b  TempEmailProvider (mail.tm — 零配置自动临时邮箱)
# ═══════════════════════════════════════════════════════

class TempEmailProvider:
    """mail.tm API — create disposable email + read messages, zero config"""
    BASE = "https://api.mail.tm"

    def __init__(self):
        self.email = None
        self.password = None
        self.token = None
        self._domain = None

    def create(self):
        """Create a new temp email. Returns email address or None."""
        try:
            domains = self._api('/domains', method='GET')
            members = domains if isinstance(domains, list) else domains.get('hydra:member', [])
            if not members:
                log("mail.tm: no domains available", False)
                return None
            self._domain = members[0].get('domain', '')
            uid = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
            self.email = f"ws{uid}@{self._domain}"
            self.password = gen_password()
            result = self._api('/accounts', method='POST',
                               data={'address': self.email, 'password': self.password})
            if not result or not result.get('id'):
                log(f"mail.tm: create failed — {result}", False)
                return None
            token_resp = self._api('/token', method='POST',
                                   data={'address': self.email, 'password': self.password})
            self.token = token_resp.get('token') if token_resp else None
            if not self.token:
                log("mail.tm: token failed", False)
                return None
            log(f"mail.tm: {self.email} ready", True)
            return self.email
        except Exception as e:
            log(f"mail.tm create error: {e}", False)
            return None

    def get_messages(self):
        """Fetch all messages. Returns list of dicts."""
        if not self.token:
            return []
        try:
            data = self._api('/messages', method='GET', auth=True)
            members = data if isinstance(data, list) else data.get('hydra:member', [])
            return members
        except Exception:
            return []

    def get_code(self, sender_keyword="yahoo", max_wait=120):
        """Poll for verification code from sender. Returns code string or None."""
        start = time.time()
        while time.time() - start < max_wait:
            msgs = self.get_messages()
            for msg in msgs:
                from_addr = msg.get('from', {}).get('address', '').lower()
                subject = msg.get('subject', '').lower()
                if sender_keyword.lower() in from_addr or sender_keyword.lower() in subject:
                    full = self._api(f"/messages/{msg['id']}", method='GET', auth=True)
                    if full:
                        text = full.get('text', '') or ''
                        html = full.get('html', [''])[0] if full.get('html') else ''
                        body = text + html
                        codes = re.findall(r'\b(\d{6,8})\b', body)
                        if codes:
                            log(f"mail.tm code: {codes[0]} (from {from_addr})", True)
                            return codes[0]
                        links = re.findall(r'https?://[^\s<>"\']+', body)
                        for link in links:
                            if any(k in link.lower() for k in ['verify', 'confirm', 'oobcode']):
                                log(f"mail.tm verify link found", True)
                                return link
            elapsed = int(time.time() - start)
            if elapsed % 30 == 0 and elapsed > 0:
                log(f"mail.tm: waiting for email... ({elapsed}s/{max_wait}s)")
            time.sleep(8)
        log("mail.tm: no email received", False)
        return None

    def get_windsurf_verify(self, max_wait=180):
        """Poll for Windsurf/Codeium verification code or link."""
        start = time.time()
        while time.time() - start < max_wait:
            msgs = self.get_messages()
            for msg in msgs:
                from_addr = msg.get('from', {}).get('address', '').lower()
                subject = msg.get('subject', '').lower()
                if any(k in from_addr or k in subject for k in ['codeium', 'windsurf', 'verify']):
                    full = self._api(f"/messages/{msg['id']}", method='GET', auth=True)
                    if full:
                        text = full.get('text', '') or ''
                        html = full.get('html', [''])[0] if full.get('html') else ''
                        body = text + html
                        codes = re.findall(r'\b(\d{6})\b', body)
                        if codes:
                            log(f"mail.tm Windsurf code: {codes[0]}", True)
                            return {"type": "code", "value": codes[0]}
                        content = html_mod.unescape(body)
                        links = re.findall(r'https?://[^\s<>"\']+', content)
                        for link in links:
                            if any(k in link.lower() for k in ['verify', 'confirm', 'oobcode', 'windsurf', 'codeium']):
                                log(f"mail.tm Windsurf verify link found!", True)
                                return {"type": "link", "value": link}
            elapsed = int(time.time() - start)
            if elapsed % 30 == 0 and elapsed > 0:
                log(f"mail.tm: waiting for Windsurf email... ({elapsed}s/{max_wait}s)")
            time.sleep(10)
        log("mail.tm: no Windsurf verification email", False)
        return None

    def _api(self, path, method='GET', data=None, auth=False):
        """Call mail.tm API (via proxy)."""
        url = self.BASE + path
        body = json.dumps(data).encode() if data else None
        req = Request(url, data=body, method=method)
        req.add_header('Content-Type', 'application/json')
        req.add_header('Accept', 'application/json')
        if auth and self.token:
            req.add_header('Authorization', f'Bearer {self.token}')
        port = detect_proxy()
        try:
            if port:
                handler = ProxyHandler({'https': f'http://127.0.0.1:{port}', 'http': f'http://127.0.0.1:{port}'})
                resp = build_opener(handler, HTTPSHandler(context=_ssl_ctx)).open(req, timeout=12)
            else:
                resp = urlopen(req, timeout=12, context=_ssl_ctx)
            return json.loads(resp.read())
        except HTTPError as e:
            try:
                return json.loads(e.read())
            except Exception:
                return None
        except Exception:
            return None


# ═══════════════════════════════════════════════════════
# §3  Protobuf 最小编解码
# ═══════════════════════════════════════════════════════

def encode_proto(value: str, field: int = 1) -> bytes:
    b = value.encode('utf-8')
    tag = (field << 3) | 2
    length = len(b)
    len_bytes = bytearray()
    while length > 127:
        len_bytes.append((length & 0x7f) | 0x80)
        length >>= 7
    len_bytes.append(length)
    return bytes([tag]) + bytes(len_bytes) + b


def read_varint(data: bytes, pos: int):
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]; pos += 1
        if shift < 28:
            result |= (b & 0x7f) << shift
        else:
            result += (b & 0x7f) * (2 ** shift)
        if (b & 0x80) == 0:
            break
        shift += 7
    return result, pos


def extract_proto_strings(buf: bytes) -> list:
    strings = []
    pos = 0
    while pos < len(buf):
        try:
            tag, new_pos = read_varint(buf, pos)
        except Exception:
            break
        fn = tag >> 3; wt = tag & 0x07
        if fn == 0 or fn > 1000 or new_pos >= len(buf):
            break
        pos = new_pos
        if wt == 0:
            _, pos = read_varint(buf, pos)
        elif wt == 2:
            length, pos = read_varint(buf, pos)
            if length < 0 or length > 65536 or pos + length > len(buf):
                break
            data = buf[pos:pos + length]
            try:
                s = data.decode('utf-8')
                if all(0x20 <= ord(c) <= 0x7e or c in '\n\r\t' for c in s):
                    strings.append((fn, s))
                else:
                    strings.extend(extract_proto_strings(data))
            except UnicodeDecodeError:
                strings.extend(extract_proto_strings(data))
            pos += length
        elif wt == 1:
            pos += 8
        elif wt == 5:
            pos += 4
        else:
            break
    return strings


# ═══════════════════════════════════════════════════════
# §4  Firebase Auth + Windsurf gRPC
# ═══════════════════════════════════════════════════════

def firebase_signin(email: str, password: str) -> dict:
    payload = {'email': email, 'password': password, 'returnSecureToken': True}
    fb_headers = {'Referer': 'https://windsurf.com/'}
    for attempt in range(3):  # up to 3 attempts with node switching
        for key in FIREBASE_KEYS:
            url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}'
            for use_p in [True, False]:  # proxy first — Google blocked direct in China
                try:
                    r = http_json(url, payload, use_proxy=use_p, timeout=10, headers=fb_headers)
                    if r.get('idToken'):
                        return r
                    if r.get('error'):
                        return r  # real Firebase error, no retry needed
                except Exception:
                    continue
        # All keys+modes failed — try switching proxy node
        if not _clash_switch_node():
            break
    return {'error': 'signin_failed'}


def windsurf_register_user(id_token: str) -> dict:
    buf = encode_proto(id_token)
    for url in REGISTER_URLS:
        for use_p in [False, True]:  # direct first — Windsurf endpoints reachable in China
            try:
                raw = http_bin(url, buf, use_proxy=use_p, timeout=12)
                if raw and len(raw) > 3:
                    strings = extract_proto_strings(raw)
                    for fn, s in strings:
                        if len(s) > 30 and ' ' not in s:
                            return {'ok': True, 'apiKey': s}
                    if raw[0] == 0x0a:
                        length, pos = read_varint(raw, 1)
                        if pos + length <= len(raw):
                            api_key = raw[pos:pos + length].decode('utf-8', errors='replace')
                            if api_key and len(api_key) > 10:
                                return {'ok': True, 'apiKey': api_key}
            except Exception:
                continue
    return {'ok': False, 'error': 'all_register_channels_failed'}


def windsurf_plan_status(id_token: str) -> dict:
    buf = encode_proto(id_token)
    for url in PLAN_STATUS_URLS:
        for use_p in [False, True]:  # direct first — Windsurf endpoints reachable in China
            try:
                raw = http_bin(url, buf, use_proxy=use_p, timeout=12)
                if raw and len(raw) > 0:
                    strings = extract_proto_strings(raw)
                    raw_strings = [f"f{fn}={s}" for fn, s in strings]
                    plan = 'no_response'
                    for fn, s in strings:
                        sl = s.lower().strip()
                        if sl in ('pro_trial', 'trial'):
                            plan = 'pro_trial'
                            break
                        if sl == 'free':
                            plan = 'free'
                    if plan == 'no_response' and strings:
                        plan = 'unknown'
                    return {'ok': True, 'plan': plan, 'raw_strings': raw_strings}
            except Exception:
                continue
    return {'ok': False, 'plan': 'unreachable'}


# ═══════════════════════════════════════════════════════
# §5  号池操作
# ═══════════════════════════════════════════════════════

def load_pool_accounts():
    best = []
    paths = []
    for fp in ACCT_FILE_PATHS:
        if fp.exists():
            paths.append(fp)
            try:
                d = json.loads(fp.read_text(encoding='utf-8'))
                if isinstance(d, list) and len(d) > len(best):
                    best = d
            except Exception:
                pass
    return best, paths


def inject_to_pool(email, password, api_key=None, source="yahoo_pro_trial", verified_plan=None):
    _, paths = load_pool_accounts()
    if not paths:
        default = ACCT_FILE_PATHS[0]
        default.parent.mkdir(parents=True, exist_ok=True)
        default.write_text('[]', encoding='utf-8')
        paths = [default]

    if verified_plan and verified_plan.lower() == 'free':
        log(f"inject: REJECT {email} — plan=free", False)
        return False

    actual_plan = verified_plan if verified_plan else "pro_trial"
    now_iso = datetime.now(CST).isoformat()
    updated = 0

    for fp in paths:
        try:
            accts = []
            if fp.exists():
                accts = json.loads(fp.read_text(encoding='utf-8'))
            if not isinstance(accts, list):
                accts = []

            found = False
            for a in accts:
                if a.get('email', '').lower() == email.lower():
                    found = True
                    if api_key:
                        a['apiKey'] = api_key
                    if password and not a.get('password'):
                        a['password'] = password
                    a['_activatedBy'] = source
                    a['_activatedAt'] = now_iso
                    if verified_plan:
                        a.setdefault('usage', {})['plan'] = verified_plan
                    updated += 1
                    break

            if not found:
                entry = {
                    "email": email,
                    "password": password,
                    "source": source,
                    "addedAt": now_iso,
                    "usage": {"plan": actual_plan},
                }
                if api_key:
                    entry["apiKey"] = api_key
                accts.append(entry)
                updated += 1

            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps(accts, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            log(f"Inject error {fp.name}: {e}", False)

    return updated > 0


# ═══════════════════════════════════════════════════════
# §6  激活管线 — signIn → Plan验证 → apiKey → 注入
# ═══════════════════════════════════════════════════════

def activate_account(email: str, password: str, quiet=False) -> dict:
    t0 = time.time()
    if not quiet:
        log(f"Phase 4: activate {email}")

    # Step 1: Firebase signIn
    login = firebase_signin(email, password)
    id_token = login.get('idToken')
    if not id_token:
        err = login.get('error', {})
        if isinstance(err, dict):
            err = err.get('message', str(err))
        log(f"signIn FAIL: {err}", False)
        return {'email': email, 'status': 'signin_failed', 'error': str(err)}

    # Step 2: Plan status — Pro Trial确认 (零支付, 自动激活)
    plan_info = windsurf_plan_status(id_token)
    verified_plan = plan_info.get('plan', 'unknown') if plan_info.get('ok') else 'probe_failed'
    if verified_plan == 'free':
        log(f"REJECT: {email} plan=free (Pro Trial未激活或已过期)", False)
        return {'email': email, 'status': 'rejected_free', 'plan': 'free'}
    if not quiet:
        raw = plan_info.get('raw_strings', [])[:3]
        log(f"Plan: {verified_plan} [{', '.join(raw)}]", verified_plan in ('pro_trial', 'unknown'))

    # Step 3: RegisterUser → apiKey
    reg = windsurf_register_user(id_token)
    api_key = reg.get('apiKey')

    # Step 4: Inject to pool
    status = 'activated' if api_key else 'partial'
    inject_to_pool(email, password, api_key=api_key, source="yahoo_pro_trial",
                   verified_plan=verified_plan)

    elapsed = int((time.time() - t0) * 1000)
    result = {
        'email': email,
        'status': status,
        'plan': verified_plan,
        'apiKey': api_key,
        'timestamp': datetime.now(CST).isoformat(),
        'ms': elapsed,
    }

    if api_key:
        log(f"apiKey: {api_key[:25]}... plan={verified_plan} ({elapsed}ms)", True)
    else:
        log(f"partial: no apiKey ({elapsed}ms)", False)

    save_result(result)
    return result


# ═══════════════════════════════════════════════════════
# §7  SMS验证服务 (唯一成本: ~$0.05-0.10)
# ═══════════════════════════════════════════════════════

import base64


def ps_http(method, url, body=None, headers=None, proxy=None, timeout=20):
    ps = ['$ProgressPreference="SilentlyContinue"']
    iwr = f'Invoke-WebRequest -Uri "{url}" -Method {method} -UseBasicParsing -TimeoutSec {timeout}'
    if proxy:
        iwr += f' -Proxy "{proxy}"'
    if body:
        escaped = body.replace('"', '`"')
        iwr += f' -Body "{escaped}" -ContentType "application/json"'
    if headers:
        h = "; ".join(f'"{k}"="{v}"' for k, v in headers.items())
        iwr += f' -Headers @{{{h}}}'
    ps.append(f'try {{ $r = ({iwr}).Content; if ($r -is [byte[]]) {{ [System.Text.Encoding]::UTF8.GetString($r) }} else {{ $r }} }} catch {{ Write-Output ("ERROR:" + $_.Exception.Message) }}')
    enc = base64.b64encode('\n'.join(ps).encode('utf-16-le')).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                       capture_output=True, text=True, timeout=timeout + 25,
                       encoding='utf-8', errors='replace')
    out = r.stdout.strip()
    if not out or out.startswith("ERROR:"):
        raise RuntimeError(out or f"empty, stderr={r.stderr[:200]}")
    for i, ch in enumerate(out):
        if ch in ('{', '['):
            try:
                return json.loads(out[i:])
            except:
                continue
    return {"_raw": out[:500]}


class SMSActivateProvider:
    """SMS-Activate API — ~$0.10/号"""
    BASE = "https://api.sms-activate.org/stubs/handler_api.php"
    SERVICE_MAP = {"yahoo": "yh", "google": "go"}

    def __init__(self, api_key, proxy=None):
        self.api_key = api_key
        self.proxy = proxy

    def get_number(self, service="yahoo", country="us"):
        svc = self.SERVICE_MAP.get(service, service)
        cc = {"us": "187", "uk": "16", "ru": "0", "in": "22"}.get(country, "187")
        r = ps_http("GET", f"{self.BASE}?api_key={self.api_key}&action=getNumber&service={svc}&country={cc}",
                     proxy=self.proxy)
        raw = r.get("_raw", str(r)) if isinstance(r, dict) else str(r)
        if "ACCESS_NUMBER" in raw:
            parts = raw.split(":")
            if len(parts) >= 3:
                return {"activation_id": parts[1], "number": parts[2]}
        raise RuntimeError(f"get_number failed: {raw[:100]}")

    def get_sms_code(self, activation_id, timeout=120):
        start = time.time()
        while time.time() - start < timeout:
            r = ps_http("GET", f"{self.BASE}?api_key={self.api_key}&action=getStatus&id={activation_id}",
                        proxy=self.proxy)
            raw = r.get("_raw", str(r)) if isinstance(r, dict) else str(r)
            if "STATUS_OK" in raw:
                return raw.split(":")[-1].strip()
            elif "STATUS_CANCEL" in raw:
                raise RuntimeError("SMS cancelled")
            time.sleep(5)
        return None

    def cancel(self, activation_id):
        try:
            ps_http("GET", f"{self.BASE}?api_key={self.api_key}&action=setStatus&id={activation_id}&status=8",
                    proxy=self.proxy)
        except Exception:
            pass

    def get_balance(self):
        r = ps_http("GET", f"{self.BASE}?api_key={self.api_key}&action=getBalance", proxy=self.proxy)
        raw = r.get("_raw", str(r)) if isinstance(r, dict) else str(r)
        if "ACCESS_BALANCE" in raw:
            return float(raw.split(":")[-1].strip())
        return 0


class FiveSimProvider:
    """5sim.net API — ~$0.05/号"""
    BASE = "https://5sim.net/v1"

    def __init__(self, api_key, proxy=None):
        self.api_key = api_key
        self.proxy = proxy

    def get_number(self, service="yahoo", country="usa"):
        cc = {"us": "usa", "uk": "england", "ru": "russia"}.get(country, country)
        r = ps_http("GET", f"{self.BASE}/user/buy/activation/{cc}/any/{service}",
                     headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                     proxy=self.proxy)
        if isinstance(r, dict) and r.get("phone"):
            return {"activation_id": str(r["id"]), "number": r["phone"]}
        raise RuntimeError(f"5sim get_number failed: {r}")

    def get_sms_code(self, activation_id, timeout=120):
        start = time.time()
        while time.time() - start < timeout:
            r = ps_http("GET", f"{self.BASE}/user/check/{activation_id}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        proxy=self.proxy)
            if isinstance(r, dict):
                sms_list = r.get("sms", [])
                if sms_list:
                    code = sms_list[0].get("code", "")
                    if code:
                        return code
            time.sleep(5)
        return None

    def cancel(self, activation_id):
        pass

    def get_balance(self):
        r = ps_http("GET", f"{self.BASE}/user/profile",
                     headers={"Authorization": f"Bearer {self.api_key}"},
                     proxy=self.proxy)
        if isinstance(r, dict):
            return float(r.get("balance", 0))
        return 0


class CaptchaSolver2Captcha:
    """2Captcha — ~$0.001/次 FunCaptcha"""
    BASE = "https://2captcha.com"

    def __init__(self, api_key, proxy=None):
        self.api_key = api_key
        self.proxy = proxy

    def solve_funcaptcha(self, public_key, page_url, surl=None):
        log("2Captcha: FunCaptcha...")
        params = {"key": self.api_key, "method": "funcaptcha", "publickey": public_key,
                  "pageurl": page_url, "json": "1"}
        if surl:
            params["surl"] = surl
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        result = ps_http("GET", f"{self.BASE}/in.php?{qs}", proxy=self.proxy)
        if not isinstance(result, dict) or result.get("status") != 1:
            raise RuntimeError(f"submit failed: {result}")
        task_id = result["request"]
        for _ in range(24):
            time.sleep(5)
            r = ps_http("GET", f"{self.BASE}/res.php?key={self.api_key}&action=get&id={task_id}&json=1",
                        proxy=self.proxy)
            if isinstance(r, dict):
                if r.get("status") == 1:
                    log("FunCaptcha solved!", True)
                    return r["request"]
                elif r.get("request") != "CAPCHA_NOT_READY":
                    raise RuntimeError(f"error: {r}")
        raise RuntimeError("timeout 120s")

    def get_balance(self):
        r = ps_http("GET", f"{self.BASE}/res.php?key={self.api_key}&action=getbalance&json=1", proxy=self.proxy)
        if isinstance(r, dict):
            return float(r.get("request", 0))
        return 0


# ═══════════════════════════════════════════════════════
# §7.5  OnePlus手机SIM短信桥 — 完全零成本 (S0)
# ═══════════════════════════════════════════════════════

def _adb(*args, serial=None, timeout=10):
    """Execute ADB command targeting OnePlus"""
    if not ADB_EXE:
        return "", False
    cmd = [ADB_EXE]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding='utf-8', errors='replace')
        return r.stdout.strip(), r.returncode == 0
    except Exception as e:
        return str(e), False


def _adb_phone_connected():
    """Check if OnePlus is connected via ADB"""
    out, ok = _adb("devices")
    if ok:
        for line in out.splitlines():
            if ONEPLUS_SERIAL in line and "\tdevice" in line:
                return True
    return False


def _adb_get_sim_number():
    """Get SIM phone number from OnePlus via ADB"""
    # Method 1: service call iphonesubinfo 15
    out, ok = _adb("shell", "service", "call", "iphonesubinfo", "15", serial=ONEPLUS_SERIAL)
    if ok and "Parcel" in out:
        chars = re.findall(r"'(.+?)'", out)
        if chars:
            raw = ''.join(chars)
            digits = re.sub(r'[^0-9+]', '', raw)
            if len(digits) >= 8:
                return digits
    # Method 2: dumpsys telephony
    out, ok = _adb("shell", "dumpsys", "telephony.registry", serial=ONEPLUS_SERIAL)
    if ok:
        m = re.search(r'mLine1Number=(\+?\d+)', out)
        if m:
            return m.group(1)
    return None


class OnePlusSMSBridge:
    """OnePlus手机SIM短信桥 — ADB直读SMS, 完全零成本"""

    def __init__(self):
        self.phone_number = None
        self.connected = _adb_phone_connected()
        if self.connected:
            self.phone_number = _adb_get_sim_number()
            if self.phone_number:
                log(f"OnePlus SIM: {self.phone_number}", True)
            else:
                log("OnePlus connected but SIM number not detected", False)

    @property
    def available(self):
        return self.connected

    def get_number(self, service="yahoo", country="us"):
        if not self.connected:
            raise RuntimeError("OnePlus not connected via ADB")
        # For Yahoo: Chinese number needs +86 prefix if not present
        number = self.phone_number or "MANUAL"
        if number != "MANUAL" and not number.startswith('+'):
            if len(number) == 11 and number.startswith('1'):
                number = '+86' + number
        return {"activation_id": "oneplus_sim", "number": number}

    def get_sms_code(self, activation_id=None, timeout=120, keyword=None):
        """Poll OnePlus SMS inbox via ADB for verification code"""
        log(f"OnePlus: waiting for SMS (max {timeout}s)...")
        start = time.time()
        seen = set()

        # Snapshot initial SMS IDs to skip old messages
        initial_ids = self._get_all_sms_ids()
        seen.update(initial_ids)

        while time.time() - start < timeout:
            code = self._read_latest_sms(keyword=keyword, seen=seen)
            if code:
                log(f"OnePlus SMS code: {code}", True)
                return code
            elapsed = int(time.time() - start)
            if elapsed > 0 and elapsed % 15 == 0:
                log(f"Waiting for SMS... ({elapsed}s/{timeout}s)")
            time.sleep(3)

        log("OnePlus SMS timeout", False)
        return None

    def _get_all_sms_ids(self):
        """Get all current SMS IDs to track new messages"""
        ids = set()
        out, ok = _adb("shell", "content", "query", "--uri", "content://sms/inbox",
                        "--projection", "_id", "--sort", "date DESC",
                        serial=ONEPLUS_SERIAL)
        if ok:
            for line in out.splitlines():
                m = re.search(r'_id=(\d+)', line)
                if m:
                    ids.add(m.group(1))
        return ids

    def _read_latest_sms(self, keyword=None, seen=None):
        """Read latest SMS messages and extract verification code"""
        if seen is None:
            seen = set()
        out, ok = _adb("shell", "content", "query", "--uri", "content://sms/inbox",
                        "--projection", "_id:body:date:address",
                        "--sort", "date DESC",
                        serial=ONEPLUS_SERIAL)
        if not ok:
            return None

        for line in out.splitlines()[:10]:  # Check only latest 10
            m_id = re.search(r'_id=(\d+)', line)
            m_body = re.search(r'body=(.+?)(?:,\s*date=|$)', line)
            if m_id and m_body:
                sid = m_id.group(1)
                body = m_body.group(1)
                if sid in seen:
                    continue
                seen.add(sid)
                # Yahoo verification codes are typically 5-8 digits
                codes = re.findall(r'\b(\d{5,8})\b', body)
                if codes:
                    # Filter: Yahoo/verification context
                    body_lower = body.lower()
                    if keyword and keyword.lower() not in body_lower:
                        continue
                    # Accept if it looks like a verification SMS
                    if any(k in body_lower for k in ['yahoo', 'verify', 'code', 'verification',
                                                      '\u9a8c\u8bc1\u7801', '\u9a8c\u8bc1', 'confirm']):
                        return codes[0]
                    # Also accept any 5-6 digit code from new SMS (likely verification)
                    if len(codes[0]) in (5, 6):
                        return codes[0]
        return None

    def cancel(self, activation_id=None):
        pass

    def get_balance(self):
        return float('inf')  # Free!


# ═══════════════════════════════════════════════════════
# §8  浏览器引擎 — Chrome + DrissionPage
# ═══════════════════════════════════════════════════════

def find_chrome():
    for p in [
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    ]:
        if os.path.exists(p):
            return p
    return None


def find_turnstile_patch():
    for tp in [SCRIPT_DIR / "turnstilePatch", SCRIPT_DIR / "_archive" / "turnstilePatch",
               PROJECT_ROOT / "turnstilePatch"]:
        if tp.exists():
            return str(tp)
    return None


def kill_chrome():
    try:
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, timeout=8)
        time.sleep(1.5)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# §9  Phase 1: Yahoo邮箱创建
# ═══════════════════════════════════════════════════════

def create_yahoo_with_existing_email(existing_email=None, no_proxy=False):
    """
    Yahoo 'My email' path — 2026-04 breakthrough:
      NO phone verification, NO WhatsApp, NO SMS!
      Steps: Click 'My email' → name + email + birth year → email code → done
      Returns: {email, password, first_name, last_name} or None
    """
    fn = random.choice(FIRST_NAMES)
    ln = random.choice(LAST_NAMES)
    yahoo_pw = gen_password()
    birth_y = str(random.randint(1985, 2002))

    temp_provider = None
    if not existing_email:
        secrets = load_secrets()
        gmail_base = secrets.get("GMAIL_BASE", "")
        gmail_pw = secrets.get("GMAIL_APP_PASSWORD", "")
        if gmail_base and gmail_pw:
            idx = random.randint(100, 999)
            user = gmail_base.split("@")[0] if "@" in gmail_base else gmail_base
            domain = gmail_base.split("@")[1] if "@" in gmail_base else "gmail.com"
            existing_email = f"{user}+ws{idx}@{domain}"
            log(f"Email source: Gmail IMAP ({existing_email})", True)
        else:
            log("Gmail IMAP not configured — trying mail.tm auto email...", True)
            temp_provider = TempEmailProvider()
            existing_email = temp_provider.create()
            if not existing_email:
                if gmail_base:
                    idx = random.randint(100, 999)
                    user = gmail_base.split("@")[0] if "@" in gmail_base else gmail_base
                    domain = gmail_base.split("@")[1] if "@" in gmail_base else "gmail.com"
                    existing_email = f"{user}+ws{idx}@{domain}"
                    log(f"mail.tm failed, fallback to Gmail (manual code): {existing_email}", False)
                else:
                    log("No email source available", False)
                    return None

    print(f"\n  Phase 1: Yahoo Create [MY_EMAIL path — no phone!]")
    print(f"  Name: {fn} {ln} | Existing: {existing_email}")

    from DrissionPage import ChromiumOptions, ChromiumPage

    chrome = find_chrome()
    if not chrome:
        log("Chrome not found", False)
        return None

    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="yahoo_myemail_")
    co = ChromiumOptions()
    co.set_browser_path(chrome)
    co.set_argument("--incognito")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")
    co.set_user_data_path(tmp_dir)
    co.auto_port()
    co.headless(False)
    if no_proxy:
        co.set_argument("--no-proxy-server")
        log("No-proxy mode: direct connection", True)
    else:
        px = proxy_str()
        if px:
            co.set_argument(f"--proxy-server={px.replace('http://', '')}")

    page = ChromiumPage(co)
    try:
        # Go directly to 'usernameregsimplified' URL — the 'My email' path
        my_email_url = YAHOO_SIGNUP_URL + '?intl=us&lang=en-US&specId=usernameregsimplified&done=https%3A%2F%2Fwww.yahoo.com'
        page.get(my_email_url)
        time.sleep(random.uniform(3, 5))
        log("Navigated to 'My email' path", True)

        # Fill form: name, email, birth year
        for sel, val in [("@id=reg-firstName", fn),
                          ("@id=usernamereg-firstName", fn),
                          ("tag:input@name=firstName", fn)]:
            try:
                el = page.ele(sel, timeout=3)
                if el and not el.value:
                    el.input(val)
                    time.sleep(random.uniform(0.3, 0.7))
                    break
            except Exception:
                pass

        for sel, val in [("@id=reg-lastName", ln),
                          ("@id=usernamereg-lastName", ln),
                          ("tag:input@name=lastName", ln)]:
            try:
                el = page.ele(sel, timeout=3)
                if el and not el.value:
                    el.input(val)
                    time.sleep(random.uniform(0.3, 0.7))
                    break
            except Exception:
                pass

        for sel in ["@id=reg-email", "@id=usernamereg-email",
                     "tag:input@name=email", "tag:input@type=email"]:
            try:
                el = page.ele(sel, timeout=3)
                if el:
                    el.clear()
                    el.input(existing_email)
                    time.sleep(random.uniform(0.3, 0.7))
                    log(f"Email: {existing_email}", True)
                    break
            except Exception:
                pass

        for sel in ["@id=reg-birthYear", "@id=usernamereg-year",
                     "tag:input@name=birthYear"]:
            try:
                el = page.ele(sel, timeout=3)
                if el:
                    el.input(birth_y)
                    time.sleep(0.3)
                    break
            except Exception:
                pass

        log("Form filled, clicking Next...", True)
        time.sleep(random.uniform(1, 2))

        clicked = False
        for sel in ["tag:button@text():Next", "@type=submit"]:
            try:
                btn = page.ele(sel, timeout=3)
                if btn:
                    btn.click()
                    clicked = True
                    time.sleep(random.uniform(3, 6))
                    break
            except Exception:
                pass
        if not clicked:
            log("Next not found", False)
            input("  >> Click Next in browser, press Enter...")
            time.sleep(2)

        body = (page.html or "").lower()
        url_now = page.url or ""

        if "challenge/fail" in url_now or "something went wrong" in body:
            log("Yahoo anti-fraud blocked (error 3692). Try --no-proxy", False)
            return None

        if "funcaptcha" in body or "arkoselabs" in body or "captcha" in body:
            log("CAPTCHA — solve manually", False)
            input("  >> Solve CAPTCHA, press Enter...")
            time.sleep(2)
            body = (page.html or "").lower()

        # Email verification code
        if "verify" in body or "code" in body or "enter the code" in body:
            log("Email code page reached!", True)
            code = None
            # Strategy 1: mail.tm auto read
            if temp_provider and temp_provider.token:
                log("Reading Yahoo code from mail.tm...", True)
                code = temp_provider.get_code(sender_keyword="yahoo", max_wait=120)
            # Strategy 2: Gmail IMAP
            if not code:
                print(f"  >> Check {existing_email} for Yahoo code")
            secrets = load_secrets()
            gmail_pw = secrets.get("GMAIL_APP_PASSWORD", "")
            if not code and gmail_pw and "gmail.com" in existing_email:
                base_email = existing_email.split("+")[0] + "@gmail.com" if "+" in existing_email else existing_email
                try:
                    import imaplib, email as emaillib
                    imap = imaplib.IMAP4_SSL("imap.gmail.com")
                    imap.login(base_email, gmail_pw)
                    imap.select("INBOX")
                    for _ in range(12):
                        time.sleep(10)
                        _, msgs = imap.search(None, '(FROM "yahoo" UNSEEN)')
                        ids = msgs[0].split()
                        if ids:
                            _, data = imap.fetch(ids[-1], "(RFC822)")
                            msg = emaillib.message_from_bytes(data[0][1])
                            text = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        text = part.get_payload(decode=True).decode(errors="replace")
                                        break
                            else:
                                text = msg.get_payload(decode=True).decode(errors="replace")
                            m = re.search(r'\b(\d{6,8})\b', text)
                            if m:
                                code = m.group(1)
                                log(f"Email code: {code}", True)
                                break
                    imap.logout()
                except Exception as e:
                    log(f"IMAP: {e}", False)
            if not code:
                code = input("  >> Enter code from email: ").strip()
            if code:
                for sel in ["@id=verification-code-field", "@name=code",
                             "@id=reg-code", "@name=verificationCode",
                             "tag:input@type=tel", "tag:input@maxlength=8"]:
                    try:
                        ci = page.ele(sel, timeout=5)
                        if ci:
                            ci.clear()
                            ci.input(code)
                            time.sleep(0.5)
                            break
                    except Exception:
                        pass
                for sel in ["tag:button@text():Verify", "tag:button@text():Submit",
                             "tag:button@text():Next", "@type=submit"]:
                    try:
                        btn = page.ele(sel, timeout=3)
                        if btn:
                            btn.click()
                            time.sleep(random.uniform(3, 5))
                            break
                    except Exception:
                        pass
                log("Code submitted!", True)

        # Password setup
        time.sleep(2)
        body = (page.html or "").lower()
        if "password" in body and ("set" in body or "create" in body or "choose" in body):
            log("Setting password...", True)
            for sel in ["@id=password", "@name=password", "tag:input@type=password"]:
                try:
                    pw_el = page.ele(sel, timeout=3)
                    if pw_el:
                        pw_el.input(yahoo_pw)
                        time.sleep(0.5)
                        break
                except Exception:
                    pass
            for sel in ["tag:button@text():Next", "tag:button@text():Done",
                         "@type=submit"]:
                try:
                    btn = page.ele(sel, timeout=3)
                    if btn:
                        btn.click()
                        time.sleep(3)
                        break
                except Exception:
                    pass

        # Check success
        time.sleep(3)
        body = (page.html or "").lower()
        url_now = page.url or ""
        success = any(kw in body or kw in url_now for kw in
                      ["welcome", "inbox", "done", "you're in", "mail.yahoo.com", "guce.yahoo.com"])
        if success:
            log("Yahoo created via 'My email' path!", True)
        else:
            log("Status unclear", False)
            if "phone" in body:
                log("Phone still required — 'My email' path failed", False)
                return None
            input("  >> Verify in browser, press Enter...")

        # Try to detect if Yahoo assigned a @yahoo.com email
        yahoo_email = existing_email
        try:
            page_html = page.html or ""
            yahoo_addrs = re.findall(r'[\w.+-]+@yahoo\.com', page_html)
            if yahoo_addrs:
                yahoo_email = yahoo_addrs[0]
                log(f"Yahoo assigned email: {yahoo_email}", True)
        except Exception:
            pass

        try:
            page.quit()
        except Exception:
            pass
        log(f"Yahoo ready: {yahoo_email}", True)
        return {"email": yahoo_email, "password": yahoo_pw,
                "first_name": fn, "last_name": ln,
                "existing_email": existing_email, "method": "my_email",
                "temp_provider": temp_provider}
    except Exception as e:
        log(f"Yahoo 'My email' error: {e}", False)
        traceback.print_exc()
        return None
    finally:
        try:
            page.quit()
        except Exception:
            pass


def create_yahoo_account(sms_provider=None, captcha_solver=None):
    """
    创建Yahoo邮箱. 仅需SMS验证 (~$0.05-0.10), 零其他成本.
    返回: {email, password, first_name, last_name} or None
    """
    username = gen_username()
    email = f"{username}@yahoo.com"
    fn = random.choice(FIRST_NAMES)
    ln = random.choice(LAST_NAMES)
    yahoo_pw = gen_password()
    birth_m = str(random.randint(1, 12))
    birth_d = str(random.randint(1, 28))
    birth_y = str(random.randint(1985, 2002))

    mode = "FULL_AUTO" if (captcha_solver and sms_provider) else \
           "SEMI_AUTO" if sms_provider else "MANUAL"

    print(f"\n  Phase 1: Yahoo Create [{mode}]")
    print(f"  Name: {fn} {ln} | User: {username} | Email: {email}")

    from DrissionPage import ChromiumOptions, ChromiumPage

    chrome = find_chrome()
    if not chrome:
        log("Chrome not found", False)
        return None

    co = ChromiumOptions()
    co.set_browser_path(chrome)
    co.set_argument("--incognito")
    co.auto_port()
    co.headless(False)
    px = proxy_str()
    if px:
        co.set_argument(f"--proxy-server={px.replace('http://', '')}")

    page = ChromiumPage(co)
    sms_activation = None

    try:
        page.get(YAHOO_SIGNUP_URL)
        time.sleep(random.uniform(2, 4))

        # Auto-fill form
        for sel, val in [
            ("@id=usernamereg-firstName", fn),
            ("@id=usernamereg-lastName", ln),
            ("@id=usernamereg-yid", username),
            ("@id=usernamereg-password", yahoo_pw),
        ]:
            try:
                el = page.ele(sel, timeout=3)
                if el:
                    el.input(val)
                    time.sleep(random.uniform(0.3, 0.7))
            except Exception:
                pass

        # Birth date
        for sel, val in [("@id=usernamereg-month", birth_m),
                         ("@id=usernamereg-day", birth_d),
                         ("@id=usernamereg-year", birth_y)]:
            try:
                el = page.ele(sel, timeout=2)
                if el:
                    el.input(val)
            except Exception:
                pass

        # Phone number via SMS provider
        if sms_provider:
            log("Getting virtual number (~$0.05-0.10)...")
            try:
                num_data = sms_provider.get_number(service="yahoo", country="us")
                phone = num_data["number"]
                sms_activation = num_data["activation_id"]
                log(f"Virtual number: {phone}", True)
                try:
                    phone_input = page.ele("@id=usernamereg-phone", timeout=3)
                    if phone_input:
                        phone_input.input(phone)
                except Exception:
                    pass
            except Exception as e:
                log(f"SMS error: {e}", False)
                sms_provider = None

        log("Form filled, clicking Continue...", True)

        # Click Continue
        for sel in ["@id=reg-submit-button", "tag:button@text():Continue", "@type=submit"]:
            try:
                btn = page.ele(sel, timeout=3)
                if btn:
                    btn.click()
                    time.sleep(random.uniform(3, 5))
                    break
            except Exception:
                pass

        # Handle CAPTCHA
        body = (page.html or "").lower()
        if "funcaptcha" in body or "arkoselabs" in body or "captcha" in body:
            if captcha_solver:
                log("FunCaptcha detected, solving via API...")
                try:
                    token = captcha_solver.solve_funcaptcha(YAHOO_FUNCAPTCHA_KEY, YAHOO_SIGNUP_URL)
                    page.run_js(f"""
                        if (window.ArkoseEnforcement) window.ArkoseEnforcement.setToken('{token}');
                        var cb = document.querySelector('[data-callback]');
                        if (cb) cb.setAttribute('data-token', '{token}');
                    """)
                    time.sleep(2)
                except Exception as e:
                    log(f"CAPTCHA API fail: {e}", False)
                    print("  >> Please solve CAPTCHA manually in browser")
                    input("  >> Press Enter when done...")
            else:
                log("FunCaptcha detected — solve manually in browser", False)
                input("  >> Press Enter when CAPTCHA solved...")

        # Handle SMS verification
        body = (page.html or "").lower()
        if "phone" in body or "verify" in body or "code" in body:
            if sms_provider and sms_activation:
                log("Waiting for SMS code (max 120s)...")
                code = sms_provider.get_sms_code(sms_activation, timeout=120)
                if code:
                    log(f"SMS code: {code}", True)
                    for sel in ["@id=verification-code-field", "@name=code", "tag:input@type=tel"]:
                        try:
                            ci = page.ele(sel, timeout=3)
                            if ci:
                                ci.input(code)
                                break
                        except Exception:
                            pass
                    time.sleep(0.5)
                    for sel in ["tag:button@text():Verify", "@type=submit"]:
                        try:
                            btn = page.ele(sel, timeout=2)
                            if btn:
                                btn.click()
                                break
                        except Exception:
                            pass
                    time.sleep(3)
                    log("SMS code submitted!", True)
                else:
                    log("No SMS code received", False)
                    input("  >> Complete phone verification manually, then press Enter...")
            else:
                log("Phone verification required — complete manually", False)
                input("  >> Press Enter when done...")

        # Wait for account creation
        time.sleep(3)
        body = (page.html or "").lower()
        url = page.url
        if "yahoo.com" in url and any(k in body for k in ["welcome", "inbox", "done", "account created"]):
            log("Yahoo account created!", True)
        else:
            if mode != "FULL_AUTO":
                print("  >> Confirm Yahoo account is created, then press Enter")
                input()

        page.quit()

        return {
            "email": email,
            "password": yahoo_pw,
            "first_name": fn,
            "last_name": ln,
        }

    except Exception as e:
        log(f"Yahoo create error: {e}", False)
        traceback.print_exc()
        if sms_activation and sms_provider:
            sms_provider.cancel(sms_activation)
        return None
    finally:
        try:
            page.quit()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════
# §10  Phase 2: Windsurf注册 (零成本, 零信用卡)
# ═══════════════════════════════════════════════════════

def register_windsurf(email, fn, ln):
    """
    Windsurf注册 — DrissionPage + turnstilePatch
    Pro Trial自动激活: 2周100积分, 无需任何支付
    """
    ws_pw = gen_password()
    log(f"Phase 2: Windsurf register {email}")

    from DrissionPage import ChromiumOptions, ChromiumPage
    import tempfile

    kill_chrome()
    chrome = find_chrome()
    if not chrome:
        return None

    tmp_user_dir = tempfile.mkdtemp(prefix="ws_yahoo_")
    co = ChromiumOptions()
    co.set_browser_path(chrome)
    co.set_argument("--incognito")
    co.set_user_data_path(tmp_user_dir)
    co.auto_port()
    co.headless(False)
    px = proxy_str()
    if px:
        co.set_argument(f"--proxy-server={px.replace('http://', '')}")

    tp = find_turnstile_patch()
    if tp:
        co.set_argument("--allow-extensions-in-incognito")
        co.add_extension(tp)
        log(f"turnstilePatch: OK", True)

    page = ChromiumPage(co)
    if tp:
        time.sleep(3)

    try:
        page.get(WINDSURF_REGISTER_URL)
        time.sleep(random.uniform(2.5, 4))

        # Fill form
        for sel, val in [('@name=firstName', fn), ('@name=lastName', ln), ('@name=email', email)]:
            try:
                el = page.ele(sel, timeout=5)
                if el:
                    el.input(val)
                    time.sleep(random.uniform(0.3, 0.7))
            except Exception:
                pass

        # Checkbox
        try:
            cb = page.ele('tag:input@type=checkbox', timeout=2)
            if cb and not cb.attr('checked'):
                cb.click()
        except Exception:
            pass

        # Continue
        for sel in ['tag:button@text():Continue', '@type=submit']:
            try:
                btn = page.ele(sel, timeout=3)
                if btn:
                    btn.click()
                    time.sleep(random.uniform(3, 5))
                    break
            except Exception:
                pass

        log("Waiting for Turnstile...")

        # Wait for password step
        for _ in range(40):
            try:
                body = (page.html or "").lower()
                if "password" in body and ("confirm" in body or "set your" in body):
                    break
                if any(k in body for k in ["dashboard", "welcome to windsurf"]):
                    page.quit()
                    return {"windsurf_password": ws_pw, "status": "done"}
                for s in ['tag:button@text():Continue', '@type=submit']:
                    try:
                        b = page.ele(s, timeout=1)
                        if b and not b.attr('disabled'):
                            b.click()
                            time.sleep(2)
                            break
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(1)

        # Password step
        pw_input = page.ele('@type=password', timeout=8)
        if pw_input:
            log("Password step reached", True)
            pw_input.input(ws_pw)
            time.sleep(0.5)
            try:
                pc = page.ele('@placeholder:Confirm', timeout=3)
                if pc:
                    pc.input(ws_pw)
            except Exception:
                pass
            for sel in ['@type=submit', 'tag:button@text():Continue', 'tag:button@text():Sign up']:
                try:
                    btn = page.ele(sel, timeout=3)
                    if btn:
                        btn.click()
                        time.sleep(3)
                        break
                except Exception:
                    pass

            # Wait for verify or done
            VERIFY_KW = ["verify your email", "check your email", "we've sent", "check your inbox"]
            DONE_KW = ["dashboard", "welcome to windsurf", "get started"]

            for _ in range(45):
                time.sleep(1)
                try:
                    body2 = (page.html or "").lower()
                    if any(k in body2 for k in VERIFY_KW):
                        log("Verification page reached — need email code", True)
                        return {"windsurf_password": ws_pw, "status": "verify_pending", "page": page}
                    if any(k in body2 for k in DONE_KW):
                        log("Registration complete (no verify needed)!", True)
                        page.quit()
                        return {"windsurf_password": ws_pw, "status": "done"}
                except Exception:
                    pass

        page.quit()
        return {"windsurf_password": ws_pw, "status": "error"}

    except Exception as e:
        log(f"Windsurf register error: {e}", False)
        try:
            page.quit()
        except Exception:
            pass
        return None


# ═══════════════════════════════════════════════════════
# §11  Phase 3: Yahoo IMAP取验证码
# ═══════════════════════════════════════════════════════

def yahoo_imap_get_verify(email, password, max_wait=180):
    """从Yahoo IMAP获取Windsurf验证链接/验证码"""
    log(f"Phase 3: Yahoo IMAP {email}")
    try:
        mail = imaplib.IMAP4_SSL("imap.mail.yahoo.com", 993)
        mail.login(email, password)
        mail.select("INBOX")
        log("Yahoo IMAP connected!", True)

        start = time.time()
        while time.time() - start < max_wait:
            for query in [
                '(FROM "codeium" UNSEEN)', '(FROM "windsurf" UNSEEN)',
                '(FROM "noreply" UNSEEN)', '(SUBJECT "verify" UNSEEN)',
            ]:
                try:
                    _, data = mail.search(None, query)
                    ids = data[0].split() if data[0] else []
                    for eid in reversed(ids[-5:]):
                        _, msg_data = mail.fetch(eid, "(RFC822)")
                        msg = email_lib.message_from_bytes(msg_data[0][1])
                        body_text = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() in ("text/plain", "text/html"):
                                    try:
                                        body_text += part.get_payload(decode=True).decode(errors="replace")
                                    except Exception:
                                        pass
                        else:
                            try:
                                body_text = msg.get_payload(decode=True).decode(errors="replace")
                            except Exception:
                                pass

                        # Extract 6-digit code
                        codes = re.findall(r'\b(\d{6})\b', body_text)
                        if codes:
                            mail.logout()
                            log(f"Verification code: {codes[0]}", True)
                            return {"type": "code", "value": codes[0]}

                        # Extract verification link
                        content = html_mod.unescape(body_text)
                        links = re.findall(r'https?://[^\s<>"\']+', content)
                        for link in links:
                            if any(k in link.lower() for k in ['verify', 'confirm', 'oobcode', 'windsurf', 'codeium']):
                                mail.logout()
                                log(f"Verification link found!", True)
                                return {"type": "link", "value": link}
                except Exception:
                    pass

            elapsed = int(time.time() - start)
            if elapsed % 30 == 0 and elapsed > 0:
                log(f"Waiting for email... ({elapsed}s/{max_wait}s)")
            time.sleep(10)

        mail.logout()
        log("No verification email received", False)
        return None

    except Exception as e:
        log(f"IMAP error: {e}", False)
        return None


def gmail_imap_get_verify(gmail_alias, max_wait=180):
    """从Gmail IMAP获取Windsurf验证链接/验证码 (用于'My email'路径)"""
    secrets = load_secrets()
    gmail_pw = secrets.get("GMAIL_APP_PASSWORD", "")
    if not gmail_pw:
        log("GMAIL_APP_PASSWORD not set in secrets.env", False)
        return None

    base_email = gmail_alias.split("+")[0] + "@gmail.com" if "+" in gmail_alias else gmail_alias
    log(f"Phase 3: Gmail IMAP {base_email} (alias: {gmail_alias})")

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(base_email, gmail_pw)
        mail.select("INBOX")
        log("Gmail IMAP connected!", True)

        start = time.time()
        while time.time() - start < max_wait:
            for query in [
                '(FROM "codeium" UNSEEN)', '(FROM "windsurf" UNSEEN)',
                '(FROM "noreply" UNSEEN)', '(SUBJECT "verify" UNSEEN)',
            ]:
                try:
                    _, data = mail.search(None, query)
                    ids = data[0].split() if data[0] else []
                    for eid in reversed(ids[-5:]):
                        _, msg_data = mail.fetch(eid, "(RFC822)")
                        msg = email_lib.message_from_bytes(msg_data[0][1])

                        to_field = (msg.get("To", "") + " " + msg.get("Delivered-To", "")).lower()
                        if "+" in gmail_alias and gmail_alias.lower() not in to_field:
                            continue

                        body_text = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() in ("text/plain", "text/html"):
                                    try:
                                        body_text += part.get_payload(decode=True).decode(errors="replace")
                                    except Exception:
                                        pass
                        else:
                            try:
                                body_text = msg.get_payload(decode=True).decode(errors="replace")
                            except Exception:
                                pass

                        codes = re.findall(r'\b(\d{6})\b', body_text)
                        if codes:
                            mail.logout()
                            log(f"Gmail verification code: {codes[0]}", True)
                            return {"type": "code", "value": codes[0]}

                        content = html_mod.unescape(body_text)
                        links = re.findall(r'https?://[^\s<>"\']+', content)
                        for link in links:
                            if any(k in link.lower() for k in ['verify', 'confirm', 'oobcode', 'windsurf', 'codeium']):
                                mail.logout()
                                log(f"Gmail verification link found!", True)
                                return {"type": "link", "value": link}
                except Exception:
                    pass

            elapsed = int(time.time() - start)
            if elapsed % 30 == 0 and elapsed > 0:
                log(f"Waiting for Gmail email... ({elapsed}s/{max_wait}s)")
            time.sleep(10)

        mail.logout()
        log("No verification email in Gmail", False)
        return None

    except Exception as e:
        log(f"Gmail IMAP error: {e}", False)
        return None


def enter_verify_code(page, code):
    """在Windsurf验证页面输入6位码"""
    log(f"Entering code: {code}")
    try:
        time.sleep(2)
        result = page.run_js(f"""
            var code = '{code}';
            var inputs = Array.from(document.querySelectorAll('input'));
            var visible = inputs.filter(function(inp) {{
                return inp.type !== 'hidden' && inp.type !== 'checkbox' &&
                       inp.type !== 'radio' && inp.type !== 'submit' &&
                       inp.type !== 'password' && inp.offsetParent !== null;
            }});
            if (visible.length >= 6) {{
                for (var i = 0; i < 6; i++) {{
                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(visible[i], code[i]);
                    visible[i].dispatchEvent(new Event('input', {{bubbles: true}}));
                    visible[i].dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
                return {{filled: true, method: 'multi'}};
            }} else if (visible.length >= 1) {{
                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(visible[0], code);
                visible[0].dispatchEvent(new Event('input', {{bubbles: true}}));
                visible[0].dispatchEvent(new Event('change', {{bubbles: true}}));
                return {{filled: true, method: 'single'}};
            }}
            return {{filled: false}};
        """)
        if result and result.get('filled'):
            log("Code entered!", True)
            time.sleep(1)
            page.run_js("""
                var btns = document.querySelectorAll('button');
                for (var b of btns) {
                    var t = (b.innerText || '').toLowerCase();
                    if (t.includes('verify') || t.includes('submit') || t.includes('continue') || t.includes('confirm')) {
                        b.click(); break;
                    }
                }
            """)
            time.sleep(5)
            return True
        return False
    except Exception as e:
        log(f"Code entry error: {e}", False)
        return False


# ═══════════════════════════════════════════════════════
# §12  全链路 — 一推到底
# ═══════════════════════════════════════════════════════

def full_pipeline():
    """
    Yahoo Pro Trial 一推到底:
      Yahoo邮箱 → Windsurf注册 → 邮件验证 → API激活 → 号池注入
      全链路成本: ~$0.05-0.15 (仅SMS)
      虚拟卡需求: 0 (Pro Trial = 免费)
    """
    t0 = time.time()
    secrets = load_secrets()
    px = proxy_str()

    print(f"\n{'=' * 70}")
    print(f"  Yahoo Pro Trial — 一推到底 · 道法自然 · 万法归宗")
    print(f"  {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S CST')}")
    print(f"  Proxy: {px or 'NONE'}")
    print(f"  Virtual Card Required: NO (Pro Trial = FREE)")
    print(f"{'=' * 70}")

    # Initialize services — SMS三源降级
    sms_provider = None
    captcha_solver = None
    sms_source = "NONE"

    # S0: OnePlus手机SIM (完全零成本, 首选!)
    try:
        bridge = OnePlusSMSBridge()
        if bridge.available:
            sms_provider = bridge
            sms_source = f"OnePlus SIM {bridge.phone_number or 'ADB'} (FREE!)"
            log(f"S0 OnePlus: {bridge.phone_number} — ZERO COST", True)
    except Exception as e:
        log(f"S0 OnePlus: unavailable ({e})")

    # S1/S2: 付费SMS降级 (OnePlus不可用时)
    if not sms_provider:
        sms_key = secrets.get('SMS_API_KEY', '')
        if sms_key:
            svc = secrets.get('SMS_SERVICE', 'smsactivate')
            if svc == '5sim':
                sms_provider = FiveSimProvider(sms_key, px)
            else:
                sms_provider = SMSActivateProvider(sms_key, px)
            try:
                bal = sms_provider.get_balance()
                sms_source = f"{svc} (${bal:.2f})"
                log(f"S1 {svc}: ${bal:.2f}", True)
            except Exception as e:
                log(f"S1 {svc} check failed: {e}", False)
                sms_provider = None

    # S3: 手动 (无任何自动化)
    if not sms_provider:
        sms_source = "MANUAL (solve phone verification by hand)"
        log("S3 Manual mode — no auto SMS", False)

    print(f"  SMS Source: {sms_source}")

    captcha_key = secrets.get('CAPTCHA_API_KEY', '')
    if captcha_key:
        captcha_solver = CaptchaSolver2Captcha(captcha_key, px)
        try:
            bal = captcha_solver.get_balance()
            log(f"CAPTCHA: ${bal:.2f}", True)
        except Exception:
            captcha_solver = None

    # Phase 1: Yahoo邮箱 — try 'My email' (no phone!) then SMS fallback
    no_proxy = '--no-proxy' in sys.argv
    yahoo = None

    # Strategy A: 'My email' path — zero phone verification
    gmail_base = secrets.get("GMAIL_BASE", "")
    if gmail_base:
        log("Trying 'My email' path (no phone verification)...", True)
        yahoo = create_yahoo_with_existing_email(no_proxy=no_proxy)
        if yahoo:
            log(f"'My email' path OK: {yahoo['email']}", True)

    # Strategy B: Standard path with SMS
    if not yahoo:
        log("Falling back to standard SMS path...", True)
        yahoo = create_yahoo_account(sms_provider=sms_provider, captcha_solver=captcha_solver)

    if not yahoo:
        log("Phase 1 FAIL: Yahoo creation failed", False)
        return None

    email = yahoo["email"]
    yahoo_pw = yahoo["password"]
    fn = yahoo["first_name"]
    ln = yahoo["last_name"]

    # Phase 2: Windsurf注册 (零支付)
    ws = register_windsurf(email, fn, ln)
    if not ws:
        log("Phase 2 FAIL: Windsurf registration failed", False)
        return None

    ws_pw = ws["windsurf_password"]
    page = ws.get("page")

    # Phase 3: Email verification (三源自动降级)
    if ws["status"] == "verify_pending":
        method = yahoo.get("method", "standard")
        existing = yahoo.get("existing_email", "")
        temp_prov = yahoo.get("temp_provider")
        verify = None

        # S0: mail.tm auto read (零配置)
        if temp_prov and hasattr(temp_prov, 'get_windsurf_verify'):
            log("Phase 3: mail.tm auto read Windsurf verification...", True)
            verify = temp_prov.get_windsurf_verify(max_wait=180)

        # S1: Gmail IMAP
        if not verify and method == "my_email" and existing and "gmail" in existing.lower():
            log("Phase 3: Gmail IMAP fallback...", True)
            verify = gmail_imap_get_verify(existing, max_wait=180)

        # S2: Yahoo IMAP
        if not verify and "@yahoo.com" in email.lower():
            verify = yahoo_imap_get_verify(email, yahoo_pw, max_wait=180)

        if verify:
            if verify["type"] == "code" and page:
                enter_verify_code(page, verify["value"])
            elif verify["type"] == "link":
                try:
                    urlopen(Request(verify["value"]), timeout=20, context=_ssl_ctx)
                    log("Verification link clicked!", True)
                except Exception:
                    if page:
                        try:
                            page.get(verify["value"])
                            time.sleep(5)
                        except Exception:
                            pass
        else:
            log("Auto verification failed — manual fallback", False)
            if page:
                page.new_tab()
                if method == "my_email" and "gmail" in (existing or email).lower():
                    page.get("https://mail.google.com")
                    mail_name = "Gmail"
                else:
                    page.get("https://mail.yahoo.com")
                    mail_name = "Yahoo Mail"
                time.sleep(3)
                print(f"  >> Open {mail_name}, find Windsurf verification email, click link")
                input("  >> Press Enter when done...")

    if page:
        try:
            page.quit()
        except Exception:
            pass

    # Phase 4+5: Activate (signIn → plan check → apiKey → inject)
    result = activate_account(email, ws_pw)

    elapsed = int(time.time() - t0)
    result['yahoo_password'] = yahoo_pw
    result['windsurf_password'] = ws_pw
    result['first_name'] = fn
    result['last_name'] = ln
    result['total_seconds'] = elapsed

    print(f"\n{'=' * 70}")
    if result.get('apiKey'):
        print(f"  DONE! Yahoo Pro Trial activated")
    else:
        print(f"  PARTIAL: registered but activation incomplete")
    print(f"  Email:       {email}")
    print(f"  Yahoo PW:    {yahoo_pw}")
    print(f"  Windsurf PW: {ws_pw}")
    print(f"  Plan:        {result.get('plan', '?')}")
    print(f"  ApiKey:      {(result.get('apiKey') or 'N/A')[:30]}...")
    is_oneplus = isinstance(sms_provider, OnePlusSMSBridge) if sms_provider else False
    is_my_email = yahoo.get("method") == "my_email"
    cost_str = '$0 (My email path, ZERO COST!)' if is_my_email else \
               '$0 (OnePlus SIM, ZERO COST!)' if is_oneplus else \
               '~$0.10 (SMS only, zero card)'
    print(f"  Cost:        {cost_str}")
    print(f"  Time:        {elapsed}s")
    print(f"{'=' * 70}\n")

    return result


# ═══════════════════════════════════════════════════════
# §13  收割已有Yahoo账号
# ═══════════════════════════════════════════════════════

def parse_yahoo_accounts():
    if not YAHOO_FILE.exists():
        return []
    text = YAHOO_FILE.read_text(encoding='utf-8')
    accounts = []
    lines = text.strip().split('\n')
    SKIP_DOMAINS = ['3lux.shop', 'example.com']
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('邮箱：') or line.startswith('邮箱:'):
            email = re.split(r'[：:]', line, 1)[-1].strip()
            if i + 1 < len(lines):
                pw_line = lines[i + 1].strip()
                if pw_line.startswith('密码：') or pw_line.startswith('密码:'):
                    pw = re.split(r'[：:]', pw_line, 1)[-1].strip()
                    if '@' in email and pw and not any(d in email for d in SKIP_DOMAINS):
                        accounts.append((email, pw))
                    i += 2
                    continue
        if '----' in line:
            parts = line.split('----')
            if len(parts) == 2 and '@' in parts[0]:
                e, p = parts[0].strip(), parts[1].strip()
                if not any(d in e for d in SKIP_DOMAINS):
                    accounts.append((e, p))
            i += 1
            continue
        if '\t' in line:
            parts = line.split('\t')
            if len(parts) >= 2 and '@' in parts[0]:
                e, p = parts[0].strip(), parts[1].strip()
                if not any(d in e for d in SKIP_DOMAINS):
                    accounts.append((e, p))
            i += 1
            continue
        if '@' in line and not line.startswith(('卡', 'Hi', '您', '#')) and not any(d in line for d in SKIP_DOMAINS):
            bare = line.strip()
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt and '@' not in nxt and not nxt.startswith(('邮箱', '密码', '卡', 'Hi', '您')):
                    accounts.append((bare, nxt))
                    i += 2
                    continue
        i += 1
    return accounts


def harvest():
    """收割: 激活账号.txt中所有未入池的Yahoo账号"""
    yahoo = parse_yahoo_accounts()
    pool, _ = load_pool_accounts()
    pool_keyed = {a.get('email', '').lower() for a in pool if a.get('apiKey')}
    fresh = [(e, p) for e, p in yahoo if e.lower() not in pool_keyed]

    print(f"\n{'=' * 70}")
    print(f"  Yahoo Harvest — {len(fresh)} pending (total {len(yahoo)})")
    print(f"  Virtual Card: NOT NEEDED (Pro Trial = FREE)")
    print(f"{'=' * 70}")

    if not fresh:
        log("All Yahoo accounts already harvested", True)
        return

    success = 0
    for i, (email, pw) in enumerate(fresh):
        print(f"\n{'─' * 40} [{i+1}/{len(fresh)}]")
        r = activate_account(email, pw)
        if r.get('apiKey'):
            success += 1
        if i < len(fresh) - 1:
            time.sleep(2)

    print(f"\n  Harvest: {success}/{len(fresh)} activated")


# ═══════════════════════════════════════════════════════
# §14  状态 + 探测
# ═══════════════════════════════════════════════════════

def show_status():
    secrets = load_secrets()
    pool, paths = load_pool_accounts()
    yahoo = parse_yahoo_accounts()
    results = load_results()
    pool_keyed = {a.get('email', '').lower() for a in pool if a.get('apiKey')}
    yahoo_fresh = [(e, p) for e, p in yahoo if e.lower() not in pool_keyed]

    print(f"\n{'=' * 70}")
    print(f"  Yahoo Pro Trial — Status · {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 70}")

    print(f"\n  [底层解构]")
    print(f"    Virtual Card Required:  NO")
    print(f"    Pro Trial Cost:         $0 (2 weeks, 100 credits, zero payment)")

    # OnePlus detection
    print(f"\n  [OnePlus手机 — S0首选]")
    try:
        bridge = OnePlusSMSBridge()
        if bridge.available:
            print(f"    ADB:     CONNECTED (serial: {ONEPLUS_SERIAL})")
            print(f"    SIM:     {bridge.phone_number or 'detected but number unknown'}")
            print(f"    SMS Cost: $0 (ADB direct read, ZERO COST!)")
            print(f"    Status:  READY")
        else:
            print(f"    ADB:     NOT CONNECTED")
            print(f"    Action:  USB connect OnePlus + enable USB debug")
    except Exception as e:
        print(f"    Error:   {e}")

    oneplus_ok = bridge.available if bridge else False
    print(f"\n  [成本]")
    if oneplus_ok:
        print(f"    Total Cost/Account: $0 (OnePlus SIM = free SMS)")
    else:
        print(f"    Yahoo SMS Cost:     ~$0.05-0.10 per account")
        print(f"    Total Cost/Account: ~$0.05-0.15")

    print(f"\n  [号池]")
    real = [a for a in pool if '@' in a.get('email', '') and 'example' not in a.get('email', '')]
    has_key = [a for a in real if a.get('apiKey')]
    print(f"    Accounts: {len(real)} total, {len(has_key)} with apiKey")
    print(f"    Pool files: {len(paths)}")

    print(f"\n  [Yahoo来源]")
    print(f"    Total: {len(yahoo)} | Fresh: {len(yahoo_fresh)}")
    if yahoo_fresh:
        print(f"    >> Run --harvest to activate {len(yahoo_fresh)} Yahoo accounts")

    print(f"\n  [Services]")
    sms_key = secrets.get('SMS_API_KEY', '')
    captcha_key = secrets.get('CAPTCHA_API_KEY', '')
    print(f"    SMS_API_KEY:     {'SET' if sms_key else 'NOT SET (manual phone)'}")
    print(f"    SMS_SERVICE:     {secrets.get('SMS_SERVICE', 'smsactivate')}")
    print(f"    CAPTCHA_API_KEY: {'SET' if captcha_key else 'NOT SET (manual captcha)'}")
    print(f"    Proxy:           {proxy_str() or 'NONE'}")

    print(f"\n  [Results]")
    ok = sum(1 for r in results if r.get('status') == 'activated')
    print(f"    Total: {len(results)} | Activated: {ok}")
    for r in results[-5:]:
        icon = '+' if r.get('status') == 'activated' else '-'
        ts = r.get('timestamp', '?')[:19]
        print(f"    [{icon}] {ts} {r.get('email', '?')[:35]} {r.get('plan', '?')}")

    print(f"\n{'=' * 70}\n")


def probe():
    print(f"\n{'=' * 70}")
    print(f"  Yahoo Pro Trial — Full Probe")
    print(f"{'=' * 70}")

    # Proxy
    port = detect_proxy()
    log(f"Proxy: {'127.0.0.1:' + str(port) if port else 'NONE'}")

    # Firebase
    print("\n  [Firebase]")
    fb_headers = {'Referer': 'https://windsurf.com/'}
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={key}'
        for use_p in [False, True]:
            label = f"{'proxy' if use_p else 'direct'}-{key[-4:]}"
            try:
                r = http_json(url, {'returnSecureToken': True}, use_proxy=use_p, timeout=8, headers=fb_headers)
                msg = r.get('error', {}).get('message', 'OK') if isinstance(r.get('error'), dict) else 'OK'
                log(f"  {label}: OK ({msg[:40]})", True)
            except Exception as e:
                log(f"  {label}: FAIL ({str(e)[:50]})", False)

    # RegisterUser
    print("\n  [RegisterUser gRPC]")
    dummy = encode_proto("probe")
    for url in REGISTER_URLS:
        host = url.split('/')[2]
        try:
            r = http_bin(url, dummy, timeout=10)
            log(f"  {host}: OK ({len(r)}B)", True)
        except Exception as e:
            log(f"  {host}: FAIL ({str(e)[:50]})", False)

    # Browser
    print("\n  [Browser]")
    chrome = find_chrome()
    log(f"  Chrome: {'OK' if chrome else 'MISSING'}", bool(chrome))
    tp = find_turnstile_patch()
    log(f"  turnstilePatch: {'OK' if tp else 'MISSING'}", bool(tp))
    try:
        import DrissionPage
        log(f"  DrissionPage: OK", True)
    except ImportError:
        log(f"  DrissionPage: MISSING (pip install DrissionPage)", False)

    # OnePlus Phone — S0
    print("\n  [OnePlus手机 — S0首选]")
    log(f"  ADB: {ADB_EXE or 'NOT FOUND'}", ADB_EXE is not None)
    oneplus_connected = _adb_phone_connected()
    log(f"  OnePlus ({ONEPLUS_SERIAL}): {'CONNECTED' if oneplus_connected else 'NOT FOUND'}", oneplus_connected)
    if oneplus_connected:
        sim_num = _adb_get_sim_number()
        log(f"  SIM Number: {sim_num or 'not detected'}", sim_num is not None)
        # Test SMS reading
        out, ok = _adb("shell", "content", "query", "--uri", "content://sms/inbox",
                        "--projection", "_id:body:date",
                        "--sort", "date DESC",
                        serial=ONEPLUS_SERIAL)
        sms_count = len(out.splitlines()) if ok else 0
        log(f"  SMS Inbox: {sms_count} messages readable", sms_count > 0)
        if sms_count > 0:
            log(f"  SMS Bridge: READY (zero cost!)", True)

    # SMS paid services — S1/S2 fallback
    print("\n  [SMS Paid — S1/S2 fallback]")
    secrets = load_secrets()
    sms_key = secrets.get('SMS_API_KEY', '')
    if sms_key:
        svc = secrets.get('SMS_SERVICE', 'smsactivate')
        try:
            if svc == '5sim':
                p = FiveSimProvider(sms_key, proxy_str())
            else:
                p = SMSActivateProvider(sms_key, proxy_str())
            bal = p.get_balance()
            log(f"  {svc}: OK (${bal:.2f})", True)
        except Exception as e:
            log(f"  {svc}: FAIL ({str(e)[:50]})", False)
    else:
        log(f"  Paid SMS: NOT CONFIGURED (OnePlus is primary anyway)", not oneplus_connected)

    print(f"\n  [Virtual Card Status]")
    log(f"  NOT NEEDED. Pro Trial = FREE (2 weeks, 100 credits, $0)", True)
    log(f"  No credit card, no virtual card, no payment of any kind.", True)

    # Summary
    print(f"\n  [Summary]")
    if oneplus_connected:
        log(f"  BEST PATH: OnePlus SIM → Yahoo → Windsurf → Pro Trial ($0 TOTAL)", True)
    elif sms_key:
        log(f"  PATH: Paid SMS → Yahoo → Windsurf → Pro Trial (~$0.10)", True)
    else:
        log(f"  PATH: Manual → Yahoo → Windsurf → Pro Trial (manual SMS)", False)

    print(f"\n{'=' * 70}\n")


# ═══════════════════════════════════════════════════════
# §15  CLI
# ═══════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Yahoo Pro Trial — 一推到底 (零虚拟卡)")
    parser.add_argument("--batch", type=int, default=0, help="Batch register N accounts")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--harvest", action="store_true", help="Harvest existing Yahoo accounts")
    parser.add_argument("--probe", action="store_true", help="Full-chain probe")
    parser.add_argument("--no-proxy", action="store_true", help="Bypass proxy (direct China IP)")
    parser.add_argument("--my-email", type=str, default="", help="'My email' path with given email (bypasses phone)")
    args = parser.parse_args()

    print(f"\n{'=' * 70}")
    print(f"  Yahoo Pro Trial Engine v{VERSION}")
    print(f"  Virtual Card = ZERO. Pro Trial = FREE.")
    print(f"  {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S CST')}")
    print(f"{'=' * 70}")

    if args.status:
        show_status()
    elif args.harvest:
        harvest()
    elif args.probe:
        probe()
    elif args.my_email:
        no_px = args.no_proxy
        yahoo = create_yahoo_with_existing_email(existing_email=args.my_email, no_proxy=no_px)
        if yahoo:
            log(f"Yahoo created: {yahoo['email']} (method: {yahoo.get('method')})", True)
        else:
            log("'My email' path failed", False)
    elif args.batch > 0:
        success = 0
        for i in range(args.batch):
            print(f"\n{'━' * 50} [{i+1}/{args.batch}]")
            r = full_pipeline()
            if r and r.get('apiKey'):
                success += 1
            if i < args.batch - 1:
                delay = random.uniform(15, 45)
                log(f"Cooling {delay:.0f}s...")
                time.sleep(delay)
        print(f"\n  Batch: {success}/{args.batch} activated")
    else:
        r = full_pipeline()
        if r:
            log(f"Result: {r.get('email')} — {r.get('status')}", r.get('status') == 'activated')
