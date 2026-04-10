#!/usr/bin/env python3
"""
Pool Proxy v1.0 — 号池代理 · API请求透明注入
==============================================
道生一: pool_engine 持续写入最优 apiKey 到 _pool_apikey.txt
一生二: pool_proxy 拦截 HTTP 请求，注入当前最优 apiKey
二生三: 所有请求自动携带最优账号凭据
三生万物: 用户无感，额度无限

Architecture:
  - HTTP Proxy on :19876
  - Reads _pool_apikey.txt for current best apiKey
  - Injects Authorization header into forwarded requests
  - /pool/health endpoint for guardian health checks

Usage:
  python pool_proxy.py          # Start proxy on :19876
  python pool_proxy.py status   # Check if proxy is running
"""

import os, sys, json, time, threading
import http.client
import urllib.parse

# Fix Windows GBK console encoding — prevent UnicodeEncodeError for all print()
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

VERSION = '1.0.0'
SCRIPT_DIR = Path(__file__).parent
PROXY_PORT = 19876
ENGINE_PORT = 19877

APPDATA = Path(os.environ.get('APPDATA', ''))
POOL_KEY_FILE = APPDATA / 'Windsurf' / '_pool_apikey.txt'

# Stats
_stats = {
    'requests': 0,
    'proxied': 0,
    'forwarded_raw': 0,
    'errors': 0,
    'start_time': time.time(),
}
_stats_lock = threading.Lock()


def _log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def _read_pool_key() -> str:
    """Read current best apiKey from pool key file."""
    try:
        if POOL_KEY_FILE.exists():
            key = POOL_KEY_FILE.read_text(encoding='utf-8', errors='replace').strip()
            if key and len(key) > 20 and key.startswith('sk-ws'):
                return key
    except Exception:
        pass
    return ''


class PoolProxyHandler(BaseHTTPRequestHandler):
    """Transparent proxy that injects pool apiKey into forwarded requests."""

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors()
        self.send_header('Content-Length', len(body))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._proxy_request()

    def do_POST(self):
        self._proxy_request()

    def do_PUT(self):
        self._proxy_request()

    def do_DELETE(self):
        self._proxy_request()

    def _proxy_request(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Health check endpoint
        if path == '/pool/health':
            key = _read_pool_key()
            with _stats_lock:
                stats_copy = dict(_stats)
            return self._json({
                'ok': True,
                'proxy': f'pool_proxy v{VERSION}',
                'port': PROXY_PORT,
                'pool_key_valid': bool(key),
                'pool_key_preview': (key[:15] + '...') if key else None,
                'stats': stats_copy,
            })

        # Status endpoint
        if path == '/pool/status':
            key = _read_pool_key()
            with _stats_lock:
                stats_copy = dict(_stats)
            stats_copy['uptime_s'] = round(time.time() - stats_copy.pop('start_time', time.time()))
            return self._json({
                'ok': True,
                'version': VERSION,
                'pool_key_valid': bool(key),
                'stats': stats_copy,
            })

        # Proxy logic: read pool key, inject into request
        with _stats_lock:
            _stats['requests'] += 1

        pool_key = _read_pool_key()
        if not pool_key:
            _log('[!] No pool account, forwarding as-is')
            with _stats_lock:
                _stats['forwarded_raw'] += 1

        # Forward to pool engine API if path starts with /api/
        if path.startswith('/api/'):
            try:
                conn = http.client.HTTPConnection('127.0.0.1', ENGINE_PORT, timeout=10)

                # Read request body if present
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length) if content_length > 0 else None

                # Forward headers, inject apiKey if available
                headers = {}
                for key_name in self.headers:
                    if key_name.lower() not in ('host', 'connection'):
                        headers[key_name] = self.headers[key_name]
                if pool_key:
                    headers['X-Pool-ApiKey'] = pool_key

                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                resp_body = resp.read()

                self.send_response(resp.status)
                for h, v in resp.getheaders():
                    if h.lower() not in ('transfer-encoding', 'connection'):
                        self.send_header(h, v)
                self._cors()
                self.end_headers()
                try:
                    self.wfile.write(resp_body)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass

                with _stats_lock:
                    _stats['proxied'] += 1
                conn.close()
                return
            except Exception as e:
                _log(f'[ERR] Proxy to engine failed: {e}')
                with _stats_lock:
                    _stats['errors'] += 1
                return self._json({'ok': False, 'error': str(e)}, 502)

        # Default: return 404 for unknown paths
        self._json({'ok': False, 'error': 'not found', 'hint': 'Use /pool/health or /api/*'}, 404)


def serve():
    port = PROXY_PORT
    server = None
    for attempt in range(3):
        try:
            server = HTTPServer(('127.0.0.1', port), PoolProxyHandler)
            break
        except OSError:
            port += 1

    if not server:
        print(f'  [ERR] Cannot bind to any port {PROXY_PORT}-{port}')
        return

    print(f'  Pool Proxy v{VERSION}: http://127.0.0.1:{port}/')
    print(f'  Health:  http://127.0.0.1:{port}/pool/health')
    print(f'  KeyFile: {POOL_KEY_FILE}')
    print(f'  Press Ctrl+C to stop')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
        print('\nPool Proxy stopped.')


def check_status():
    import urllib.request
    try:
        r = urllib.request.urlopen(f'http://127.0.0.1:{PROXY_PORT}/pool/health', timeout=2)
        d = json.loads(r.read())
        print(json.dumps(d, indent=2, ensure_ascii=False))
    except Exception:
        print(f'Pool Proxy not running on :{PROXY_PORT}')
        sys.exit(1)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'serve'
    if cmd == 'status':
        check_status()
    elif cmd in ('serve', 'start', ''):
        serve()
    else:
        print(f'Pool Proxy v{VERSION}')
        print(f'  serve    Start proxy (default)')
        print(f'  status   Check if running')


if __name__ == '__main__':
    main()
