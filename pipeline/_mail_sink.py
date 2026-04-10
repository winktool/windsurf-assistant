#!/usr/bin/env python3
"""
aiotvr.xyz 邮件接收器 — 部署到阿里云VPS
=========================================
- SMTP catch-all on port 25 (接收所有 @aiotvr.xyz 邮件)
- HTTP API on port 8025 (查询收到的邮件)

API:
  GET /emails?to=xxx@aiotvr.xyz         → 查询某地址的所有邮件
  GET /emails?to=xxx@aiotvr.xyz&code=1  → 直接返回6位验证码
  GET /health                            → 健康检查
  GET /stats                             → 统计
  DELETE /emails?to=xxx@aiotvr.xyz      → 清空某地址的邮件

部署: scp到VPS → pip3 install aiosmtpd → python3 _mail_sink.py
"""

import asyncio
import json
import re
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from email import policy
from email.parser import BytesParser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import SMTP as SMTPServer
except ImportError:
    print("ERROR: pip3 install aiosmtpd")
    raise

CST = timezone(timedelta(hours=8))
LISTEN_HOST = '0.0.0.0'
SMTP_PORT = 25
HTTP_PORT = 8025
MAX_EMAILS_PER_ADDR = 50
MAX_AGE_SECONDS = 7200  # 2小时自动清理

# 邮件存储 {to_addr: [{'from','subject','body','body_html','timestamp'}, ...]}
email_store = defaultdict(list)
store_lock = threading.Lock()
stats = {'received': 0, 'queries': 0, 'started': datetime.now(CST).isoformat()}


class MailHandler:
    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        # 接受所有 @aiotvr.xyz 的邮件
        if address.lower().endswith('@aiotvr.xyz'):
            envelope.rcpt_tos.append(address)
            return '250 OK'
        return '550 not relaying to that domain'

    async def handle_DATA(self, server, session, envelope):
        parser = BytesParser(policy=policy.default)
        msg = parser.parsebytes(envelope.content)

        from_addr = str(msg.get('From', ''))
        subject = str(msg.get('Subject', ''))

        # 提取纯文本body
        body_text = ''
        body_html = ''
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == 'text/plain' and not body_text:
                    try:
                        body_text = part.get_content()
                    except Exception:
                        body_text = str(part.get_payload(decode=True) or b'', 'utf-8', 'replace')
                elif ct == 'text/html' and not body_html:
                    try:
                        body_html = part.get_content()
                    except Exception:
                        body_html = str(part.get_payload(decode=True) or b'', 'utf-8', 'replace')
        else:
            ct = msg.get_content_type()
            try:
                content = msg.get_content()
            except Exception:
                content = str(msg.get_payload(decode=True) or b'', 'utf-8', 'replace')
            if ct == 'text/html':
                body_html = content
            else:
                body_text = content

        # 存储
        for rcpt in envelope.rcpt_tos:
            addr = rcpt.lower().strip()
            entry = {
                'from': from_addr,
                'subject': subject,
                'body': body_text[:5000],
                'body_html': body_html[:10000],
                'timestamp': datetime.now(CST).isoformat(),
                'epoch': time.time(),
            }
            with store_lock:
                email_store[addr].append(entry)
                # 限制每地址最多N封
                if len(email_store[addr]) > MAX_EMAILS_PER_ADDR:
                    email_store[addr] = email_store[addr][-MAX_EMAILS_PER_ADDR:]
                stats['received'] += 1

            ts = datetime.now(CST).strftime('%H:%M:%S')
            print(f"[{ts}] RECV to={addr} from={from_addr[:40]} subj={subject[:50]}")

        return '250 Message accepted'


def cleanup_old():
    """清理过期邮件"""
    now = time.time()
    with store_lock:
        for addr in list(email_store.keys()):
            email_store[addr] = [e for e in email_store[addr]
                                 if now - e.get('epoch', 0) < MAX_AGE_SECONDS]
            if not email_store[addr]:
                del email_store[addr]


class HTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默HTTP日志

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == '/health':
            self._send_json({'status': 'ok', 'time': datetime.now(CST).isoformat()})
            return

        if path == '/stats':
            cleanup_old()
            with store_lock:
                total_addrs = len(email_store)
                total_emails = sum(len(v) for v in email_store.values())
            self._send_json({**stats, 'active_addresses': total_addrs,
                             'stored_emails': total_emails})
            return

        if path == '/emails':
            to_addr = qs.get('to', [''])[0].lower().strip()
            want_code = qs.get('code', [''])[0] == '1'

            if not to_addr:
                self._send_json({'error': 'missing ?to=xxx@aiotvr.xyz'}, 400)
                return

            with store_lock:
                stats['queries'] += 1
                emails = list(email_store.get(to_addr, []))

            if want_code:
                # 直接提取最新的6位验证码
                for email in reversed(emails):
                    combined = (email.get('subject', '') + ' ' +
                                email.get('body', '') + ' ' +
                                email.get('body_html', ''))
                    codes = re.findall(r'\b(\d{6})\b', combined)
                    if codes:
                        self._send_json({'code': codes[0], 'from': email.get('from', ''),
                                         'subject': email.get('subject', '')})
                        return
                self._send_json({'code': None, 'count': len(emails)})
                return

            self._send_json({'to': to_addr, 'count': len(emails), 'emails': emails})
            return

        self._send_json({'error': 'not found', 'endpoints': ['/health', '/stats', '/emails']}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == '/emails':
            to_addr = qs.get('to', [''])[0].lower().strip()
            if to_addr:
                with store_lock:
                    removed = len(email_store.pop(to_addr, []))
                self._send_json({'deleted': removed, 'to': to_addr})
                return

        self._send_json({'error': 'bad request'}, 400)


def run_http():
    server = HTTPServer((LISTEN_HOST, HTTP_PORT), HTTPHandler)
    print(f"[HTTP] Listening on {LISTEN_HOST}:{HTTP_PORT}")
    server.serve_forever()


def run_cleanup_loop():
    while True:
        time.sleep(300)  # 每5分钟清理一次
        cleanup_old()


async def run_smtp():
    handler = MailHandler()
    controller = Controller(handler, hostname=LISTEN_HOST, port=SMTP_PORT)
    controller.start()
    print(f"[SMTP] Listening on {LISTEN_HOST}:{SMTP_PORT}")
    print(f"[READY] aiotvr.xyz mail sink — catch-all active")
    print(f"[API] http://60.205.171.100:{HTTP_PORT}/emails?to=xxx@aiotvr.xyz")
    # 保持运行
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        controller.stop()


def main():
    print("=" * 60)
    print("  aiotvr.xyz Mail Sink")
    print(f"  SMTP: {LISTEN_HOST}:{SMTP_PORT}")
    print(f"  HTTP: {LISTEN_HOST}:{HTTP_PORT}")
    print(f"  Started: {datetime.now(CST).isoformat()}")
    print("=" * 60)

    # HTTP线程
    http_thread = threading.Thread(target=run_http, daemon=True)
    http_thread.start()

    # 清理线程
    cleanup_thread = threading.Thread(target=run_cleanup_loop, daemon=True)
    cleanup_thread.start()

    # SMTP (async主循环)
    asyncio.run(run_smtp())


if __name__ == '__main__':
    main()
