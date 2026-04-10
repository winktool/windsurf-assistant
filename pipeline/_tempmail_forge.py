#!/usr/bin/env python3
"""
temp-mail.io 铸造引擎 — 唯变所适·推进到底
==========================================
临时邮箱(temp-mail.io) + 浏览器注册 + API激活 = 全自动铸造

流程:
  1. temp-mail.io API → 创建真实可收信邮箱
  2. DrissionPage浏览器 → windsurf.com注册 → Turnstile自动过
  3. temp-mail.io轮询 → 提取6位验证码
  4. JS注入验证码 → 注册完成
  5. Firebase signIn → RegisterUser → apiKey
  6. 注入号池

用法:
  python _tempmail_forge.py forge          # 铸造1个
  python _tempmail_forge.py forge 10       # 批量铸造10个
  python _tempmail_forge.py probe          # 探测temp-mail.io可用性
  python _tempmail_forge.py status         # 查看铸造记录
"""

import json, os, sys, time, random, string, re, ssl, socket, struct
import subprocess, traceback

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen, ProxyHandler, build_opener
from urllib.error import HTTPError, URLError

VERSION = '1.0.0'
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
CST = timezone(timedelta(hours=8))

SECRETS_ENV = Path(r'e:\道\道生一\一生二\secrets.env')
RESULTS_FILE = SCRIPT_DIR / '_tempmail_forge_results.json'
LOG_FILE = SCRIPT_DIR / '_tempmail_forge.log'

# temp-mail.io API (fallback)
TEMPMAIL_API = 'https://api.internal.temp-mail.io/api/v3'

# mail.tm API (primary — 域名未被Windsurf封杀)
MAILTM_API = 'https://api.mail.tm'

# === 自建域名邮件 (primary) ===
CUSTOM_DOMAIN = 'aiotvr.xyz'
MAIL_SINK_API = 'http://60.205.171.100:8025'

# Windsurf
WINDSURF_REGISTER_URL = 'https://windsurf.com/account/register'

# Firebase
FIREBASE_KEYS = [
    'AIzaSyDsOl-1XpT5err0Tcnx8FFod1H8gVGIycY',
    'AIzaSyDKm6GGxMJfCbNf-k0kPytiGLaqFJpeSac',
    'AIzaSyAMqIapVSEvhFgg-dhjdugyJMJnLqWib74',
    'AIzaSyDcBDyyRFI0hhJaslEMHBLAh5iJ_KPOd1M',
]

REGISTER_URLS = [
    'https://register.windsurf.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
    'https://server.codeium.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
]

PLAN_STATUS_URLS = [
    'https://server.codeium.com/exa.seat_management_pb.SeatManagementService/GetPlanStatus',
    'https://web-backend.windsurf.com/exa.seat_management_pb.SeatManagementService/GetPlanStatus',
]

# 号池文件
LH_ACCT_FILE = (
    Path(os.environ.get('APPDATA', '')) / 'Windsurf' / 'User' / 'globalStorage'
    / 'windsurf-login-accounts.json'
)

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

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


# ============================================================
# 基础工具
# ============================================================

def detect_proxy():
    for port in [7890, 7897, 7891]:
        try:
            s = socket.socket(); s.settimeout(1)
            s.connect(('127.0.0.1', port)); s.close()
            return port
        except Exception:
            continue
    return 0

PP = detect_proxy()

def _open_url(req, timeout=10):
    """统一URL请求: 先尝试直连, 失败则走proxy"""
    from urllib.request import HTTPSHandler
    # 优先直连 (避免proxy SSL问题)
    try:
        return urlopen(req, timeout=timeout, context=_ssl_ctx)
    except Exception:
        if PP > 0:
            h = ProxyHandler({'https': f'http://127.0.0.1:{PP}', 'http': f'http://127.0.0.1:{PP}'})
            sh = HTTPSHandler(context=_ssl_ctx)
            return build_opener(h, sh).open(req, timeout=timeout)
        raise

def log(msg, ok=None):
    icon = "+" if ok is True else ("-" if ok is False else "*")
    ts = datetime.now(CST).strftime("%H:%M:%S")
    line = f"  [{ts}][{icon}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now(CST).strftime('%Y-%m-%d')} {line}\n")
    except Exception:
        pass


def http_json(url, data=None, method=None, timeout=15, headers=None):
    body = json.dumps(data).encode('utf-8') if data is not None else None
    m = method or ('POST' if body else 'GET')
    req = Request(url, data=body, method=m)
    if body:
        req.add_header('Content-Type', 'application/json')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if PP > 0:
        handler = ProxyHandler({'https': f'http://127.0.0.1:{PP}', 'http': f'http://127.0.0.1:{PP}'})
        resp = build_opener(handler).open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    return json.loads(resp.read())


def gen_password():
    chars = string.ascii_letters + string.digits + "!@#$"
    pw = (random.choice(string.ascii_uppercase) + random.choice(string.ascii_lowercase) +
          random.choice(string.digits) + random.choice("!@#$") +
          ''.join(random.choices(chars, k=12)))
    return ''.join(random.sample(pw, len(pw)))


def encode_proto(token_str):
    token_bytes = token_str.encode('utf-8')
    length = len(token_bytes)
    header = b'\x0a'
    varint = b''
    n = length
    while n > 0x7f:
        varint += bytes([n & 0x7f | 0x80])
        n >>= 7
    varint += bytes([n])
    return header + varint + token_bytes


def http_bin(url, buf, timeout=15):
    """发送 protobuf 二进制请求，返回响应 bytes"""
    req = Request(url, data=buf, method='POST')
    req.add_header('Content-Type', 'application/proto')
    if PP > 0:
        h = ProxyHandler({'https': f'http://127.0.0.1:{PP}', 'http': f'http://127.0.0.1:{PP}'})
        resp = build_opener(h).open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    return resp.read()


def extract_proto_strings(buf):
    """从 protobuf bytes 递归提取可读字符串"""
    def read_varint(data, pos):
        result = 0; shift = 0
        while pos < len(data):
            b = data[pos]; pos += 1
            result |= (b & 0x7f) << shift
            if not (b & 0x80):
                return result, pos
            shift += 7
        return result, pos

    strings = []; pos = 0
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


def verify_plan_status(id_token):
    """查询 GetPlanStatus，返回 'pro_trial'/'free'/'unknown'/'unreachable'"""
    buf = encode_proto(id_token)
    for url in PLAN_STATUS_URLS:
        try:
            resp = http_bin(url, buf, timeout=15)
            if resp and len(resp) > 5:
                strings = extract_proto_strings(resp)
                for fn, s in strings:
                    sl = s.lower().strip()
                    if sl in ('pro_trial', 'trial'):
                        return 'pro_trial'
                    if sl == 'free':
                        return 'free'
                if strings:
                    return 'unknown'
        except Exception:
            continue
    return 'unreachable'


def load_results():
    if RESULTS_FILE.exists():
        try:
            return json.load(open(RESULTS_FILE, 'r', encoding='utf-8'))
        except Exception:
            pass
    return []


def save_result(result):
    results = load_results()
    results.append(result)
    with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


# ============================================================
# 自建域名邮件 API (aiotvr.xyz → VPS catch-all)
# ============================================================

def custom_create_email():
    """生成随机 @aiotvr.xyz 邮箱地址"""
    prefix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    addr = f"{prefix}@{CUSTOM_DOMAIN}"
    # 验证 mail sink 可达
    try:
        resp = urlopen(f'{MAIL_SINK_API}/health', timeout=8)
        data = json.loads(resp.read())
        if data.get('status') != 'ok':
            raise RuntimeError(f"mail sink unhealthy: {data}")
    except Exception as e:
        raise RuntimeError(f"mail sink不可达: {e}")
    return addr


def custom_wait_verify_code(email, max_wait=300, poll_interval=5):
    """轮询VPS mail sink HTTP API，提取6位验证码"""
    from urllib.parse import quote
    start = time.time()
    attempt = 0
    while time.time() - start < max_wait:
        attempt += 1
        elapsed = int(time.time() - start)
        log(f"轮询VPS收件箱... {elapsed}s/{max_wait}s (第{attempt}次)")
        try:
            # 先尝试 ?code=1 快捷接口
            url = f'{MAIL_SINK_API}/emails?to={quote(email)}&code=1'
            resp = urlopen(url, timeout=10)
            data = json.loads(resp.read())
            code = data.get('code')
            if code and re.match(r'^\d{6}$', str(code)):
                log(f"验证码找到: {code}", True)
                return str(code)
            # code=1 没找到，尝试全量查
            url2 = f'{MAIL_SINK_API}/emails?to={quote(email)}'
            resp2 = urlopen(url2, timeout=10)
            data2 = json.loads(resp2.read())
            emails = data2.get('emails', [])
            if emails:
                for em in emails:
                    subj = em.get('subject', '')
                    body = em.get('body', '') + ' ' + em.get('body_html', '')
                    combined = (subj + ' ' + body).lower()
                    log(f"收到邮件: subj={subj[:60]}")
                    if any(k in combined for k in ['windsurf', 'codeium', 'verify', 'code']):
                        codes = re.findall(r'\b(\d{6})\b', body)
                        if codes:
                            log(f"验证码: {codes[0]}", True)
                            return codes[0]
                        codes = re.findall(r'\b(\d{6})\b', subj)
                        if codes:
                            log(f"验证码(subject): {codes[0]}", True)
                            return codes[0]
        except HTTPError as e:
            if e.code != 404:
                log(f"轮询异常: HTTP {e.code}", False)
        except Exception as e:
            log(f"轮询异常: {e}", False)
        time.sleep(poll_interval)
    log(f"{max_wait}s内未收到验证邮件", False)
    return None


# ============================================================
# mail.tm API (primary — 域名未被Windsurf封杀)
# ============================================================

def mailtm_get_domain():
    """获取 mail.tm 当前可用域名"""
    req = Request(f'{MAILTM_API}/domains')
    req.add_header('Accept', 'application/ld+json')
    resp = _open_url(req, timeout=10)
    data = json.loads(resp.read())
    items = data.get('hydra:member', data) if isinstance(data, dict) else data
    if items:
        return items[0].get('domain', '')
    raise RuntimeError("mail.tm 无可用域名")


def mailtm_create():
    """创建 mail.tm 邮箱，返回 (email, jwt_token)"""
    domain = mailtm_get_domain()
    prefix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    addr = f"{prefix}@{domain}"
    pw = 'ForgePass2026!'
    body = json.dumps({'address': addr, 'password': pw}).encode()
    req = Request(f'{MAILTM_API}/accounts', data=body, method='POST')
    req.add_header('Content-Type', 'application/json')
    _open_url(req, timeout=10)
    # 登录获取 JWT
    body2 = json.dumps({'address': addr, 'password': pw}).encode()
    req2 = Request(f'{MAILTM_API}/token', data=body2, method='POST')
    req2.add_header('Content-Type', 'application/json')
    resp2 = _open_url(req2, timeout=10)
    token_data = json.loads(resp2.read())
    jwt = token_data.get('token', '')
    if not jwt:
        raise RuntimeError(f"mail.tm token failed: {token_data}")
    return addr, jwt


def mailtm_inbox(jwt, timeout=10):
    """读取 mail.tm 收件箱"""
    req = Request(f'{MAILTM_API}/messages')
    req.add_header('Authorization', f'Bearer {jwt}')
    resp = _open_url(req, timeout=timeout)
    data = json.loads(resp.read())
    return data.get('hydra:member', []) if isinstance(data, dict) else data


def mailtm_read_message(jwt, msg_id, timeout=10):
    """读取单封邮件的完整内容"""
    req = Request(f'{MAILTM_API}/messages/{msg_id}')
    req.add_header('Authorization', f'Bearer {jwt}')
    resp = _open_url(req, timeout=timeout)
    return json.loads(resp.read())


def mailtm_wait_verify_code(email, jwt, max_wait=300, poll_interval=5):
    """轮询 mail.tm 收件箱，提取6位验证码"""
    start = time.time()
    attempt = 0
    while time.time() - start < max_wait:
        attempt += 1
        elapsed = int(time.time() - start)
        log(f"轮询mail.tm... {elapsed}s/{max_wait}s (第{attempt}次)")
        try:
            msgs = mailtm_inbox(jwt)
            for msg in msgs:
                subj = str(msg.get('subject', '') or '')
                from_obj = msg.get('from', {})
                if isinstance(from_obj, dict):
                    from_addr = from_obj.get('address', '')
                elif isinstance(from_obj, list) and from_obj:
                    from_addr = from_obj[0].get('address', '') if isinstance(from_obj[0], dict) else str(from_obj[0])
                else:
                    from_addr = str(from_obj)
                intro = str(msg.get('intro', '') or '')
                combined = (subj + ' ' + intro + ' ' + from_addr).lower()
                if any(k in combined for k in ['windsurf', 'codeium', 'verify', 'code', 'confirmation']):
                    # 读取完整邮件提取验证码
                    msg_id = msg.get('id', '')
                    if msg_id:
                        full = mailtm_read_message(jwt, msg_id)
                        body_text = str(full.get('text', '') or '')
                        body_html = str(full.get('html', '') or '')
                        all_text = subj + ' ' + body_text + ' ' + body_html
                        codes = re.findall(r'\b(\d{6})\b', all_text)
                        if codes:
                            log(f"验证码: {codes[0]}", True)
                            return codes[0]
                    log(f"收到邮件但未找到验证码: subj={subj[:50]}")
        except HTTPError as e:
            if e.code != 404:
                log(f"轮询异常: HTTP {e.code}", False)
        except Exception as e:
            log(f"轮询异常: {e}", False)
        time.sleep(poll_interval)
    log(f"{max_wait}s内未收到验证邮件", False)
    return None


# ============================================================
# temp-mail.io API (fallback)
# ============================================================

def tempmail_create():
    """创建临时邮箱，返回 (email, token)"""
    r = http_json(f'{TEMPMAIL_API}/email/new', data={}, timeout=15)
    email = r.get('email', '')
    token = r.get('token', '')
    if not email or not token:
        raise RuntimeError(f"temp-mail.io create failed: {r}")
    return email, token


def tempmail_inbox(email, token, timeout=10):
    """读取收件箱，返回消息列表"""
    req = Request(f'{TEMPMAIL_API}/email/{email}/messages')
    req.add_header('Authorization', f'Bearer {token}')
    if PP > 0:
        handler = ProxyHandler({'https': f'http://127.0.0.1:{PP}', 'http': f'http://127.0.0.1:{PP}'})
        resp = build_opener(handler).open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    return json.loads(resp.read())


def tempmail_wait_verify_code(email, token, max_wait=240, poll_interval=6):
    """轮询temp-mail.io收件箱，提取6位验证码"""
    start = time.time()
    attempt = 0
    while time.time() - start < max_wait:
        attempt += 1
        elapsed = int(time.time() - start)
        log(f"轮询收件箱... {elapsed}s/{max_wait}s (第{attempt}次)")
        try:
            msgs = tempmail_inbox(email, token)
            if isinstance(msgs, list) and len(msgs) > 0:
                for msg in msgs:
                    subject = msg.get('subject', '') or ''
                    body_text = msg.get('body_text', '') or msg.get('body', '') or ''
                    from_addr = msg.get('from', '') or ''
                    log(f"收到邮件: from={str(from_addr)[:40]} subj={subject[:50]}")
                    combined = (subject + ' ' + body_text + ' ' + str(from_addr)).lower()
                    if any(k in combined for k in ['windsurf', 'codeium', 'verify', 'code']):
                        codes = re.findall(r'\b(\d{6})\b', body_text)
                        if codes:
                            log(f"验证码找到: {codes[0]}", True)
                            return codes[0]
                        codes = re.findall(r'\b(\d{6})\b', subject)
                        if codes:
                            log(f"验证码(subject): {codes[0]}", True)
                            return codes[0]
        except HTTPError as e:
            if e.code != 404:
                log(f"轮询异常: HTTP {e.code}", False)
        except Exception as e:
            log(f"轮询异常: {e}", False)
        time.sleep(poll_interval)
    log(f"{max_wait}s内未收到验证邮件", False)
    return None


# ============================================================
# Firebase 激活
# ============================================================

def firebase_signin(email, password):
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}'
        try:
            r = http_json(url, {'email': email, 'password': password, 'returnSecureToken': True})
            if r.get('idToken'):
                return r
            err = r.get('error', {}).get('message', '')
            if any(k in str(err) for k in ['INVALID_PASSWORD', 'EMAIL_NOT_FOUND', 'INVALID_LOGIN']):
                return {'error': str(err)}
        except HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            try:
                return json.loads(body)
            except Exception:
                if '400' in str(e.code):
                    return {'error': f'HTTP_{e.code}'}
        except Exception:
            continue
    return {'error': 'all_keys_failed'}


def windsurf_register_user(id_token):
    buf = encode_proto(id_token)
    for url in REGISTER_URLS:
        try:
            req = Request(url, data=buf, method='POST')
            req.add_header('Content-Type', 'application/proto')
            if PP > 0:
                h = ProxyHandler({'https': f'http://127.0.0.1:{PP}', 'http': f'http://127.0.0.1:{PP}'})
                resp = build_opener(h).open(req, timeout=20)
            else:
                resp = urlopen(req, timeout=20, context=_ssl_ctx)
            data = resp.read()
            if data and len(data) > 10:
                text = data.decode('utf-8', errors='replace')
                m = re.findall(r'(sk-ws-[0-9a-zA-Z_-]{20,})', text)
                if m:
                    return {'apiKey': m[0]}
        except Exception:
            continue
    return {}


def activate_account(email, password):
    """Firebase signIn → RegisterUser → apiKey → GetPlanStatus 验证
    返回: (api_key, plan) 元组, plan='pro_trial'/'free'/'unknown'/'unreachable'"""
    log(f"激活: {email}")
    t0 = time.time()

    login = firebase_signin(email, password)
    id_token = login.get('idToken')
    if not id_token:
        err = login.get('error', {})
        if isinstance(err, dict):
            err = err.get('message', str(err))
        log(f"signIn失败: {err}", False)
        return None, 'signin_failed'

    reg = windsurf_register_user(id_token)
    api_key = reg.get('apiKey')
    elapsed = int((time.time() - t0) * 1000)

    if api_key:
        log(f"apiKey: {api_key[:25]}... ({elapsed}ms)", True)
    else:
        log(f"无apiKey ({elapsed}ms)", False)

    # 验证 Plan Status
    plan = verify_plan_status(id_token)
    log(f"Plan Status: {plan}", plan == 'pro_trial')

    return api_key, plan


# ============================================================
# 号池注入
# ============================================================

def inject_to_pool(email, password, api_key=None, source="tempmail_forge", plan_tag="Trial"):
    if not LH_ACCT_FILE.exists():
        log(f"号池文件不存在: {LH_ACCT_FILE}", False)
        return False

    try:
        pool = json.loads(LH_ACCT_FILE.read_text(encoding='utf-8'))
    except Exception:
        pool = []

    existing = {a.get('email', '').lower() for a in pool}
    if email.lower() in existing:
        # 更新apiKey
        for a in pool:
            if a.get('email', '').lower() == email.lower():
                if api_key and not a.get('apiKey'):
                    a['apiKey'] = api_key
                    a['source'] = source
                    LH_ACCT_FILE.write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding='utf-8')
                    log(f"号池更新apiKey: {email}", True)
                    return True
        return False

    entry = {
        "email": email,
        "password": password,
        "usage": {"plan": plan_tag},
        "source": source,
        "addedAt": datetime.now(CST).isoformat(),
    }
    if api_key:
        entry["apiKey"] = api_key
    pool.append(entry)
    LH_ACCT_FILE.write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding='utf-8')
    log(f"注入号池: {email}", True)
    return True


# ============================================================
# 浏览器注册 (DrissionPage + turnstilePatch)
# ============================================================

EDGE_EXE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
GMAIL_BASE = 'zhouyoukang1234@gmail.com'
GMAIL_PW = 'wsy057066wsy'

def find_browser():
    """查找可用浏览器: Edge 优先, Chrome 备用"""
    if os.path.exists(EDGE_EXE):
        return EDGE_EXE
    for p in [
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    ]:
        if os.path.exists(p):
            return p
    return None


def kill_stale_browsers():
    for proc in ['chrome.exe', 'msedge.exe']:
        try:
            subprocess.run(["taskkill", "/F", "/IM", proc], capture_output=True, timeout=8)
        except Exception:
            pass
    time.sleep(1.5)


_browser_counter = 0

def setup_browser(proxy=None, with_turnstile=True):
    """启动浏览器实例 (独立端口+user-data-dir, 避免冲突)"""
    global _browser_counter
    _browser_counter += 1
    from DrissionPage import ChromiumOptions, ChromiumPage
    import tempfile
    browser = find_browser()
    co = ChromiumOptions()
    if browser:
        co.set_browser_path(browser)
    co.auto_port()
    co.headless(False)
    co.set_argument('--no-first-run')
    co.set_argument('--disable-popup-blocking')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--disable-features=IsolateOrigins,site-per-process')
    # 持久化profile (Cloudflare更信任有历史的浏览器)
    ws_profile = os.path.join(tempfile.gettempdir(), f'ws_forge_profile_{_browser_counter}')
    co.set_argument(f'--user-data-dir={ws_profile}')
    if proxy:
        co.set_argument(f"--proxy-server={proxy}")
    if with_turnstile:
        # v2: 只加载anti-detection, 不注入假token
        for tp_path in [
            SCRIPT_DIR / "turnstilePatch",
            SCRIPT_DIR / "_archive" / "turnstilePatch",
            PROJECT_ROOT / "turnstilePatch",
        ]:
            if tp_path.exists():
                co.add_extension(str(tp_path))
                log(f"turnstilePatch: {tp_path}", True)
                break
        else:
            log("turnstilePatch未找到(非必需)", False)
    page = ChromiumPage(co)
    # CDP隐藏webdriver (比extension更底层)
    try:
        page.run_cdp('Page.addScriptToEvaluateOnNewDocument', source="""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """)
    except Exception:
        pass
    time.sleep(3)
    return page


def setup_gmail_browser():
    """启动专门用于读Gmail的独立浏览器实例 (独立user-data-dir避免冲突)"""
    from DrissionPage import ChromiumOptions, ChromiumPage
    import tempfile
    browser = find_browser()
    co = ChromiumOptions()
    if browser:
        co.set_browser_path(browser)
    co.auto_port()
    co.headless(False)
    co.set_argument('--no-first-run')
    # 独立用户数据目录, 避免和Windsurf浏览器冲突
    gmail_profile = os.path.join(tempfile.gettempdir(), f'gmail_browser_{os.getpid()}')
    co.set_argument(f'--user-data-dir={gmail_profile}')
    page = ChromiumPage(co)
    time.sleep(2)

    # 登录 Gmail
    log("Gmail浏览器: 登录中...")
    page.get('https://mail.google.com')
    time.sleep(4)

    for attempt in range(3):
        url = page.url
        if 'mail.google.com/mail' in url and 'signin' not in url.lower():
            log("Gmail浏览器: 已登录!", True)
            return page

        email_input = page.ele('tag:input@type=email', timeout=3)
        if email_input:
            email_input.input(GMAIL_BASE)
            time.sleep(0.5)
            next_btn = page.ele('#identifierNext', timeout=3)
            if next_btn:
                next_btn.click()
                time.sleep(4)

        pw_input = page.ele('tag:input@type=password', timeout=5)
        if pw_input:
            pw_input.input(GMAIL_PW)
            time.sleep(0.5)
            pw_next = page.ele('#passwordNext', timeout=3)
            if pw_next:
                pw_next.click()
                time.sleep(6)
                if 'mail.google.com/mail' in page.url:
                    log("Gmail浏览器: 登录成功!", True)
                    return page

    log("Gmail浏览器: 登录失败", False)
    try: page.quit()
    except Exception: pass
    return None


def gmail_mark_old_read(gmail_page):
    """将收件箱中旧的Windsurf验证邮件标记为已读，避免误读旧验证码"""
    try:
        gmail_page.get('https://mail.google.com/mail/u/0/#inbox')
        time.sleep(3)
        # 全选并标记已读所有未读邮件 (用键盘快捷键)
        gmail_page.run_js("""
            // 点击“全选”复选框
            var cb = document.querySelector('div[gh="mtb"] span[role="checkbox"], div[act="10"]');
            if (cb) cb.click();
        """)
        time.sleep(1)
        # 点击“标记为已读”
        gmail_page.run_js("""
            var btns = document.querySelectorAll('div[act], div[data-tooltip]');
            for (var b of btns) {
                var tip = (b.getAttribute('data-tooltip') || '').toLowerCase();
                if (tip.includes('read') || tip.includes('已读')) {
                    b.click(); break;
                }
            }
        """)
        time.sleep(1)
        log("Gmail: 旧邮件已标记已读", True)
    except Exception as e:
        log(f"Gmail标记已读异常: {e}")


def gmail_web_read_code(gmail_page, max_wait=180, poll_interval=8):
    """在独立Gmail浏览器实例中轮询收件箱，提取最新未读Windsurf验证码。
    不影响Windsurf浏览器。"""
    start = time.time()
    attempt = 0
    INBOX_URL = 'https://mail.google.com/mail/u/0/#inbox'

    while time.time() - start < max_wait:
        attempt += 1
        elapsed = int(time.time() - start)
        log(f"Gmail轮询... {elapsed}s/{max_wait}s (第{attempt}次)")
        try:
            gmail_page.get(INBOX_URL)
            time.sleep(4)

            # 只点击未读(zE)的Windsurf邮件
            clicked = gmail_page.run_js("""
                var rows = document.querySelectorAll('tr.zA.zE');
                for (var i = 0; i < Math.min(rows.length, 5); i++) {
                    var t = (rows[i].innerText || '').toLowerCase();
                    if (t.includes('windsurf') || t.includes('codeium') || t.includes('verify')) {
                        rows[i].click();
                        return true;
                    }
                }
                return false;
            """)

            if clicked:
                time.sleep(3)
                body = gmail_page.run_js("return document.body?.innerText || '';")
                codes = re.findall(r'\b(\d{6})\b', body)
                if codes:
                    log(f"验证码: {codes[0]}", True)
                    return codes[0]
                log("邮件已打开但未提取到6位码")

        except Exception as e:
            log(f"Gmail轮询异常: {str(e)[:80]}", False)
        time.sleep(poll_interval)

    log(f"{max_wait}s内未收到验证邮件", False)
    return None


def browser_register(page, email, password):
    """
    浏览器注册Windsurf账号。自动检测表单变体:
      A: email + password + confirmPassword (单页)
      B: firstName + lastName + email + agreeTOS → Turnstile → password (多步)
    返回: "verify" | "done" | "exists" | "error"
    """
    fn = random.choice(FIRST_NAMES)
    ln = random.choice(LAST_NAMES)
    log(f"注册: {email} ({fn} {ln})")

    VERIFY_KW = ["verify your email", "check your email", "verification email",
                 "we've sent", "we sent", "sent an email", "check your inbox",
                 "enter the code", "verification code", "we just sent",
                 "almost done", "one more step"]
    DONE_KW = ["dashboard", "welcome to windsurf", "get started", "open windsurf"]
    EXISTS_KW = ["already registered", "already exists", "email is already in use", "account already exists"]

    try:
        page.get(WINDSURF_REGISTER_URL)
        time.sleep(random.uniform(3, 5))

        # ---- React兼容填写辅助函数 ----
        def react_fill(fields_dict):
            """用nativeValueSetter + input/change事件填写React表单"""
            js_fills = []
            for name, val in fields_dict.items():
                js_fills.append(f"fill('{name}', '{val}');")
            page.run_js("""
                var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                function fill(name, val) {
                    var el = document.querySelector('input[name="' + name + '"]');
                    if (!el) return;
                    el.focus();
                    setter.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """ + '\n'.join(js_fills))

        def react_click_continue():
            """点击Continue按钮 (兼容disabled状态)"""
            return page.run_js("""
                var btns = document.querySelectorAll('button');
                for (var b of btns) {
                    if (b.innerText.trim().toLowerCase() === 'continue') {
                        if (b.disabled) return 'disabled';
                        b.click();
                        return 'clicked';
                    }
                }
                return 'not_found';
            """)

        # ---- Step 1: 名字+邮箱 ----
        has_fn = page.run_js("return !!document.querySelector('input[name=firstName]');")
        if has_fn:
            log("Step1: 填写name+email+TOS")
            react_fill({'firstName': fn, 'lastName': ln, 'email': email})
            # checkbox
            page.run_js("""
                var cb = document.querySelector('input[type="checkbox"]');
                if (cb && !cb.checked) cb.click();
            """)
            time.sleep(1)
            r = react_click_continue()
            log(f"Step1 Continue: {r}")
            if r == 'disabled':
                log("Continue仍disabled, 等待2s重试...")
                time.sleep(2)
                r = react_click_continue()
                log(f"Step1 重试: {r}")
            time.sleep(3)

        # ---- Step 2: 密码 ----
        # 等待密码字段出现
        pw_found = False
        for wait in range(20):
            pw_count = page.run_js("""
                var pws = document.querySelectorAll('input[type=password]');
                var vis = 0;
                for (var p of pws) { if (p.offsetWidth > 10) vis++; }
                return vis;
            """)
            if pw_count and pw_count >= 2:
                pw_found = True
                log(f"Step2: 密码字段出现 ({wait+1}s)")
                break
            if pw_count and pw_count >= 1:
                pw_found = True
                log(f"Step2: 密码字段出现 ({wait+1}s, count={pw_count})")
                break
            time.sleep(1)
            if wait % 5 == 4:
                vis = page.run_js("return document.body?.innerText?.substring(0,100)||'';") or ''
                log(f"等待密码({wait+1}s): {vis[:60]}")

        if not pw_found:
            # 可能直接跳到验证码页面
            vis = (page.run_js("return document.body?.innerText?.substring(0,300)||'';") or '').lower()
            if any(k in vis for k in VERIFY_KW):
                log("直接到达验证码页面!", True)
                return "verify"
            if any(k in vis for k in DONE_KW):
                return "done"
            log(f"密码字段未出现(20s)", False)
            return "error"

        react_fill({'password': password, 'confirmPassword': password})
        time.sleep(1)
        r = react_click_continue()
        log(f"Step2 Continue: {r}")
        if r == 'disabled':
            time.sleep(2)
            r = react_click_continue()
            log(f"Step2 重试: {r}")
        time.sleep(3)

        # 等待Turnstile解决 → 验证码/完成页面 (含刷新重试)
        for ts_attempt in range(3):
            if ts_attempt > 0:
                log(f"Turnstile重试 #{ts_attempt+1}: 刷新页面重填表单...")
                page.get(WINDSURF_REGISTER_URL)
                time.sleep(random.uniform(3, 5))
                # 重新检测并填写
                has_fn2 = page.run_js("return !!document.querySelector('input[name=firstName]');")
                if has_fn2:
                    react_fill({'firstName': fn, 'lastName': ln, 'email': email})
                    page.run_js('var cb=document.querySelector("input[type=checkbox]");if(cb&&!cb.checked)cb.click();')
                    time.sleep(1)
                    react_click_continue()
                    time.sleep(3)
                    # 等待密码
                    for _w in range(15):
                        pc = page.run_js("var p=document.querySelectorAll('input[type=password]');var v=0;for(var x of p)if(x.offsetWidth>10)v++;return v;")
                        if pc and pc >= 1:
                            break
                        time.sleep(1)
                    react_fill({'password': password, 'confirmPassword': password})
                    time.sleep(1)
                    react_click_continue()
                    time.sleep(3)

            log(f"等待Turnstile/验证码... (attempt {ts_attempt+1})")
            turnstile_stuck_since = None
            for wait_i in range(45):
                time.sleep(1)
                try:
                    visible = page.run_js("return document.body?.innerText?.substring(0,600)||'';") or ''
                    vl = visible.lower()
                    if any(k in vl for k in VERIFY_KW):
                        log("验证码页面到达!", True)
                        return "verify"
                    if any(k in vl for k in DONE_KW):
                        log("注册直接完成!", True)
                        return "done"
                    matched_exists = [k for k in EXISTS_KW if k in vl]
                    if matched_exists:
                        log(f"邮箱已注册 (matched: {matched_exists})", False)
                        log(f"  页面文字: {vl[:200]}")
                        return "exists"

                    if 'verify that you are human' in vl:
                        if turnstile_stuck_since is None:
                            turnstile_stuck_since = wait_i
                        # 每5秒尝试点击Continue
                        if wait_i % 5 == 4:
                            page.run_js("""
                                var btns = document.querySelectorAll('button');
                                for (var b of btns) {
                                    if (b.innerText.trim().toLowerCase()==='continue' && !b.disabled) {b.click();break;}
                                }
                            """)
                    else:
                        turnstile_stuck_since = None

                    if wait_i % 15 == 14:
                        btn_info = page.run_js("""
                            var btns = document.querySelectorAll('button');
                            return Array.from(btns).map(function(b){return b.innerText.trim().substring(0,12)+'(d='+b.disabled+')';});
                        """) or []
                        log(f"等待({wait_i+1}s) btns={btn_info}")
                except Exception:
                    pass

            # 本轮超时, 检查是否Turnstile卡住
            if turnstile_stuck_since is not None:
                log(f"Turnstile卡住{45 - turnstile_stuck_since}s, 将刷新重试...")
                continue  # 下一轮重试
            break  # 非Turnstile问题,不重试

        log("验证码页面超时", False)
        return "error"

    except Exception as e:
        log(f"注册异常: {e}", False)
        traceback.print_exc()
        return "error"


def enter_verify_code(page, code):
    """在Windsurf验证码页面输入6位码 (纯JS, 支持shadow DOM/iframe)"""
    log(f"输入验证码: {code}")
    try:
        time.sleep(2)
        # 先诊断页面结构
        diag = page.run_js("""
            var info = {url: location.href, title: document.title};
            info.allInputs = document.querySelectorAll('input').length;
            info.iframes = document.querySelectorAll('iframe').length;
            info.shadowHosts = document.querySelectorAll('*').length;
            info.bodyText = (document.body?.innerText || '').substring(0, 300);
            // 检查所有input的详细信息
            var inputs = Array.from(document.querySelectorAll('input'));
            info.inputDetails = inputs.map(function(inp) {
                return {type: inp.type, name: inp.name, id: inp.id,
                        visible: inp.offsetParent !== null,
                        display: getComputedStyle(inp).display,
                        w: inp.offsetWidth, h: inp.offsetHeight};
            });
            return info;
        """)
        log(f"页面诊断: inputs={diag.get('allInputs')}, iframes={diag.get('iframes')}")
        log(f"页面文字: {str(diag.get('bodyText',''))[:150]}")
        if diag.get('inputDetails'):
            for d in diag['inputDetails'][:8]:
                log(f"  input: type={d.get('type')} name={d.get('name')} visible={d.get('visible')} {d.get('w')}x{d.get('h')}")

        # 方式1: 逐个focus+keyboard模拟 (最可靠的React OTP方式)
        result = page.run_js(f"""
            var code = '{code}';
            var filled = false;
            var inputs = Array.from(document.querySelectorAll('input'));
            var usable = inputs.filter(function(inp) {{
                if (inp.type === 'hidden' || inp.type === 'checkbox' || inp.type === 'radio' ||
                    inp.type === 'submit' || inp.type === 'password') return false;
                var s = getComputedStyle(inp);
                return s.display !== 'none' && s.visibility !== 'hidden' && inp.offsetWidth > 0;
            }});

            if (usable.length >= 6) {{
                var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                for (var i = 0; i < 6; i++) {{
                    var inp = usable[i];
                    inp.focus();
                    // 清空再填
                    setter.call(inp, '');
                    inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                    // 用 InputEvent 模拟键盘输入
                    setter.call(inp, code[i]);
                    inp.dispatchEvent(new InputEvent('input', {{
                        bubbles: true, inputType: 'insertText', data: code[i]
                    }}));
                    inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                    // 也发keyboard事件
                    inp.dispatchEvent(new KeyboardEvent('keydown', {{key: code[i], bubbles: true}}));
                    inp.dispatchEvent(new KeyboardEvent('keyup', {{key: code[i], bubbles: true}}));
                }}
                filled = true;
            }} else if (usable.length >= 1) {{
                var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(usable[0], code);
                usable[0].dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: code}}));
                usable[0].dispatchEvent(new Event('change', {{bubbles: true}}));
                filled = true;
            }}

            // 也设置隐藏的otpCode字段
            var hidden = document.querySelector('input[name="otpCode"]');
            if (hidden) {{
                var setter2 = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter2.call(hidden, code);
                hidden.dispatchEvent(new Event('input', {{bubbles: true}}));
                hidden.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}

            return {{filled: filled, visibleCount: inputs.filter(function(i){{ return i.type !== 'hidden'; }}).length, usableCount: usable.length}};
        """)
        log(f"JS填入: {result}")

        if result and result.get('filled'):
            time.sleep(2)
            # 点击提交 (Create account / Verify / Submit)
            clicked_btn = page.run_js("""
                var btns = document.querySelectorAll('button');
                var keywords = ['create account', 'create', 'verify', 'submit'];
                for (var k of keywords) {
                    for (var b of btns) {
                        var t = (b.innerText || '').toLowerCase().trim();
                        if (t.includes(k) && !t.includes('back') && !b.disabled) {
                            b.click();
                            return t;
                        }
                    }
                }
                return null;
            """)
            log(f"验证码提交按钮: {clicked_btn}")
            time.sleep(8)

            # 检查结果
            check = page.run_js("return {url: location.href, text: (document.body?.innerText||'').substring(0,300)};")
            check_text = (check.get('text', '') if check else '').lower()
            if any(k in check_text for k in ['verified', 'success', 'welcome', 'dashboard', 'get started', 'open windsurf']):
                log("验证成功!", True)
                return True
            if 'check your inbox' in check_text or 'verification code' in check_text:
                log(f"验证码可能未生效, 重试提交...")
                page.run_js("""
                    var btns = document.querySelectorAll('button');
                    for (var b of btns) {
                        var t = (b.innerText || '').toLowerCase();
                        if ((t.includes('create') || t.includes('verify')) && !b.disabled) {
                            b.click(); break;
                        }
                    }
                """)
                time.sleep(8)
                check2 = page.run_js("return (document.body?.innerText||'').substring(0,200);") or ''
                log(f"重试后: {check2[:100]}")
            log(f"验证后: {check_text[:100]}")
            return True  # 乐观返回, 让Firebase激活来判断
        else:
            log("JS未能填入验证码", False)
            return False
    except Exception as e:
        log(f"验证码输入异常: {e}", False)
        return False


# ============================================================
# 核心铸造流程
# ============================================================

ALIAS_STATE_FILE = SCRIPT_DIR / '_gmail_alias_index.txt'

def next_alias_index():
    """获取并递增 Gmail alias 索引"""
    try:
        idx = int(ALIAS_STATE_FILE.read_text().strip())
    except Exception:
        idx = 1
    ALIAS_STATE_FILE.write_text(str(idx + 1))
    return idx


def forge_one(proxy_str=None, ws_browser=None, gmail_browser=None):
    """
    铸造一个Windsurf Pro Trial账号。
    mail.tm API创建邮箱 + 单浏览器注册Windsurf + API轮询验证码。
    返回: dict with email, password, apiKey, status
    """
    t0 = time.time()
    own_ws = (ws_browser is None)

    # Step 1: 创建mail.tm临时邮箱
    log("Step 1: 创建mail.tm邮箱...")
    try:
        ws_email, mail_jwt = mailtm_create()
        domain = ws_email.split('@')[1]
        log(f"邮箱: {ws_email}", True)
    except Exception as e:
        log(f"mail.tm创建失败: {e}", False)
        return {'status': 'email_create_failed', 'error': str(e)}

    ws_password = gen_password()

    # Step 2: 启动浏览器
    page = ws_browser
    if own_ws:
        log("Step 2: 启动浏览器...")
        for _retry in range(3):
            try:
                page = setup_browser(proxy=None, with_turnstile=True)
                break
            except Exception as e:
                log(f"浏览器启动失败(retry {_retry+1}): {e}", False)
                kill_stale_browsers()
                time.sleep(3)
        else:
            return {'email': ws_email, 'password': ws_password, 'status': 'browser_error',
                    'domain': domain}

    # Step 3: 浏览器注册 Windsurf
    log(f"Step 3: 浏览器注册Windsurf ({ws_email})...")
    try:
        reg_status = browser_register(page, ws_email, ws_password)
    except Exception as e:
        log(f"注册异常: {e}", False)
        if own_ws:
            try: page.quit()
            except Exception: pass
        return {'email': ws_email, 'password': ws_password, 'status': 'browser_error',
                'error': str(e), 'domain': domain}

    if reg_status == "done":
        log("注册直接完成,进入激活...", True)
        api_key, plan = activate_account(ws_email, ws_password)
        plan_tag = 'Trial' if plan == 'pro_trial' else plan
        inject_to_pool(ws_email, ws_password, api_key, source="forge_mailtm", plan_tag=plan_tag)
        is_trial = (plan == 'pro_trial')
        status = 'pro_trial' if (api_key and is_trial) else ('free' if plan == 'free' else ('activated' if api_key else 'registered'))
        result = {
            'email': ws_email, 'password': ws_password, 'apiKey': api_key,
            'status': status, 'plan': plan, 'domain': domain, 'path': 'mailtm',
            'ms': int((time.time() - t0) * 1000),
            'timestamp': datetime.now(CST).isoformat(),
        }
        save_result(result)
        if own_ws:
            try: page.quit()
            except Exception: pass
        return result

    if reg_status != "verify":
        log(f"注册失败: {reg_status}", False)
        if own_ws:
            try: page.quit()
            except Exception: pass
        return {'email': ws_email, 'password': ws_password, 'status': reg_status,
                'domain': domain}

    # Step 4: mail.tm API轮询验证码 (无需Gmail浏览器!)
    log("Step 4: mail.tm轮询验证码...")
    code = mailtm_wait_verify_code(ws_email, mail_jwt, max_wait=180, poll_interval=6)

    if not code:
        if own_ws:
            try: page.quit()
            except Exception: pass
        result = {
            'email': ws_email, 'password': ws_password, 'status': 'verify_timeout',
            'domain': domain, 'ms': int((time.time() - t0) * 1000),
            'timestamp': datetime.now(CST).isoformat(),
        }
        save_result(result)
        return result

    # Step 5: 浏览器输入验证码
    log("Step 5: 浏览器输入验证码...")
    verified = enter_verify_code(page, code)

    if not verified:
        if own_ws:
            try: page.quit()
            except Exception: pass
        result = {
            'email': ws_email, 'password': ws_password, 'verify_code': code,
            'status': 'code_entry_failed', 'domain': domain,
            'ms': int((time.time() - t0) * 1000),
            'timestamp': datetime.now(CST).isoformat(),
        }
        save_result(result)
        return result

    # Step 6: Firebase激活 + Plan验证 (含重试)
    log("Step 6: Firebase激活 + Plan验证...")
    api_key, plan = activate_account(ws_email, ws_password)

    # plan可能需要传播时间, 若free则等待重试
    if plan == 'free' and api_key:
        for retry_delay in [5, 10, 15]:
            log(f"Plan=free, {retry_delay}s后重试...")
            time.sleep(retry_delay)
            _, plan2 = activate_account(ws_email, ws_password)
            if plan2 == 'pro_trial':
                plan = plan2
                log("Plan已升级为pro_trial!", True)
                break
            log(f"Plan仍为: {plan2}")

    # Step 7: 注入号池
    plan_tag = 'Trial' if plan == 'pro_trial' else plan
    inject_to_pool(ws_email, ws_password, api_key, source="forge_mailtm", plan_tag=plan_tag)

    is_trial = (plan == 'pro_trial')
    status = 'pro_trial' if (api_key and is_trial) else ('free' if plan == 'free' else ('activated' if api_key else 'registered'))
    elapsed_ms = int((time.time() - t0) * 1000)
    result = {
        'email': ws_email, 'password': ws_password, 'apiKey': api_key,
        'verify_code': code, 'status': status, 'plan': plan, 'domain': domain,
        'path': 'mailtm', 'ms': elapsed_ms,
        'timestamp': datetime.now(CST).isoformat(),
    }
    save_result(result)

    if api_key and plan == 'pro_trial':
        log(f"铸造完成: {ws_email} → Pro Trial ({elapsed_ms}ms)", True)
    elif api_key:
        log(f"铸造完成: {ws_email} → {plan} ({elapsed_ms}ms)", False)
    else:
        log(f"铸造部分完成: {ws_email} plan={plan} ({elapsed_ms}ms)", False)

    if own_ws:
        try: page.quit()
        except Exception: pass
    return result


def forge_batch(n, delay_min=10, delay_max=25):
    """批量铸造N个账号 (mail.tm + 单浏览器)"""
    print(f"\n{'=' * 70}")
    print(f"  mail.tm 铸造引擎 v{VERSION}")
    print(f"  数量: {n}")
    print(f"  {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S CST')}")
    print(f"{'=' * 70}\n")

    success = 0
    failed = 0
    pro_trial_count = 0

    kill_stale_browsers()

    for i in range(n):
        print(f"\n{'#' * 20} [{i+1}/{n}] {'#' * 20}")
        # 每次创建新的浏览器 (干净session)
        ws_page = setup_browser(proxy=None, with_turnstile=True)
        try:
            result = forge_one(ws_browser=ws_page)
        except Exception as e:
            log(f"forge_one异常: {e}", False)
            result = {'status': 'exception', 'error': str(e)}
        finally:
            try: ws_page.quit()
            except Exception: pass

        plan = result.get('plan', '?')
        if result.get('apiKey') and plan == 'pro_trial':
            success += 1
            pro_trial_count += 1
            log(f"[{pro_trial_count}/{n}] Pro Trial: {result.get('email','?')}", True)
        elif result.get('apiKey'):
            success += 1
            log(f"[{success}/{n}] {plan}: {result.get('email','?')}", False)
        else:
            failed += 1
            log(f"失败: {result.get('status')} plan={plan}", False)

        if i < n - 1:
            delay = random.uniform(delay_min, delay_max)
            log(f"冷却 {delay:.0f}s...")
            time.sleep(delay)

    try: gmail.quit()
    except Exception: pass

    print(f"\n{'=' * 70}")
    print(f"  铸造完成: {success} 成功 / {failed} 失败 / {n} 总计")
    print(f"  Pro Trial: {pro_trial_count} (可用Claude)")
    print(f"{'=' * 70}\n")
    return success, failed


# ============================================================
# 探测
# ============================================================

def probe():
    """探测temp-mail.io可用性"""
    print(f"\n{'=' * 60}")
    print(f"  temp-mail.io 探测 | Proxy: port {PP}")
    print(f"{'=' * 60}\n")

    # 创建邮箱
    try:
        email, token = tempmail_create()
        log(f"创建邮箱: {email}", True)
        log(f"域名: {email.split('@')[1]}", True)
    except Exception as e:
        log(f"创建失败: {e}", False)
        return

    # 读取收件箱
    try:
        msgs = tempmail_inbox(email, token)
        log(f"收件箱: {len(msgs)} 封", True)
    except Exception as e:
        log(f"收件箱读取失败: {e}", False)

    # 检查可用域名
    try:
        req = Request(f'{TEMPMAIL_API}/domains')
        if PP > 0:
            handler = ProxyHandler({'https': f'http://127.0.0.1:{PP}', 'http': f'http://127.0.0.1:{PP}'})
            resp = build_opener(handler).open(req, timeout=10)
        else:
            resp = urlopen(req, timeout=10, context=_ssl_ctx)
        domains = json.loads(resp.read())
        if isinstance(domains, list):
            log(f"可用域名: {', '.join(d.get('name','?') if isinstance(d,dict) else str(d) for d in domains[:10])}", True)
        else:
            log(f"域名响应: {json.dumps(domains, ensure_ascii=False)[:150]}")
    except Exception as e:
        log(f"域名查询: {e}")

    # 检查DrissionPage
    try:
        from DrissionPage import ChromiumOptions
        log("DrissionPage: OK", True)
    except Exception:
        log("DrissionPage: 未安装", False)

    # 检查turnstilePatch
    found = False
    for tp in [SCRIPT_DIR / "turnstilePatch", PROJECT_ROOT / "turnstilePatch"]:
        if tp.exists():
            log(f"turnstilePatch: {tp}", True)
            found = True
            break
    if not found:
        log("turnstilePatch: 未找到", False)

    # 检查号池
    if LH_ACCT_FILE.exists():
        try:
            pool = json.loads(LH_ACCT_FILE.read_text(encoding='utf-8'))
            has_key = sum(1 for a in pool if a.get('apiKey'))
            log(f"号池: {len(pool)} 总 / {has_key} apiKey", True)
        except Exception:
            log("号池: 读取失败", False)

    print(f"\n{'=' * 60}\n")


def show_status():
    results = load_results()
    print(f"\n{'=' * 60}")
    print(f"  temp-mail.io 铸造记录")
    print(f"{'=' * 60}\n")

    if not results:
        print("  (无记录)")
    else:
        activated = [r for r in results if r.get('apiKey')]
        registered = [r for r in results if r.get('status') == 'registered']
        failed = [r for r in results if r.get('status') not in ('activated', 'registered')]
        print(f"  总记录: {len(results)}")
        print(f"  激活(apiKey): {len(activated)}")
        print(f"  已注册(无key): {len(registered)}")
        print(f"  失败: {len(failed)}")

        # 域名统计
        domains = {}
        for r in results:
            d = r.get('domain', '?')
            domains[d] = domains.get(d, 0) + 1
        print(f"\n  域名分布: {domains}")

        # 最近5条
        print(f"\n  最近5条:")
        for r in results[-5:]:
            ts = r.get('timestamp', '?')[:19]
            email = r.get('email', '?')
            status = r.get('status', '?')
            key = 'Y' if r.get('apiKey') else 'N'
            print(f"    {ts}  {email:40}  {status:15}  key={key}")

    print(f"\n{'=' * 60}\n")


# ============================================================
# CLI
# ============================================================

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()

    if cmd == 'probe':
        probe()
    elif cmd == 'status':
        show_status()
    elif cmd == 'forge':
        n = 1
        if len(sys.argv) >= 3:
            try:
                n = int(sys.argv[2])
            except ValueError:
                pass
        if n == 1:
            kill_stale_browsers()
            forge_one()
        else:
            forge_batch(n)
    else:
        print(f"  未知命令: {cmd}")
        print(__doc__)


if __name__ == '__main__':
    main()
