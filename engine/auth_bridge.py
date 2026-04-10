#!/usr/bin/env python3
"""
Auth Bridge v1.0 — Firebase → idToken → Extension Injection
============================================================
道生一: 一个函数完成换号

调用链:
  switch_account(email, password)
    → firebase_login(email, password) → idToken
    → write oneshot_token.json
    → wait for extension to inject (poll inject_result.json)
    → return (ok, account_label, apiKey_preview)

依赖: 只需 Python stdlib (ssl, urllib, json, os)
被调用方: wam_engine.py, pool_engine.py, CLI
"""

import json, os, ssl, time, socket
from pathlib import Path
from urllib.request import Request, urlopen, ProxyHandler, build_opener

# ── Config ──
FIREBASE_KEYS = [
    'AIzaSyDsOl-1XpT5err0Tcnx8FFod1H8gVGIycY',
    'AIzaSyDKm6GGxMJfCbNf-k0kPytiGLaqFJpeSac',
]
RELAY_URL = 'https://YOUR_RELAY_HOST.example.com'
PROXY_HOST = '127.0.0.1'
PROXY_PORTS = [7890, 7897, 7891, 10808, 1080]

# ── Paths (per-user, supports cross-user operation) ──
_DEFAULT_HOME = Path(os.environ.get('USERPROFILE', os.path.expanduser('~')))
WAM_DIR = _DEFAULT_HOME / '.wam-hot'
TOKEN_FILE = WAM_DIR / 'oneshot_token.json'
RESULT_FILE = WAM_DIR / 'inject_result.json'

def _resolve_user_home(target_user=None):
    """Resolve home directory. None=current user, 'Administrator'/'ai'/etc=specific user."""
    if not target_user:
        return _DEFAULT_HOME
    # Windows: C:\Users\<username>
    candidate = Path('C:/Users') / target_user
    if candidate.exists():
        return candidate
    return _DEFAULT_HOME

def _user_paths(target_user=None):
    """Get (wam_dir, token_file, result_file) for target user."""
    home = _resolve_user_home(target_user)
    wam = home / '.wam-hot'
    return wam, wam / 'oneshot_token.json', wam / 'inject_result.json'

# ── SSL context (skip cert verify for relay) ──
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

_proxy_cache = None


def _detect_proxy():
    global _proxy_cache
    if _proxy_cache is not None:
        return _proxy_cache
    for port in PROXY_PORTS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect((PROXY_HOST, port))
            s.close()
            _proxy_cache = port
            return port
        except:
            continue
    _proxy_cache = 0
    return 0


def _https_json(url, data, use_proxy=False, timeout=12):
    body = json.dumps(data).encode('utf-8')
    req = Request(url, data=body, method='POST')
    req.add_header('Content-Type', 'application/json')

    proxy_port = _detect_proxy() if use_proxy else 0
    if proxy_port > 0 and use_proxy:
        handler = ProxyHandler({
            'https': f'http://{PROXY_HOST}:{proxy_port}',
            'http': f'http://{PROXY_HOST}:{proxy_port}',
        })
        opener = build_opener(handler)
        resp = opener.open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout, context=_ctx)
    return json.loads(resp.read())


def _firebase_login_once(email, password):
    """Single attempt across all channels. Returns (idToken, channel) or (None, error)."""
    payload = {'email': email, 'password': password, 'returnSecureToken': True}

    # Priority 1: Firebase via local proxy (Clash 7890)
    for key in FIREBASE_KEYS:
        try:
            url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}'
            result = _https_json(url, payload, use_proxy=True, timeout=12)
            if result.get('idToken'):
                return result['idToken'], f'proxy-{key[-4:]}'
            err = result.get('error', {})
            msg = err.get('message', '') if isinstance(err, dict) else str(err)
            if 'INVALID' in msg or 'NOT_FOUND' in msg or 'DISABLED' in msg:
                return None, msg
        except:
            continue

    # Priority 2: Firebase direct (no proxy)
    for key in FIREBASE_KEYS:
        try:
            url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}'
            result = _https_json(url, payload, use_proxy=False, timeout=12)
            if result.get('idToken'):
                return result['idToken'], f'direct-{key[-4:]}'
            err = result.get('error', {})
            msg = err.get('message', '') if isinstance(err, dict) else str(err)
            if 'INVALID' in msg or 'NOT_FOUND' in msg or 'DISABLED' in msg:
                return None, msg
        except:
            continue

    return None, 'all_channels_failed'


def firebase_login(email, password, retries=2):
    """Firebase signInWithPassword with retry. Returns (idToken, channel) or (None, error)."""
    global _proxy_cache
    last_err = ''
    for attempt in range(retries):
        if attempt > 0:
            _proxy_cache = None  # Reset proxy cache on retry
            time.sleep(1)
        tok, ch = _firebase_login_once(email, password)
        if tok:
            return tok, ch
        last_err = ch
        if 'INVALID' in ch or 'NOT_FOUND' in ch or 'DISABLED' in ch:
            return None, ch  # Permanent error, no retry
    return None, last_err


def write_token(email, id_token, target_user=None):
    """Write idToken to oneshot_token.json for the extension to pick up."""
    wam_dir, token_file, result_file = _user_paths(target_user)
    wam_dir.mkdir(parents=True, exist_ok=True)
    # Clear previous result
    if result_file.exists():
        result_file.unlink(missing_ok=True)
    # Write token
    token_file.write_text(json.dumps({
        'idToken': id_token,
        'email': email,
        'ts': time.time(),
    }), encoding='utf-8')


def wait_for_injection(timeout_sec=35, target_user=None):
    """Poll inject_result.json until extension completes injection.
    Returns (ok, result_dict) or (False, {'error': 'timeout'}).
    """
    _, _, result_file = _user_paths(target_user)
    start_ts = time.time()
    deadline = start_ts + timeout_sec
    while time.time() < deadline:
        if result_file.exists():
            try:
                data = json.loads(result_file.read_text(encoding='utf-8'))
                # Result timestamp in ms; accept if within our window
                if data.get('ts', 0) > start_ts * 1000 - 5000:
                    return data.get('ok', False), data
            except:
                pass
        time.sleep(0.5)
    return False, {'error': 'timeout', 'detail': f'No result after {timeout_sec}s'}


def switch_account(email, password, wait=True, target_user=None):
    """Complete account switch: Firebase login → token → extension inject.
    
    Args:
        target_user: None=current user, 'Administrator'/'ai'=specific user
    Returns:
        (ok: bool, info: dict)
        info keys: email, account, apiKey, channel, error, ms
    """
    t0 = time.time()

    # Step 1: Firebase login
    id_token, channel = firebase_login(email, password)
    if not id_token:
        return False, {'error': f'Firebase login failed: {channel}', 'email': email,
                       'ms': int((time.time() - t0) * 1000)}

    # Step 2: Write token for extension
    write_token(email, id_token, target_user=target_user)

    if not wait:
        return True, {'email': email, 'channel': channel, 'status': 'token_written',
                      'ms': int((time.time() - t0) * 1000)}

    # Step 3: Wait for extension injection
    ok, result = wait_for_injection(timeout_sec=35, target_user=target_user)
    ms = int((time.time() - t0) * 1000)
    result['email'] = email
    result['channel'] = channel
    result['ms'] = ms
    return ok, result


def load_accounts(accounts_file=None, target_user=None):
    """Load accounts from JSON file. Returns list of {email, password, ...}."""
    if accounts_file:
        paths = [Path(accounts_file)]
    else:
        # Search all known user globalStorage paths
        search_users = [target_user] if target_user else []
        # Always include current user's APPDATA
        gs_current = Path(os.environ.get('APPDATA', '')) / 'Windsurf' / 'User' / 'globalStorage'
        all_gs = [gs_current]
        for u in ['Administrator', 'ai', 'zhou']:
            gs = Path(f'C:/Users/{u}/AppData/Roaming/Windsurf/User/globalStorage')
            if gs.exists() and gs != gs_current:
                all_gs.append(gs)
        paths = []
        for gs in all_gs:
            paths.append(gs / 'windsurf-login-accounts.json')
            paths.append(gs / 'zhouyoukang.windsurf-assistant' / 'windsurf-login-accounts.json')
    # Merge all accounts files, deduplicate by email
    seen = set()
    merged = []
    for p in paths:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
                if isinstance(data, list):
                    for a in data:
                        e = a.get('email', '')
                        if e and e not in seen:
                            seen.add(e)
                            merged.append(a)
            except:
                pass
    return merged


# ── CLI ──
if __name__ == '__main__':
    import sys
    args = sys.argv[1:]

    # Parse --user flag
    target_user = None
    if '--user' in args:
        idx = args.index('--user')
        if idx + 1 < len(args):
            target_user = args[idx + 1]
            args = args[:idx] + args[idx+2:]

    if not args or args[0] == 'help':
        print('auth_bridge.py — WAM Auth Bridge')
        print('  switch [email]        — switch to account (first available if omitted)')
        print('  test                  — test Firebase login')
        print('  status                — show last injection result')
        print('  --user <name>         — target user (Administrator/ai/zhou)')
        sys.exit(0)

    if args[0] == 'status':
        _, _, rf = _user_paths(target_user)
        if rf.exists():
            data = json.loads(rf.read_text(encoding='utf-8'))
            print(json.dumps(data, indent=2))
        else:
            print('No injection result yet')
        sys.exit(0)

    if args[0] == 'test':
        accounts = load_accounts(target_user=target_user)
        if not accounts:
            print('No accounts found')
            sys.exit(1)
        for acc in accounts[:5]:
            email, pw = acc.get('email', ''), acc.get('password', '')
            if not email or not pw:
                continue
            print(f'Testing {email}...')
            tok, ch = firebase_login(email, pw)
            if tok:
                print(f'  OK via {ch}: idToken={tok[:30]}... ({len(tok)} chars)')
                sys.exit(0)
            else:
                print(f'  Failed: {ch}')
        print('All accounts failed')
        sys.exit(1)

    if args[0] == 'switch':
        email_target = args[1] if len(args) > 1 else None
        accounts = load_accounts(target_user=target_user)
        if not accounts:
            print('No accounts found')
            sys.exit(1)

        # Find target account
        target = None
        for acc in accounts:
            if email_target and email_target.lower() in acc.get('email', '').lower():
                target = acc
                break
        if not target and not email_target:
            target = accounts[0]
        if not target:
            print(f'Account matching "{email_target}" not found')
            sys.exit(1)

        print(f'Switching to {target["email"]}' + (f' (user={target_user})' if target_user else '') + '...')
        ok, info = switch_account(target['email'], target['password'], target_user=target_user)
        print(json.dumps(info, indent=2))
        sys.exit(0 if ok else 1)
