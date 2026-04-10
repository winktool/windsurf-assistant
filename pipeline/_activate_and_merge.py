#!/usr/bin/env python3
"""
激活+原子合并 — 两阶段策略
Phase A: signIn→RegisterUser→保存apiKey到独立结果文件(不会被覆盖)
Phase B: 读取号池→合并结果→写回号池→立即验证→失败则重试
"""
import json, os, sys, time, ssl, socket, shutil

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen, ProxyHandler, build_opener
from urllib.error import HTTPError

CST = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).parent

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

WS = Path(os.environ.get('APPDATA', '')) / 'Windsurf' / 'User' / 'globalStorage'
POOL_FILE = WS / 'windsurf-login-accounts.json'
RESULTS_FILE = SCRIPT_DIR / '_activated_keys.json'

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE
_proxy = None


def log(msg, ok=None):
    icon = "+" if ok is True else ("-" if ok is False else "*")
    ts = datetime.now(CST).strftime("%H:%M:%S")
    print(f"  [{ts}][{icon}] {msg}")


def proxy():
    global _proxy
    if _proxy is not None:
        return _proxy
    for port in PROXY_PORTS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(('127.0.0.1', port))
            s.close()
            _proxy = port
            return port
        except Exception:
            pass
    _proxy = 0
    return 0


def post_json(url, data, use_proxy=True, timeout=12):
    body = json.dumps(data).encode('utf-8')
    req = Request(url, data=body, method='POST')
    req.add_header('Content-Type', 'application/json')
    p = proxy() if use_proxy else 0
    try:
        if p > 0:
            h = ProxyHandler({'https': f'http://127.0.0.1:{p}', 'http': f'http://127.0.0.1:{p}'})
            resp = build_opener(h).open(req, timeout=timeout)
        else:
            resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
        return json.loads(resp.read())
    except HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {'error': f'HTTP {e.code}'}
    except Exception as e:
        return {'error': str(e)}


def post_bin(url, data, use_proxy=True, timeout=15):
    req = Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/proto')
    req.add_header('Accept', 'application/proto')
    req.add_header('connect-protocol-version', '1')
    p = proxy() if use_proxy else 0
    if p > 0:
        h = ProxyHandler({'https': f'http://127.0.0.1:{p}', 'http': f'http://127.0.0.1:{p}'})
        resp = build_opener(h).open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
    return resp.read()


def encode_proto(value, field=1):
    b = value.encode('utf-8')
    tag = (field << 3) | 2
    ln = len(b)
    lb = bytearray()
    while ln > 127:
        lb.append((ln & 0x7f) | 0x80)
        ln >>= 7
    lb.append(ln)
    return bytes([tag]) + bytes(lb) + b


def parse_proto_str(buf):
    if not buf or len(buf) < 3 or buf[0] != 0x0a:
        return None
    pos = 1
    ln = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]; pos += 1
        ln |= (b & 0x7f) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
    if pos + ln > len(buf):
        return None
    return buf[pos:pos + ln].decode('utf-8', errors='replace')


def signin(email, password):
    payload = {'email': email, 'password': password, 'returnSecureToken': True}
    for key in FIREBASE_KEYS:
        url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}'
        for use_p in [True, False]:
            r = post_json(url, payload, use_proxy=use_p)
            if r.get('idToken'):
                return r
    return {'error': 'signin_failed'}


def register_user(id_token):
    buf = encode_proto(id_token)
    for url in REGISTER_URLS:
        for use_p in [True, False]:
            try:
                resp = post_bin(url, buf, use_proxy=use_p)
                key = parse_proto_str(resp)
                if key and len(key) > 50:
                    return key
            except Exception:
                pass
    return None


def load_results():
    if RESULTS_FILE.exists():
        try:
            return json.loads(RESULTS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}


def save_result(email, api_key):
    results = load_results()
    results[email.lower()] = {
        'apiKey': api_key,
        'activatedAt': datetime.now(CST).isoformat(),
    }
    RESULTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')


# ═══════════════════════════════════════════════════════
# Phase A: 激活并保存到独立文件
# ═══════════════════════════════════════════════════════

def phase_a():
    print("\n" + "=" * 60)
    print("  Phase A · signIn → RegisterUser → 保存到独立结果文件")
    print("=" * 60)

    pool = json.loads(POOL_FILE.read_text(encoding='utf-8'))
    existing = load_results()
    targets = [
        a for a in pool
        if a.get('password') and not a.get('apiKey')
        and a.get('email', '').lower() not in existing
    ]

    log(f"池: {len(pool)}个 | 待激活: {len(targets)}个 | 已有结果: {len(existing)}个")

    if not targets:
        log("无新目标 (可能已全部激活到结果文件)", True)
        return

    ok = 0
    for i, acct in enumerate(targets):
        email = acct['email']
        pw = acct['password']
        log(f"[{i+1}/{len(targets)}] {email}")

        r = signin(email, pw)
        id_token = r.get('idToken')
        if not id_token:
            err = r.get('error', {})
            if isinstance(err, dict):
                err = err.get('message', str(err))
            log(f"  signIn失败: {err}", False)
            time.sleep(1)
            continue

        api_key = register_user(id_token)
        if not api_key:
            log(f"  RegisterUser失败", False)
            time.sleep(1)
            continue

        save_result(email, api_key)
        log(f"  apiKey: {api_key[:35]}... (已存结果文件)", True)
        ok += 1
        time.sleep(0.5)

    log(f"Phase A: {ok}/{len(targets)}成功, 结果在 {RESULTS_FILE.name}")


# ═══════════════════════════════════════════════════════
# Phase B: 合并结果到号池 (带重试)
# ═══════════════════════════════════════════════════════

def phase_b():
    print("\n" + "=" * 60)
    print("  Phase B · 合并apiKey到号池文件 (原子操作+重试验证)")
    print("=" * 60)

    results = load_results()
    if not results:
        log("无结果可合并", False)
        return

    log(f"待合并: {len(results)}个apiKey")

    MAX_RETRIES = 5
    for attempt in range(1, MAX_RETRIES + 1):
        # 读取当前号池
        pool = json.loads(POOL_FILE.read_text(encoding='utf-8'))

        # 合并
        merged = 0
        for a in pool:
            email = a.get('email', '').lower()
            if email in results and not a.get('apiKey'):
                a['apiKey'] = results[email]['apiKey']
                a['_activatedBy'] = 'activate_merge'
                a['_activatedAt'] = results[email]['activatedAt']
                merged += 1

        if merged == 0:
            # 检查是否所有结果都已经在池里了
            already = sum(1 for a in pool if a.get('email', '').lower() in results and a.get('apiKey'))
            log(f"无新合并 ({already}个已有apiKey)", True)
            break

        # 备份
        backup = POOL_FILE.with_suffix('.json.bak')
        shutil.copy2(POOL_FILE, backup)

        # 写入
        POOL_FILE.write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding='utf-8')

        # 等一小段时间让文件系统刷新
        time.sleep(0.3)

        # 验证
        verify = json.loads(POOL_FILE.read_text(encoding='utf-8'))
        verified = sum(1 for a in verify if a.get('email', '').lower() in results and a.get('apiKey'))

        if verified >= len(results):
            log(f"尝试{attempt}: 合并{merged}个, 验证{verified}/{len(results)}通过", True)
            break
        else:
            log(f"尝试{attempt}: 合并{merged}个, 验证{verified}/{len(results)} — 文件被覆盖,重试...", False)
            time.sleep(2)
    else:
        log(f"合并{MAX_RETRIES}次尝试均被覆盖", False)
        log(f"apiKey安全存储在: {RESULTS_FILE}")
        log(f"建议: 关闭Windsurf后手动运行 phase_b")

    # 最终统计
    final = json.loads(POOL_FILE.read_text(encoding='utf-8'))
    keys = len([a for a in final if a.get('apiKey')])
    log(f"号池: {len(final)}个 | {keys}个有apiKey ({keys*100//len(final)}%)")


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'

    if cmd == 'a':
        phase_a()
    elif cmd == 'b':
        phase_b()
    elif cmd == 'all':
        phase_a()
        phase_b()
    elif cmd == 'status':
        results = load_results()
        pool = json.loads(POOL_FILE.read_text(encoding='utf-8'))
        in_pool = sum(1 for a in pool if a.get('email', '').lower() in results and a.get('apiKey'))
        print(f"结果文件: {len(results)}个apiKey")
        print(f"已在号池: {in_pool}/{len(results)}")
        for email, data in results.items():
            k = data['apiKey'][:30] + '...'
            in_p = any(a.get('email', '').lower() == email and a.get('apiKey') for a in pool)
            tag = 'IN_POOL' if in_p else 'NOT_IN_POOL'
            print(f"  [{tag}] {email} → {k}")
    else:
        print("用法: python _activate_and_merge.py [all|a|b|status]")


if __name__ == '__main__':
    main()
