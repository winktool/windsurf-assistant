#!/usr/bin/env python3
"""
Run unlimited engine with output to file.
Usage: python _run_unlimited.py
"""
import sys, os, io, traceback, json, time, ssl, urllib.request, struct, re
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
LOG = SCRIPT_DIR / '_unlimited_log.txt'

# Prevent concurrent runs
LOCK = SCRIPT_DIR / '_unlimited.lock'
import atexit
if LOCK.exists():
    try:
        pid = int(LOCK.read_text().strip())
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if h:
            k32.CloseHandle(h)
            print(f'Another instance running (pid={pid}). Exiting.')
            sys.exit(1)
    except Exception:
        pass
LOCK.write_text(str(os.getpid()))
def _cleanup():
    try: LOCK.unlink(missing_ok=True)
    except: pass
atexit.register(_cleanup)

# Output
_f = open(LOG, 'w', encoding='utf-8', buffering=1)

def out(msg=''):
    _f.write(msg + '\n')
    _f.flush()

# Config
WS_APPDATA = Path(os.environ.get('APPDATA', '')) / 'Windsurf'
ASST_FILE = WS_APPDATA / 'User' / 'globalStorage' / 'windsurf-assistant-accounts.json'  # 70 ak, primary
POOL_FILE = WS_APPDATA / 'User' / 'globalStorage' / 'windsurf-login-accounts.json'
WAM_FILE  = WS_APPDATA / 'User' / 'globalStorage' / 'local.wam' / 'windsurf-login-accounts.json'
YAHOO_FILE = SCRIPT_DIR.parent / 'data' / '账号.txt'
RESULT_FILE = SCRIPT_DIR / '_deep_audit_result.json'

FIREBASE_KEYS = ['AIzaSyDsOl-1XpT5err0Tcnx8FFod1H8gVGIycY', 'AIzaSyDKm6GGxMJfCbNf-k0kPytiGLaqFJpeSac']
REGISTER_URLS = [
    'https://register.windsurf.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
    'https://server.codeium.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
    'https://web-backend.windsurf.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
]
PLAN_STATUS_URLS = [
    'https://web-backend.windsurf.com/exa.seat_management_pb.SeatManagementService/GetPlanStatus',
]

TARGET_POOL_SIZE = 30

# SSL
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

def detect_proxy():
    for port in [7890, 7891, 1080]:
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(('127.0.0.1', port))
            s.close()
            return port
        except Exception:
            continue
    return 0

def http_json(url, payload=None, use_proxy=False, timeout=12):
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    handlers = [urllib.request.HTTPSHandler(context=_ssl_ctx)]
    if use_proxy:
        port = detect_proxy()
        if port:
            handlers.append(urllib.request.ProxyHandler({'https': f'http://127.0.0.1:{port}', 'http': f'http://127.0.0.1:{port}'}))
    opener = urllib.request.build_opener(*handlers)
    try:
        resp = opener.open(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')
        try:
            return json.loads(body)
        except Exception:
            return {'error': body[:200]}
    except Exception as e:
        return {'error': str(e)[:200]}

def http_bin(url, buf, use_proxy=True, timeout=15):
    req = urllib.request.Request(url, data=buf, method='POST')
    req.add_header('Content-Type', 'application/grpc-web+proto')
    req.add_header('Accept', 'application/grpc-web+proto')
    req.add_header('x-grpc-web', '1')
    port = detect_proxy() if use_proxy else 0
    handlers = [urllib.request.HTTPSHandler(context=_ssl_ctx)]
    if port > 0:
        handlers.append(urllib.request.ProxyHandler({'https': f'http://127.0.0.1:{port}', 'http': f'http://127.0.0.1:{port}'}))
    resp = urllib.request.build_opener(*handlers).open(req, timeout=timeout)
    return resp.read()

def write_varint(val):
    b = bytearray()
    while val > 0x7F:
        b.append((val & 0x7F) | 0x80)
        val >>= 7
    b.append(val & 0x7F)
    return bytes(b)

def read_varint(data, pos):
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos

def encode_proto(value, field=1):
    """Encode a string into gRPC-web framed protobuf"""
    b = value.encode('utf-8')
    tag = (field << 3) | 2
    inner = bytes([tag]) + write_varint(len(b)) + b
    return struct.pack('>BI', 0, len(inner)) + inner

def extract_proto_strings(buf):
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

def firebase_signin(email, pw):
    payload = {'email': email, 'password': pw, 'returnSecureToken': True}
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}'
        for use_p in [True, False]:
            try:
                r = http_json(url, payload, use_proxy=use_p, timeout=12)
                if r.get('idToken'):
                    return r
            except Exception:
                continue
    return {'error': 'signin_failed'}

def register_user(id_token):
    buf = encode_proto(id_token)
    for url in REGISTER_URLS:
        for use_p in [True, False]:
            try:
                resp = http_bin(url, buf, use_proxy=use_p, timeout=15)
                if resp and len(resp) > 5:
                    # Skip 5-byte gRPC frame header
                    body = resp[5:] if resp[0] == 0 else resp
                    strings = extract_proto_strings(body)
                    for fn, s in strings:
                        if len(s) > 20 and s.isascii():
                            return {'ok': True, 'apiKey': s}
            except Exception:
                continue
    return {'ok': False}

def plan_status(id_token):
    buf = encode_proto(id_token)
    for url in PLAN_STATUS_URLS:
        for use_p in [True, False]:
            try:
                resp = http_bin(url, buf, use_proxy=use_p, timeout=10)
                if resp and len(resp) > 5:
                    body = resp[5:] if resp[0] == 0 else resp
                    strings = extract_proto_strings(body)
                    plan = 'no_response'
                    for fn, s in strings:
                        sl = s.lower().strip()
                        if sl in ('pro_trial', 'trial'):
                            plan = 'pro_trial'
                            break
                        if 'trial' in sl:
                            plan = 'pro_trial'
                            break
                        if sl == 'free':
                            plan = 'free'
                    if plan == 'no_response' and strings:
                        plan = 'unknown'
                    return {'ok': True, 'plan': plan}
            except Exception:
                continue
    return {'ok': False, 'plan': 'unreachable'}

def load_pool():
    """Load pool from the file with the MOST accounts (avoid partial writes)"""
    best = []
    for fp in [ASST_FILE, POOL_FILE, WAM_FILE]:
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding='utf-8'))
                if isinstance(data, list) and len(data) > len(best):
                    best = data
            except Exception:
                pass
    return best

def save_pool(pool):
    for fp in [ASST_FILE, POOL_FILE, WAM_FILE]:
        if fp.exists():
            try:
                fp.write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass

def parse_yahoo():
    if not YAHOO_FILE.exists():
        return []
    text = YAHOO_FILE.read_text(encoding='utf-8')
    accounts = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Format: 邮箱：xxx\n密码：xxx
        if line.startswith('邮箱') and ':' in line:
            email = line.split(':', 1)[-1].split('：', 1)[-1].strip()
            if i + 1 < len(lines):
                pw_line = lines[i + 1].strip()
                if pw_line.startswith('密码') and (':' in pw_line or '：' in pw_line):
                    pw = pw_line.split(':', 1)[-1].split('：', 1)[-1].strip()
                    if '@yahoo' in email.lower():
                        accounts.append((email, pw))
                    i += 2
                    continue
        # Format: email----password
        if '----' in line and '@' in line:
            parts = line.split('----', 1)
            if len(parts) == 2 and '@' in parts[0]:
                accounts.append((parts[0].strip(), parts[1].strip()))
        # Format: email\npassword (consecutive lines)
        elif '@yahoo' in line.lower() and not line.startswith(('#', '卡', 'Hi')):
            if i + 1 < len(lines):
                pw = lines[i + 1].strip()
                if pw and not pw.startswith(('邮箱', '卡', 'Hi', '#', '@')):
                    accounts.append((line, pw))
                    i += 2
                    continue
        i += 1
    return accounts

def activate_one(email, pw):
    """Firebase sign-in → Register → get apiKey"""
    login = firebase_signin(email, pw)
    tok = login.get('idToken')
    if not tok:
        return {'error': 'signin_failed'}
    reg = register_user(tok)
    if reg.get('apiKey'):
        return {'ok': True, 'apiKey': reg['apiKey'], 'idToken': tok}
    return {'ok': False, 'error': 'register_failed'}

# ═══════════════════════════════════════════════════════
# Main engine
# ═══════════════════════════════════════════════════════

out('[START] Unlimited Engine')
out(f'[TIME] {datetime.now(CST).isoformat()}')

try:
    pool = load_pool()
    real = [a for a in pool if '@' in a.get('email', '')
            and 'example' not in a.get('email', '') and a.get('password')]
    out(f'[POOL] {len(pool)} total, {len(real)} with password')

    # Phase A: Deep audit
    out(f'\n{"▓" * 70}')
    out(f'  Phase A: Deep Audit — {len(real)} accounts')
    out(f'{"▓" * 70}')

    stats = {'pro_trial': [], 'free': [], 'fail': [], 'new': []}

    for i, a in enumerate(real):
        email = a.get('email', '')
        pw = a.get('password', '')
        ts = datetime.now(CST).strftime('%H:%M:%S')

        try:
            login = firebase_signin(email, pw)
            tok = login.get('idToken')
            if not tok:
                stats['fail'].append(email)
                out(f'  [{ts}][-] {i+1:2}/{len(real)} {email[:42]:42} SIGNIN_FAIL')
                continue

            ps = plan_status(tok, debug=(i < 3))
            plan = ps.get('plan', 'unknown')
            if plan in ('pro_trial', 'unknown', 'unreachable'):
                stats['pro_trial'].append(email)
            elif plan == 'free':
                stats['free'].append(email)
            else:
                stats['fail'].append(email)

            tag = ''
            if not a.get('apiKey'):
                reg = register_user(tok)
                if reg.get('apiKey'):
                    a['apiKey'] = reg['apiKey']
                    stats['new'].append(email)
                    tag = ' *NEW*'

            icon = '+' if plan == 'pro_trial' else '-'
            out(f'  [{ts}][{icon}] {i+1:2}/{len(real)} {email[:42]:42} {plan:12}{tag}')
        except Exception as exc:
            stats['fail'].append(email)
            out(f'  [{ts}][!] {i+1:2}/{len(real)} {email[:42]:42} ERROR: {str(exc)[:50]}')
        time.sleep(0.3)

    save_pool(pool)

    # Save audit result
    RESULT_FILE.write_text(json.dumps({
        'timestamp': datetime.now(CST).isoformat(),
        'total': len(real),
        'pro_trial': stats['pro_trial'],
        'free': stats['free'],
        'fail': stats['fail'],
        'new': stats['new'],
    }, indent=2, ensure_ascii=False), encoding='utf-8')

    active = len(stats['pro_trial'])
    out(f'\n  Audit: {active} pro_trial | {len(stats["free"])} free | {len(stats["fail"])} fail | {len(stats["new"])} new')

    # Phase B: Check deficit
    deficit = max(0, TARGET_POOL_SIZE - active)
    if deficit == 0:
        out(f'\n  [DONE] Pool healthy: {active} >= {TARGET_POOL_SIZE}')
        out(f'\n{"▓" * 70}')
        out(f'  无限引擎完成 — 号池已满')
        out(f'{"▓" * 70}')
    else:
        out(f'\n  [DEFICIT] Need {deficit} more ({active}/{TARGET_POOL_SIZE})')

        # Source 1: Harvest 账号.txt
        out(f'\n  [Source 1] 账号.txt harvest')
        yahoo = parse_yahoo()
        pool_emails = {a.get('email', '').lower() for a in pool if a.get('apiKey')}
        fresh = [(e, p) for e, p in yahoo if e.lower() not in pool_emails]
        out(f'    Fresh Yahoo: {len(fresh)}')

        total_new = 0
        for email, pw in fresh:
            if total_new >= deficit:
                break
            ts = datetime.now(CST).strftime('%H:%M:%S')
            r = activate_one(email, pw)
            if r.get('apiKey'):
                # Inject into pool
                entry = {'email': email, 'password': pw, 'apiKey': r['apiKey'],
                         'usage': {'plan': 'pro_trial'}}
                pool.append(entry)
                total_new += 1
                out(f'  [{ts}][+] S1 {total_new}/{deficit} {email[:40]} — ACTIVATED')
            else:
                out(f'  [{ts}][-] S1 {email[:40]} — {r.get("error", "?")}')
            time.sleep(1)

        if total_new > 0:
            save_pool(pool)

        out(f'    Source 1: {total_new} activated')
        out(f'\n{"▓" * 70}')
        out(f'  无限引擎完成')
        out(f'  活跃pro_trial: {active + total_new}')
        out(f'  本次新增: {total_new}')
        out(f'{"▓" * 70}')

except Exception:
    out(f'[ERROR]\n{traceback.format_exc()}')
finally:
    out(f'[END] {datetime.now(CST).isoformat()}')
    _f.close()
