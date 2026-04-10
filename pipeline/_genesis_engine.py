#!/usr/bin/env python3
"""
创世引擎 · Genesis Engine — 万物之源 · 一推到底
================================================
天下万物生于有，有生于无。反者道之动。

从无到有，一条命令彻底解决账号来源问题:
  邮箱之源 → 浏览器注册 → 邮件验证 → API激活 → 号池注入 → 生命守护

五源并行(优先级递降，自动降级):
  S0: 已购Yahoo账号 (账号.txt) → 直接API激活 (10s/个，100%成功)
  S1: tempmail.lol API → 浏览器注册 → API轮询验证 → 激活
  S2: Mail.tm API → 浏览器注册 → API轮询验证 → 激活
  S3: Gmail+alias → 浏览器注册 → IMAP/Spam验证码 → 激活
  S4: 手动邮箱 → 浏览器注册 → 手动验证 → 激活

用法:
  python _genesis_engine.py status                # 全景: 号池+管线+资源
  python _genesis_engine.py probe                 # API/邮箱/浏览器可达性
  python _genesis_engine.py forge                 # 铸造1个账号 (自动选源)
  python _genesis_engine.py forge 5               # 批量铸造5个
  python _genesis_engine.py forge --source=yahoo  # 指定源: yahoo/tempmail/mailtm/gmail
  python _genesis_engine.py activate EMAIL PW     # 激活已注册账号
  python _genesis_engine.py activate-all          # 批量激活所有未激活
  python _genesis_engine.py harvest               # 收割: 激活账号.txt中所有Yahoo
  python _genesis_engine.py revive                # 复活: 重试所有pending_verify
  python _genesis_engine.py guard                 # 守护进程: 自动监控+补充
  python _genesis_engine.py dashboard             # Web仪表盘 :19930

secrets.env:
  GMAIL_BASE=xxx@gmail.com          (Gmail+alias源)
  GMAIL_APP_PASSWORD=xxxx xxxx      (Gmail应用专用密码)
"""

import json, os, sys, time, random, string, re, ssl, socket, struct
import subprocess, threading, traceback
import html as html_mod

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
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import Counter

VERSION = '1.0.0'
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT.parent / 'data'  # Windsurf万法归宗/data/
CST = timezone(timedelta(hours=8))
DASHBOARD_PORT = 19930

SECRETS_ENV = Path(r'e:\道\道生一\一生二\secrets.env')
GENESIS_RESULTS = SCRIPT_DIR / '_genesis_results.json'
GENESIS_LOG = SCRIPT_DIR / '_genesis.log'

# ═══════════════════════════════════════════════════════
# §1  常量 — 从逆向知识库提取
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

# Account file paths
WS_APPDATA = Path(os.environ.get('APPDATA', '')) / 'Windsurf'
WS_GLOBALSTORE = WS_APPDATA / 'User' / 'globalStorage'
ACCT_FILE_PATHS = [
    WS_GLOBALSTORE / 'zhouyoukang.windsurf-assistant' / 'windsurf-assistant-accounts.json',
    WS_GLOBALSTORE / 'windsurf-login-accounts.json',
    WS_GLOBALSTORE / 'undefined_publisher.windsurf-login-helper' / 'windsurf-login-accounts.json',
]
YAHOO_FILE = DATA_DIR / '账号.txt'


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
            with open(GENESIS_LOG, 'a', encoding='utf-8') as f:
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


def http_json(url, data=None, method='POST', use_proxy=True, timeout=15, headers=None):
    body = json.dumps(data).encode('utf-8') if data else None
    req = Request(url, data=body, method=method)
    req.add_header('Content-Type', 'application/json')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    proxy_port = detect_proxy() if use_proxy else 0
    try:
        if proxy_port > 0:
            handler = ProxyHandler({'https': f'http://127.0.0.1:{proxy_port}', 'http': f'http://127.0.0.1:{proxy_port}'})
            opener = build_opener(handler)
            resp = opener.open(req, timeout=timeout)
        else:
            resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read()
        try:
            return json.loads(body)
        except Exception:
            raise RuntimeError(f"HTTP {e.code}: {body[:300]}")


def http_bin(url, bin_data, use_proxy=True, timeout=15):
    req = Request(url, data=bin_data, method='POST')
    req.add_header('Content-Type', 'application/proto')
    req.add_header('Accept', 'application/proto')
    proxy_port = detect_proxy() if use_proxy else 0
    if proxy_port > 0:
        handler = ProxyHandler({'https': f'http://127.0.0.1:{proxy_port}', 'http': f'http://127.0.0.1:{proxy_port}'})
        opener = build_opener(handler)
        resp = opener.open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    return resp.read()


def http_get(url, use_proxy=True, timeout=15, headers=None):
    req = Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    proxy_port = detect_proxy() if use_proxy else 0
    if proxy_port > 0:
        handler = ProxyHandler({'https': f'http://127.0.0.1:{proxy_port}', 'http': f'http://127.0.0.1:{proxy_port}'})
        opener = build_opener(handler)
        resp = opener.open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    return resp.read()


def http_get_json(url, use_proxy=True, timeout=15, headers=None):
    raw = http_get(url, use_proxy=use_proxy, timeout=timeout, headers=headers)
    return json.loads(raw)


def gen_password():
    chars = string.ascii_letters + string.digits + "!@#$"
    pw = (random.choice(string.ascii_uppercase) + random.choice(string.ascii_lowercase) +
          random.choice(string.digits) + random.choice("!@#$") +
          ''.join(random.choices(chars, k=12)))
    return ''.join(random.sample(pw, len(pw)))


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
    return d


def load_genesis_results():
    if GENESIS_RESULTS.exists():
        try:
            return json.loads(GENESIS_RESULTS.read_text(encoding='utf-8'))
        except Exception:
            pass
    return []


def save_genesis_result(result):
    results = load_genesis_results()
    results.append(result)
    GENESIS_RESULTS.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')


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


def parse_proto_str(buf: bytes) -> str:
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
        fn = tag >> 3; wt = tag & 0x07
        if fn == 0 or fn > 1000 or pos >= len(buf):
            break
        if wt == 0:
            val, pos = read_varint(buf, pos)
            fields.setdefault(fn, []).append({'varint': val})
        elif wt == 2:
            length, pos = read_varint(buf, pos)
            if length < 0 or length > 65536 or pos + length > len(buf):
                break
            data = buf[pos:pos + length]
            s = None
            try:
                s = data.decode('utf-8')
                if not all(0x20 <= ord(c) <= 0x7e or c in '\n\r\t' for c in s):
                    s = None
            except Exception:
                pass
            fields.setdefault(fn, []).append({'bytes': data, 'string': s, 'length': length})
            pos += length
        elif wt == 1:
            pos += 8
        elif wt == 5:
            pos += 4
        else:
            break
    return fields


def classify_plan_fields(fields: dict) -> str:
    """从protobuf fields推断真实plan类型. 返回 pro_trial/free/unknown."""
    strings = []
    for fn, vals in fields.items():
        for v in vals:
            if v.get('string'):
                strings.append((fn, v['string'].lower()))
    for fn, s in strings:
        if 'pro_trial' in s or ('pro' in s and 'trial' in s):
            return 'pro_trial'
    for fn, s in strings:
        if 'trial' in s:
            return 'pro_trial'
    for fn, s in strings:
        if s.strip() == 'free':
            return 'free'
    if strings:
        return 'unknown'
    return 'no_response'


# ═══════════════════════════════════════════════════════
# §4  Firebase Auth
# ═══════════════════════════════════════════════════════

def firebase_signin(email: str, password: str) -> dict:
    payload = {'email': email, 'password': password, 'returnSecureToken': True}
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}'
        for use_p in [False, True]:
            try:
                r = http_json(url, payload, use_proxy=use_p, timeout=12)
                if r.get('idToken'):
                    return r
            except Exception:
                continue
    return {'error': 'signin_failed'}


def firebase_get_user(id_token: str) -> dict:
    payload = {'idToken': id_token}
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={key}'
        for use_p in [False, True]:
            try:
                r = http_json(url, payload, use_proxy=use_p, timeout=12)
                users = r.get('users', [])
                if users:
                    return users[0]
            except Exception:
                continue
    return {}


# ═══════════════════════════════════════════════════════
# §5  Windsurf gRPC — RegisterUser + GetPlanStatus
# ═══════════════════════════════════════════════════════

def windsurf_register_user(id_token: str) -> dict:
    buf = encode_proto(id_token)
    for url in REGISTER_URLS:
        try:
            resp = http_bin(url, buf, timeout=15)
            api_key = parse_proto_str(resp)
            if api_key:
                return {'ok': True, 'apiKey': api_key}
        except Exception:
            continue
    return {'ok': False, 'error': 'all_register_channels_failed'}


def extract_proto_strings(buf: bytes) -> list:
    """Extract ALL readable strings from protobuf bytes (recursive, handles nested)."""
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


def windsurf_plan_status(id_token: str) -> dict:
    """GetPlanStatus → parse plan info. Returns {plan, raw_strings}."""
    buf = encode_proto(id_token)
    for url in PLAN_STATUS_URLS:
        try:
            resp = http_bin(url, buf, timeout=15)
            if resp and len(resp) > 5:
                strings = extract_proto_strings(resp)
                raw_strings = [f"f{fn}={s}" for fn, s in strings]
                # Classify plan from extracted strings
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
# §6  号池操作
# ═══════════════════════════════════════════════════════

def load_pool_accounts():
    """Load accounts from all known pool files, return (best_list, all_paths)."""
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


def inject_to_pool(email, password, api_key=None, source="genesis", verified_plan=None):
    """Upsert account to all pool files.
    道法自然: verified_plan携带API验证的真实plan, 不再盲目假设pro_trial."""
    _, paths = load_pool_accounts()
    if not paths:
        # Create default path
        default = ACCT_FILE_PATHS[0]
        default.parent.mkdir(parents=True, exist_ok=True)
        default.write_text('[]', encoding='utf-8')
        paths = [default]

    # Claude可用性门控: free plan一律拒绝入池
    if verified_plan and verified_plan.lower() == 'free':
        log(f"inject_to_pool: 拒绝 {email} — plan=free, Claude不可用", False)
        return False

    actual_plan = verified_plan if verified_plan else "pro_trial"

    updated = 0
    now_iso = datetime.now(CST).isoformat()
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
                    if api_key and a.get('apiKey') != api_key:
                        a['apiKey'] = api_key
                    if password and not a.get('password'):
                        a['password'] = password
                    a['_activatedBy'] = source
                    a['_activatedAt'] = now_iso
                    if verified_plan:
                        a.setdefault('usage', {})['plan'] = verified_plan
                        a['_verifiedPlan'] = verified_plan
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
                if verified_plan:
                    entry["_verifiedPlan"] = verified_plan
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
# §7  激活管线 — 核心 (signIn → apiKey → inject)
# ═══════════════════════════════════════════════════════

def activate_account(email: str, password: str, quiet=False) -> dict:
    """完整激活管线: Firebase signIn → Plan验证 → RegisterUser → apiKey → inject.
    道法自然: 以Claude可用性为锚点, free/试用过期一律不入池."""
    t0 = time.time()
    if not quiet:
        log(f"激活: {email}")

    # Step 1: Firebase signIn
    login = firebase_signin(email, password)
    id_token = login.get('idToken')
    if not id_token:
        err = login.get('error', {})
        if isinstance(err, dict):
            err = err.get('message', str(err))
        log(f"signIn失败: {err}", False)
        return {'email': email, 'status': 'signin_failed', 'error': str(err)}

    # Step 1.5: Plan验证 — Claude可用性锚点 (反者道之动: 不信表象, 只信plan)
    plan_info = windsurf_plan_status(id_token)
    verified_plan = plan_info.get('plan', 'unknown') if plan_info.get('ok') else 'probe_failed'
    if verified_plan == 'free':
        log(f"拒绝入池: {email} plan=free, Claude不可用", False)
        return {'email': email, 'status': 'rejected_free', 'plan': 'free',
                'error': 'plan=free, 仅限免费模型, Claude Opus不可用'}
    if not quiet:
        log(f"Plan验证: {verified_plan} ({', '.join(plan_info.get('raw_strings', [])[:3])})")

    # Step 2: RegisterUser → apiKey
    reg = windsurf_register_user(id_token)
    api_key = reg.get('apiKey')

    # Step 3: Inject to pool (携带真实plan, 不再硬编码pro_trial)
    status = 'activated' if api_key else 'partial'
    inject_to_pool(email, password, api_key=api_key, source="genesis_activate",
                   verified_plan=verified_plan)

    elapsed = int((time.time() - t0) * 1000)
    result = {
        'email': email,
        'status': status,
        'apiKey': api_key,
        'timestamp': datetime.now(CST).isoformat(),
        'ms': elapsed,
    }

    if api_key:
        log(f"apiKey: {api_key[:25]}... ({elapsed}ms)", True)
    else:
        log(f"激活不完整: 无apiKey ({elapsed}ms)", False)

    save_genesis_result(result)
    return result


# ═══════════════════════════════════════════════════════
# §8  邮箱之源 — 五源降级
# ═══════════════════════════════════════════════════════

def parse_yahoo_accounts():
    """从账号.txt解析Yahoo/Gmail账号 (email+password对). 跳过卡号等非邮箱行."""
    if not YAHOO_FILE.exists():
        return []
    text = YAHOO_FILE.read_text(encoding='utf-8')
    accounts = []
    lines = text.strip().split('\n')
    # 排除的域名 (卡号/卡密等)
    SKIP_DOMAINS = ['3lux.shop', 'example.com', 'ex.com']
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Format 1: 邮箱：xxx / 密码：xxx (full-width or half-width colon)
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
        # Format 2: email----password
        if '----' in line:
            parts = line.split('----')
            if len(parts) == 2 and '@' in parts[0]:
                e, p = parts[0].strip(), parts[1].strip()
                if not any(d in e for d in SKIP_DOMAINS):
                    accounts.append((e, p))
            i += 1
            continue
        # Format 3: email\tpassword (tab-separated)
        if '\t' in line:
            parts = line.split('\t')
            if len(parts) >= 2 and '@' in parts[0]:
                e, p = parts[0].strip(), parts[1].strip()
                if not any(d in e for d in SKIP_DOMAINS):
                    accounts.append((e, p))
            i += 1
            continue
        # Format 4: bare email on one line, password on next (no prefix)
        if '@' in line and not line.startswith(('卡', 'Hi', '您', '#')) and not any(d in line for d in SKIP_DOMAINS):
            bare_email = line.strip()
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                # Next line is password if it doesn't look like another email or header
                if next_line and '@' not in next_line and not next_line.startswith(('邮箱', '密码', '卡', 'Hi', '您')):
                    accounts.append((bare_email, next_line))
                    i += 2
                    continue
        i += 1
    return accounts


class TempMailSource:
    """tempmail.lol — 冷域名临时邮箱"""
    name = "tempmail.lol"

    def __init__(self):
        self.token = None
        self.address = None

    def create_inbox(self):
        d = http_get_json("https://api.tempmail.lol/v2/inbox/create", timeout=20)
        if not isinstance(d, dict) or not d.get("address"):
            raise RuntimeError(f"create failed: {d}")
        self.address = d["address"]
        self.token = d.get("token", "")
        return self.address

    def wait_for_email(self, timeout=180, poll=5, subject_filter=None):
        if not self.token:
            return None
        start = time.time()
        while time.time() - start < timeout:
            try:
                d = http_get_json(f"https://api.tempmail.lol/v2/inbox?token={self.token}", timeout=15)
                emails = d.get("emails", []) if isinstance(d, dict) else []
                for m in emails:
                    subj = m.get("subject", "")
                    if subject_filter and subject_filter.lower() not in subj.lower():
                        continue
                    return {"subject": subj, "from": m.get("from", ""), "body": m.get("body", "")}
            except Exception:
                pass
            elapsed = int(time.time() - start)
            if elapsed > 0 and elapsed % 20 == 0:
                log(f"等待邮件... ({elapsed}s/{timeout}s)")
            time.sleep(poll)
        return None


class MailTmSource:
    """Mail.tm — 备用临时邮箱"""
    name = "Mail.tm"
    API = "https://api.mail.tm"

    def __init__(self):
        self.token = None
        self.address = None
        self._pw = None

    def create_inbox(self):
        d = http_get_json(f"{self.API}/domains", timeout=30)
        members = d.get("hydra:member", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
        active = [x["domain"] for x in members if isinstance(x, dict) and x.get("isActive")]
        if not active:
            raise RuntimeError("No active domains")
        dom = active[0]
        pfx = "ws" + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        self.address = f"{pfx}@{dom}"
        self._pw = ''.join(random.choices(string.ascii_letters + string.digits, k=14))
        http_json(f"{self.API}/accounts", {"address": self.address, "password": self._pw}, timeout=30)
        tok = http_json(f"{self.API}/token", {"address": self.address, "password": self._pw}, timeout=30)
        self.token = tok.get("token", "") if isinstance(tok, dict) else ""
        if not self.token:
            raise RuntimeError(f"token failed")
        return self.address

    def wait_for_email(self, timeout=180, poll=5, subject_filter=None):
        if not self.token:
            return None
        start = time.time()
        seen = set()
        while time.time() - start < timeout:
            try:
                d = http_get_json(f"{self.API}/messages?page=1",
                                  headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
                msgs = d.get("hydra:member", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
                for m in msgs:
                    mid = m.get("id", "")
                    if not mid or mid in seen:
                        continue
                    seen.add(mid)
                    subj = m.get("subject", "")
                    if subject_filter and subject_filter.lower() not in subj.lower():
                        continue
                    raw = http_get(f"{self.API}/messages/{mid}",
                                   headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
                    full = json.loads(raw)
                    body_parts = full.get("html", [full.get("text", "")])
                    body = body_parts if isinstance(body_parts, str) else " ".join(str(x) for x in body_parts)
                    return {"subject": full.get("subject", subj), "from": full.get("from", {}).get("address", "?"), "body": body}
            except Exception:
                pass
            elapsed = int(time.time() - start)
            if elapsed > 0 and elapsed % 20 == 0:
                log(f"等待邮件... ({elapsed}s/{timeout}s)")
            time.sleep(poll)
        return None


def extract_verify_link(body: str) -> str:
    if not body:
        return None
    content = html_mod.unescape(str(body))
    all_urls = re.findall(r'https?://[^\s<>"\']+', content)
    all_urls = [re.sub(r'["\'>;\s]+$', '', u.rstrip('.')) for u in all_urls]
    verify = [u for u in all_urls if any(k in u.lower() for k in ['verify', 'confirm', 'oobcode', 'continueurl', 'apikey'])]
    if verify:
        return verify[0]
    ws = [u for u in all_urls if 'windsurf' in u.lower() or 'codeium' in u.lower() or 'firebaseapp' in u.lower()]
    if ws:
        return ws[0]
    ext = [u for u in all_urls if not any(d in u.lower() for d in ['tempmail', 'mail.tm', 'guerrillamail'])]
    return ext[0] if ext else None


def extract_verify_code(body: str) -> str:
    if not body:
        return None
    content = html_mod.unescape(str(body))
    codes = re.findall(r'\b(\d{6})\b', content)
    return codes[0] if codes else None


# ═══════════════════════════════════════════════════════
# §9  浏览器注册引擎
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
    for tp in [
        SCRIPT_DIR / "turnstilePatch",
        SCRIPT_DIR / "_archive" / "turnstilePatch",
        PROJECT_ROOT / "turnstilePatch",
    ]:
        if tp.exists():
            return str(tp)
    return None


def browser_register_with_email(email_addr, ws_password, proxy_str=None):
    """
    浏览器注册: DrissionPage + turnstilePatch
    返回: (status, verify_data)
      status: "verify_page"|"done"|"exists"|"error"
      verify_data: 当status=verify_page时包含page引用(调用方负责quit)
    """
    from DrissionPage import ChromiumOptions, ChromiumPage

    # Kill stale chrome
    try:
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, timeout=8)
        time.sleep(1.5)
    except Exception:
        pass

    chrome = find_chrome()
    if not chrome:
        return ("error", {"reason": "Chrome not found"})

    import tempfile
    tmp_user_dir = tempfile.mkdtemp(prefix="ws_forge_")
    co = ChromiumOptions()
    co.set_browser_path(chrome)
    co.set_argument("--incognito")
    co.set_user_data_path(tmp_user_dir)
    co.auto_port()
    co.headless(False)
    if proxy_str:
        co.set_argument(f"--proxy-server={proxy_str.replace('http://', '')}")

    tp = find_turnstile_patch()
    if tp:
        co.set_argument("--allow-extensions-in-incognito")
        co.add_extension(tp)
        log(f"turnstilePatch: OK", True)

    fn = random.choice(FIRST_NAMES)
    ln = random.choice(LAST_NAMES)
    page = ChromiumPage(co)
    if tp:
        time.sleep(3)

    try:
        log(f"导航: {WINDSURF_REGISTER_URL}")
        page.get(WINDSURF_REGISTER_URL)
        time.sleep(random.uniform(2.5, 4))

        # 填写表单
        for sel, val in [('@name=firstName', fn), ('@name=lastName', ln), ('@name=email', email_addr)]:
            try:
                el = page.ele(sel, timeout=5)
                if el:
                    el.input(val)
                    time.sleep(random.uniform(0.3, 0.7))
            except Exception:
                pass

        # 勾选Terms
        try:
            cb = page.ele('tag:input@type=checkbox', timeout=2)
            if cb and not cb.attr('checked'):
                cb.click()
                time.sleep(0.3)
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

        log("等待Turnstile...")

        # 等待页面进展
        for _ in range(40):
            try:
                body = (page.html or "").lower()
                if any(k in body for k in ["verify your email", "check your email"]):
                    log("无密码直接跳到验证 — 可能被拒绝", False)
                    page.quit()
                    return ("blocked", {"reason": "password step skipped"})
                if "password" in body and ("confirm" in body or "set your" in body):
                    break
                if any(k in body for k in ["dashboard", "welcome to windsurf"]):
                    page.quit()
                    return ("done", {"first_name": fn, "last_name": ln})
                # 点击可用按钮
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

        # 密码步骤
        pw_input = page.ele('@type=password', timeout=8)
        if pw_input:
            log("密码步骤到达", True)
            pw_input.input(ws_password)
            time.sleep(0.5)
            try:
                pw_confirm = page.ele('@placeholder:Confirm', timeout=3)
                if not pw_confirm:
                    pw_confirm = page.ele('css:input[type=password]:nth-child(2)', timeout=2)
                if pw_confirm:
                    pw_confirm.input(ws_password)
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

            # 被动等待验证码页面 (最多45s)
            VERIFY_KW = ["verify your email", "check your email", "we've sent", "we sent",
                         "check your inbox", "please verify", "almost done", "one more step"]
            DONE_KW = ["dashboard", "welcome to windsurf", "get started", "you're all set"]

            for attempt in range(45):
                time.sleep(1)
                try:
                    body2 = (page.html or "").lower()
                    if any(k in body2 for k in VERIFY_KW):
                        log("验证码页面到达!", True)
                        return ("verify_page", {"page": page, "first_name": fn, "last_name": ln})
                    if any(k in body2 for k in DONE_KW):
                        log("注册直接完成!", True)
                        page.quit()
                        return ("done", {"first_name": fn, "last_name": ln})
                    url2 = page.url or ""
                    if "register" not in url2 and "windsurf.com" in url2 and attempt > 5:
                        page.quit()
                        return ("done", {"first_name": fn, "last_name": ln})
                except Exception:
                    pass

            page.quit()
            return ("error", {"reason": "verify_page_timeout"})
        else:
            body = (page.html or "").lower()
            if any(k in body for k in ["verify your email", "check your email"]):
                page.quit()
                return ("blocked", {"reason": "no_password_step"})
            page.quit()
            return ("error", {"reason": "password_field_not_found"})

    except Exception as e:
        log(f"浏览器异常: {e}", False)
        try:
            page.quit()
        except Exception:
            pass
        return ("error", {"reason": str(e)})


def enter_verify_code(page, code):
    """在验证码页面输入6位码 — 纯JS方案"""
    log(f"输入验证码: {code}")
    try:
        time.sleep(2)
        result = page.run_js(f"""
            var code = '{code}';
            var filled = false;
            var inputs = Array.from(document.querySelectorAll('input'));
            var visible = inputs.filter(function(inp) {{
                return inp.type !== 'hidden' && inp.type !== 'checkbox' &&
                       inp.type !== 'radio' && inp.type !== 'submit' &&
                       inp.type !== 'password' && inp.offsetParent !== null;
            }});
            if (visible.length >= 6) {{
                for (var i = 0; i < Math.min(6, visible.length); i++) {{
                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(visible[i], code[i]);
                    visible[i].dispatchEvent(new Event('input', {{bubbles: true}}));
                    visible[i].dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
                filled = true;
            }} else if (visible.length >= 1) {{
                var inp = visible[0];
                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(inp, code);
                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                filled = true;
            }}
            return {{filled: filled, visibleCount: visible.length}};
        """)
        if result and result.get('filled'):
            log("验证码已填入", True)
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
            time.sleep(6)
            check = page.run_js("return {url: location.href, text: (document.body?.innerText||'').substring(0,300)};")
            check_text = (check.get('text', '') if check else '').lower()
            if any(k in check_text for k in ['dashboard', 'welcome', 'success', 'verified', 'you can now']):
                log("验证成功!", True)
                return True
            return True  # optimistic
        return False
    except Exception as e:
        log(f"验证码输入异常: {e}", False)
        return False


# ═══════════════════════════════════════════════════════
# §10  IMAP 验证码获取 (Gmail Spam)
# ═══════════════════════════════════════════════════════

def imap_get_verify_code(base_email, app_password, alias_email=None, max_wait=150, known_codes=None):
    """从Gmail IMAP获取Windsurf 6位验证码 (含垃圾箱)"""
    import imaplib, email as email_lib
    if known_codes is None:
        known_codes = set()

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(base_email, app_password)
    except Exception as e:
        log(f"IMAP登录失败: {e}", False)
        return None

    start = time.time()
    try:
        # 搜索收件箱和垃圾箱
        for folder in ["INBOX", "[Gmail]/Spam", "[Gmail]/All Mail"]:
            try:
                mail.select(folder)
            except Exception:
                continue

            while time.time() - start < max_wait:
                queries = ['(FROM "noreply@codeium.com" UNSEEN)', '(FROM "noreply@windsurf.com" UNSEEN)', '(SUBJECT "verify" UNSEEN)']
                if alias_email:
                    queries.insert(0, f'(TO "{alias_email}" UNSEEN)')

                for query in queries:
                    try:
                        _, data = mail.search(None, query)
                        ids = data[0].split() if data[0] else []
                        for eid in reversed(ids[-5:]):
                            try:
                                _, msg_data = mail.fetch(eid, "(RFC822)")
                                raw = msg_data[0][1]
                                msg = email_lib.message_from_bytes(raw)
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
                                        body_text = str(msg.get_payload())

                                codes = re.findall(r'\b(\d{6})\b', body_text)
                                for code in codes:
                                    if code not in known_codes:
                                        mail.logout()
                                        return code
                            except Exception:
                                continue
                    except Exception:
                        continue

                elapsed = int(time.time() - start)
                if elapsed > 0 and elapsed % 15 == 0:
                    log(f"IMAP轮询中... {elapsed}s/{max_wait}s")
                time.sleep(5)

    except Exception as e:
        log(f"IMAP异常: {e}", False)
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return None


# ═══════════════════════════════════════════════════════
# §11  铸造 — 核心E2E流程
# ═══════════════════════════════════════════════════════

def forge_one(source="auto") -> dict:
    """
    铸造一个账号: 邮箱→注册→验证→激活→注入
    source: "auto"|"yahoo"|"tempmail"|"mailtm"|"gmail"
    """
    t0 = time.time()
    print(f"\n{'═' * 70}")
    print(f"  创世引擎 · 铸造账号 · source={source}")
    print(f"  {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S CST')}")
    print(f"{'═' * 70}")

    # S0: Yahoo — 已购账号直接激活 (最快最稳)
    if source in ("auto", "yahoo"):
        yahoo_accounts = parse_yahoo_accounts()
        pool_accts, _ = load_pool_accounts()
        pool_emails = {a.get('email', '').lower() for a in pool_accts}

        # 找未入池的Yahoo账号
        fresh_yahoo = [(e, p) for e, p in yahoo_accounts if e.lower() not in pool_emails]
        if fresh_yahoo:
            email, pw = fresh_yahoo[0]
            log(f"S0-Yahoo: {email} (剩余{len(fresh_yahoo)-1}个)", True)
            result = activate_account(email, pw)
            result['source'] = 'yahoo'
            result['total_ms'] = int((time.time() - t0) * 1000)
            return result
        elif source == "yahoo":
            log("Yahoo账号已全部入池", False)
            return {'status': 'exhausted', 'source': 'yahoo'}

    # 浏览器注册流程 (S1-S4)
    ws_password = gen_password()
    proxy_port = detect_proxy()
    proxy_str = f"http://127.0.0.1:{proxy_port}" if proxy_port else None

    # S1: tempmail.lol
    if source in ("auto", "tempmail"):
        log("S1-tempmail.lol: 创建邮箱...")
        try:
            mail_src = TempMailSource()
            email = mail_src.create_inbox()
            log(f"邮箱: {email}", True)

            status, data = browser_register_with_email(email, ws_password, proxy_str)
            if status == "done":
                log("注册完成(无需验证)!", True)
                result = activate_account(email, ws_password)
                result['source'] = 'tempmail'
                result['total_ms'] = int((time.time() - t0) * 1000)
                return result

            if status == "verify_page":
                page = data.get('page')
                log("等待验证邮件 (180s)...")
                msg = mail_src.wait_for_email(timeout=180, poll=5)
                if msg:
                    log(f"邮件到达! Subject: {msg.get('subject', '?')}", True)
                    link = extract_verify_link(msg.get('body', ''))
                    code = extract_verify_code(msg.get('body', ''))
                    if code and page:
                        enter_verify_code(page, code)
                    elif link:
                        try:
                            http_get(link, timeout=20)
                            log("验证链接已点击", True)
                        except Exception:
                            pass
                    try:
                        page.quit()
                    except Exception:
                        pass
                    result = activate_account(email, ws_password)
                    result['source'] = 'tempmail'
                    result['total_ms'] = int((time.time() - t0) * 1000)
                    return result
                else:
                    log("未收到验证邮件", False)
                    try:
                        page.quit()
                    except Exception:
                        pass
            elif status not in ("blocked",):
                log(f"浏览器注册状态: {status}", False)

            if source == "tempmail":
                return {'email': email, 'status': f'register_{status}', 'source': 'tempmail'}
        except Exception as e:
            log(f"S1失败: {e}", False)
            if source == "tempmail":
                return {'status': 'error', 'source': 'tempmail', 'error': str(e)}

    # S2: Mail.tm
    if source in ("auto", "mailtm"):
        log("S2-Mail.tm: 创建邮箱...")
        try:
            mail_src = MailTmSource()
            email = mail_src.create_inbox()
            log(f"邮箱: {email}", True)

            status, data = browser_register_with_email(email, ws_password, proxy_str)
            if status == "done":
                result = activate_account(email, ws_password)
                result['source'] = 'mailtm'
                result['total_ms'] = int((time.time() - t0) * 1000)
                return result

            if status == "verify_page":
                page = data.get('page')
                log("等待验证邮件 (180s)...")
                msg = mail_src.wait_for_email(timeout=180, poll=5)
                if msg:
                    log(f"邮件到达!", True)
                    link = extract_verify_link(msg.get('body', ''))
                    code = extract_verify_code(msg.get('body', ''))
                    if code and page:
                        enter_verify_code(page, code)
                    elif link:
                        try:
                            http_get(link, timeout=20)
                        except Exception:
                            pass
                    try:
                        page.quit()
                    except Exception:
                        pass
                    result = activate_account(email, ws_password)
                    result['source'] = 'mailtm'
                    result['total_ms'] = int((time.time() - t0) * 1000)
                    return result
                else:
                    log("未收到验证邮件", False)
                    try:
                        page.quit()
                    except Exception:
                        pass

            if source == "mailtm":
                return {'email': email, 'status': f'register_{status}', 'source': 'mailtm'}
        except Exception as e:
            log(f"S2失败: {e}", False)
            if source == "mailtm":
                return {'status': 'error', 'source': 'mailtm', 'error': str(e)}

    # S3: Gmail+alias
    if source in ("auto", "gmail"):
        secrets = load_secrets()
        gmail_base = secrets.get('GMAIL_BASE', '')
        gmail_pw = secrets.get('GMAIL_APP_PASSWORD', '')
        if gmail_base and gmail_pw:
            # 读取索引
            state_file = SCRIPT_DIR / "_genesis_gmail_state.json"
            state = {"next_index": 20}
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text(encoding='utf-8'))
                except Exception:
                    pass
            idx = state.get("next_index", 20)
            prefix = secrets.get('GMAIL_ALIAS_PREFIX', 'ws')
            alias_part = f"{prefix}{idx:03d}"
            user_part = gmail_base.split('@')[0]
            email = f"{user_part}+{alias_part}@gmail.com"

            log(f"S3-Gmail: {email} (index={idx})")

            status, data = browser_register_with_email(email, ws_password, proxy_str)
            if status == "done":
                state["next_index"] = idx + 1
                state_file.write_text(json.dumps(state, indent=2), encoding='utf-8')
                result = activate_account(email, ws_password)
                result['source'] = 'gmail'
                result['total_ms'] = int((time.time() - t0) * 1000)
                return result

            if status == "verify_page":
                page = data.get('page')
                log("IMAP取验证码...")
                # 收集已知验证码
                known = set()
                for r in load_genesis_results():
                    vc = r.get('verify_code')
                    if vc:
                        known.add(vc)
                code = imap_get_verify_code(gmail_base, gmail_pw, alias_email=email, max_wait=150, known_codes=known)
                if code and page:
                    log(f"验证码: {code}", True)
                    enter_verify_code(page, code)
                    try:
                        page.quit()
                    except Exception:
                        pass
                    state["next_index"] = idx + 1
                    state_file.write_text(json.dumps(state, indent=2), encoding='utf-8')
                    result = activate_account(email, ws_password)
                    result['source'] = 'gmail'
                    result['verify_code'] = code
                    result['total_ms'] = int((time.time() - t0) * 1000)
                    return result
                else:
                    log("IMAP未获取到验证码", False)
                    try:
                        page.quit()
                    except Exception:
                        pass
                    # 仍然递增索引
                    state["next_index"] = idx + 1
                    state_file.write_text(json.dumps(state, indent=2), encoding='utf-8')

            if source == "gmail":
                return {'email': email, 'status': f'register_{status}', 'source': 'gmail'}
        else:
            log("S3-Gmail: 未配置GMAIL_BASE/GMAIL_APP_PASSWORD", False)
            if source == "gmail":
                return {'status': 'no_config', 'source': 'gmail'}

    elapsed = int((time.time() - t0) * 1000)
    log(f"所有源均失败 ({elapsed}ms)", False)
    return {'status': 'all_sources_failed', 'total_ms': elapsed}


def forge_batch(n, source="auto"):
    """批量铸造N个账号"""
    print(f"\n{'▓' * 70}")
    print(f"  创世引擎 · 批量铸造 · N={n} · source={source}")
    print(f"{'▓' * 70}")

    success = 0
    results = []
    for i in range(n):
        print(f"\n{'━' * 50} [{i+1}/{n}]")
        r = forge_one(source)
        results.append(r)
        if r.get('apiKey') or r.get('status') == 'activated':
            success += 1

        if i < n - 1:
            delay = random.uniform(8, 20)
            log(f"冷却 {delay:.0f}s...")
            time.sleep(delay)

    print(f"\n{'▓' * 70}")
    print(f"  铸造完成: {success}/{n} 成功")
    for r in results:
        icon = '+' if r.get('apiKey') or r.get('status') == 'activated' else '-'
        print(f"  [{icon}] {r.get('email', '?')[:40]:40} {r.get('status', '?')}")
    print(f"{'▓' * 70}\n")
    return results


# ═══════════════════════════════════════════════════════
# §12  收割 — Yahoo批量激活
# ═══════════════════════════════════════════════════════

def harvest_yahoo():
    """收割: 激活账号.txt中所有未入池的Yahoo账号"""
    yahoo = parse_yahoo_accounts()
    pool, _ = load_pool_accounts()
    pool_emails = {a.get('email', '').lower() for a in pool}
    pool_with_key = {a.get('email', '').lower() for a in pool if a.get('apiKey')}

    fresh = [(e, p) for e, p in yahoo if e.lower() not in pool_with_key]

    print(f"\n{'═' * 70}")
    print(f"  收割 Yahoo 账号 — {len(fresh)} 待激活 (总{len(yahoo)}个)")
    print(f"{'═' * 70}")

    if not fresh:
        log("所有Yahoo账号已激活", True)
        return

    success = 0
    for i, (email, pw) in enumerate(fresh):
        print(f"\n{'─' * 40} [{i+1}/{len(fresh)}]")
        r = activate_account(email, pw)
        if r.get('apiKey'):
            success += 1
        if i < len(fresh) - 1:
            time.sleep(2)

    print(f"\n  收割完成: {success}/{len(fresh)}")


# ═══════════════════════════════════════════════════════
# §13  复活 — 重试pending账号
# ═══════════════════════════════════════════════════════

def revive_pending():
    """复活: 尝试激活所有pending_verify的Gmail alias账号"""
    # 从gmail_alias_results加载
    gmail_results_file = SCRIPT_DIR / "_gmail_alias_results.json"
    if not gmail_results_file.exists():
        log("无Gmail alias结果文件", False)
        return

    results = json.loads(gmail_results_file.read_text(encoding='utf-8'))
    pending = [r for r in results if r.get('status') == 'pending_verify']

    print(f"\n{'═' * 70}")
    print(f"  复活 — {len(pending)} 个pending_verify账号")
    print(f"{'═' * 70}")

    if not pending:
        log("无pending账号", True)
        return

    success = 0
    for i, r in enumerate(pending):
        email = r.get('email', '')
        pw = r.get('windsurf_password', '')
        if not email or not pw:
            continue
        print(f"\n{'─' * 40} [{i+1}/{len(pending)}]")
        result = activate_account(email, pw)
        if result.get('apiKey'):
            success += 1
        time.sleep(1)

    print(f"\n  复活完成: {success}/{len(pending)}")


# ═══════════════════════════════════════════════════════
# §14  全景状态
# ═══════════════════════════════════════════════════════

def show_status():
    pool, paths = load_pool_accounts()
    yahoo = parse_yahoo_accounts()
    genesis_results = load_genesis_results()
    now_ms = time.time() * 1000
    now = datetime.now(CST)

    print(f"\n{'═' * 75}")
    print(f"  创世引擎 · 全景 v{VERSION} — {now.strftime('%Y-%m-%d %H:%M:%S CST')}")
    print(f"{'═' * 75}")

    # Pool summary
    real = [a for a in pool if '@' in a.get('email', '') and 'example' not in a.get('email', '')]
    has_key = [a for a in real if a.get('apiKey')]
    plans = Counter(a.get('usage', {}).get('plan', '?') for a in real)
    healthy = low = exhausted = 0
    expiring_soon = []

    for a in real:
        u = a.get('usage', {})
        dr = u.get('daily', {}).get('remaining', 0) if u.get('daily') else 0
        wr = u.get('weekly', {}).get('remaining', 0) if u.get('weekly') else 0
        eff = min(dr, wr) if (dr or wr) else -1
        pe = u.get('planEnd', 0)
        days = max(0, (pe - now_ms) / 86400000) if pe and pe > 1e12 else -1
        if 0 <= days <= 5:
            expiring_soon.append((a.get('email', '?'), days))
        if eff > 5:
            healthy += 1
        elif eff > 0:
            low += 1
        elif eff == 0:
            exhausted += 1

    # Audit-based accurate counts (survives Windsurf pool resets)
    audit_path = SCRIPT_DIR / '_audit_results.json'
    audit_pro = audit_free = audit_dead = 0
    audit_ts = ''
    if audit_path.exists():
        try:
            ad = json.loads(audit_path.read_text(encoding='utf-8'))
            audit_pro = len(ad.get('pro_trial', []))
            audit_free = len(ad.get('free', []))
            audit_dead = len(ad.get('dead', []))
            audit_ts = ad.get('timestamp', '')[:19]
        except Exception:
            pass

    print(f"\n  号池: {len(real)} 真实 ({len(has_key)} 有apiKey) | {len(pool)-len(real)} 测试 | {len(paths)} 文件")
    if audit_pro:
        print(f"  审计: {audit_pro} Pro Trial ✓ | {audit_free} Free ✗ | {audit_dead} Dead ✗  ({audit_ts})")
    print(f"  池标: {dict(plans)}")
    print(f"  健康: {healthy} | 低: {low} | 耗尽: {exhausted}")

    if expiring_soon:
        print(f"\n  即将到期 ({len(expiring_soon)}):")
        for e, d in sorted(expiring_soon, key=lambda x: x[1])[:5]:
            print(f"    {e[:42]:42} {d:.1f}天")

    # Yahoo未入池
    pool_emails = {a.get('email', '').lower() for a in pool}
    pool_keyed = {a.get('email', '').lower() for a in pool if a.get('apiKey')}
    yahoo_fresh = [(e, p) for e, p in yahoo if e.lower() not in pool_keyed]
    print(f"\n  Yahoo来源: {len(yahoo)} 总 | {len(yahoo_fresh)} 未激活")

    # Gmail alias
    secrets = load_secrets()
    gmail_base = secrets.get('GMAIL_BASE', '')
    gmail_state_file = SCRIPT_DIR / "_genesis_gmail_state.json"
    gmail_idx = 20
    if gmail_state_file.exists():
        try:
            gmail_idx = json.loads(gmail_state_file.read_text(encoding='utf-8')).get('next_index', 20)
        except Exception:
            pass
    gmail_status = f"index={gmail_idx}" if gmail_base else "未配置"
    print(f"  Gmail来源: {gmail_base or 'N/A'} ({gmail_status})")

    # Genesis results
    if genesis_results:
        ok = sum(1 for r in genesis_results if r.get('status') == 'activated')
        print(f"\n  创世记录: {len(genesis_results)} 总 | {ok} 激活成功")
        for r in genesis_results[-5:]:
            icon = '+' if r.get('status') == 'activated' else '-'
            ts = r.get('timestamp', '?')[:19]
            print(f"    [{icon}] {ts} {r.get('email', '?')[:35]:35} {r.get('source', '?')}")

    # Temp email probe
    print(f"\n  临时邮箱:")
    for name, check_fn in [("tempmail.lol", lambda: http_get_json("https://api.tempmail.lol/v2/inbox/create", timeout=10)),
                            ("Mail.tm", lambda: http_get_json("https://api.mail.tm/domains", timeout=10))]:
        try:
            d = check_fn()
            if d:
                print(f"    {name:15} OK")
            else:
                print(f"    {name:15} ??")
        except Exception as e:
            print(f"    {name:15} FAIL ({str(e)[:40]})")

    # 推荐行动
    print(f"\n  {'─' * 40}")
    print(f"  推荐行动:")
    if yahoo_fresh:
        print(f"    harvest  — 收割{len(yahoo_fresh)}个Yahoo账号 (最快)")
    if len(has_key) < 10:
        print(f"    forge 5  — 号池不足, 铸造5个新账号")
    if expiring_soon:
        print(f"    forge {len(expiring_soon)}  — 补充即将到期的{len(expiring_soon)}个")
    if not yahoo_fresh and len(has_key) >= 10 and not expiring_soon:
        print(f"    号池健康, 无需紧急操作")
    print(f"\n{'═' * 75}\n")


# ═══════════════════════════════════════════════════════
# §15  探测
# ═══════════════════════════════════════════════════════

def probe():
    print(f"\n{'═' * 60}")
    print(f"  创世引擎 · 可达性探测")
    print(f"{'═' * 60}")

    proxy_port = detect_proxy()
    log(f"代理: {'127.0.0.1:' + str(proxy_port) if proxy_port else 'NONE'}")

    # Firebase
    print("\n[Firebase]")
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={key}'
        for use_p in [False, True]:
            label = f"{'proxy' if use_p else 'direct'}-{key[-4:]}"
            try:
                r = http_json(url, {'returnSecureToken': True}, use_proxy=use_p, timeout=8)
                msg = r.get('error', {}).get('message', 'OK') if isinstance(r.get('error'), dict) else str(r.get('error', 'OK'))
                log(f"  {label}: OK ({msg[:40]})", True)
            except Exception as e:
                log(f"  {label}: FAIL ({str(e)[:50]})", False)

    # RegisterUser
    print("\n[RegisterUser gRPC]")
    dummy = encode_proto("test_probe")
    for url in REGISTER_URLS:
        host = url.split('/')[2]
        try:
            r = http_bin(url, dummy, timeout=10)
            log(f"  {host}: OK ({len(r)}B)", True)
        except Exception as e:
            log(f"  {host}: FAIL ({str(e)[:50]})", False)

    # Temp email
    print("\n[临时邮箱]")
    try:
        d = http_get_json("https://api.tempmail.lol/v2/inbox/create", timeout=15)
        log(f"  tempmail.lol: OK ({d.get('address', '?')})", True)
    except Exception as e:
        log(f"  tempmail.lol: FAIL ({str(e)[:50]})", False)
    try:
        d = http_get_json("https://api.mail.tm/domains", timeout=15)
        doms = [x.get('domain') for x in d.get('hydra:member', []) if x.get('isActive')]
        log(f"  Mail.tm: OK (domains={doms[:3]})", True)
    except Exception as e:
        log(f"  Mail.tm: FAIL ({str(e)[:50]})", False)

    # Chrome + turnstilePatch
    print("\n[浏览器]")
    chrome = find_chrome()
    log(f"  Chrome: {'OK' if chrome else 'MISSING'}", bool(chrome))
    tp = find_turnstile_patch()
    log(f"  turnstilePatch: {'OK' if tp else 'MISSING'}", bool(tp))
    try:
        import DrissionPage
        log(f"  DrissionPage: OK", True)
    except ImportError:
        log(f"  DrissionPage: MISSING (pip install DrissionPage)", False)

    # Gmail IMAP
    print("\n[Gmail IMAP]")
    secrets = load_secrets()
    gb = secrets.get('GMAIL_BASE', '')
    gp = secrets.get('GMAIL_APP_PASSWORD', '')
    if gb and gp:
        try:
            import imaplib
            m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            m.login(gb, gp)
            m.select("INBOX")
            _, data = m.search(None, "ALL")
            count = len(data[0].split()) if data[0] else 0
            m.logout()
            log(f"  {gb}: OK ({count} emails)", True)
        except Exception as e:
            log(f"  {gb}: FAIL ({str(e)[:50]})", False)
    else:
        log(f"  未配置 GMAIL_BASE/GMAIL_APP_PASSWORD", False)

    print(f"\n{'═' * 60}\n")


# ═══════════════════════════════════════════════════════
# §16  守护进程
# ═══════════════════════════════════════════════════════

def guard(check_interval=300, min_pool=10, auto_forge=3):
    """
    守护进程: 定期检查号池, 自动补充
    check_interval: 检查间隔(秒)
    min_pool: 最小有效账号数
    auto_forge: 每次自动铸造数
    """
    print(f"\n{'═' * 70}")
    print(f"  创世引擎 · 守护进程 · 间隔{check_interval}s · 阈值{min_pool}")
    print(f"{'═' * 70}\n")

    while True:
        try:
            pool, _ = load_pool_accounts()
            real_with_key = [a for a in pool if a.get('apiKey') and '@' in a.get('email', '')]
            now_ms = time.time() * 1000

            # 有效账号 = 有apiKey + 未过期
            effective = 0
            for a in real_with_key:
                pe = a.get('usage', {}).get('planEnd', 0)
                if pe and pe > 1e12:
                    days = (pe - now_ms) / 86400000
                    if days > 1:
                        effective += 1
                else:
                    effective += 1  # 无过期信息视为有效

            ts = datetime.now(CST).strftime('%H:%M:%S')
            log(f"[守护] 有效账号: {effective}/{min_pool} (总{len(real_with_key)})")

            if effective < min_pool:
                need = min_pool - effective
                forge_count = min(need, auto_forge)
                log(f"[守护] 低于阈值! 自动铸造 {forge_count} 个...")
                forge_batch(forge_count, source="auto")

        except Exception as e:
            log(f"[守护] 异常: {e}", False)

        time.sleep(check_interval)


# ═══════════════════════════════════════════════════════
# §17  Web Dashboard
# ═══════════════════════════════════════════════════════

def dashboard_html():
    pool, _ = load_pool_accounts()
    real = [a for a in pool if '@' in a.get('email', '') and 'example' not in a.get('email', '')]
    has_key = sum(1 for a in real if a.get('apiKey'))
    yahoo = parse_yahoo_accounts()
    pool_keyed = {a.get('email', '').lower() for a in real if a.get('apiKey')}
    yahoo_fresh = len([(e, p) for e, p in yahoo if e.lower() not in pool_keyed])
    genesis = load_genesis_results()
    ok = sum(1 for r in genesis if r.get('status') == 'activated')
    now = datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')

    rows = ""
    for a in real[-50:]:
        e = a.get('email', '?')
        key_short = (a.get('apiKey', '')[:20] + '...') if a.get('apiKey') else 'NONE'
        src = a.get('source', a.get('_activatedBy', '?'))
        u = a.get('usage', {})
        plan = u.get('plan', '?')
        rows += f"<tr><td>{e[:45]}</td><td>{plan}</td><td><code>{key_short}</code></td><td>{src}</td></tr>\n"

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Genesis Dashboard</title>
<style>body{{font-family:system-ui;background:#0a0a0a;color:#e0e0e0;padding:20px;max-width:1200px;margin:0 auto}}
h1{{color:#00ff88;border-bottom:2px solid #00ff88;padding-bottom:10px}}
.stats{{display:flex;gap:20px;flex-wrap:wrap;margin:20px 0}}
.stat{{background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:15px 25px;min-width:120px}}
.stat .num{{font-size:2em;font-weight:bold;color:#00ff88}}.stat .label{{color:#888;font-size:.85em}}
table{{width:100%;border-collapse:collapse;margin:20px 0}}
th{{background:#1a1a2e;padding:10px;text-align:left;border-bottom:2px solid #333}}
td{{padding:8px 10px;border-bottom:1px solid #222}}
tr:hover{{background:#1a1a2e}}
code{{background:#222;padding:2px 6px;border-radius:3px;font-size:.85em}}
.btn{{background:#00ff88;color:#000;border:none;padding:10px 20px;border-radius:5px;cursor:pointer;font-weight:bold;margin:5px}}
.btn:hover{{background:#00cc6a}}</style></head><body>
<h1>Genesis Engine Dashboard</h1>
<p style="color:#888">{now} CST</p>
<div class="stats">
<div class="stat"><div class="num">{len(real)}</div><div class="label">Total Accounts</div></div>
<div class="stat"><div class="num">{has_key}</div><div class="label">With ApiKey</div></div>
<div class="stat"><div class="num">{yahoo_fresh}</div><div class="label">Yahoo Ready</div></div>
<div class="stat"><div class="num">{ok}/{len(genesis)}</div><div class="label">Genesis OK</div></div>
</div>
<h2>Accounts</h2>
<table><thead><tr><th>Email</th><th>Plan</th><th>ApiKey</th><th>Source</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(dashboard_html().encode('utf-8'))

    def log_message(self, fmt, *args):
        pass


def start_dashboard():
    server = HTTPServer(('127.0.0.1', DASHBOARD_PORT), DashboardHandler)
    log(f"Dashboard: http://127.0.0.1:{DASHBOARD_PORT}", True)
    import webbrowser
    webbrowser.open(f"http://127.0.0.1:{DASHBOARD_PORT}")
    server.serve_forever()


# ═══════════════════════════════════════════════════════
# §18  审计 — 锚定本源，去伪存真
# ═══════════════════════════════════════════════════════

def deep_repair():
    """Deep repair: merge ALL result sources (genesis + api_register) into pool."""
    pool, paths = load_pool_accounts()
    if not paths:
        log("No pool files found", False)
        return 0

    # Collect full apiKeys from ALL result files
    all_keys = {}  # email_lower -> full apiKey
    all_passwords = {}  # email_lower -> password

    # Genesis results
    genesis = load_genesis_results()
    for r in genesis:
        e = r.get('email', '').lower()
        k = r.get('apiKey', '')
        pw = r.get('password', '')
        if e and k and '...' not in k:
            all_keys[e] = k
        if e and pw:
            all_passwords[e] = pw

    # API register results
    ar_path = SCRIPT_DIR / '_api_register_results.json'
    if ar_path.exists():
        try:
            for r in json.loads(ar_path.read_text(encoding='utf-8')):
                e = r.get('email', '').lower()
                k = r.get('apiKey', '')
                pw = r.get('password', '')
                if e and k and '...' not in k:
                    all_keys.setdefault(e, k)
                if e and pw:
                    all_passwords.setdefault(e, pw)
        except Exception:
            pass

    log(f"Repair sources: {len(all_keys)} keys, {len(all_passwords)} passwords")

    fixed = 0
    for fp in paths:
        try:
            accts = json.loads(fp.read_text(encoding='utf-8'))
            changed = False
            for a in accts:
                e = a.get('email', '').lower()
                if not a.get('apiKey') and e in all_keys:
                    a['apiKey'] = all_keys[e]
                    a['_repairedAt'] = datetime.now(CST).isoformat()
                    fixed += 1
                    changed = True
                if not a.get('password') and e in all_passwords:
                    a['password'] = all_passwords[e]
                    changed = True
            if changed:
                fp.write_text(json.dumps(accts, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as ex:
            log(f"Repair error {fp.name}: {ex}", False)

    # Verify
    pool2, _ = load_pool_accounts()
    wk = sum(1 for a in pool2 if a.get('apiKey'))
    log(f"Repaired {fixed}. Pool: {wk}/{len(pool2)} apiKey", True)
    return fixed


def audit_pool():
    """审计号池: signIn → GetPlanStatus → 标记真实plan. 去除Free."""
    # Step 0: deep repair first
    print(f"\n{'═' * 70}")
    print(f"  审计号池 · 锚定本源 — {datetime.now(CST).strftime('%H:%M:%S')}")
    print(f"{'═' * 70}")

    print(f"\n  [Phase 0] Deep Repair...")
    deep_repair()

    # Reload pool after repair
    pool, paths = load_pool_accounts()
    if not paths:
        log("No pool files found", False)
        return

    # Accounts with password (can signIn to check)
    checkable = [a for a in pool if a.get('email') and a.get('password')]
    no_pw = [a for a in pool if a.get('email') and not a.get('password')]

    print(f"\n  [Phase 1] 号池概况")
    print(f"    可检查 (有password): {len(checkable)}")
    print(f"    不可检查 (无password): {len(no_pw)}")
    print(f"    有apiKey: {sum(1 for a in pool if a.get('apiKey'))}")

    # Step 1: SignIn + GetPlanStatus for all checkable accounts
    print(f"\n  [Phase 2] Firebase signIn + GetPlanStatus")
    print(f"  {'─' * 60}")

    stats = {'pro_trial': 0, 'free': 0, 'unknown': 0, 'no_response': 0,
             'unreachable': 0, 'signin_fail': 0, 'error': 0}
    audit_results = []  # (email, real_plan, apiKey, detail)

    for i, a in enumerate(checkable):
        email = a['email']
        pw = a['password']
        pool_plan = a.get('usage', {}).get('plan', '?')

        try:
            login = firebase_signin(email, pw)
            if not login.get('idToken'):
                err = login.get('error', '?')
                stats['signin_fail'] += 1
                audit_results.append((email, 'signin_fail', a.get('apiKey'), err))
                print(f"  [{i+1:3}/{len(checkable)}] {email[:38]:38} ✗ signIn_FAIL")
                time.sleep(0.3)
                continue

            id_token = login['idToken']

            # Get plan status
            ps = windsurf_plan_status(id_token)
            real_plan = ps.get('plan', 'unreachable')
            detail = '; '.join(ps.get('raw_strings', [])[:5])

            # Ensure we have apiKey (register if missing)
            api_key = a.get('apiKey')
            if not api_key:
                reg = windsurf_register_user(id_token)
                api_key = reg.get('apiKey')
                if api_key:
                    a['apiKey'] = api_key

            stats[real_plan] = stats.get(real_plan, 0) + 1
            audit_results.append((email, real_plan, api_key, detail))

            icon = '+' if real_plan == 'pro_trial' else '-'
            key_status = 'Y' if api_key else 'N'
            print(f"  [{i+1:3}/{len(checkable)}] {email[:38]:38} [{icon}] {real_plan:12} key={key_status} {detail[:40]}")

            # Update pool entry with real plan
            a['_auditedPlan'] = real_plan
            a['_auditedAt'] = datetime.now(CST).isoformat()

        except Exception as e:
            stats['error'] += 1
            audit_results.append((email, 'error', a.get('apiKey'), str(e)[:60]))
            print(f"  [{i+1:3}/{len(checkable)}] {email[:38]:38} ✗ {str(e)[:50]}")

        time.sleep(0.5)

    # Write audit annotations back to pool
    for fp in paths:
        try:
            fp.write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    # Summary
    print(f"\n{'═' * 70}")
    print(f"  审计结果")
    print(f"{'═' * 70}")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        if v:
            icon = '✓' if k == 'pro_trial' else '✗'
            print(f"  {icon} {k:20} {v:4} 个")

    pro_emails = [e for e, p, k, d in audit_results if p == 'pro_trial']
    free_emails = [e for e, p, k, d in audit_results if p == 'free']
    dead_emails = [e for e, p, k, d in audit_results if p == 'signin_fail']

    print(f"\n  Pro Trial (真实有效): {len(pro_emails)}")
    print(f"  Free (无意义): {len(free_emails)}")
    print(f"  Dead (signIn失败): {len(dead_emails)}")

    # Save audit
    audit_path = SCRIPT_DIR / '_audit_results.json'
    audit_data = {
        'timestamp': datetime.now(CST).isoformat(),
        'stats': stats,
        'pro_trial': pro_emails,
        'free': free_emails,
        'dead': dead_emails,
        'all': [(e, p, bool(k), d) for e, p, k, d in audit_results],
    }
    audit_path.write_text(json.dumps(audit_data, indent=2, ensure_ascii=False), encoding='utf-8')
    log(f"审计报告: {audit_path.name}", True)

    if free_emails or dead_emails:
        print(f"\n  → 运行 'purge' 清除 {len(free_emails)} Free + {len(dead_emails)} Dead 账号")


def purge_pool():
    """原子操作: repair apiKey + 标记Free为'free' + 标记pro_trial → 单次写入.
    pool_engine已内置'free'过滤，标记即排除。"""

    print(f"\n{'═' * 70}")
    print(f"  号池净化 · 原子操作 — {datetime.now(CST).strftime('%H:%M:%S')}")
    print(f"{'═' * 70}")

    # ── Step 1: Collect ALL repair sources (apiKeys + passwords) ──
    all_keys = {}
    all_passwords = {}
    genesis = load_genesis_results()
    for r in genesis:
        e = r.get('email', '').lower()
        k = r.get('apiKey', '')
        pw = r.get('password', '')
        if e and k and '...' not in k:
            all_keys[e] = k
        if e and pw:
            all_passwords[e] = pw
    ar_path = SCRIPT_DIR / '_api_register_results.json'
    if ar_path.exists():
        try:
            for r in json.loads(ar_path.read_text(encoding='utf-8')):
                e = r.get('email', '').lower()
                k = r.get('apiKey', '')
                pw = r.get('password', '')
                if e and k and '...' not in k:
                    all_keys.setdefault(e, k)
                if e and pw:
                    all_passwords.setdefault(e, pw)
        except Exception:
            pass

    # ── Step 2: Load audit results for plan classification ──
    audit_path = SCRIPT_DIR / '_audit_results.json'
    pro_set = set()
    free_set = set()
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding='utf-8'))
        pro_set = set(e.lower() for e in audit.get('pro_trial', []))
        free_set = set(e.lower() for e in audit.get('free', []))

    # ── Step 3: Read pool → repair + label → single atomic write ──
    _, paths = load_pool_accounts()
    if not paths:
        log("No pool files found", False)
        return

    repaired = 0
    marked_free = 0
    marked_pro = 0
    for fp in paths:
        try:
            accts = json.loads(fp.read_text(encoding='utf-8'))
            for a in accts:
                e = a.get('email', '').lower()
                # Repair apiKey
                if not a.get('apiKey') and e in all_keys:
                    a['apiKey'] = all_keys[e]
                    a['_repairedAt'] = datetime.now(CST).isoformat()
                    repaired += 1
                # Repair password
                if not a.get('password') and e in all_passwords:
                    a['password'] = all_passwords[e]
                # Mark Free (pool_engine will skip these)
                usage = a.setdefault('usage', {})
                if e in free_set:
                    if usage.get('plan', '').lower() != 'free':
                        usage['plan'] = 'free'
                        marked_free += 1
                # Mark Pro Trial
                elif e in pro_set:
                    if usage.get('plan', '').lower() not in ('pro_trial', 'trial'):
                        usage['plan'] = 'pro_trial'
                        marked_pro += 1

            # SINGLE atomic write
            fp.write_text(json.dumps(accts, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as ex:
            log(f"Error {fp.name}: {ex}", False)

    # ── Step 4: Write blacklist to engine directory (survives Windsurf pool resets) ──
    dead_set = set()
    if audit_path.exists():
        dead_set = set(e.lower() for e in audit.get('dead', []))
    bl_path = SCRIPT_DIR.parent / 'engine' / '_free_blacklist.json'
    bl_data = {
        'description': 'Accounts audited as Free/dead by genesis engine. pool_engine skips these.',
        'updated': datetime.now(CST).isoformat(),
        'free': sorted(free_set),
        'dead': sorted(dead_set),
    }
    try:
        bl_path.parent.mkdir(parents=True, exist_ok=True)
        bl_path.write_text(json.dumps(bl_data, indent=2, ensure_ascii=False), encoding='utf-8')
        log(f"Blacklist: {bl_path.name} ({len(free_set)} free, {len(dead_set)} dead)", True)
    except Exception as ex:
        log(f"Blacklist write error: {ex}", False)

    # ── Step 5: Verify ──
    pool2, _ = load_pool_accounts()
    total = len(pool2)
    with_key = sum(1 for a in pool2 if a.get('apiKey'))
    free_count = len(free_set)
    pro_count = len(pro_set)

    print(f"\n  修复: +{repaired} apiKey")
    print(f"  标记: {marked_free} → free (排除), {marked_pro} → pro_trial")
    print(f"\n  号池: {total} 总 | {with_key} apiKey | {free_count} free(黑名单排除) | {pro_count} Pro Trial(审计确认)")
    log(f"Purge: {with_key} keys, {free_count} blacklisted, {pro_count} pro_trial confirmed", True)


# ═══════════════════════════════════════════════════════
# §19.5  深度净化 — 一键清除所有无效账号 (purge-deep)
# ═══════════════════════════════════════════════════════

def purge_deep():
    """深度净化: live API验证每个账号 → 物理移除free/dead/expired → 归档.
    道法自然: 以Claude Opus可用性为唯一锚点, 去伪存真, 推进到底."""
    print(f"\n{'═' * 70}")
    print(f"  深度净化 · purge-deep — 锚定Claude Opus可用性")
    print(f"  {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S CST')}")
    print(f"{'═' * 70}")

    pool, paths = load_pool_accounts()
    if not pool:
        log("号池为空", False)
        return

    log(f"号池: {len(pool)} 个账号, 开始逐一验证...")
    keep = []
    purged = []
    stats = {'dead': 0, 'free': 0, 'expired': 0, 'probe_fail': 0, 'no_pw': 0, 'ok': 0}

    for i, acc in enumerate(pool):
        email = acc.get('email', '?')
        pw = acc.get('password', '')
        prefix = f"[{i+1}/{len(pool)}] {email[:30]}"

        if not pw:
            stats['no_pw'] += 1
            purged.append({**acc, '_purgeReason': 'no_password', '_purgedAt': datetime.now(CST).isoformat()})
            log(f"{prefix} → 移除(无密码)", False)
            continue

        # Step 1: Firebase signIn
        login = firebase_signin(email, pw)
        id_token = login.get('idToken')
        if not id_token:
            err = login.get('error', {})
            if isinstance(err, dict):
                err = err.get('message', str(err))
            if any(k in str(err).upper() for k in ['INVALID', 'NOT_FOUND', 'DISABLED', 'WRONG']):
                stats['dead'] += 1
                purged.append({**acc, '_purgeReason': f'login_dead: {err}', '_purgedAt': datetime.now(CST).isoformat()})
                log(f"{prefix} → 移除(登录死号: {err})", False)
                continue
            # 网络临时错误 → 保留, 不冤枉
            stats['probe_fail'] += 1
            keep.append(acc)
            log(f"{prefix} → 保留(网络临时错误: {err})")
            continue

        # Step 2: GetPlanStatus — Claude可用性的ground truth
        plan_info = windsurf_plan_status(id_token)
        if plan_info.get('ok'):
            plan = plan_info.get('plan', 'unknown').lower()
            raw = plan_info.get('raw_strings', [])[:3]

            if plan == 'free':
                stats['free'] += 1
                purged.append({**acc, '_purgeReason': f'plan_free: Claude不可用', '_purgedAt': datetime.now(CST).isoformat()})
                log(f"{prefix} → 移除(plan=free, Claude不可用) [{', '.join(raw)}]", False)
                continue

            # 更新真实plan到账号
            acc.setdefault('usage', {})['plan'] = plan
            acc['_verifiedPlan'] = plan
            acc['_lastVerified'] = datetime.now(CST).isoformat()
            stats['ok'] += 1
            keep.append(acc)
            log(f"{prefix} → 保留(plan={plan}) [{', '.join(raw)}]", True)
        else:
            # API不可达 → 保留, 不冤枉
            stats['probe_fail'] += 1
            keep.append(acc)
            log(f"{prefix} → 保留(API探测失败)")

        time.sleep(0.5)  # 限速

    # Step 3: 原子写入 — 所有pool文件同步更新
    for fp in paths:
        try:
            fp.write_text(json.dumps(keep, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            log(f"Write error {fp.name}: {e}", False)

    # Step 4: 归档被清除的账号 (可恢复)
    if purged:
        archive_path = paths[0].parent / '_purge_deep_archive.json'
        try:
            existing = []
            if archive_path.exists():
                existing = json.loads(archive_path.read_text(encoding='utf-8'))
            existing.extend(purged)
            archive_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            log(f"Archive error: {e}", False)

    # Step 4.5: 写入Extension黑名单_wam_purged.json (万法归宗: 所有路径同步)
    # 反者道之动: purge-deep的API验证是ground truth, 必须写入blacklist防止load()回流
    if purged:
        bl_paths = set()
        for fp in paths:
            bl_paths.add(fp.parent / '_wam_purged.json')
        # 多用户路径
        for user in ['Administrator', 'ai', 'zhou', 'zhouyoukang']:
            bl_p = Path(f'C:/Users/{user}/AppData/Roaming/Windsurf/User/globalStorage/_wam_purged.json')
            if bl_p.parent.exists():
                bl_paths.add(bl_p)
        for bl_path in bl_paths:
            try:
                bl_existing = []
                if bl_path.exists():
                    bl_existing = json.loads(bl_path.read_text(encoding='utf-8'))
                if not isinstance(bl_existing, list):
                    bl_existing = []
                bl_emails = {(a.get('email') or '').lower() for a in bl_existing}
                added = 0
                for acc in purged:
                    e = (acc.get('email') or '').lower()
                    if e and e not in bl_emails:
                        bl_existing.append({
                            'email': acc.get('email', ''),
                            '_purgeReason': acc.get('_purgeReason', 'purge_deep'),
                            '_purgedAt': now_iso,
                        })
                        bl_emails.add(e)
                        added += 1
                if added > 0:
                    bl_path.write_text(json.dumps(bl_existing, indent=2, ensure_ascii=False), encoding='utf-8')
                    log(f"Blacklist: +{added} → {bl_path} (total {len(bl_existing)})", True)
            except Exception as e:
                log(f"Blacklist write error {bl_path}: {e}", False)

    # Step 4.6: 同步清洁pool到所有用户路径 (防止load()从脏源合并)
    for user in ['Administrator', 'ai', 'zhou', 'zhouyoukang']:
        user_pool = Path(f'C:/Users/{user}/AppData/Roaming/Windsurf/User/globalStorage/windsurf-login-accounts.json')
        if user_pool.exists() and user_pool not in paths:
            try:
                user_pool.write_text(json.dumps(keep, indent=2, ensure_ascii=False), encoding='utf-8')
                log(f"Synced clean pool → {user_pool}", True)
            except Exception:
                pass
        # User目录下的副本
        user_pool2 = Path(f'C:/Users/{user}/AppData/Roaming/Windsurf/User/windsurf-login-accounts.json')
        if user_pool2.exists():
            try:
                user_pool2.write_text(json.dumps(keep, indent=2, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass

    # Step 5: 报告
    removed = len(purged)
    print(f"\n{'═' * 70}")
    print(f"  深度净化完成 — 锚定Claude Opus可用性")
    print(f"  保留: {len(keep)} | 移除: {removed}")
    print(f"    登录死号: {stats['dead']}")
    print(f"    Free无Claude: {stats['free']}")
    print(f"    无密码: {stats['no_pw']}")
    print(f"    探测失败(保留): {stats['probe_fail']}")
    print(f"    Claude可用: {stats['ok']}")
    print(f"{'═' * 70}")
    log(f"purge-deep: {len(keep)} kept, {removed} removed ({stats['dead']} dead, {stats['free']} free, {stats['no_pw']} no_pw)", True)


# ═══════════════════════════════════════════════════════
# §19  CLI
# ═══════════════════════════════════════════════════════

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'

    if cmd == 'status':
        show_status()

    elif cmd == 'probe':
        probe()

    elif cmd == 'forge':
        n = 1
        source = "auto"
        for a in sys.argv[2:]:
            if a.startswith('--source='):
                source = a.split('=', 1)[1]
            elif a.isdigit():
                n = int(a)
        if n == 1:
            forge_one(source)
        else:
            forge_batch(n, source)

    elif cmd == 'activate' and len(sys.argv) >= 4:
        activate_account(sys.argv[2], sys.argv[3])

    elif cmd == 'activate-all':
        pool, _ = load_pool_accounts()
        need = [(a['email'], a['password']) for a in pool
                if a.get('email') and a.get('password') and not a.get('apiKey')]
        print(f"\n  批量激活: {len(need)} 个")
        for i, (e, p) in enumerate(need):
            print(f"\n{'─' * 40} [{i+1}/{len(need)}]")
            activate_account(e, p)
            time.sleep(1)

    elif cmd == 'harvest':
        harvest_yahoo()

    elif cmd == 'revive':
        revive_pending()

    elif cmd == 'repair':
        deep_repair()

    elif cmd == 'audit':
        audit_pool()

    elif cmd == 'purge':
        purge_pool()

    elif cmd == 'purge-deep':
        purge_deep()

    elif cmd == 'guard':
        interval = 300
        threshold = 10
        for a in sys.argv[2:]:
            if a.startswith('--interval='):
                interval = int(a.split('=', 1)[1])
            elif a.startswith('--min='):
                threshold = int(a.split('=', 1)[1])
        guard(check_interval=interval, min_pool=threshold)

    elif cmd == 'dashboard':
        start_dashboard()

    else:
        print(__doc__)
