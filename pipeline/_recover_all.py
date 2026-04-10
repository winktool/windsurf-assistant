#!/usr/bin/env python3
"""
找回一切 · Recover All — 逆流推进到底
======================================
Phase 1: 激活11个有密码无apiKey的账号 (signIn → RegisterUser → inject)
Phase 2: 对45个无密码账号发送Firebase密码重置邮件
Phase 3: 重置后批量激活

用法:
  python _recover_all.py phase1          # 激活有密码无Key的
  python _recover_all.py phase2          # 发送密码重置邮件
  python _recover_all.py phase3 EMAIL PW # 重置密码后激活单个
  python _recover_all.py status          # 查看全景状态
"""

import json, os, sys, time, ssl, socket, re, string, random

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

CST = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).parent

# Firebase常量
FIREBASE_KEYS = [
    'AIzaSyDsOl-1XpT5err0Tcnx8FFod1H8gVGIycY',
    'AIzaSyDKm6GGxMJfCbNf-k0kPytiGLaqFJpeSac',
]

REGISTER_URLS = [
    'https://register.windsurf.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
    'https://server.codeium.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
    'https://web-backend.windsurf.com/exa.seat_management_pb.SeatManagementService/RegisterUser',
]

PROXY_PORTS = [7890, 7897, 7891, 10808, 1080]

# 号池路径
WS_APPDATA = Path(os.environ.get('APPDATA', '')) / 'Windsurf'
WS_GLOBALSTORE = WS_APPDATA / 'User' / 'globalStorage'
POOL_PATHS = [
    WS_GLOBALSTORE / 'zhouyoukang.windsurf-assistant' / 'windsurf-login-accounts.json',
    WS_GLOBALSTORE / 'undefined_publisher.windsurf-login-helper' / 'windsurf-login-accounts.json',
    WS_GLOBALSTORE / 'windsurf-login-accounts.json',
]

# ═══════════════════════════════════════════════════════
# 工具层
# ═══════════════════════════════════════════════════════

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE
_proxy_cache = None


def log(msg, ok=None):
    icon = "✓" if ok is True else ("✗" if ok is False else "·")
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
        except Exception:
            continue
    _proxy_cache = 0
    return 0


def http_json(url, data=None, method='POST', use_proxy=True, timeout=15):
    body = json.dumps(data).encode('utf-8') if data else None
    req = Request(url, data=body, method=method)
    req.add_header('Content-Type', 'application/json')
    proxy_port = detect_proxy() if use_proxy else 0
    try:
        if proxy_port > 0:
            handler = ProxyHandler({
                'https': f'http://127.0.0.1:{proxy_port}',
                'http': f'http://127.0.0.1:{proxy_port}'
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
        except Exception:
            raise RuntimeError(f"HTTP {e.code}: {body[:300]}")


def http_bin(url, bin_data, use_proxy=True, timeout=15):
    req = Request(url, data=bin_data, method='POST')
    req.add_header('Content-Type', 'application/proto')
    req.add_header('Accept', 'application/proto')
    req.add_header('connect-protocol-version', '1')
    proxy_port = detect_proxy() if use_proxy else 0
    if proxy_port > 0:
        handler = ProxyHandler({
            'https': f'http://127.0.0.1:{proxy_port}',
            'http': f'http://127.0.0.1:{proxy_port}'
        })
        opener = build_opener(handler)
        resp = opener.open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    return resp.read()


# ═══════════════════════════════════════════════════════
# Protobuf 最小编解码
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
        result |= (b & 0x7f) << shift
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


# ═══════════════════════════════════════════════════════
# Firebase Auth
# ═══════════════════════════════════════════════════════

def firebase_signin(email: str, password: str) -> dict:
    """Firebase signIn → idToken"""
    payload = {'email': email, 'password': password, 'returnSecureToken': True}
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


def firebase_send_password_reset(email: str) -> dict:
    """Firebase发送密码重置邮件"""
    payload = {'requestType': 'PASSWORD_RESET', 'email': email}
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={key}'
        for use_p in [True, False]:
            try:
                r = http_json(url, payload, use_proxy=use_p, timeout=12)
                if r.get('email'):
                    return {'ok': True, 'email': r['email']}
            except Exception as e:
                last_err = str(e)
                continue
    return {'ok': False, 'error': last_err if 'last_err' in dir() else 'all_failed'}


# ═══════════════════════════════════════════════════════
# Windsurf RegisterUser
# ═══════════════════════════════════════════════════════

def windsurf_register_user(id_token: str) -> dict:
    """RegisterUser → apiKey"""
    buf = encode_proto(id_token)
    for url in REGISTER_URLS:
        for use_p in [True, False]:
            try:
                resp = http_bin(url, buf, use_proxy=use_p, timeout=15)
                api_key = parse_proto_str(resp)
                if api_key and len(api_key) > 50:
                    return {'ok': True, 'apiKey': api_key}
            except Exception:
                continue
    return {'ok': False, 'error': 'all_register_channels_failed'}


# ═══════════════════════════════════════════════════════
# 号池操作
# ═══════════════════════════════════════════════════════

def load_pool():
    """加载最大的号池"""
    best = []
    best_path = None
    for fp in POOL_PATHS:
        if fp.exists():
            try:
                d = json.loads(fp.read_text(encoding='utf-8'))
                if isinstance(d, list) and len(d) > len(best):
                    best = d
                    best_path = fp
            except Exception:
                pass
    return best, best_path


def save_pool(data, path):
    """保存号池"""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def inject_apikey(pool, email, api_key):
    """将apiKey注入号池中匹配的账号"""
    for a in pool:
        if a.get('email', '').lower() == email.lower():
            a['apiKey'] = api_key
            a['_activatedBy'] = 'recover_all'
            a['_activatedAt'] = datetime.now(CST).isoformat()
            return True
    return False


# ═══════════════════════════════════════════════════════
# Phase 1: 激活有密码无apiKey的账号
# ═══════════════════════════════════════════════════════

def phase1():
    print("\n" + "=" * 60)
    print("  Phase 1 · 激活有密码无apiKey的账号")
    print("  signIn → idToken → RegisterUser → apiKey → inject")
    print("=" * 60)

    pool, pool_path = load_pool()
    if not pool:
        log("号池为空", False)
        return

    targets = [a for a in pool if a.get('password') and not a.get('apiKey')]
    log(f"号池: {len(pool)}个 | 目标: {len(targets)}个有密码无apiKey")

    if not targets:
        log("没有需要激活的账号", True)
        return

    proxy = detect_proxy()
    log(f"代理: {'127.0.0.1:' + str(proxy) if proxy else '无代理'}")

    success = 0
    fail = 0
    for i, acct in enumerate(targets):
        email = acct['email']
        pw = acct['password']
        log(f"[{i+1}/{len(targets)}] {email}")

        # Step 1: signIn
        login = firebase_signin(email, pw)
        id_token = login.get('idToken')
        if not id_token:
            err = login.get('error', {})
            if isinstance(err, dict):
                err = err.get('message', str(err))
            log(f"  signIn失败: {err}", False)
            fail += 1
            time.sleep(1)
            continue

        log(f"  signIn成功, idToken长度={len(id_token)}", True)

        # Step 2: RegisterUser
        reg = windsurf_register_user(id_token)
        api_key = reg.get('apiKey')
        if not api_key:
            log(f"  RegisterUser失败: {reg.get('error','?')}", False)
            fail += 1
            time.sleep(1)
            continue

        log(f"  apiKey: {api_key[:30]}...", True)

        # Step 3: inject
        inject_apikey(pool, email, api_key)
        success += 1
        time.sleep(0.5)

    # 保存
    save_pool(pool, pool_path)
    log(f"\n  Phase 1 完成: {success}成功 / {fail}失败 / {len(targets)}总计", success > 0)

    # 同步到其他池文件
    for fp in POOL_PATHS:
        if fp.exists() and fp != pool_path:
            try:
                fp.write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding='utf-8')
                log(f"  同步: {fp.parent.name}/{fp.name}", True)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════
# Phase 2: 对无密码账号发送Firebase密码重置
# ═══════════════════════════════════════════════════════

def phase2():
    print("\n" + "=" * 60)
    print("  Phase 2 · 对无密码账号发送Firebase密码重置邮件")
    print("=" * 60)

    pool, _ = load_pool()
    targets = [a for a in pool if not a.get('password') and not a.get('apiKey')]
    log(f"号池: {len(pool)}个 | 目标: {len(targets)}个无密码无apiKey")

    if not targets:
        log("没有需要重置的账号", True)
        return

    # 按域名分组分析
    yahoo = [a for a in targets if 'yahoo.com' in a.get('email', '')]
    shop = [a for a in targets if 'yahoo.com' not in a.get('email', '')]
    log(f"  Yahoo邮箱: {len(yahoo)}个 (可收重置邮件)")
    log(f"  Shop域名: {len(shop)}个 (无法收重置邮件, 跳过)")

    if not yahoo:
        log("没有Yahoo邮箱可以重置", False)
        return

    sent = 0
    failed = 0
    for i, acct in enumerate(yahoo):
        email = acct['email']
        log(f"[{i+1}/{len(yahoo)}] 发送重置邮件: {email}")

        result = firebase_send_password_reset(email)
        if result.get('ok'):
            log(f"  已发送重置邮件到 {email}", True)
            sent += 1
        else:
            log(f"  发送失败: {result.get('error','?')}", False)
            failed += 1

        # 避免限流
        time.sleep(1.5)

    print()
    log(f"Phase 2 完成: {sent}封已发送 / {failed}封失败")
    if sent > 0:
        print()
        print("  ┌─────────────────────────────────────────────────┐")
        print("  │ 下一步:                                          │")
        print("  │ 1. 登录各Yahoo邮箱收取重置邮件                    │")
        print("  │ 2. 点击重置链接设置新密码                         │")
        print("  │ 3. 运行: python _recover_all.py phase3 EMAIL PW  │")
        print("  │    或批量: python _recover_all.py phase3-batch    │")
        print("  └─────────────────────────────────────────────────┘")


# ═══════════════════════════════════════════════════════
# Phase 3: 重置密码后激活单个/批量
# ═══════════════════════════════════════════════════════

def phase3_single(email, password):
    print(f"\n  Phase 3 · 激活: {email}")

    pool, pool_path = load_pool()

    # signIn
    login = firebase_signin(email, password)
    id_token = login.get('idToken')
    if not id_token:
        err = login.get('error', {})
        if isinstance(err, dict):
            err = err.get('message', str(err))
        log(f"signIn失败: {err}", False)
        return False

    log(f"signIn成功", True)

    # RegisterUser
    reg = windsurf_register_user(id_token)
    api_key = reg.get('apiKey')
    if not api_key:
        log(f"RegisterUser失败", False)
        return False

    log(f"apiKey: {api_key[:30]}...", True)

    # 更新号池
    found = False
    for a in pool:
        if a.get('email', '').lower() == email.lower():
            a['password'] = password
            a['apiKey'] = api_key
            a['_activatedBy'] = 'recover_phase3'
            a['_activatedAt'] = datetime.now(CST).isoformat()
            found = True
            break

    if not found:
        # 新增
        pool.append({
            'email': email,
            'password': password,
            'apiKey': api_key,
            'source': 'recover_phase3',
            'addedAt': datetime.now(CST).isoformat(),
            'usage': {'plan': 'pro_trial', 'mode': 'quota'},
        })

    save_pool(pool, pool_path)
    for fp in POOL_PATHS:
        if fp.exists() and fp != pool_path:
            try:
                fp.write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass

    log(f"已注入号池", True)
    return True


# ═══════════════════════════════════════════════════════
# Status: 全景状态
# ═══════════════════════════════════════════════════════

def status():
    print("\n" + "=" * 60)
    print("  号池全景状态")
    print("=" * 60)

    pool, pool_path = load_pool()
    if not pool:
        log("号池为空", False)
        return

    total = len(pool)
    has_pw = len([a for a in pool if a.get('password')])
    has_key = len([a for a in pool if a.get('apiKey')])
    no_pw = len([a for a in pool if not a.get('password')])
    no_key = len([a for a in pool if not a.get('apiKey')])
    pw_no_key = len([a for a in pool if a.get('password') and not a.get('apiKey')])
    no_pw_no_key = len([a for a in pool if not a.get('password') and not a.get('apiKey')])

    from collections import Counter
    domains = Counter()
    for a in pool:
        e = a.get('email', '')
        d = e.split('@')[-1] if '@' in e else '?'
        domains[d] += 1

    domain_keys = Counter()
    for a in pool:
        if a.get('apiKey'):
            e = a.get('email', '')
            d = e.split('@')[-1] if '@' in e else '?'
            domain_keys[d] += 1

    print(f"""
  总计: {total} 账号
  ├── 有apiKey:     {has_key} ({has_key*100//total}%) ← 可用
  ├── 有密码无Key:  {pw_no_key} ← Phase 1可激活
  ├── 无密码无Key:  {no_pw_no_key} ← Phase 2重置后激活
  └── 域名分布:""")

    for d, c in domains.most_common():
        kc = domain_keys.get(d, 0)
        print(f"      {d}: {c}个 (已激活{kc})")

    print(f"\n  池文件: {pool_path}")


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()

    if cmd == 'status':
        status()
    elif cmd == 'phase1':
        phase1()
    elif cmd == 'phase2':
        phase2()
    elif cmd == 'phase3':
        if len(sys.argv) < 4:
            print("用法: python _recover_all.py phase3 EMAIL PASSWORD")
            return
        phase3_single(sys.argv[2], sys.argv[3])
    elif cmd == 'all':
        phase1()
        phase2()
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == '__main__':
    main()
