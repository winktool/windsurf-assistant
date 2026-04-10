#!/usr/bin/env python3
"""
混合注册引擎 — 反者道之动
========================================
逆向发现:
  - Firebase signUp API 被 Cloud Function 阻断 ("Email/password signups are disabled")
  - Web注册页 windsurf.com/account/register 需通过 Cloudflare Turnstile
  - Turnstile token 经服务端验证, 假token无效
  → 注册必须经过浏览器 (DrissionPage / Playwright)

本引擎价值 — 后处理管线 (纯API, 零浏览器):
  1. Firebase signIn(email, pw) → idToken
  2. RegisterUser(protobuf idToken) → apiKey
  3. GetPlanStatus → 确认 Pro Trial
  4. 注入号池 (所有账号文件同步)

用法:
  python _api_register_engine.py activate EMAIL PW  # 激活: signIn→apiKey→注池
  python _api_register_engine.py activate-all        # 批量激活所有未激活账号
  python _api_register_engine.py verify EMAIL PW     # 验证Pro Trial状态
  python _api_register_engine.py probe               # 探测API可达性
  python _api_register_engine.py browser             # 启动浏览器注册(委托_register_one.py)
  python _api_register_engine.py browser --n=3       # 浏览器批量注册
  python _api_register_engine.py pool-status         # 号池状态
"""

import json, os, sys, time, random, string, re, ssl, socket, struct
import html as html_mod
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen, ProxyHandler, build_opener
from urllib.error import HTTPError, URLError

# Fix Windows GBK console
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
CST = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════════════
# §1  常量 — 从 netLayer.js 逆向提取
# ═══════════════════════════════════════════════════════

FIREBASE_KEYS = [
    'AIzaSyDsOl-1XpT5err0Tcnx8FFod1H8gVGIycY',
    'AIzaSyDKm6GGxMJfCbNf-k0kPytiGLaqFJpeSac',
]

RELAYS = [
    'https://aiotvr.xyz/wam',
    'https://YOUR_RELAY_HOST.example.com',
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

PROXY_PORTS = [7890, 7897, 7891, 10808, 1080]

FIRST_NAMES = ["Alex","Jordan","Taylor","Morgan","Casey","Riley","Quinn","Avery",
    "Charlie","Dakota","Emerson","Finley","Harper","Jamie","Kendall","Logan"]
LAST_NAMES = ["Anderson","Brooks","Carter","Davis","Edwards","Fisher","Garcia",
    "Hughes","Irving","Jensen","Kim","Lee","Mitchell","Nelson"]


# ═══════════════════════════════════════════════════════
# §2  工具层
# ═══════════════════════════════════════════════════════

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

_proxy_cache = None


def log(msg, ok=None):
    icon = "+" if ok is True else ("-" if ok is False else "*")
    ts = datetime.now(CST).strftime("%H:%M:%S")
    print(f"  [{ts}][{icon}] {msg}")


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
        except:
            continue
    _proxy_cache = 0
    return 0


def http_json(url, data=None, method='POST', use_proxy=True, timeout=15, headers=None):
    """HTTP JSON request via urllib. Returns parsed dict."""
    body = json.dumps(data).encode('utf-8') if data else None
    req = Request(url, data=body, method=method)
    req.add_header('Content-Type', 'application/json')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    proxy_port = detect_proxy() if use_proxy else 0
    try:
        if proxy_port > 0:
            handler = ProxyHandler({
                'https': f'http://127.0.0.1:{proxy_port}',
                'http': f'http://127.0.0.1:{proxy_port}',
            })
            opener = build_opener(handler)
            resp = opener.open(req, timeout=timeout)
        else:
            resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read()
        try:
            return json.loads(body)
        except:
            raise RuntimeError(f"HTTP {e.code}: {body[:300]}")


def http_bin(url, bin_data, use_proxy=True, timeout=15):
    """HTTP POST binary (protobuf). Returns raw bytes."""
    req = Request(url, data=bin_data, method='POST')
    req.add_header('Content-Type', 'application/proto')
    req.add_header('Accept', 'application/proto')

    proxy_port = detect_proxy() if use_proxy else 0
    if proxy_port > 0:
        handler = ProxyHandler({
            'https': f'http://127.0.0.1:{proxy_port}',
            'http': f'http://127.0.0.1:{proxy_port}',
        })
        opener = build_opener(handler)
        resp = opener.open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    return resp.read()


def http_get(url, use_proxy=True, timeout=15, headers=None):
    """HTTP GET, returns text."""
    req = Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    proxy_port = detect_proxy() if use_proxy else 0
    if proxy_port > 0:
        handler = ProxyHandler({
            'https': f'http://127.0.0.1:{proxy_port}',
            'http': f'http://127.0.0.1:{proxy_port}',
        })
        opener = build_opener(handler)
        resp = opener.open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    return resp.read()


def http_get_json(url, use_proxy=True, timeout=15, headers=None):
    """HTTP GET JSON."""
    raw = http_get(url, use_proxy=use_proxy, timeout=timeout, headers=headers)
    return json.loads(raw)


def gen_password():
    chars = string.ascii_letters + string.digits + "!@#$"
    pw = (random.choice(string.ascii_uppercase) + random.choice(string.ascii_lowercase) +
          random.choice(string.digits) + random.choice("!@#$") +
          ''.join(random.choices(chars, k=12)))
    return ''.join(random.sample(pw, len(pw)))


# ═══════════════════════════════════════════════════════
# §3  Protobuf 最小编解码 — 移植自 netLayer.js
# ═══════════════════════════════════════════════════════

def encode_proto(value: str, field: int = 1) -> bytes:
    """Protobuf: encode string as field with wire type 2 (length-delimited)."""
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
        b = data[pos]
        pos += 1
        if shift < 28:
            result |= (b & 0x7f) << shift
        else:
            result += (b & 0x7f) * (2 ** shift)
        if (b & 0x80) == 0:
            break
        shift += 7
    return result, pos


def parse_proto_str(buf: bytes) -> str:
    """Extract field 1 string from protobuf (same as netLayer.js parseProtoStr)."""
    if not buf or len(buf) < 3 or buf[0] != 0x0a:
        return None
    length, pos = read_varint(buf, 1)
    if pos + length > len(buf):
        return None
    return buf[pos:pos + length].decode('utf-8', errors='replace')


def parse_proto_msg(buf: bytes) -> dict:
    """Structure-aware protobuf parser → { fieldNum: [values] }."""
    fields = {}
    pos = 0
    while pos < len(buf):
        tag, pos = read_varint(buf, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if field_num == 0 or field_num > 1000 or pos >= len(buf):
            break
        if wire_type == 0:  # varint
            val, pos = read_varint(buf, pos)
            fields.setdefault(field_num, []).append({'value': val})
        elif wire_type == 2:  # length-delimited
            length, pos = read_varint(buf, pos)
            if length < 0 or length > 65536 or pos + length > len(buf):
                break
            data = buf[pos:pos + length]
            s = None
            try:
                s = data.decode('utf-8')
                if not all(0x20 <= ord(c) <= 0x7e or c in '\n\r\t' for c in s):
                    s = None
            except:
                pass
            fields.setdefault(field_num, []).append({
                'bytes': data, 'string': s, 'length': length
            })
            pos += length
        elif wire_type == 1:  # 64-bit
            if pos + 8 > len(buf):
                break
            fields.setdefault(field_num, []).append({'bytes': buf[pos:pos + 8]})
            pos += 8
        elif wire_type == 5:  # 32-bit
            if pos + 4 > len(buf):
                break
            fields.setdefault(field_num, []).append({'bytes': buf[pos:pos + 4]})
            pos += 4
        else:
            break
    return fields


# ═══════════════════════════════════════════════════════
# §4  Firebase Auth — signUp + sendOobCode + signIn
# ═══════════════════════════════════════════════════════

def firebase_signup(email: str, password: str) -> dict:
    """Firebase signUp → creates new account, returns {idToken, localId, email} or {error}."""
    payload = {'email': email, 'password': password, 'returnSecureToken': True}

    # Try relay first
    for relay in RELAYS:
        try:
            # Relay may support signUp passthrough
            r = http_json(f'{relay}/firebase/signup', payload, timeout=15)
            if r.get('idToken'):
                log(f"signUp OK via relay", True)
                return r
            if r.get('error', {}).get('message'):
                msg = r['error']['message']
                if 'EMAIL_EXISTS' in msg:
                    return {'error': msg}
        except:
            pass

    # Direct Firebase signUp — direct first (proxy causes SSL issues with Google)
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={key}'
        for use_p in [False, True]:
            try:
                r = http_json(url, payload, use_proxy=use_p, timeout=12)
                if r.get('idToken'):
                    channel = f"{'proxy' if use_p else 'direct'}-{key[-4:]}"
                    log(f"signUp OK via {channel}", True)
                    return r
                if r.get('error', {}).get('message'):
                    msg = r['error']['message']
                    if 'EMAIL_EXISTS' in msg:
                        return {'error': msg}
                    # WEAK_PASSWORD, INVALID_EMAIL → permanent
                    if any(k in msg for k in ['WEAK_PASSWORD', 'INVALID_EMAIL', 'TOO_MANY_ATTEMPTS']):
                        return {'error': msg}
            except Exception as e:
                continue

    return {'error': 'all_channels_failed'}


def firebase_send_verification(id_token: str) -> dict:
    """Send email verification via Firebase."""
    payload = {'requestType': 'VERIFY_EMAIL', 'idToken': id_token}

    for relay in RELAYS:
        try:
            r = http_json(f'{relay}/firebase/send-oob', payload, timeout=15)
            if r.get('email'):
                log(f"Verification email sent via relay", True)
                return r
        except:
            pass

    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={key}'
        for use_p in [False, True]:
            try:
                r = http_json(url, payload, use_proxy=use_p, timeout=12)
                if r.get('email'):
                    channel = f"{'proxy' if use_p else 'direct'}-{key[-4:]}"
                    log(f"Verification email sent via {channel}", True)
                    return r
            except:
                continue

    return {'error': 'send_verification_failed'}


def firebase_signin(email: str, password: str) -> dict:
    """Firebase signIn → idToken (for refreshing token after verification)."""
    payload = {'email': email, 'password': password, 'returnSecureToken': True}

    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}'
        for use_p in [False, True]:
            try:
                r = http_json(url, payload, use_proxy=use_p, timeout=12)
                if r.get('idToken'):
                    return r
            except:
                continue

    return {'error': 'signin_failed'}


def firebase_get_user(id_token: str) -> dict:
    """Get user info from Firebase (check emailVerified)."""
    payload = {'idToken': id_token}

    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={key}'
        for use_p in [False, True]:
            try:
                r = http_json(url, payload, use_proxy=use_p, timeout=12)
                users = r.get('users', [])
                if users:
                    return users[0]
            except:
                continue

    return {}


# ═══════════════════════════════════════════════════════
# §5  Windsurf gRPC — RegisterUser + GetPlanStatus
# ═══════════════════════════════════════════════════════

def windsurf_register(id_token: str) -> dict:
    """RegisterUser → apiKey via protobuf gRPC."""
    buf = encode_proto(id_token)

    # Channel 1: Relay
    for relay in RELAYS:
        try:
            resp = http_bin(f'{relay}/windsurf/register', buf, timeout=15)
            api_key = parse_proto_str(resp)
            if api_key:
                log(f"RegisterUser OK via relay: apiKey={api_key[:20]}...", True)
                return {'ok': True, 'apiKey': api_key}
        except:
            pass

    # Channel 2: Direct gRPC
    for url in REGISTER_URLS:
        try:
            resp = http_bin(url, buf, timeout=15)
            api_key = parse_proto_str(resp)
            if api_key:
                log(f"RegisterUser OK via {url.split('/')[2]}: apiKey={api_key[:20]}...", True)
                return {'ok': True, 'apiKey': api_key}
        except Exception as e:
            log(f"RegisterUser {url.split('/')[2]}: {e}")
            continue

    return {'ok': False, 'error': 'all_register_channels_failed'}


def windsurf_plan_status(id_token: str) -> dict:
    """GetPlanStatus → parse plan info from protobuf."""
    buf = encode_proto(id_token)

    for url in PLAN_STATUS_URLS:
        try:
            resp = http_bin(url, buf, timeout=15)
            if resp:
                fields = parse_proto_msg(resp)
                # Extract plan info from protobuf fields
                result = {'raw_fields': {}}
                for fn, vals in fields.items():
                    for v in vals:
                        if v.get('string'):
                            result['raw_fields'][fn] = v['string']
                        elif 'value' in v:
                            result['raw_fields'][fn] = v['value']
                return result
        except:
            continue
    return {}


# ═══════════════════════════════════════════════════════
# §6  临时邮箱层 — 纯HTTP, 零浏览器
# ═══════════════════════════════════════════════════════

class TempMailLol:
    """tempmail.lol — cold domain, less likely blocked."""
    def __init__(self):
        self.token = None
        self.address = None

    def create_inbox(self):
        d = http_get_json("https://api.tempmail.lol/v2/inbox/create", timeout=20)
        if not isinstance(d, dict) or not d.get("address"):
            raise RuntimeError(f"tempmail.lol create failed: {d}")
        self.address = d["address"]
        self.token = d.get("token", "")
        log(f"tempmail.lol: {self.address}", True)
        return self.address

    def wait_for_email(self, timeout=180, poll=5, subject_filter=None):
        if not self.token:
            return None
        start = time.time()
        while time.time() - start < timeout:
            try:
                d = http_get_json(
                    f"https://api.tempmail.lol/v2/inbox?token={self.token}",
                    timeout=15)
                emails = d.get("emails", []) if isinstance(d, dict) else []
                for m in emails:
                    subj = m.get("subject", "")
                    if subject_filter and subject_filter.lower() not in subj.lower():
                        continue
                    return {
                        "subject": subj,
                        "from": m.get("from", ""),
                        "body": m.get("body", ""),
                    }
            except:
                pass
            elapsed = int(time.time() - start)
            if elapsed > 0 and elapsed % 20 == 0:
                log(f"Waiting for email... ({elapsed}s/{timeout}s)")
            time.sleep(poll)
        return None


class MailTm:
    """Mail.tm — another cold domain provider."""
    API = "https://api.mail.tm"

    def __init__(self):
        self.token = None
        self.address = None

    def create_inbox(self):
        d = http_get_json(f"{self.API}/domains", timeout=30)
        members = d.get("hydra:member", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
        active = [x["domain"] for x in members if isinstance(x, dict) and x.get("isActive")]
        if not active:
            raise RuntimeError("No active Mail.tm domains")
        dom = active[0]
        pfx = "ws" + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        addr = f"{pfx}@{dom}"
        pw = ''.join(random.choices(string.ascii_letters + string.digits, k=14))
        # Create account
        http_json(f"{self.API}/accounts", {"address": addr, "password": pw}, timeout=30)
        # Get token
        tok = http_json(f"{self.API}/token", {"address": addr, "password": pw}, timeout=30)
        self.token = tok.get("token", "") if isinstance(tok, dict) else ""
        self.address = addr
        if not self.token:
            raise RuntimeError(f"Mail.tm token failed: {tok}")
        log(f"Mail.tm: {addr}", True)
        return addr

    def wait_for_email(self, timeout=180, poll=5, subject_filter=None):
        if not self.token:
            return None
        start = time.time()
        seen_ids = set()
        while time.time() - start < timeout:
            try:
                d = http_get_json(
                    f"{self.API}/messages?page=1",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=15)
                msgs = d.get("hydra:member", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
                for m in msgs:
                    mid = m.get("id", "")
                    if not mid or mid in seen_ids:
                        continue
                    seen_ids.add(mid)
                    subj = m.get("subject", "")
                    if subject_filter and subject_filter.lower() not in subj.lower():
                        continue
                    # Fetch full message
                    full_raw = http_get(
                        f"{self.API}/messages/{mid}",
                        headers={"Authorization": f"Bearer {self.token}"},
                        timeout=15)
                    full = json.loads(full_raw)
                    body_parts = full.get("html", [full.get("text", "")])
                    body = body_parts if isinstance(body_parts, str) else " ".join(str(x) for x in body_parts)
                    return {
                        "subject": full.get("subject", subj),
                        "from": full.get("from", {}).get("address", "?"),
                        "body": body,
                    }
            except:
                pass
            elapsed = int(time.time() - start)
            if elapsed > 0 and elapsed % 20 == 0:
                log(f"Waiting for email... ({elapsed}s/{timeout}s)")
            time.sleep(poll)
        return None


def create_temp_email():
    """Create temp email, returns (provider_instance, email_address) or (None, None)."""
    providers = [
        ("tempmail.lol", TempMailLol),
        ("Mail.tm", MailTm),
    ]
    for name, cls in providers:
        try:
            p = cls()
            addr = p.create_inbox()
            if addr:
                return p, addr
        except Exception as e:
            log(f"{name} failed: {e}", False)
    return None, None


# ═══════════════════════════════════════════════════════
# §7  验证链接提取
# ═══════════════════════════════════════════════════════

def extract_verify_link(body: str) -> str:
    """Extract verification link from email body."""
    if not body:
        return None
    content = html_mod.unescape(str(body))
    all_urls = re.findall(r'https?://[^\s<>"\']+', content)
    all_urls = [re.sub(r'["\'>;\s]+$', '', u.rstrip('.')) for u in all_urls]

    # Priority 1: Firebase/Windsurf verification links
    verify = [u for u in all_urls
              if any(k in u.lower() for k in ['verify', 'confirm', 'oobcode', 'continueurl', 'apikey'])]
    if verify:
        return verify[0]

    # Priority 2: Any windsurf/codeium link
    ws = [u for u in all_urls if 'windsurf' in u.lower() or 'codeium' in u.lower() or 'firebaseapp' in u.lower()]
    if ws:
        return ws[0]

    # Priority 3: Any non-tempmail link
    ext = [u for u in all_urls if not any(d in u.lower() for d in ['tempmail', 'mail.tm', 'guerrillamail'])]
    return ext[0] if ext else None


def click_verify_link(url: str) -> bool:
    """Click verification link via HTTP GET."""
    try:
        # Follow redirects
        resp = http_get(url, timeout=20)
        log(f"Verification link clicked: {url[:60]}...", True)
        return True
    except Exception as e:
        log(f"Verify link error: {e}", False)
        # Some links return 302/4xx but still verify — try once more with different approach
        try:
            import urllib.request
            proxy_port = detect_proxy()
            if proxy_port:
                handler = ProxyHandler({'https': f'http://127.0.0.1:{proxy_port}', 'http': f'http://127.0.0.1:{proxy_port}'})
                opener = build_opener(handler)
                opener.open(url, timeout=20)
            else:
                urlopen(url, timeout=20, context=_ssl_ctx)
            return True
        except:
            return False


# ═══════════════════════════════════════════════════════
# §8  号池注入
# ═══════════════════════════════════════════════════════

ACCT_PATHS = None

def _get_acct_paths():
    global ACCT_PATHS
    if ACCT_PATHS:
        return ACCT_PATHS
    appdata = os.environ.get('APPDATA', '')
    gs = Path(appdata) / 'Windsurf' / 'User' / 'globalStorage'
    paths = []
    if gs.exists():
        # Walk for all accounts JSON files
        for root, dirs, fnames in os.walk(str(gs)):
            for f in fnames:
                if 'accounts' in f.lower() and f.endswith('.json'):
                    paths.append(Path(root) / f)
    hb = Path(os.path.expanduser('~')) / '.wam' / 'accounts-backup.json'
    if hb.exists():
        paths.append(hb)
    ACCT_PATHS = paths
    return paths


def inject_to_pool(email, password, api_key=None, source="api_engine"):
    """Upsert account to all pool files: update existing or add new."""
    paths = _get_acct_paths()
    if not paths:
        log("No account files found!", False)
        return False

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
                if a.get('email') == email:
                    found = True
                    changed = False
                    if api_key and a.get('apiKey') != api_key:
                        a['apiKey'] = api_key
                        changed = True
                    if password and not a.get('password'):
                        a['password'] = password
                        changed = True
                    if source:
                        a['_activatedBy'] = source
                        a['_activatedAt'] = datetime.now(CST).isoformat()
                        changed = True
                    if changed:
                        updated += 1
                    break

            if not found:
                entry = {
                    "email": email,
                    "password": password,
                    "source": source,
                    "addedAt": datetime.now(CST).isoformat(),
                    "usage": {"plan": "pro_trial", "mode": "quota"},
                }
                if api_key:
                    entry["apiKey"] = api_key
                accts.append(entry)
                updated += 1

            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps(accts, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            log(f"Inject error {fp.name}: {e}", False)

    if updated:
        log(f"Updated {updated} entries across files", True)
    return updated > 0


# ═══════════════════════════════════════════════════════
# §9  核心管线: activate (后处理) + browser (注册委托)
# ═══════════════════════════════════════════════════════

def activate_account(email: str, password: str) -> dict:
    """Post-registration pipeline: signIn → RegisterUser → apiKey → verify → inject.
    对已通过浏览器注册的账号执行API激活。"""
    t0 = time.time()
    print(f"\n{'═' * 60}")
    print(f"  账号激活管线 — {email}")
    print(f"{'═' * 60}")

    # Step 1: Firebase signIn
    print(f"\n[Step 1] Firebase signIn...")
    login = firebase_signin(email, password)
    id_token = login.get('idToken')
    if not id_token:
        log(f"signIn failed: {login.get('error', 'unknown')}", False)
        return {'email': email, 'status': 'signin_failed', 'error': login.get('error')}
    log(f"idToken: {id_token[:30]}... ({len(id_token)} chars)", True)

    # Step 2: RegisterUser → apiKey
    print(f"\n[Step 2] RegisterUser (protobuf gRPC)...")
    reg = windsurf_register(id_token)
    api_key = reg.get('apiKey')
    if api_key:
        log(f"apiKey: {api_key[:25]}...", True)
    else:
        log(f"RegisterUser: {reg.get('error', 'no apiKey')}", False)

    # Step 3: Check plan status
    print(f"\n[Step 3] Plan status...")
    plan_info = windsurf_plan_status(id_token)
    raw = plan_info.get('raw_fields', {})
    log(f"Plan fields: {raw}")

    # Step 4: Check email verified status
    print(f"\n[Step 4] Firebase user info...")
    user_info = firebase_get_user(id_token)
    email_verified = user_info.get('emailVerified', False)
    log(f"emailVerified: {email_verified}")

    # Step 5: Inject to pool
    print(f"\n[Step 5] Inject to pool...")
    status = 'activated' if api_key else 'partial'
    inject_to_pool(email, password, api_key=api_key, source="activate_pipeline")

    elapsed = int((time.time() - t0) * 1000)
    result = {
        'email': email,
        'status': status,
        'apiKey': api_key[:20] + '...' if api_key else None,
        'emailVerified': email_verified,
        'plan_fields': raw,
        'timestamp': datetime.now(CST).isoformat(),
        'ms': elapsed,
    }

    print(f"\n{'═' * 60}")
    icon = '✅' if api_key else '⚠️'
    print(f"  {icon} {email}")
    print(f"     apiKey: {(api_key[:25] + '...') if api_key else 'NONE'}")
    print(f"     emailVerified: {email_verified} | {elapsed}ms")
    print(f"{'═' * 60}")

    _save_result(result)
    return result


def activate_all_unactivated():
    """Scan pool for accounts without apiKey and activate them."""
    paths = _get_acct_paths()
    # Load largest file as source of truth
    best = []
    for fp in paths:
        try:
            d = json.loads(fp.read_text(encoding='utf-8'))
            if isinstance(d, list) and len(d) > len(best):
                best = d
        except:
            pass

    need_activate = []
    for a in best:
        email = a.get('email', '')
        pw = a.get('password', '')
        if not email or not pw or '@' not in email:
            continue
        if a.get('apiKey'):
            continue  # Already has apiKey
        # Check if account might be activatable
        u = a.get('usage', {})
        plan = (u.get('plan') or '').lower()
        if plan == 'free':
            continue  # Skip known free
        need_activate.append((email, pw))

    if not need_activate:
        log("No accounts need activation", True)
        return

    print(f"\n{'═' * 60}")
    print(f"  批量激活 — {len(need_activate)} 个账号")
    print(f"{'═' * 60}")

    success = 0
    for i, (email, pw) in enumerate(need_activate):
        print(f"\n{'─' * 40} [{i+1}/{len(need_activate)}]")
        r = activate_account(email, pw)
        if r.get('apiKey'):
            success += 1
        if i < len(need_activate) - 1:
            time.sleep(2)  # Brief cooldown

    print(f"\n  激活完成: {success}/{len(need_activate)}")


def verify_account(email: str, password: str):
    """Verify account status: signIn → plan status → report."""
    print(f"\n  验证: {email}")
    login = firebase_signin(email, password)
    if not login.get('idToken'):
        print(f"  ❌ signIn failed: {login.get('error')}")
        return

    id_token = login['idToken']
    user = firebase_get_user(id_token)
    plan = windsurf_plan_status(id_token)
    reg = windsurf_register(id_token)

    print(f"  emailVerified: {user.get('emailVerified')}")
    print(f"  plan fields: {plan.get('raw_fields', {})}")
    print(f"  apiKey: {reg.get('apiKey', 'NONE')[:25]}..." if reg.get('apiKey') else "  apiKey: NONE")


def browser_register(n=1):
    """Delegate to _register_one.py for browser-based registration."""
    reg_script = SCRIPT_DIR / '_register_one.py'
    if not reg_script.exists():
        log(f"_register_one.py not found at {reg_script}", False)
        return

    for i in range(n):
        if n > 1:
            print(f"\n{'▓' * 15} [{i+1}/{n}] {'▓' * 15}")

        log(f"Launching browser registration...")
        import subprocess
        try:
            r = subprocess.run(
                [sys.executable, str(reg_script), '--no-wait'],
                cwd=str(SCRIPT_DIR),
                timeout=600,
            )
            if r.returncode == 0:
                log("Browser registration completed", True)
                # Try to activate the last registered account
                results_file = SCRIPT_DIR / '_register_results.json'
                if results_file.exists():
                    try:
                        results = json.loads(results_file.read_text(encoding='utf-8'))
                        if results:
                            last = results[-1]
                            email = last.get('email', '')
                            pw = last.get('password', '')
                            if email and pw and last.get('status') in ('verified', 'registered', 'verified_browser'):
                                log(f"Auto-activating: {email}")
                                activate_account(email, pw)
                    except:
                        pass
            else:
                log(f"Browser registration exit code: {r.returncode}", False)
        except subprocess.TimeoutExpired:
            log("Browser registration timeout (10min)", False)
        except Exception as e:
            log(f"Browser registration error: {e}", False)

        if i < n - 1:
            delay = random.uniform(10, 25)
            log(f"Cooldown {delay:.0f}s...")
            time.sleep(delay)


def pool_status():
    """Show current pool status."""
    paths = _get_acct_paths()
    best = []
    for fp in paths:
        try:
            d = json.loads(fp.read_text(encoding='utf-8'))
            if isinstance(d, list) and len(d) > len(best):
                best = d
        except:
            pass

    now_ms = time.time() * 1000
    good = exp_soon = expired = no_plan = has_key = 0
    for a in best:
        if a.get('apiKey'):
            has_key += 1
        u = a.get('usage', {})
        pe = u.get('planEnd', 0)
        if pe and pe > 1.5e12:
            days = (pe - now_ms) / 86400000
            if days > 3:
                good += 1
            elif days > 0:
                exp_soon += 1
            else:
                expired += 1
        else:
            no_plan += 1

    print(f"\n{'═' * 50}")
    print(f"  号池状态 — {len(best)} 个账号 ({len(paths)} 文件)")
    print(f"{'═' * 50}")
    print(f"  Good(>3d): {good}")
    print(f"  Expiring:  {exp_soon}")
    print(f"  Expired:   {expired}")
    print(f"  NoPlanEnd: {no_plan}")
    print(f"  HasApiKey: {has_key}")
    print(f"{'═' * 50}\n")


def _save_result(result):
    results_file = SCRIPT_DIR / "_api_register_results.json"
    results = []
    if results_file.exists():
        try:
            results = json.loads(results_file.read_text(encoding='utf-8'))
        except:
            pass
    results.append(result)
    results_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    log(f"Result saved", True)


# ═══════════════════════════════════════════════════════
# §10  探测 + 批量 + CLI
# ═══════════════════════════════════════════════════════

def probe_all():
    """Probe all API endpoints for reachability."""
    print(f"\n{'═' * 60}")
    print(f"  API可达性探测 — {datetime.now(CST).strftime('%H:%M:%S')}")
    print(f"{'═' * 60}")

    proxy_port = detect_proxy()
    log(f"Proxy: {'127.0.0.1:' + str(proxy_port) if proxy_port else 'NONE'}")

    # Firebase
    print("\n[Firebase Identity Toolkit]")
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={key}'
        for use_p in [True, False]:
            label = f"{'proxy' if use_p else 'direct'}-{key[-4:]}"
            try:
                # Send empty signUp to test connectivity (will get error but proves reachability)
                r = http_json(url, {'returnSecureToken': True}, use_proxy=use_p, timeout=8)
                msg = r.get('error', {}).get('message', 'OK')
                log(f"  {label}: ✅ reachable ({msg})", True)
            except Exception as e:
                log(f"  {label}: ❌ {str(e)[:60]}", False)

    # Relay
    print("\n[Relay Servers]")
    for relay in RELAYS:
        try:
            r = http_get(relay, timeout=10)
            log(f"  {relay}: ✅ ({len(r)} bytes)", True)
        except Exception as e:
            log(f"  {relay}: ❌ {str(e)[:60]}", False)

    # RegisterUser
    print("\n[RegisterUser gRPC]")
    dummy_token = encode_proto("test_probe_token")
    for url in REGISTER_URLS:
        host = url.split('/')[2]
        try:
            r = http_bin(url, dummy_token, timeout=10)
            log(f"  {host}: ✅ ({len(r)} bytes)", True)
        except Exception as e:
            log(f"  {host}: ❌ {str(e)[:60]}", False)

    # PlanStatus
    print("\n[GetPlanStatus gRPC]")
    for url in PLAN_STATUS_URLS:
        host = url.split('/')[2]
        try:
            r = http_bin(url, dummy_token, timeout=10)
            log(f"  {host}: ✅ ({len(r)} bytes)", True)
        except Exception as e:
            log(f"  {host}: ❌ {str(e)[:60]}", False)

    # Temp email
    print("\n[Temp Email Providers]")
    try:
        d = http_get_json("https://api.tempmail.lol/v2/inbox/create", timeout=15)
        log(f"  tempmail.lol: ✅ {d.get('address', '?')}", True)
    except Exception as e:
        log(f"  tempmail.lol: ❌ {str(e)[:60]}", False)
    try:
        d = http_get_json("https://api.mail.tm/domains", timeout=15)
        doms = [x.get('domain') for x in d.get('hydra:member', []) if x.get('isActive')]
        log(f"  Mail.tm: ✅ domains={doms[:3]}", True)
    except Exception as e:
        log(f"  Mail.tm: ❌ {str(e)[:60]}", False)

    print(f"\n{'═' * 60}\n")


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'help'

    if cmd == 'activate' and len(sys.argv) >= 4:
        activate_account(sys.argv[2], sys.argv[3])

    elif cmd == 'activate-all':
        activate_all_unactivated()

    elif cmd == 'verify' and len(sys.argv) >= 4:
        verify_account(sys.argv[2], sys.argv[3])

    elif cmd == 'probe':
        probe_all()

    elif cmd == 'browser':
        n = 1
        for a in sys.argv[2:]:
            if a.startswith('--n='):
                n = int(a.split('=')[1])
        browser_register(n)

    elif cmd == 'pool-status':
        pool_status()

    else:
        print(__doc__)
