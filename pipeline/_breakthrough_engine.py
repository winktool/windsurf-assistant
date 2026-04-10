"""
突破引擎 · 道法自然 · 上善若水
====================================
根因: Windsurf服务端静默封杀一次性邮箱域名 + Gmail别名只给free plan
解法: GitHub OAuth完全绕过邮件验证 → 验证是否pro_trial → 批量化

Phase 1: GitHub OAuth单账号注册 (用现有GitHub session)
Phase 2: Plan验证 (从state.vscdb读protobuf确认pro_trial)
Phase 3: Auth快照采集 (harvest到WAM引擎)
Phase 4: GitHub账号工厂 (temp-email→GitHub→OAuth→Windsurf, 批量化)

用法:
  python _breakthrough_engine.py probe         # 探测注册页当前状态
  python _breakthrough_engine.py github        # GitHub OAuth注册1个
  python _breakthrough_engine.py harvest       # 采集当前Windsurf auth快照
  python _breakthrough_engine.py verify EMAIL  # 验证指定账号plan
  python _breakthrough_engine.py factory N     # GitHub账号工厂(批量N个)
  python _breakthrough_engine.py status        # 全局状态
"""

import json, os, sys, time, random, string, re, socket, subprocess, ssl, struct
import html as html_mod

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen, build_opener, ProxyHandler

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
CST = timezone(timedelta(hours=8))

# === Paths ===
WS_APPDATA = Path(os.environ.get('APPDATA', '')) / 'Windsurf'
WS_GLOBALSTORE = WS_APPDATA / 'User' / 'globalStorage'
WS_STATE_DB = WS_GLOBALSTORE / 'state.vscdb'
SNAPSHOT_FILE = SCRIPT_DIR.parent / 'engine' / '_wam_snapshots.json'
RESULTS_FILE = SCRIPT_DIR / '_breakthrough_results.json'
LOG_FILE = SCRIPT_DIR / '_breakthrough.log'

ACCT_FILE_PATHS = [
    WS_GLOBALSTORE / 'zhouyoukang.windsurf-assistant' / 'windsurf-assistant-accounts.json',
    WS_GLOBALSTORE / 'windsurf-login-accounts.json',
    WS_GLOBALSTORE / 'undefined_publisher.windsurf-login-helper' / 'windsurf-login-accounts.json',
]

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
WINDSURF_REGISTER_URL = "https://windsurf.com/account/register"

FIREBASE_KEYS = [
    'AIzaSyDsOl-1XpT5err0Tcnx8FFod1H8gVGIycY',
    'AIzaSyDKm6GGxMJfCbNf-k0kPytiGLaqFJpeSac',
    'AIzaSyAMqIapVSEvhFgg-dhjdugyJMJnLqWib74',
    'AIzaSyDcBDyyRFI0hhJaslEMHBLAh5iJ_KPOd1M',
]

REGISTER_URLS = [
    'https://server.codeium.com/exa.api_server_pb.ApiServerService/RegisterUser',
    'https://web-backend.windsurf.com/exa.api_server_pb.ApiServerService/RegisterUser',
]

PLAN_STATUS_URLS = [
    'https://server.codeium.com/exa.seat_management_pb.SeatManagementService/GetPlanStatus',
    'https://web-backend.windsurf.com/exa.seat_management_pb.SeatManagementService/GetPlanStatus',
]

FIRST_NAMES = ["Alex","Jordan","Taylor","Morgan","Casey","Riley","Quinn","Avery",
               "Charlie","Finley","Harper","Jamie","Logan","Parker","Reese","Sam"]
LAST_NAMES  = ["Anderson","Brooks","Carter","Davis","Fisher","Garcia","Hughes",
               "Kim","Lee","Mitchell","Nelson","Park","Rivera","Smith","Turner"]

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


# ═══════════════════════════════════════════════════════
# §1  工具层
# ═══════════════════════════════════════════════════════

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


def detect_proxy():
    for port in [7890, 7897, 7891, 10808]:
        try:
            s = socket.socket(); s.settimeout(0.8)
            s.connect(('127.0.0.1', port)); s.close()
            return port
        except Exception:
            pass
    return 0


PP = detect_proxy()


def http_json(url, data=None, method='POST', timeout=15):
    body = json.dumps(data).encode('utf-8') if data else None
    req = Request(url, data=body, method=method)
    req.add_header('Content-Type', 'application/json')
    try:
        if PP > 0:
            handler = ProxyHandler({'https': f'http://127.0.0.1:{PP}', 'http': f'http://127.0.0.1:{PP}'})
            resp = build_opener(handler).open(req, timeout=timeout)
        else:
            resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
        return json.loads(resp.read())
    except Exception as e:
        try:
            return json.loads(e.read()) if hasattr(e, 'read') else {'_error': str(e)}
        except Exception:
            return {'_error': str(e)}


def http_bin(url, bin_data, timeout=15):
    req = Request(url, data=bin_data, method='POST')
    req.add_header('Content-Type', 'application/proto')
    req.add_header('Accept', 'application/proto')
    if PP > 0:
        handler = ProxyHandler({'https': f'http://127.0.0.1:{PP}', 'http': f'http://127.0.0.1:{PP}'})
        resp = build_opener(handler).open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    return resp.read()


def gen_password():
    chars = string.ascii_letters + string.digits + "!@#$"
    pw = (random.choice(string.ascii_uppercase) + random.choice(string.ascii_lowercase) +
          random.choice(string.digits) + random.choice("!@#$") +
          ''.join(random.choices(chars, k=12)))
    return ''.join(random.sample(pw, len(pw)))


def save_result(result):
    results = []
    if RESULTS_FILE.exists():
        try:
            results = json.loads(RESULTS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    results.append(result)
    RESULTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')


# ═══════════════════════════════════════════════════════
# §2  Protobuf 编解码
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
    result = 0; shift = 0
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
# §3  Firebase Auth + Windsurf gRPC
# ═══════════════════════════════════════════════════════

def firebase_signin(email, password):
    payload = {'email': email, 'password': password, 'returnSecureToken': True}
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}'
        r = http_json(url, payload)
        if r.get('idToken'):
            return r
    return {'error': 'signin_failed'}


def windsurf_plan_status(id_token):
    buf = encode_proto(id_token)
    for url in PLAN_STATUS_URLS:
        try:
            resp = http_bin(url, buf, timeout=15)
            if resp and len(resp) > 5:
                strings = extract_proto_strings(resp)
                raw = [f"f{fn}={s}" for fn, s in strings]
                plan = 'no_response'
                for fn, s in strings:
                    sl = s.lower().strip()
                    if sl in ('pro_trial', 'trial'):
                        plan = 'pro_trial'; break
                    if sl == 'free':
                        plan = 'free'
                if plan == 'no_response' and strings:
                    plan = 'unknown'
                return {'ok': True, 'plan': plan, 'raw': raw}
        except Exception:
            continue
    return {'ok': False, 'plan': 'unreachable'}


def windsurf_register_user(id_token):
    buf = encode_proto(id_token)
    for url in REGISTER_URLS:
        try:
            resp = http_bin(url, buf, timeout=15)
            strings = extract_proto_strings(resp)
            for fn, s in strings:
                if s.startswith('sk-ws-'):
                    return {'ok': True, 'apiKey': s}
        except Exception:
            continue
    return {'ok': False}


# ═══════════════════════════════════════════════════════
# §4  State.vscdb 读取 (Ground Truth)
# ═══════════════════════════════════════════════════════

def db_read(key):
    """从state.vscdb读取指定key的值"""
    if not WS_STATE_DB.exists():
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(str(WS_STATE_DB), timeout=3)
        for table in ['ItemTable', 'cursorAuth']:
            try:
                row = conn.execute(f"SELECT value FROM {table} WHERE key=?", (key,)).fetchone()
                if row:
                    conn.close()
                    return row[0]
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return None


def read_current_auth():
    """从state.vscdb读取当前活跃的auth信息"""
    result = {}
    auth_keys = [
        'codeium.firebaseIdToken',
        'codeium.firebaseRefreshToken',
        'codeium.apiKey',
        'codeium.profile',
    ]
    for key in auth_keys:
        val = db_read(key)
        if val:
            result[key] = val

    # 尝试从profile提取email
    profile = result.get('codeium.profile', '')
    if profile:
        try:
            p = json.loads(profile)
            result['_email'] = p.get('email', '')
            result['_displayName'] = p.get('displayName', '')
        except Exception:
            pass

    return result


def read_proto_quota():
    """从state.vscdb读protobuf配额信息(Ground Truth)"""
    raw = db_read('windsurf.seatInfoCompact')
    if not raw:
        raw = db_read('codeium.seatInfoCompact')
    if not raw:
        return None

    if isinstance(raw, str):
        import base64
        try:
            raw = base64.b64decode(raw)
        except Exception:
            raw = raw.encode('utf-8')

    strings = extract_proto_strings(raw)
    return {f"f{fn}": s for fn, s in strings}


# ═══════════════════════════════════════════════════════
# §5  号池操作
# ═══════════════════════════════════════════════════════

def load_pool():
    best = []
    for fp in ACCT_FILE_PATHS:
        if fp.exists():
            try:
                d = json.loads(fp.read_text(encoding='utf-8'))
                if isinstance(d, list) and len(d) > len(best):
                    best = d
            except Exception:
                pass
    return best


def inject_to_pool(email, password='', api_key=None, source='breakthrough', plan=None):
    """注入/更新账号到号池. free plan拒绝入池."""
    if plan and plan.lower() == 'free':
        log(f"拒绝入池: {email} plan=free (Claude不可用)", False)
        return False

    now_iso = datetime.now(CST).isoformat()
    updated = 0
    for fp in ACCT_FILE_PATHS:
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
                    if plan:
                        a.setdefault('usage', {})['plan'] = plan
                    updated += 1
                    break

            if not found:
                entry = {
                    "email": email, "password": password, "source": source,
                    "addedAt": now_iso, "usage": {"plan": plan or "pro_trial"},
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
# §6  GitHub OAuth注册 (Phase 1)
# ═══════════════════════════════════════════════════════

def github_oauth_register(use_default_profile=True):
    """
    GitHub OAuth注册Windsurf — 完全绕过邮件验证
    use_default_profile=True: 使用已登录GitHub的Chrome profile
    use_default_profile=False: incognito模式(需手动登录GitHub)

    返回: {email, plan, apiKey, status} 或 None
    """
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except ImportError:
        log("DrissionPage未安装: pip install DrissionPage", False)
        return None

    # 杀残留Chrome进程
    try:
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                       capture_output=True, timeout=5)
        time.sleep(2)
    except Exception:
        pass

    co = ChromiumOptions()
    co.set_browser_path(CHROME_EXE)
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    co.auto_port()
    co.headless(False)

    if use_default_profile:
        default_profile = Path(os.environ.get('LOCALAPPDATA', '')) / 'Google' / 'Chrome' / 'User Data'
        if default_profile.exists():
            co.set_argument(f'--user-data-dir={default_profile}')
            co.set_argument('--profile-directory=Default')
            log("使用默认Chrome profile (已登录GitHub)", True)
        else:
            log("默认Chrome profile不存在, 使用incognito", False)
            use_default_profile = False

    if not use_default_profile:
        co.set_argument('--incognito')

    proxy_port = detect_proxy()
    if proxy_port:
        co.set_argument(f'--proxy-server=127.0.0.1:{proxy_port}')

    page = ChromiumPage(co)
    try:
        # Step 1: 导航到注册页
        log(f"导航: {WINDSURF_REGISTER_URL}")
        page.get(WINDSURF_REGISTER_URL)
        time.sleep(random.uniform(2, 4))

        # Step 2: 找到并点击"Sign up with GitHub"
        github_btn = None
        for sel in [
            'tag:button@text():GitHub',
            'css:button[data-provider="github"]',
            'xpath://button[contains(text(),"GitHub")]',
            'xpath://a[contains(text(),"GitHub")]',
            'xpath://button[contains(.,"GitHub")]',
        ]:
            try:
                btn = page.ele(sel, timeout=3)
                if btn:
                    github_btn = btn
                    log(f"GitHub按钮: {sel}", True)
                    break
            except Exception:
                pass

        if not github_btn:
            body = page.html or ""
            log(f"未找到GitHub按钮. 页面片段: {body[:300]}", False)
            # 尝试直接OAuth URL
            log("尝试直接导航OAuth URL...")
            page.get("https://windsurf.com/api/auth/github")
            time.sleep(3)
        else:
            github_btn.click()
            time.sleep(random.uniform(2, 4))

        # Step 3: 处理GitHub流程
        start = time.time()
        MAX_WAIT = 180  # 3分钟
        last_log = 0

        DONE_KW = ["dashboard", "welcome to windsurf", "get started",
                    "cascade", "open windsurf", "your workspace"]
        ERROR_KW = ["already have an account", "already registered",
                     "email is already in use", "account already exists"]

        while time.time() - start < MAX_WAIT:
            url = page.url or ""
            body = (page.html or "").lower()
            elapsed = int(time.time() - start)

            # GitHub登录页 — 等待手动登录
            if "github.com/login" in url or "github.com/session" in url:
                if elapsed - last_log >= 10:
                    log(f"等待GitHub登录... ({elapsed}s) 请在浏览器登录")
                    last_log = elapsed
                time.sleep(2)
                continue

            # GitHub OAuth授权页 — 自动Authorize
            if ("github.com/login/oauth" in url or
                ("github.com" in url and "authorize" in url)):
                log("GitHub OAuth授权页 — 自动点击Authorize...")
                for sel in [
                    'css:button[name="authorize"]',
                    'css:input[value="Authorize"]',
                    'css:button[type=submit]',
                    'xpath://button[contains(text(),"Authorize")]',
                    'xpath://input[@type="submit"]',
                ]:
                    try:
                        btn = page.ele(sel, timeout=2)
                        if btn:
                            btn.click()
                            log("Authorize已点击", True)
                            time.sleep(3)
                            break
                    except Exception:
                        pass
                continue

            # Windsurf注册完成
            if any(k in body for k in DONE_KW):
                log("GitHub OAuth注册成功!", True)
                time.sleep(3)  # 等待Windsurf完成初始化

                # 从URL/页面提取邮箱
                email = _extract_email_from_page(page)
                page.quit()

                # Step 4: 验证plan + 获取apiKey
                return _post_oauth_harvest(email)

            # 已注册错误
            if any(k in body for k in ERROR_KW):
                log("此GitHub账号已注册过Windsurf", False)
                # 尝试提取邮箱
                email = _extract_email_from_page(page)
                page.quit()
                if email:
                    log(f"已有账号: {email} — 尝试验证plan...")
                    return _post_oauth_harvest(email)
                return {'status': 'already_registered'}

            if elapsed - last_log >= 15:
                log(f"等待中... URL={url[:80]} ({elapsed}s)")
                last_log = elapsed

            time.sleep(2)

        log(f"GitHub OAuth超时({MAX_WAIT}s)", False)
        page.quit()
        return None

    except Exception as e:
        log(f"GitHub OAuth异常: {e}", False)
        import traceback; traceback.print_exc()
        try:
            page.quit()
        except Exception:
            pass
        return None


def _extract_email_from_page(page):
    """从Windsurf页面提取当前登录邮箱"""
    try:
        # 尝试从页面文字提取
        body = page.run_js("return document.body?.innerText || '';") or ""
        emails = re.findall(r'[\w.-]+@[\w.-]+\.\w+', body)
        if emails:
            return emails[0]
    except Exception:
        pass

    # 从state.vscdb读取
    auth = read_current_auth()
    if auth.get('_email'):
        return auth['_email']

    return None


def _post_oauth_harvest(email=None):
    """OAuth注册后的收尾: 从state.vscdb采集auth并验证plan"""
    log("Post-OAuth: 采集auth快照...")

    # 等待Windsurf写入state.vscdb
    for attempt in range(10):
        time.sleep(2)
        auth = read_current_auth()
        if auth.get('codeium.firebaseIdToken'):
            break
    else:
        log("state.vscdb中未找到auth信息 (Windsurf可能未同步)", False)
        # 仍然继续, 使用可用信息

    auth = read_current_auth()
    real_email = auth.get('_email', email or 'unknown')
    id_token = auth.get('codeium.firebaseIdToken', '')
    api_key = auth.get('codeium.apiKey', '')

    log(f"Email: {real_email}")
    log(f"idToken: {'OK' if id_token else 'MISSING'}")
    log(f"apiKey: {api_key[:30] + '...' if api_key else 'MISSING'}")

    # Plan验证
    plan = 'unknown'
    if id_token:
        plan_info = windsurf_plan_status(id_token)
        plan = plan_info.get('plan', 'unknown')
        raw = plan_info.get('raw', [])
        log(f"Plan: {plan} ({', '.join(raw[:3])})", plan == 'pro_trial')

        # 如果还没apiKey, 尝试RegisterUser
        if not api_key:
            reg = windsurf_register_user(id_token)
            api_key = reg.get('apiKey', '')
            if api_key:
                log(f"RegisterUser获取apiKey: {api_key[:25]}...", True)

    # Quota Ground Truth
    quota = read_proto_quota()
    if quota:
        log(f"Quota: {quota}")

    # 注入号池
    inject_to_pool(real_email, api_key=api_key, source='github_oauth', plan=plan)

    # 保存结果
    result = {
        'email': real_email,
        'status': 'activated' if api_key else 'registered',
        'plan': plan,
        'apiKey': api_key,
        'path': 'github_oauth',
        'timestamp': datetime.now(CST).isoformat(),
    }
    save_result(result)

    # 采集WAM快照
    _harvest_wam_snapshot(real_email, auth)

    return result


def _harvest_wam_snapshot(email, auth_data):
    """将auth数据保存为WAM快照"""
    if not auth_data.get('codeium.firebaseIdToken'):
        return
    snapshots = {}
    if SNAPSHOT_FILE.exists():
        try:
            snapshots = json.loads(SNAPSHOT_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass

    snap = {
        'harvestedAt': datetime.now(CST).isoformat(),
        'source': 'breakthrough_github_oauth',
    }
    for key, val in auth_data.items():
        if not key.startswith('_'):
            snap[key] = val

    snapshots[email] = snap
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps(snapshots, indent=2, ensure_ascii=False), encoding='utf-8')
    log(f"WAM快照已保存: {email}", True)


# ═══════════════════════════════════════════════════════
# §6b  GitHub OAuth 登录 (已注册账号)
# ═══════════════════════════════════════════════════════

def github_oauth_login():
    """
    GitHub OAuth登录已有Windsurf账号 — 获取auth快照
    用于: 已通过GitHub OAuth注册但未采集auth的账号
    """
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except ImportError:
        log("DrissionPage未安装", False)
        return None

    try:
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                       capture_output=True, timeout=5)
        time.sleep(2)
    except Exception:
        pass

    co = ChromiumOptions()
    co.set_browser_path(CHROME_EXE)
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    co.auto_port()
    co.headless(False)

    default_profile = Path(os.environ.get('LOCALAPPDATA', '')) / 'Google' / 'Chrome' / 'User Data'
    if default_profile.exists():
        co.set_argument(f'--user-data-dir={default_profile}')
        co.set_argument('--profile-directory=Default')

    proxy_port = detect_proxy()
    if proxy_port:
        co.set_argument(f'--proxy-server=127.0.0.1:{proxy_port}')

    page = ChromiumPage(co)
    try:
        # 导航到登录页
        login_url = "https://windsurf.com/account/login"
        log(f"导航: {login_url}")
        page.get(login_url)
        time.sleep(random.uniform(2, 4))

        # 找GitHub登录按钮
        github_btn = None
        for sel in [
            'tag:button@text():GitHub',
            'xpath://button[contains(text(),"GitHub")]',
            'xpath://a[contains(text(),"GitHub")]',
            'xpath://button[contains(.,"GitHub")]',
        ]:
            try:
                btn = page.ele(sel, timeout=3)
                if btn:
                    github_btn = btn
                    log(f"GitHub登录按钮: {sel}", True)
                    break
            except Exception:
                pass

        if not github_btn:
            log("未找到GitHub登录按钮", False)
            page.quit()
            return None

        github_btn.click()
        time.sleep(random.uniform(2, 4))

        # 处理GitHub OAuth流程
        start = time.time()
        MAX_WAIT = 120
        last_log = 0

        DONE_KW = ["dashboard", "welcome", "get started", "cascade",
                    "open windsurf", "your workspace", "settings", "account"]

        while time.time() - start < MAX_WAIT:
            url = page.url or ""
            body = (page.html or "").lower()
            elapsed = int(time.time() - start)

            # GitHub授权页
            if "github.com" in url and ("oauth" in url or "authorize" in url):
                log("GitHub OAuth授权页...")
                for sel in [
                    'css:button[name="authorize"]',
                    'css:input[value="Authorize"]',
                    'css:button[type=submit]',
                ]:
                    try:
                        btn = page.ele(sel, timeout=2)
                        if btn:
                            btn.click()
                            time.sleep(3)
                            break
                    except Exception:
                        pass
                continue

            # 登录成功
            if any(k in body for k in DONE_KW) and "windsurf.com" in url:
                log("GitHub OAuth登录成功!", True)
                time.sleep(3)

                # 尝试导航到设置页获取邮箱
                try:
                    page.get("https://windsurf.com/account/settings")
                    time.sleep(3)
                    settings_body = page.run_js("return document.body?.innerText || '';") or ""
                    emails = re.findall(r'[\w.-]+@[\w.-]+\.\w+', settings_body)
                    if emails:
                        email = emails[0]
                        log(f"设置页邮箱: {email}", True)
                    else:
                        email = _extract_email_from_page(page)
                except Exception:
                    email = None

                page.quit()

                if email:
                    log(f"邮箱: {email}")
                    # 尝试通过设置密码来完成激活
                    log("GitHub OAuth账号无密码, 需要:")
                    log("  1. 在 windsurf.com/account/settings 设置密码")
                    log("  2. 或在Windsurf桌面端登录后运行 harvest")
                    return {'email': email, 'status': 'logged_in', 'path': 'github_oauth_login'}
                else:
                    log("无法提取邮箱, 请在Windsurf桌面端登录后运行 harvest")
                    return {'status': 'logged_in_no_email', 'path': 'github_oauth_login'}

            if elapsed - last_log >= 15:
                log(f"等待... URL={url[:60]} ({elapsed}s)")
                last_log = elapsed

            time.sleep(2)

        log(f"登录超时({MAX_WAIT}s)", False)
        page.quit()
        return None

    except Exception as e:
        log(f"GitHub OAuth登录异常: {e}", False)
        try:
            page.quit()
        except Exception:
            pass
        return None


# ═══════════════════════════════════════════════════════
# §7  探测 (无副作用, 只读)
# ═══════════════════════════════════════════════════════

def probe():
    """探测当前环境状态"""
    print(f"\n{'═' * 65}")
    print(f"  突破引擎 · 环境探测 — {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 65}\n")

    # 网络
    proxy_port = detect_proxy()
    print(f"  [网络]")
    print(f"    代理: {'127.0.0.1:' + str(proxy_port) if proxy_port else '无'}")

    # Chrome
    chrome_ok = os.path.exists(CHROME_EXE)
    print(f"    Chrome: {'OK' if chrome_ok else 'MISSING'} ({CHROME_EXE})")

    # DrissionPage (subprocess check to avoid numpy hang)
    dp_ok = False
    try:
        r = subprocess.run(
            ['python', '-c', 'from DrissionPage import ChromiumPage; print("OK")'],
            capture_output=True, text=True, timeout=15,
            encoding='utf-8', errors='replace'
        )
        dp_ok = r.returncode == 0 and 'OK' in r.stdout
    except Exception:
        pass
    print(f"    DrissionPage: {'OK' if dp_ok else 'MISSING/BROKEN (numpy conflict?)'}")
    if not dp_ok:
        print(f"      修复: pip uninstall numpy -y && conda install numpy")

    # State DB
    db_ok = WS_STATE_DB.exists()
    print(f"    state.vscdb: {'OK' if db_ok else 'MISSING'}")

    # 当前Auth
    print(f"\n  [当前Windsurf Auth]")
    auth = read_current_auth()
    if auth:
        print(f"    Email: {auth.get('_email', '?')}")
        print(f"    idToken: {'OK (' + auth.get('codeium.firebaseIdToken', '')[:20] + '...)' if auth.get('codeium.firebaseIdToken') else 'MISSING'}")
        print(f"    apiKey: {auth.get('codeium.apiKey', 'MISSING')[:30]}...")
    else:
        print(f"    (无auth数据)")

    # Quota
    print(f"\n  [实时配额]")
    quota = read_proto_quota()
    if quota:
        for k, v in list(quota.items())[:8]:
            print(f"    {k}: {v}")
    else:
        print(f"    (无配额数据)")

    # 号池
    print(f"\n  [号池]")
    pool = load_pool()
    if pool:
        total = len(pool)
        sources = {}
        for a in pool:
            src = a.get('source', a.get('email', '').split('@')[-1])
            sources[src] = sources.get(src, 0) + 1
        print(f"    总计: {total}个")
        for src, cnt in sorted(sources.items(), key=lambda x: -x[1])[:5]:
            print(f"    {src}: {cnt}")
    else:
        print(f"    (空)")

    # Firebase API验证
    print(f"\n  [Firebase API]")
    for i, key in enumerate(FIREBASE_KEYS):
        try:
            r = http_json(f'https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={key}',
                          {'returnSecureToken': True}, timeout=8)
            ok = 'idToken' in r or 'error' in r
            msg = 'reachable' if ok else 'unknown'
            if r.get('error', {}).get('message'):
                msg = r['error']['message'][:40]
            print(f"    Key#{i}: {msg}")
        except Exception as e:
            print(f"    Key#{i}: error ({str(e)[:40]})")

    # 可行路径
    print(f"\n  [可行路径]")
    print(f"    P0 GitHub OAuth: {'OK' if (chrome_ok and dp_ok) else 'BLOCKED (需Chrome+DrissionPage)'}")
    print(f"    P1 Google OAuth: {'OK' if (chrome_ok and dp_ok) else 'BLOCKED'}")
    print(f"    P2 直接注册:     BLOCKED (一次性邮箱被封杀, Gmail别名只给free)")
    print(f"    P3 Yahoo:       需购买账号")

    print(f"\n  [推荐]")
    if chrome_ok and dp_ok:
        print(f"    立即运行: python _breakthrough_engine.py github")
        print(f"    验证OAuth是否获得pro_trial, 确认后可批量化")
    else:
        missing = []
        if not chrome_ok:
            missing.append("安装Chrome")
        if not dp_ok:
            missing.append("pip install DrissionPage")
        print(f"    先完成: {', '.join(missing)}")

    print(f"\n{'═' * 65}")


# ═══════════════════════════════════════════════════════
# §8  账号验证
# ═══════════════════════════════════════════════════════

def verify_account(email, password=None):
    """验证指定账号的plan状态"""
    log(f"验证: {email}")

    # 从号池找密码
    if not password:
        pool = load_pool()
        for a in pool:
            if a.get('email', '').lower() == email.lower():
                password = a.get('password', '')
                break

    if not password:
        log("无密码, 尝试从state.vscdb读取idToken...", False)
        auth = read_current_auth()
        if auth.get('_email', '').lower() == email.lower() and auth.get('codeium.firebaseIdToken'):
            id_token = auth['codeium.firebaseIdToken']
            plan_info = windsurf_plan_status(id_token)
            plan = plan_info.get('plan', 'unknown')
            log(f"Plan: {plan} ({', '.join(plan_info.get('raw', [])[:3])})",
                plan == 'pro_trial')
            return {'email': email, 'plan': plan, 'raw': plan_info.get('raw', [])}
        log("无密码且state.vscdb无匹配", False)
        return None

    # Firebase sign-in
    login = firebase_signin(email, password)
    id_token = login.get('idToken')
    if not id_token:
        log(f"Firebase登录失败: {login.get('error', '?')}", False)
        return {'email': email, 'plan': 'signin_failed'}

    # Plan验证
    plan_info = windsurf_plan_status(id_token)
    plan = plan_info.get('plan', 'unknown')
    raw = plan_info.get('raw', [])
    log(f"Plan: {plan} ({', '.join(raw[:3])})", plan == 'pro_trial')

    return {'email': email, 'plan': plan, 'raw': raw}


# ═══════════════════════════════════════════════════════
# §9  Auth快照采集
# ═══════════════════════════════════════════════════════

def harvest_current():
    """采集当前Windsurf活跃账号的auth快照"""
    log("采集当前auth快照...")
    auth = read_current_auth()
    if not auth.get('codeium.firebaseIdToken'):
        log("state.vscdb中无活跃auth", False)
        return None

    email = auth.get('_email', 'unknown')
    log(f"当前账号: {email}", True)

    # Plan验证
    id_token = auth['codeium.firebaseIdToken']
    plan_info = windsurf_plan_status(id_token)
    plan = plan_info.get('plan', 'unknown')
    log(f"Plan: {plan}", plan == 'pro_trial')

    # 保存快照
    _harvest_wam_snapshot(email, auth)

    # Quota
    quota = read_proto_quota()
    if quota:
        log(f"Quota fields: {quota}")

    return {
        'email': email,
        'plan': plan,
        'has_token': bool(auth.get('codeium.firebaseIdToken')),
        'has_apiKey': bool(auth.get('codeium.apiKey')),
    }


# ═══════════════════════════════════════════════════════
# §10  全局状态
# ═══════════════════════════════════════════════════════

def show_status():
    """显示全面的账号状态"""
    print(f"\n{'═' * 65}")
    print(f"  突破引擎 · 状态总览 — {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 65}\n")

    pool = load_pool()
    if not pool:
        print("  号池为空")
        return

    # 分类统计
    by_domain = {}
    by_plan = {}
    by_source = {}
    for a in pool:
        email = a.get('email', '')
        domain = email.split('@')[-1] if '@' in email else 'unknown'
        plan = a.get('usage', {}).get('plan', a.get('_verifiedPlan', '?'))
        source = a.get('source', '?')

        by_domain[domain] = by_domain.get(domain, 0) + 1
        by_plan[plan] = by_plan.get(plan, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1

    print(f"  总计: {len(pool)} 个账号\n")

    print(f"  [按域名]")
    for d, c in sorted(by_domain.items(), key=lambda x: -x[1]):
        print(f"    {d}: {c}")

    print(f"\n  [按Plan]")
    for p, c in sorted(by_plan.items(), key=lambda x: -x[1]):
        icon = "+" if p == 'pro_trial' else ("-" if p == 'free' else "*")
        print(f"    [{icon}] {p}: {c}")

    print(f"\n  [按来源]")
    for s, c in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"    {s}: {c}")

    # 突破结果
    if RESULTS_FILE.exists():
        try:
            results = json.loads(RESULTS_FILE.read_text(encoding='utf-8'))
            print(f"\n  [突破记录] ({len(results)}条)")
            for r in results[-5:]:
                print(f"    {r.get('email', '?')}: {r.get('status')} plan={r.get('plan')} path={r.get('path')} @ {r.get('timestamp', '?')[:19]}")
        except Exception:
            pass

    print(f"\n{'═' * 65}")


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()

    if cmd == 'probe':
        probe()

    elif cmd == 'login':
        result = github_oauth_login()
        if result:
            print(f"\n  结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
        else:
            print("\n  GitHub OAuth登录失败")

    elif cmd == 'github':
        use_default = '--incognito' not in sys.argv
        result = github_oauth_register(use_default_profile=use_default)
        if result:
            print(f"\n  结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
            if result.get('plan') == 'pro_trial':
                print(f"\n  PRO TRIAL确认! GitHub OAuth路径有效!")
                print(f"  下一步: 批量创建GitHub账号 → 批量OAuth注册")
            elif result.get('plan') == 'free':
                print(f"\n  Free plan — Claude不可用")
                print(f"  GitHub OAuth可能不给pro_trial, 需要其他方案")
            else:
                print(f"\n  Plan未确认: {result.get('plan')}")
                print(f"  可能需要等待Windsurf初始化后重新验证:")
                print(f"  python _breakthrough_engine.py verify {result.get('email', '')}")
        else:
            print("\n  GitHub OAuth失败")

    elif cmd == 'harvest':
        result = harvest_current()
        if result:
            print(f"\n  结果: {json.dumps(result, indent=2, ensure_ascii=False)}")

    elif cmd == 'verify':
        email = sys.argv[2] if len(sys.argv) >= 3 else None
        if not email:
            # 验证当前活跃账号
            auth = read_current_auth()
            email = auth.get('_email', '')
        if email:
            result = verify_account(email)
            if result:
                print(f"\n  结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
        else:
            print("用法: python _breakthrough_engine.py verify EMAIL")

    elif cmd == 'status':
        show_status()

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == '__main__':
    main()
