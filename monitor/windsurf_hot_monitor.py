#!/usr/bin/env python3
"""
windsurf_hot_monitor.py — 反者道之动 · 热监控守护进程
实时、非干扰、持续逆向监控Windsurf一切底层运行状态

六层监控:
  1. 进程发现 — 自动发现所有Windsurf进程树、PID、端口
  2. CSRF提取 — 从远程进程环境变量提取认证令牌(Windows PEB读取)
  3. gRPC探针 — 非侵入式调用LS本地gRPC-Web只读方法
  4. 文件监控 — 实时监控Windsurf数据目录状态变化
  5. SQLite读取 — 只读访问state.vscdb获取全局状态
  6. 仪表盘 — HTTP+SSE实时Web展示

Usage:
    python windsurf_hot_monitor.py              # 完整监控 + 仪表盘
    python windsurf_hot_monitor.py --once       # 单次快照
    python windsurf_hot_monitor.py --port 19900 # 指定仪表盘端口
    python windsurf_hot_monitor.py --no-dash    # 无仪表盘纯CLI
"""

import os, sys, json, time, struct, socket, sqlite3, subprocess, threading, re, signal
import http.client
import ctypes
import ctypes.wintypes
from ctypes import wintypes, Structure, byref, c_void_p, c_size_t, sizeof
from pathlib import Path
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import deque, defaultdict
from hashlib import sha256
import traceback
import argparse
import io

# ══════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════

MONITOR_PORT = 19900
POLL_FAST = 3       # 快速轮询(秒): Heartbeat, Trajectories
POLL_MED = 8        # 中速轮询: EditState, Processes
POLL_SLOW = 30      # 慢速轮询: Diagnostics, MCP
LOG_MAX = 20000     # 内存事件上限
APPDATA = Path(os.environ.get('APPDATA', ''))
WINDSURF_DATA = APPDATA / 'Windsurf'
SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / 'logs'
DASH_FILE = SCRIPT_DIR / 'dashboard.html'

LS_SERVICE = 'exa.language_server_pb.LanguageServerService'
EXT_SERVICE = 'exa.windsurf_pb.ExtensionServerService'

# 无需API Key的gRPC方法 (只读, 仅需CSRF)
GRPC_METHODS_FAST = [
    ('Heartbeat', '心跳'),
    ('GetAllCascadeTrajectories', '全部对话轨迹'),
    ('GetProcesses', '进程列表'),
]
GRPC_METHODS_MED = [
    ('GetWorkspaceEditState', '工作区编辑状态'),
    ('GetWorkspaceInfos', '工作区信息'),
    ('GetCascadeMemories', '记忆系统'),
]
GRPC_METHODS_SLOW = [
    ('GetDebugDiagnostics', '调试诊断'),
    ('GetMcpServerStates', 'MCP服务器状态'),
    ('GetUserSettings', '用户设置/模型目录'),
    ('GetUserMemories', '用户记忆'),
    ('GetAllWorkflows', '工作流'),
    ('GetUnleashData', '特性开关'),
    ('GetDefaultWebOrigins', 'Web搜索源'),
    ('WellSupportedLanguages', '支持语言'),
    ('ShouldEnableUnleash', 'Unleash开关'),
]

# ══════════════════════════════════════════════════════════════════
# Protobuf Helpers
# ══════════════════════════════════════════════════════════════════

def encode_varint(v):
    b = bytearray()
    while v > 127:
        b.append((v & 0x7f) | 0x80)
        v >>= 7
    b.append(v & 0x7f)
    return bytes(b)

def decode_varint(data, pos):
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]; pos += 1
        result |= (byte & 0x7f) << shift
        if not (byte & 0x80): break
        shift += 7
    return result, pos

def parse_proto(data, depth=0):
    """递归解析protobuf到dict, 尝试解码字符串和嵌套消息"""
    if depth > 8 or not data or len(data) < 2:
        return None
    fields = {}
    pos = 0
    try:
        while pos < len(data):
            tag, pos = decode_varint(data, pos)
            fn = tag >> 3; wt = tag & 7
            if fn == 0 or fn > 5000 or pos >= len(data): break
            key = f'f{fn}'
            if key not in fields: fields[key] = []
            if wt == 0:  # varint
                val, pos = decode_varint(data, pos)
                fields[key].append(val)
            elif wt == 2:  # length-delimited
                length, pos = decode_varint(data, pos)
                if length < 0 or length > 8388608 or pos + length > len(data): break
                chunk = data[pos:pos+length]; pos += length
                # 尝试UTF-8字符串
                try:
                    s = chunk.decode('utf-8')
                    printable = all(0x09 <= ord(c) <= 0x7e or ord(c) >= 0xa0 for c in s) and len(s) > 0
                    if printable:
                        nested = parse_proto(chunk, depth + 1) if len(chunk) > 4 else None
                        if nested and len(nested) > 0:
                            fields[key].append({'_str': s[:500], '_msg': nested})
                        else:
                            fields[key].append(s[:2000])
                    else:
                        nested = parse_proto(chunk, depth + 1)
                        if nested and len(nested) > 0:
                            fields[key].append(nested)
                        else:
                            fields[key].append(f'<{len(chunk)}B:{chunk[:32].hex()}>')
                except:
                    nested = parse_proto(chunk, depth + 1)
                    if nested and len(nested) > 0:
                        fields[key].append(nested)
                    else:
                        fields[key].append(f'<{len(chunk)}B:{chunk[:32].hex()}>')
            elif wt == 1:  # fixed64
                if pos + 8 > len(data): break
                val = struct.unpack('<q', data[pos:pos+8])[0]; pos += 8
                fields[key].append(val)
            elif wt == 5:  # fixed32
                if pos + 4 > len(data): break
                val = struct.unpack('<i', data[pos:pos+4])[0]; pos += 4
                fields[key].append(val)
            else:
                break
    except:
        pass
    return fields if fields else None

def encode_proto_string(field_num, value):
    """编码protobuf string字段: tag + len + bytes"""
    b = value.encode('utf-8') if isinstance(value, str) else value
    tag = (field_num << 3) | 2
    return encode_varint(tag) + encode_varint(len(b)) + b

def encode_proto_varint_field(field_num, value):
    """编码protobuf varint字段"""
    tag = (field_num << 3) | 0
    return encode_varint(tag) + encode_varint(value)

def encode_proto_message(fields):
    """编码protobuf消息: fields = [(field_num, type, value), ...]
    type: 's' = string, 'v' = varint, 'b' = bytes/submessage
    """
    body = b''
    for fn, ft, fv in fields:
        if ft == 's':
            body += encode_proto_string(fn, fv)
        elif ft == 'v':
            body += encode_proto_varint_field(fn, fv)
        elif ft == 'b':
            body += encode_proto_string(fn, fv)
    return body

# ── 步骤状态/类型名称映射(逆向确认) ──
STEP_STATUS_NAMES = {
    0: 'UNSPECIFIED', 1: 'PENDING', 2: 'RUNNING', 3: 'DONE',
    4: 'INVALID', 5: 'CLEARED', 6: 'CANCELED', 7: 'ERROR',
    8: 'GENERATING', 9: 'WAITING', 10: 'HALTED', 11: 'SKIPPING',
}
STEP_TYPE_NAMES = {
    2: 'FINISH', 3: 'PLAN_INPUT', 4: 'MQUERY', 5: 'CODE_ACTION',
    7: 'GREP_SEARCH', 8: 'VIEW_FILE', 9: 'LIST_DIR', 14: 'USER_INPUT',
    15: 'PLANNER_RESPONSE', 16: 'WRITE_FILE', 21: 'RUN_COMMAND',
    28: 'COMMAND_STATUS', 29: 'MEMORY', 33: 'SEARCH_WEB',
    34: 'RETRIEVE_MEMORY', 38: 'MCP_TOOL', 65: 'READ_TERMINAL', 68: 'GET_DOM_TREE',
}
ACTIVE_STEP_STATUSES = {1, 2, 8, 9}

def parse_grpc_web_response(data):
    """解析gRPC-Web响应帧"""
    frames = []
    pos = 0
    while pos + 5 <= len(data):
        flags = data[pos]
        length = struct.unpack('>I', data[pos+1:pos+5])[0]
        if length > 16777216 or pos + 5 + length > len(data): break
        payload = data[pos+5:pos+5+length]
        if flags & 0x80:  # trailer
            frames.append({'type': 'trailer', 'text': payload.decode('utf-8', errors='replace')})
        else:
            frames.append({'type': 'data', 'payload': payload, 'length': length})
        pos += 5 + length
    return frames

# ══════════════════════════════════════════════════════════════════
# Windows PEB读取 — 从远程进程提取环境变量
# ══════════════════════════════════════════════════════════════════

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

class PROCESS_BASIC_INFORMATION(Structure):
    _fields_ = [
        ("Reserved1", c_void_p),
        ("PebBaseAddress", c_void_p),
        ("Reserved2", c_void_p * 2),
        ("UniqueProcessId", c_void_p),
        ("Reserved3", c_void_p),
    ]

def read_process_env_var(pid, var_name):
    """从远程进程PEB读取指定环境变量(Windows x64)"""
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        ntdll = ctypes.WinDLL('ntdll', use_last_error=True)

        h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            return None

        try:
            pbi = PROCESS_BASIC_INFORMATION()
            ret_len = ctypes.c_ulong()
            status = ntdll.NtQueryInformationProcess(h, 0, byref(pbi), sizeof(pbi), byref(ret_len))
            if status != 0:
                return None

            # PEB.ProcessParameters at offset 0x20 (x64)
            buf8 = ctypes.create_string_buffer(8)
            n = c_size_t()
            if not kernel32.ReadProcessMemory(h, c_void_p(pbi.PebBaseAddress + 0x20), buf8, 8, byref(n)):
                return None
            params_ptr = int.from_bytes(buf8.raw, 'little')

            # RTL_USER_PROCESS_PARAMETERS.Environment at offset 0x80 (x64)
            if not kernel32.ReadProcessMemory(h, c_void_p(params_ptr + 0x80), buf8, 8, byref(n)):
                return None
            env_ptr = int.from_bytes(buf8.raw, 'little')

            # 读取环境块(最多256KB)
            env_size = 262144
            env_buf = ctypes.create_string_buffer(env_size)
            if not kernel32.ReadProcessMemory(h, c_void_p(env_ptr), env_buf, env_size, byref(n)):
                return None

            raw = env_buf.raw[:n.value] if n.value > 0 else env_buf.raw
            text = raw.decode('utf-16-le', errors='ignore')

            target = f'{var_name}='
            for var in text.split('\x00'):
                if var.startswith(target):
                    return var[len(target):].strip()
            return None
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════════
# Event Log — 中央事件总线
# ══════════════════════════════════════════════════════════════════

class EventLog:
    def __init__(self):
        self.events = deque(maxlen=LOG_MAX)
        self.sse_queues = []  # SSE客户端队列列表
        self._lock = threading.Lock()
        self._log_file = None
        self._hashes = {}     # 去重: method -> last_hash
        self._stats = defaultdict(int)
        LOG_DIR.mkdir(exist_ok=True)

    def _get_log_file(self):
        today = datetime.now().strftime('%Y%m%d')
        path = LOG_DIR / f'monitor_{today}.jsonl'
        if self._log_file is None or self._log_file.name != str(path):
            if self._log_file:
                try: self._log_file.close()
                except: pass
            self._log_file = open(path, 'a', encoding='utf-8', buffering=1)
        return self._log_file

    def emit(self, category, data, dedup_key=None):
        """发射事件。如果dedup_key相同且数据hash不变则跳过。"""
        now = datetime.now(timezone(timedelta(hours=8)))
        ts = now.strftime('%H:%M:%S.%f')[:-3]

        if dedup_key:
            h = sha256(json.dumps(data, ensure_ascii=False, default=str, sort_keys=True).encode()).hexdigest()[:16]
            if self._hashes.get(dedup_key) == h:
                return  # 数据未变化,跳过
            self._hashes[dedup_key] = h

        event = {'ts': ts, 'cat': category, 'data': data}
        self._stats[category] += 1

        with self._lock:
            self.events.append(event)
            # 写日志文件
            try:
                f = self._get_log_file()
                f.write(json.dumps(event, ensure_ascii=False, default=str) + '\n')
            except: pass
            # 推送SSE
            sse_data = json.dumps(event, ensure_ascii=False, default=str)
            dead = []
            for i, q in enumerate(self.sse_queues):
                try: q.append(sse_data)
                except: dead.append(i)
            for i in reversed(dead):
                self.sse_queues.pop(i)

        # 控制台输出
        self._console_print(ts, category, data)

    def _console_print(self, ts, cat, data):
        icons = {
            'discover': '🔍', 'grpc': '📡', 'process': '⚙️',
            'file': '📁', 'state': '💾', 'error': '❌',
            'info': 'ℹ️', 'csrf': '🔑', 'trajectory': '🤖',
            'terminal': '💻', 'mcp': '🔌', 'edit': '✏️',
        }
        icon = icons.get(cat, '·')
        summary = ''
        if isinstance(data, dict):
            if 'method' in data:
                summary = f"{data['method']}"
                if 'size' in data: summary += f" [{data['size']}B]"
                if 'status' in data: summary += f" {data['status']}"
                if 'summary' in data: summary += f" | {data['summary'][:120]}"
            elif 'msg' in data:
                summary = str(data['msg'])[:200]
            else:
                summary = json.dumps(data, ensure_ascii=False, default=str)[:200]
        else:
            summary = str(data)[:200]
        print(f'  {icon} [{ts}] {cat:12s} {summary}')

    def get_stats(self):
        return dict(self._stats)

# ══════════════════════════════════════════════════════════════════
# Process Scanner
# ══════════════════════════════════════════════════════════════════

class ProcessScanner:
    def __init__(self, log: EventLog):
        self.log = log
        self._prev_pids = set()
        self._prev_connections = {}

    def scan_processes(self):
        """扫描所有Windsurf相关进程"""
        result = {'windsurf': [], 'ls': [], 'mcp': [], 'terminal': []}
        try:
            output = subprocess.check_output(
                ['powershell', '-NoProfile', '-Command',
                 'Get-Process -Name "Windsurf","language_server*" -ErrorAction SilentlyContinue | '
                 'Select-Object Id,ProcessName,CPU,@{N="MemMB";E={[math]::Round($_.WorkingSet64/1MB)}},StartTime | '
                 'ConvertTo-Json -Compress'],
                timeout=10, text=True, creationflags=0x08000000  # CREATE_NO_WINDOW
            ).strip()
            if output:
                procs = json.loads(output)
                if isinstance(procs, dict): procs = [procs]
                for p in procs:
                    pid = p.get('Id')
                    name = p.get('ProcessName', '')
                    entry = {
                        'pid': pid, 'name': name,
                        'cpu': round(p.get('CPU', 0), 1),
                        'mem_mb': p.get('MemMB', 0),
                    }
                    if 'language_server' in name:
                        result['ls'].append(entry)
                    else:
                        result['windsurf'].append(entry)
        except Exception as e:
            self.log.emit('error', {'msg': f'进程扫描失败: {e}'})

        # 检测新进程
        current_pids = {p['pid'] for cat in result.values() for p in cat}
        new_pids = current_pids - self._prev_pids
        gone_pids = self._prev_pids - current_pids
        if new_pids:
            self.log.emit('process', {'msg': f'新进程: {new_pids}', 'pids': list(new_pids)})
        if gone_pids:
            self.log.emit('process', {'msg': f'进程退出: {gone_pids}', 'pids': list(gone_pids)})
        self._prev_pids = current_pids

        return result

    def scan_ports(self, pid):
        """扫描指定PID的监听端口"""
        ports = []
        try:
            output = subprocess.check_output(
                f'netstat -anop TCP | findstr "LISTENING" | findstr "{pid}"',
                shell=True, timeout=5, text=True, creationflags=0x08000000
            ).strip()
            for line in output.split('\n'):
                m = re.search(r'127\.0\.0\.1:(\d+)\s+0\.0\.0\.0:0\s+LISTENING', line)
                if m:
                    ports.append(int(m.group(1)))
        except: pass
        return sorted(ports)

    def scan_connections(self, pid):
        """扫描指定PID的所有连接"""
        connections = {'listening': [], 'local': [], 'external': []}
        try:
            output = subprocess.check_output(
                f'netstat -anop TCP | findstr "{pid}"',
                shell=True, timeout=5, text=True, creationflags=0x08000000
            ).strip()
            for line in output.split('\n'):
                parts = line.split()
                if len(parts) < 5: continue
                local = parts[1]; remote = parts[2]; state = parts[3]
                if state == 'LISTENING':
                    connections['listening'].append(local)
                elif remote.startswith('127.0.0.1'):
                    connections['local'].append(f'{local}<->{remote} {state}')
                else:
                    connections['external'].append(f'{remote} {state}')
        except: pass
        return connections

    def identify_grpc_port(self, ls_pid):
        """识别LS的主gRPC-Web端口(连接数最多的监听端口)"""
        ports = self.scan_ports(ls_pid)
        if not ports: return None, ports

        # 统计每个监听端口的连接数
        port_counts = {p: 0 for p in ports}
        try:
            output = subprocess.check_output(
                f'netstat -anop TCP | findstr "{ls_pid}" | findstr "ESTABLISHED"',
                shell=True, timeout=5, text=True, creationflags=0x08000000
            ).strip()
            for line in output.split('\n'):
                for p in ports:
                    if f'127.0.0.1:{p}' in line:
                        port_counts[p] = port_counts.get(p, 0) + 1
        except: pass

        # 连接数最多的端口 = gRPC-Web主端口
        if port_counts:
            grpc_port = max(port_counts, key=port_counts.get)
            return grpc_port, ports
        return ports[0] if ports else None, ports

    def find_extension_host_pid(self):
        """找到Extension Host进程PID"""
        try:
            output = subprocess.check_output(
                ['powershell', '-NoProfile', '-Command',
                 'Get-CimInstance Win32_Process -Filter "Name=\'Windsurf.exe\'" | '
                 'Where-Object { $_.CommandLine -match "extensionHost|extension-host" } | '
                 'Select-Object ProcessId | ConvertTo-Json -Compress'],
                timeout=10, text=True, creationflags=0x08000000
            ).strip()
            if output:
                data = json.loads(output)
                if isinstance(data, dict): data = [data]
                if data:
                    return data[0].get('ProcessId')
        except: pass
        return None

# ══════════════════════════════════════════════════════════════════
# gRPC-Web Client — 连接本地LS
# ══════════════════════════════════════════════════════════════════

class GrpcWebClient:
    def __init__(self, host='127.0.0.1', port=None, csrf_token=None):
        self.host = host
        self.port = port
        self.csrf_token = csrf_token

    def call(self, method, body=b'', service=None):
        """调用gRPC-Web方法,返回(status, parsed_data, raw_bytes, size)"""
        if not self.port:
            return 0, None, b'', 0
        svc = service or LS_SERVICE
        path = f'/{svc}/{method}'

        # gRPC-Web帧: 0x00 + 4字节长度(big-endian) + protobuf
        grpc_body = b'\x00' + struct.pack('>I', len(body)) + body

        headers = {
            'Content-Type': 'application/grpc-web+proto',
            'Accept': 'application/grpc-web+proto',
            'x-user-agent': 'grpc-web-python/0.1',
        }
        if self.csrf_token:
            headers['x-codeium-csrf-token'] = self.csrf_token

        try:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
            conn.request('POST', path, grpc_body, headers)
            resp = conn.getresponse()
            data = resp.read()
            conn.close()

            if resp.status == 200 and len(data) > 5:
                frames = parse_grpc_web_response(data)
                for f in frames:
                    if f['type'] == 'data' and f['payload']:
                        parsed = parse_proto(f['payload'])
                        return resp.status, parsed, f['payload'], len(data)
                # 可能没有gRPC帧,直接尝试解析整个body
                parsed = parse_proto(data)
                return resp.status, parsed, data, len(data)
            else:
                # 尝试解析错误
                err_text = data.decode('utf-8', errors='replace')[:500]
                return resp.status, {'_error': err_text}, data, len(data)
        except Exception as e:
            return -1, {'_error': str(e)}, b'', 0

    def call_connect(self, method, body=b'', service=None):
        """用connect协议调用(备用)"""
        if not self.port:
            return 0, None, b'', 0
        svc = service or LS_SERVICE
        path = f'/{svc}/{method}'
        headers = {
            'Content-Type': 'application/proto',
            'connect-protocol-version': '1',
        }
        if self.csrf_token:
            headers['x-codeium-csrf-token'] = self.csrf_token
        try:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
            conn.request('POST', path, body, headers)
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            if resp.status == 200 and data:
                parsed = parse_proto(data)
                return resp.status, parsed, data, len(data)
            return resp.status, {'_error': data.decode('utf-8', errors='replace')[:500]}, data, len(data)
        except Exception as e:
            return -1, {'_error': str(e)}, b'', 0

# ══════════════════════════════════════════════════════════════════
# State Reader — SQLite只读访问
# ══════════════════════════════════════════════════════════════════

class StateReader:
    def __init__(self, log: EventLog):
        self.log = log

    def read_vscdb(self, db_path, query="SELECT key, value FROM ItemTable ORDER BY rowid DESC LIMIT 30"):
        """只读方式读取state.vscdb"""
        if not db_path.exists():
            return None
        try:
            uri = f'file:{db_path}?mode=ro&nolock=1'
            conn = sqlite3.connect(uri, uri=True, timeout=3)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.execute(query)
            rows = cursor.fetchall()
            conn.close()
            return rows
        except Exception as e:
            return None

    def read_global_state(self):
        """读取全局state.vscdb"""
        db_path = WINDSURF_DATA / 'User' / 'globalStorage' / 'state.vscdb'
        rows = self.read_vscdb(db_path, "SELECT key, length(value) as vlen FROM ItemTable ORDER BY rowid DESC LIMIT 50")
        if rows:
            return [{'key': r[0], 'value_len': r[1]} for r in rows]
        return None

    def read_recent_workspaces(self):
        """读取最近工作区"""
        db_path = WINDSURF_DATA / 'User' / 'globalStorage' / 'state.vscdb'
        rows = self.read_vscdb(db_path,
            "SELECT key, substr(value, 1, 200) FROM ItemTable WHERE key LIKE '%workspace%' OR key LIKE '%recent%' LIMIT 20")
        if rows:
            return [{'key': r[0], 'preview': r[1][:200] if r[1] else ''} for r in rows]
        return None

# ══════════════════════════════════════════════════════════════════
# File Monitor — 监控关键文件变化
# ══════════════════════════════════════════════════════════════════

class FileMonitor:
    def __init__(self, log: EventLog):
        self.log = log
        self._snapshots = {}  # path -> (mtime, size)
        self._watch_paths = []

    def setup(self):
        """设置要监控的路径"""
        self._watch_paths = []
        dirs_to_watch = [
            WINDSURF_DATA / 'logs',
            WINDSURF_DATA / 'User' / 'globalStorage',
        ]
        for d in dirs_to_watch:
            if d.exists():
                self._watch_paths.append(d)
        # 取初始快照
        self._take_snapshot()

    def _take_snapshot(self):
        new_snap = {}
        for d in self._watch_paths:
            try:
                for f in d.rglob('*'):
                    if f.is_file() and f.stat().st_size < 100_000_000:  # 跳过>100MB
                        try:
                            st = f.stat()
                            new_snap[str(f)] = (st.st_mtime, st.st_size)
                        except: pass
            except: pass
        return new_snap

    def check_changes(self):
        """检查文件变化"""
        new_snap = self._take_snapshot()
        changes = []

        for path, (mtime, size) in new_snap.items():
            old = self._snapshots.get(path)
            if old is None:
                changes.append({'type': 'new', 'path': path, 'size': size})
            elif old[0] != mtime or old[1] != size:
                changes.append({
                    'type': 'modified', 'path': path,
                    'old_size': old[1], 'new_size': size,
                    'delta': size - old[1]
                })

        for path in set(self._snapshots.keys()) - set(new_snap.keys()):
            changes.append({'type': 'deleted', 'path': path})

        self._snapshots = new_snap

        if changes:
            # 只报告非日志的重要变化
            important = [c for c in changes if not any(x in c.get('path', '') for x in ['.log', 'telemetry', 'CachedData'])]
            if important:
                self.log.emit('file', {
                    'msg': f'{len(important)}个文件变化',
                    'changes': important[:20]
                }, dedup_key='file_changes')

        return changes

# ══════════════════════════════════════════════════════════════════
# Trajectory Analyzer — 解析对话轨迹数据
# ══════════════════════════════════════════════════════════════════

def _extract_str(obj):
    """从protobuf解析对象中提取_str值"""
    if isinstance(obj, dict) and '_str' in obj:
        return obj['_str']
    if isinstance(obj, str):
        return obj
    return None

def _extract_ts(obj_list):
    """从timestamp字段列表提取epoch秒: [{f1:[epoch], f2:[nanos]}]"""
    if obj_list and isinstance(obj_list[0], dict):
        f1 = obj_list[0].get('f1', [])
        if f1 and isinstance(f1[0], int) and f1[0] > 1700000000:
            return f1[0]
    return None

def analyze_trajectories(parsed):
    """从GetAllCascadeTrajectories响应中提取完整对话状态(verified against live protobuf)
    f1=[{f1:[uuid], f2:[detail]}]
    detail: f1=title f2=count f3=ts_update f4=active_step f5=status_enum
            f7=ts_created f9=workspace f10=ts_start f13=steps f22=state_enum
            f26=model f29=active_files
    """
    if not parsed:
        return None
    summaries = []
    try:
        items = parsed.get('f1', [])
        for item in items:
            if not isinstance(item, dict):
                continue
            traj = {}
            # UUID
            f1 = item.get('f1', [])
            if f1:
                uid = _extract_str(f1[0])
                if uid:
                    traj['id'] = uid
            if not traj.get('id'):
                continue
            # Detail
            f2 = item.get('f2', [])
            if not f2 or not isinstance(f2[0], dict):
                summaries.append(traj)
                continue
            d = f2[0]
            # Title
            df1 = d.get('f1', [])
            if df1:
                t = _extract_str(df1[0])
                if t:
                    traj['title'] = t[:150]
            # Step count
            df2 = d.get('f2', [])
            if df2 and isinstance(df2[0], int):
                traj['count'] = df2[0]
            # Timestamps: f3=last_update, f7=created, f10=start
            ts_update = _extract_ts(d.get('f3', []))
            ts_created = _extract_ts(d.get('f7', []))
            ts_start = _extract_ts(d.get('f10', []))
            if ts_update:
                traj['ts_update'] = ts_update
            if ts_created:
                traj['ts_created'] = ts_created
            if ts_start:
                traj['ts_start'] = ts_start
            # Active step UUID
            df4 = d.get('f4', [])
            if df4:
                astep = _extract_str(df4[0])
                if astep:
                    traj['active_step_id'] = astep
            # Status enum
            df5 = d.get('f5', [])
            if df5 and isinstance(df5[0], int):
                traj['status_enum'] = df5[0]
            # State enum
            df22 = d.get('f22', [])
            if df22 and isinstance(df22[0], int):
                traj['state_enum'] = df22[0]
            # Model
            df26 = d.get('f26', [])
            if df26:
                m = _extract_str(df26[0])
                if m:
                    traj['model'] = m.replace('MODEL_', '').replace('_', ' ')
            # Steps (f13)
            df13 = d.get('f13', [])
            if df13 and isinstance(df13[0], dict):
                steps = []
                step_items = df13[0].get('f1', [])
                for si in step_items:
                    if not isinstance(si, dict):
                        continue
                    step = {}
                    sf1 = si.get('f1', [])
                    if sf1:
                        step['num'] = sf1[0] if sf1 else ''
                    sf2 = si.get('f2', [])
                    if sf2:
                        step['desc'] = (_extract_str(sf2[0]) or '')[:120]
                    sf3 = si.get('f3', [])
                    if sf3 and isinstance(sf3[0], int):
                        step['status'] = sf3[0]  # 1=pending 2=running 3=done
                    sf4 = si.get('f4', [])
                    if sf4 and isinstance(sf4[0], int):
                        step['type'] = sf4[0]
                    if step:
                        steps.append(step)
                traj['steps'] = steps[:20]
            # Active files (f29)
            df29 = d.get('f29', [])
            if df29:
                files = []
                for fobj in df29[:8]:
                    fstr = _extract_str(fobj)
                    if fstr and fstr.startswith('file:///'):
                        from urllib.parse import unquote
                        fstr = unquote(fstr.replace('file:///', ''))
                    if fstr:
                        files.append(fstr)
                if files:
                    traj['files'] = files
            # Workspace (f9)
            df9 = d.get('f9', [])
            if df9 and isinstance(df9[0], dict):
                wf1 = df9[0].get('f1', [])
                if wf1 and isinstance(wf1[0], dict):
                    ws = _extract_str(wf1[0])
                    if ws:
                        if ws.startswith('file:///'):
                            from urllib.parse import unquote
                            ws = unquote(ws.replace('file:///', ''))
                        traj['workspace'] = ws
            summaries.append(traj)
    except Exception:
        pass
    return summaries if summaries else None

def _enrich_conversations(raw_trajs):
    """动态计算ago_sec/status并排序 — 每次serve时调用,保证实时性"""
    if not raw_trajs:
        return []
    now_epoch = int(time.time())
    enriched = []
    for traj in raw_trajs:
        c = dict(traj)  # shallow copy
        ts = c.get('ts_update')
        if ts:
            c['ago_sec'] = max(0, now_epoch - ts)
            c['time'] = datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')
        else:
            c['ago_sec'] = 999999
        # Active detection: ago_sec为主(实时), status_enum为辅(粘性标记)
        # status_enum=2 是"会话打开"粘性标记, 不等于正在活跃操作
        se = c.get('status_enum')
        ago = c['ago_sec']
        if ago < 120:
            c['status'] = 'active'
        elif ago < 600:
            c['status'] = 'recent' if se == 2 else 'idle'
        elif se == 2 and ago < 1800:
            c['status'] = 'recent'
        else:
            c['status'] = 'idle'
        enriched.append(c)
    STATUS_ORDER = {'active': 0, 'recent': 1, 'idle': 2}
    enriched.sort(key=lambda x: (STATUS_ORDER.get(x.get('status', 'idle'), 9), x.get('ago_sec', 999999)))
    return enriched

def extract_text_summary(parsed, max_len=300):
    """从parsed protobuf中提取文本摘要"""
    texts = []
    def _walk(obj, depth=0):
        if depth > 6: return
        if isinstance(obj, str) and len(obj) > 3 and not obj.startswith('<') and 'sk-ws-' not in obj:
            texts.append(obj[:200])
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, depth + 1)
    _walk(parsed)
    combined = ' | '.join(texts[:10])
    return combined[:max_len] if combined else ''

# ── 结构化解析器: 万法归宗 · 打通一切底层数据 ──

def parse_mcp_states(parsed):
    """解析GetMcpServerStates → MCP服务器列表"""
    if not parsed: return []
    servers = []
    items = parsed.get('f1', [])
    for wrapper in items:
        if not isinstance(wrapper, dict): continue
        for srv in wrapper.get('f1', []):
            if not isinstance(srv, dict): continue
            server = {}
            # Name (f1)
            sf1 = srv.get('f1', [])
            if sf1:
                server['name'] = _extract_str(sf1[0]) or ''
            # Command (f2)
            sf2 = srv.get('f2', [])
            if sf2:
                server['command'] = _extract_str(sf2[0]) or ''
            # Args (f3)
            sf3 = srv.get('f3', [])
            if sf3:
                server['args'] = [_extract_str(a) or str(a) for a in sf3]
            # Status (f7: 1=starting, 2=running, 3=stopped)
            sf7 = srv.get('f7', [])
            if sf7 and isinstance(sf7[0], int):
                server['status'] = {1: 'starting', 2: 'running', 3: 'stopped'}.get(sf7[0], f'unknown({sf7[0]})')
            if not server.get('name'): continue
            # Tools (f4) — extract tool names
            tools = []
            for t_wrapper in wrapper.get('f4', []):
                if isinstance(t_wrapper, dict):
                    tf1 = t_wrapper.get('f1', [])
                    if tf1:
                        tn = _extract_str(tf1[0])
                        if tn: tools.append(tn)
            server['tools'] = tools
            server['tool_count'] = len(tools)
            servers.append(server)
    return servers

def parse_memories(parsed):
    """解析GetCascadeMemories/GetUserMemories → 记忆列表"""
    if not parsed: return []
    memories = []
    def _walk_memories(obj, depth=0):
        if depth > 5 or not isinstance(obj, dict): return
        texts = []
        for k, v in obj.items():
            if isinstance(v, list):
                for item in v:
                    s = _extract_str(item)
                    if s and len(s) > 5:
                        texts.append(s)
                    elif isinstance(item, dict):
                        _walk_memories(item, depth + 1)
        if texts:
            memories.extend([{'content': t[:500]} for t in texts])
    _walk_memories(parsed)
    return memories

def parse_user_settings(parsed):
    """解析GetUserSettings → 模型配置 + 功能设置"""
    if not parsed: return {}
    result = {'models': [], 'features': [], 'raw_fields': list(parsed.keys())}
    # Extract model list from f52 fields
    items = parsed.get('f1', [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict): continue
            f52_list = item.get('f52', [])
            for model_obj in f52_list:
                if not isinstance(model_obj, dict): continue
                model = {}
                mf1 = model_obj.get('f1', [])
                if mf1:
                    model['name'] = _extract_str(mf1[0]) or ''
                mf3 = model_obj.get('f3', [])
                if mf3 and isinstance(mf3[0], int):
                    model['context_window'] = mf3[0]
                mf5 = model_obj.get('f5', [])
                if mf5 and isinstance(mf5[0], int):
                    model['enabled'] = mf5[0]
                if model.get('name'):
                    result['models'].append(model)
            # Extract feature flags from f47
            f47_list = item.get('f47', [])
            for feat in f47_list:
                if not isinstance(feat, dict): continue
                ff1 = feat.get('f1', [])
                if ff1:
                    fname = _extract_str(ff1[0])
                    if fname:
                        result['features'].append(fname)
    return result

def parse_edit_state(parsed):
    """解析GetWorkspaceEditState → 编辑状态摘要"""
    if not parsed: return {}
    result = {'files': [], 'total_size': 0}
    texts = []
    def _collect(obj, depth=0):
        if depth > 4: return
        if isinstance(obj, str) and ('file:///' in obj or '\\' in obj or '/' in obj):
            if any(ext in obj.lower() for ext in ['.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.html', '.css', '.md', '.rs', '.go', '.java', '.c']):
                texts.append(obj[:300])
        elif isinstance(obj, dict):
            for v in obj.values():
                _collect(v, depth + 1)
        elif isinstance(obj, list):
            for i in obj:
                _collect(i, depth + 1)
    _collect(parsed)
    result['files'] = list(set(texts))[:50]
    result['file_count'] = len(result['files'])
    return result

def parse_diagnostics(parsed):
    """解析GetDebugDiagnostics → 诊断信息"""
    if not parsed: return {}
    result = {'entries': [], 'raw_keys': list(parsed.keys())}
    texts = []
    def _collect(obj, depth=0):
        if depth > 5: return
        if isinstance(obj, str) and len(obj) > 5 and not obj.startswith('<'):
            texts.append(obj[:500])
        elif isinstance(obj, dict):
            for v in obj.values():
                _collect(v, depth + 1)
        elif isinstance(obj, list):
            for i in obj:
                _collect(i, depth + 1)
    _collect(parsed)
    # Deduplicate and categorize
    seen = set()
    for t in texts:
        key = t[:50]
        if key not in seen:
            seen.add(key)
            result['entries'].append(t)
    result['entry_count'] = len(result['entries'])
    return result

def parse_workflows(parsed):
    """解析GetAllWorkflows → 工作流列表"""
    if not parsed: return []
    workflows = []
    texts = []
    def _collect(obj, depth=0):
        if depth > 4: return
        s = _extract_str(obj) if isinstance(obj, (dict, str)) else None
        if s and len(s) > 2:
            texts.append(s)
        if isinstance(obj, dict):
            for v in obj.values():
                _collect(v, depth + 1)
        elif isinstance(obj, list):
            for i in obj:
                _collect(i, depth + 1)
    _collect(parsed)
    return [{'name': t[:200]} for t in texts[:20]]

def parse_unleash(parsed):
    """解析GetUnleashData → 特性开关"""
    if not parsed: return {}
    flags = {}
    def _collect(obj, depth=0):
        if depth > 4: return
        if isinstance(obj, str) and len(obj) > 2 and not obj.startswith('<'):
            flags[obj] = True
        elif isinstance(obj, dict):
            for v in obj.values():
                _collect(v, depth + 1)
        elif isinstance(obj, list):
            for i in obj:
                _collect(i, depth + 1)
    _collect(parsed)
    return flags

def parse_workspace_infos(parsed):
    """解析GetWorkspaceInfos → 工作区列表"""
    if not parsed: return []
    workspaces = []
    def _collect(obj, depth=0):
        if depth > 4: return
        s = _extract_str(obj) if isinstance(obj, (dict, str)) else None
        if s and ('file:///' in s or '\\' in s):
            from urllib.parse import unquote
            path = unquote(s.replace('file:///', '')) if s.startswith('file:///') else s
            workspaces.append({'path': path})
        if isinstance(obj, dict):
            for v in obj.values():
                _collect(v, depth + 1)
        elif isinstance(obj, list):
            for i in obj:
                _collect(i, depth + 1)
    _collect(parsed)
    return workspaces

def _collect_all_strings(obj, depth=0):
    """递归收集protobuf对象中的所有字符串值"""
    if depth > 6:
        return []
    texts = []
    if isinstance(obj, dict):
        if '_str' in obj:
            s = obj['_str']
            if s and len(s) > 3:
                texts.append(s)
        if '_msg' in obj and isinstance(obj['_msg'], dict):
            texts.extend(_collect_all_strings(obj['_msg'], depth + 1))
        for k, v in obj.items():
            if k in ('_str', '_msg'):
                continue
            texts.extend(_collect_all_strings(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            texts.extend(_collect_all_strings(item, depth + 1))
    elif isinstance(obj, str) and len(obj) > 3:
        texts.append(obj)
    return texts

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
def _is_uuid_only(s):
    """检查字符串是否仅为UUID"""
    return bool(_UUID_RE.match(s.strip())) if isinstance(s, str) else False

def parse_trajectory_detail(parsed):
    """解析GetCascadeTrajectory → 单对话完整详情(含所有步骤内容)
    实际结构(逆向验证):
      parsed.f1[0] = trajectory object:
        .f1 = conversation UUID + metadata
        .f2[] = steps array: f1=num f4=status f5={f1=start_ts f3=type f8=end_ts f28=model f32=tok_in f33=tok_out}
        .f3 = model config
        .f6 = cascade ID
        .f7 = workspace info
    步骤内容在step级字段(f5外): f38=memory/mquery, f43=code, etc.
    用户消息/Agent回复从USER_INPUT(type=14)/PLANNER_RESPONSE(type=15)步骤中提取
    """
    if not parsed:
        return None
    detail = {'steps': [], 'user_messages': [], 'agent_replies': []}
    # Navigate to trajectory object: parsed.f1[0]
    f1_list = parsed.get('f1', [])
    traj = None
    for item in f1_list:
        if isinstance(item, dict) and 'f2' in item:
            traj = item
            break
    if not traj:
        return detail
    steps_raw = traj.get('f2', [])
    for s in steps_raw:
        if not isinstance(s, dict):
            continue
        step = {}
        sf1 = s.get('f1', [])
        if sf1 and isinstance(sf1[0], int):
            step['num'] = sf1[0]
        sf4 = s.get('f4', [])
        if sf4 and isinstance(sf4[0], int):
            step['status'] = sf4[0]
            step['status_name'] = STEP_STATUS_NAMES.get(sf4[0], f'UNKNOWN({sf4[0]})')
        sf5 = s.get('f5', [])
        if sf5 and isinstance(sf5[0], dict):
            d = sf5[0]
            df3 = d.get('f3', [])
            if df3 and isinstance(df3[0], int):
                step['type'] = df3[0]
                step['type_name'] = STEP_TYPE_NAMES.get(df3[0], f'TYPE_{df3[0]}')
            # Start timestamp: f5.f1[0].f1[0]
            df1 = d.get('f1', [])
            if df1 and isinstance(df1[0], dict):
                ts_f1 = df1[0].get('f1', [])
                if ts_f1 and isinstance(ts_f1[0], int) and ts_f1[0] > 1700000000:
                    step['ts_start'] = ts_f1[0]
            # End timestamp: f5.f8[0].f1[0] (verified: f8 not f6)
            df8 = d.get('f8', [])
            if df8 and isinstance(df8[0], dict):
                ts_e = df8[0].get('f1', [])
                if ts_e and isinstance(ts_e[0], int) and ts_e[0] > 1700000000:
                    step['ts_end'] = ts_e[0]
            # Also try f6 as fallback
            if 'ts_end' not in step:
                df6 = d.get('f6', [])
                if df6 and isinstance(df6[0], dict):
                    ts_e = df6[0].get('f1', [])
                    if ts_e and isinstance(ts_e[0], int) and ts_e[0] > 1700000000:
                        step['ts_end'] = ts_e[0]
            if step.get('ts_start') and step.get('ts_end'):
                step['duration_sec'] = step['ts_end'] - step['ts_start']
            df28 = d.get('f28', [])
            if df28:
                m = _extract_str(df28[0])
                if m:
                    step['model'] = m
            df32 = d.get('f32', [])
            if df32 and isinstance(df32[0], int):
                step['tokens_in'] = df32[0]
            df33 = d.get('f33', [])
            if df33 and isinstance(df33[0], int):
                step['tokens_out'] = df33[0]
            # Content from f5 inner fields (filter UUIDs and metadata)
            content_texts = []
            for key in sorted(d.keys()):
                if key in ('f1', 'f3', 'f6', 'f8', 'f12', 'f28', 'f32', 'f33'):
                    continue
                for v in d[key]:
                    t = _extract_str(v)
                    if t and len(t) > 3 and not _is_uuid_only(t):
                        content_texts.append(t[:2000])
            if content_texts:
                step['content'] = content_texts[:10]
        # Content from step-level fields (outside f5): f20=agent_reply, f19=user_input, f38=memory, f43=code...
        step_content = []
        user_text = None
        agent_text = None
        for k in sorted(s.keys()):
            if k in ('f1', 'f4', 'f5'):
                continue
            texts = _collect_all_strings(s[k])
            meaningful = [t for t in texts if len(t) > 3 and not _is_uuid_only(t)]
            if k == 'f19' and meaningful:
                user_text = meaningful[0][:3000]
            elif k == 'f20' and meaningful:
                agent_text = meaningful[0][:3000]
            step_content.extend(meaningful)
        if step_content:
            existing = step.get('content', [])
            # Filter out UUID-only items from existing too
            existing = [c for c in existing if not _is_uuid_only(c)]
            step['content'] = (existing + step_content)[:15]
        # Extract user messages and agent replies from step-level fields
        if user_text:
            detail['user_messages'].append(user_text)
        if agent_text:
            detail['agent_replies'].append(agent_text)
        detail['steps'].append(step)
    # Sort steps by step number for correct chronological order
    detail['steps'].sort(key=lambda x: x.get('num', 0))
    detail['step_count'] = len(detail['steps'])
    detail['total_tokens_in'] = sum(s.get('tokens_in', 0) for s in detail['steps'])
    detail['total_tokens_out'] = sum(s.get('tokens_out', 0) for s in detail['steps'])
    return detail

# ══════════════════════════════════════════════════════════════════
# Dashboard Server — HTTP + SSE
# ══════════════════════════════════════════════════════════════════

_event_log_ref = None  # 全局引用
_monitor_ref = None   # 全局监控器引用
_guard_cache = {'data': {'running': False}, 'ts': 0}  # guard缓存(15s TTL)

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默HTTP日志

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self._serve_dashboard()
        elif self.path == '/events':
            self._serve_sse()
        elif self.path == '/api/status':
            self._serve_json(_event_log_ref.get_stats() if _event_log_ref else {})
        elif self.path.startswith('/api/history'):
            self._serve_history()
        elif self.path == '/api/snapshot':
            self._serve_snapshot()
        elif self.path == '/api/conversations':
            self._serve_conversations()
        elif self.path == '/api/processes':
            self._serve_processes()
        elif self.path == '/api/guard':
            self._serve_guard_status()
        elif self.path == '/api/deltas':
            self._serve_deltas()
        elif self.path == '/api/health':
            self._serve_health()
        # ── 万法归宗: 全量数据API ──
        elif self.path == '/api/memories':
            self._serve_json({
                'cascade': _monitor_ref._cached_memories if _monitor_ref else [],
                'user': _monitor_ref._cached_user_memories if _monitor_ref else [],
            })
        elif self.path == '/api/mcp':
            self._serve_json(_monitor_ref._cached_mcp_states if _monitor_ref else [])
        elif self.path == '/api/settings':
            self._serve_json(_monitor_ref._cached_user_settings if _monitor_ref else {})
        elif self.path == '/api/edit-state':
            self._serve_json(_monitor_ref._cached_edit_state if _monitor_ref else {})
        elif self.path == '/api/diagnostics':
            self._serve_json(_monitor_ref._cached_diagnostics if _monitor_ref else {})
        elif self.path == '/api/workflows':
            self._serve_json(_monitor_ref._cached_workflows if _monitor_ref else [])
        elif self.path == '/api/unleash':
            self._serve_json(_monitor_ref._cached_unleash if _monitor_ref else {})
        elif self.path == '/api/workspaces':
            self._serve_json(_monitor_ref._cached_workspace_infos if _monitor_ref else [])
        elif self.path == '/api/languages':
            self._serve_json(_monitor_ref._cached_languages if _monitor_ref else [])
        elif self.path == '/api/web-origins':
            self._serve_json(_monitor_ref._cached_web_origins if _monitor_ref else [])
        elif self.path == '/api/state':
            self._serve_state_db()
        elif self.path == '/api/grpc-methods':
            self._serve_grpc_methods()
        elif self.path.startswith('/api/grpc-raw/'):
            method = self.path.split('/api/grpc-raw/')[-1]
            self._serve_grpc_raw(method)
        elif self.path == '/api/all':
            self._serve_all()
        elif self.path.startswith('/api/conversation/'):
            conv_id = self.path.split('/api/conversation/')[1].split('?')[0].strip('/')
            if conv_id:
                self._serve_conversation_detail(conv_id)
            else:
                self.send_error(404)
        elif self.path == '/api/capabilities':
            self._serve_capabilities()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/api/grpc':
            self._handle_grpc_invoke()
        elif self.path == '/api/conversation/rename':
            self._handle_conv_action('rename')
        elif self.path == '/api/conversation/delete':
            self._handle_conv_action('delete')
        elif self.path == '/api/conversation/cancel':
            self._handle_conv_action('cancel')
        elif self.path == '/api/conversation/revert':
            self._handle_conv_action('revert')
        elif self.path == '/api/cascade/new':
            self._handle_new_cascade()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    @staticmethod
    def _get_guard_cached():
        """缓存guard PowerShell查询(15s TTL), 避免每次请求296ms开销"""
        global _guard_cache
        now = time.time()
        if now - _guard_cache['ts'] < 15:
            return _guard_cache['data']
        result = {'running': False, 'pids': [], 'mem_mb': 0}
        try:
            out = subprocess.check_output(
                ['powershell', '-NoProfile', '-Command',
                 'Get-Process -Name python* -EA SilentlyContinue | '
                 'Where-Object { $_.CommandLine -match "cascade_terminal_guard" } | '
                 'Select-Object Id,@{N="MB";E={[math]::Round($_.WorkingSet64/1MB)}},'
                 '@{N="Cmd";E={$_.CommandLine.Substring(0,[Math]::Min(120,$_.CommandLine.Length))}} | '
                 'ConvertTo-Json -Compress'],
                text=True, timeout=5, creationflags=0x08000000
            ).strip()
            if out:
                gdata = json.loads(out)
                if isinstance(gdata, dict): gdata = [gdata]
                result = {'running': True, 'pids': gdata,
                          'mem_mb': sum(g.get('MB', 0) for g in gdata)}
        except: pass
        _guard_cache = {'data': result, 'ts': now}
        return result

    def _serve_dashboard(self):
        if DASH_FILE.exists():
            content = DASH_FILE.read_bytes()
        else:
            content = b'<html><body><h1>Dashboard not found</h1></body></html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)

    def _serve_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        q = deque(maxlen=1000)
        if _event_log_ref:
            _event_log_ref.sse_queues.append(q)

        # 发送历史事件
        if _event_log_ref:
            for evt in list(_event_log_ref.events)[-100:]:
                data = json.dumps(evt, ensure_ascii=False, default=str)
                self.wfile.write(f'data: {data}\n\n'.encode('utf-8'))
            self.wfile.flush()

        try:
            while True:
                if q:
                    data = q.popleft()
                    self.wfile.write(f'data: {data}\n\n'.encode('utf-8'))
                    self.wfile.flush()
                else:
                    # 心跳
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                    time.sleep(1)
        except:
            pass
        finally:
            if _event_log_ref and q in _event_log_ref.sse_queues:
                _event_log_ref.sse_queues.remove(q)

    def _serve_json(self, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _serve_history(self):
        if _event_log_ref:
            events = list(_event_log_ref.events)[-200:]
            # 精简: 移除大载荷(trajectories内嵌对象), 减小响应体积
            slim = []
            for evt in events:
                e = dict(evt)
                if isinstance(e.get('data'), dict):
                    d = dict(e['data'])
                    d.pop('trajectories', None)
                    if 'summary' in d and isinstance(d['summary'], str):
                        d['summary'] = d['summary'][:200]
                    e['data'] = d
                slim.append(e)
            self._serve_json(slim)
        else:
            self._serve_json([])

    def _serve_snapshot(self):
        """返回全量结构化快照: LS信息 + 对话列表 + 进程 + 守护状态"""
        snap = {
            'ts': datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'),
            'ls': None, 'conversations': [], 'processes': [],
            'guard': {'running': False}, 'stats': {},
        }
        if _monitor_ref:
            snap['ls'] = {
                'pid': _monitor_ref.ls_pid,
                'grpc_port': _monitor_ref.grpc_port,
                'csrf': bool(_monitor_ref.csrf_token),
            }
            snap['conversations'] = _enrich_conversations(_monitor_ref._cached_trajectories)
            snap['processes'] = _monitor_ref._cached_processes
        if _event_log_ref:
            snap['stats'] = _event_log_ref.get_stats()
        guard_full = self._get_guard_cached()
        snap['guard'] = {
            'running': guard_full.get('running', False),
            'pids': [p.get('Id') if isinstance(p, dict) else p for p in guard_full.get('pids', [])],
            'mem_mb': guard_full.get('mem_mb', 0),
        }
        self._serve_json(snap)

    def _serve_conversations(self):
        """返回结构化对话列表(动态计算ago_sec/status)"""
        raw = _monitor_ref._cached_trajectories if _monitor_ref else []
        self._serve_json(_enrich_conversations(raw))

    def _serve_processes(self):
        """返回Windsurf进程树(从缓存)"""
        procs = _monitor_ref._cached_processes if _monitor_ref else {}
        self._serve_json(procs)

    def _serve_guard_status(self):
        """返回终端看护进程状态(使用缓存 + 日志尾部)"""
        guard = self._get_guard_cached()
        result = dict(guard)
        result.setdefault('log_tail', [])
        guard_log = LOG_DIR / 'guard_output.log'
        if guard_log.exists():
            try:
                lines = guard_log.read_text(encoding='utf-8', errors='replace').strip().split('\n')
                result['log_tail'] = lines[-30:]
            except: pass
        self._serve_json(result)

    def _serve_deltas(self):
        """返回最近对话变化(去芜留菁: 只有实际变化)"""
        deltas = _monitor_ref._conv_deltas if _monitor_ref else []
        self._serve_json(deltas[-30:])

    def _serve_health(self):
        """系统健康: 持久化运行状态一览"""
        h = {'alive': True, 'uptime_sec': 0, 'ls_pid': None, 'grpc_port': None,
             'grpc_ok': False, 'fail_streak': 0, 'conv_count': 0, 'discovery_cycle': 0}
        if _monitor_ref:
            h['uptime_sec'] = int(time.time()) - _monitor_ref._start_epoch
            h['ls_pid'] = _monitor_ref.ls_pid
            h['grpc_port'] = _monitor_ref.grpc_port
            h['grpc_ok'] = _monitor_ref._grpc_fail_streak == 0
            h['fail_streak'] = _monitor_ref._grpc_fail_streak
            h['conv_count'] = len(_monitor_ref._cached_trajectories)
            h['discovery_cycle'] = _monitor_ref._discovery_cycle
            # 万法归宗: 全量缓存摘要
            h['cached'] = {
                'memories': len(_monitor_ref._cached_memories),
                'user_memories': len(_monitor_ref._cached_user_memories),
                'mcp_servers': len(_monitor_ref._cached_mcp_states),
                'models': len(_monitor_ref._cached_user_settings.get('models', [])),
                'edit_files': _monitor_ref._cached_edit_state.get('file_count', 0),
                'diagnostics': _monitor_ref._cached_diagnostics.get('entry_count', 0),
                'workflows': len(_monitor_ref._cached_workflows),
                'unleash_flags': len(_monitor_ref._cached_unleash),
                'workspaces': len(_monitor_ref._cached_workspace_infos),
                'languages': len(_monitor_ref._cached_languages),
                'web_origins': len(_monitor_ref._cached_web_origins),
                'grpc_methods_cached': len(_monitor_ref._cached_grpc_raw),
            }
        if _event_log_ref:
            h['total_events'] = sum(_event_log_ref.get_stats().values())
        self._serve_json(h)

    def _serve_state_db(self):
        """深度读取SQLite state.vscdb全量键值(copy-based避免锁冲突)"""
        if _monitor_ref:
            db_path = WINDSURF_DATA / 'User' / 'globalStorage' / 'state.vscdb'
            rows = None
            if db_path.exists():
                try:
                    import shutil, tempfile
                    tmp = tempfile.mktemp(suffix='.db')
                    shutil.copy2(str(db_path), tmp)
                    conn = sqlite3.connect(tmp, timeout=3)
                    rows = conn.execute(
                        "SELECT key, substr(value, 1, 500) as val, length(value) as vlen FROM ItemTable ORDER BY rowid DESC LIMIT 200"
                    ).fetchall()
                    conn.close()
                    os.remove(tmp)
                except Exception:
                    pass
            if rows:
                result = [{'key': r[0], 'value_preview': (r[1] or '')[:500], 'value_len': r[2]} for r in rows]
                _monitor_ref._cached_state_db = result
                self._serve_json(result)
            else:
                self._serve_json(_monitor_ref._cached_state_db or [])
        else:
            self._serve_json([])

    def _serve_grpc_methods(self):
        """返回所有已知gRPC方法及缓存状态"""
        all_methods = GRPC_METHODS_FAST + GRPC_METHODS_MED + GRPC_METHODS_SLOW
        result = []
        for method, desc in all_methods:
            entry = {'method': method, 'desc': desc, 'cached': False}
            if _monitor_ref and method in _monitor_ref._cached_grpc_raw:
                raw = _monitor_ref._cached_grpc_raw[method]
                entry['cached'] = True
                entry['cache_ts'] = raw['ts']
                entry['cache_size'] = raw['size']
                entry['cache_age'] = int(time.time()) - raw['ts']
            result.append(entry)
        # 额外: GetUserTrajectoryDebug
        result.append({'method': 'GetUserTrajectoryDebug', 'desc': '完整轨迹调试(6MB+)',
                       'cached': 'GetUserTrajectoryDebug' in (_monitor_ref._cached_grpc_raw if _monitor_ref else {})})
        self._serve_json(result)

    def _serve_grpc_raw(self, method):
        """返回指定gRPC方法的原始缓存结果"""
        if _monitor_ref and method in _monitor_ref._cached_grpc_raw:
            raw = _monitor_ref._cached_grpc_raw[method]
            self._serve_json({
                'method': method, 'ts': raw['ts'], 'size': raw['size'],
                'data': raw['parsed']
            })
        else:
            self._serve_json({'error': f'No cache for {method}', 'available': list(_monitor_ref._cached_grpc_raw.keys()) if _monitor_ref else []})

    def _handle_grpc_invoke(self):
        """POST /api/grpc — 实时调用任意gRPC方法(万法归宗核心)"""
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
            method = body.get('method', '')
            service = body.get('service', LS_SERVICE)
            if not method:
                self._serve_json({'error': 'method required'})
                return
            if not _monitor_ref or not _monitor_ref.grpc.port:
                self._serve_json({'error': 'gRPC not connected'})
                return
            # 构造请求体(支持cascade_id自动编码或body_hex直传)
            grpc_body = None
            if body.get('cascade_id'):
                grpc_body = encode_proto_string(1, body['cascade_id'])
            elif body.get('body_hex'):
                try:
                    grpc_body = bytes.fromhex(body['body_hex'])
                except Exception:
                    pass
            # 实时调用
            grpc = _monitor_ref.grpc
            status, parsed, raw_bytes, size = grpc.call(method, service=service, body=grpc_body or b'')
            if status <= 0:
                status, parsed, raw_bytes, size = grpc.call_connect(method, service=service, body=grpc_body or b'')
            result = {
                'method': method, 'service': service,
                'http_status': status, 'size': size,
                'data': parsed,
                'summary': extract_text_summary(parsed) if parsed else '',
            }
            # 同时更新缓存
            if status == 200 and parsed:
                _monitor_ref._cached_grpc_raw[method] = {
                    'parsed': parsed, 'ts': int(time.time()), 'size': size
                }
            self._serve_json(result)
        except Exception as e:
            self._serve_json({'error': str(e)})

    def _serve_conversation_detail(self, conv_id):
        """获取单个对话完整详情: GetCascadeTrajectory → 全步骤+内容+token"""
        if not _monitor_ref or not _monitor_ref.grpc.port:
            self._serve_json({'error': 'gRPC not connected'})
            return
        # Check cache (30s TTL)
        cached = _monitor_ref._cached_trajectory_details.get(conv_id)
        if cached and (int(time.time()) - cached['ts']) < 30:
            self._serve_json(cached['detail'])
            return
        # Fetch via gRPC
        body = encode_proto_string(1, conv_id)
        grpc = _monitor_ref.grpc
        status, parsed, raw, size = grpc.call('GetCascadeTrajectory', body=body)
        if status <= 0:
            status, parsed, raw, size = grpc.call_connect('GetCascadeTrajectory', body=body)
        if status == 200 and parsed:
            detail = parse_trajectory_detail(parsed)
            if detail:
                # Enrich with basic info from cached trajectories
                for t in (_monitor_ref._cached_trajectories or []):
                    if t.get('id') == conv_id:
                        detail['id'] = conv_id
                        detail['title'] = t.get('title', '')
                        detail['model'] = t.get('model', '')
                        detail['workspace'] = t.get('workspace', '')
                        detail['files'] = t.get('files', [])
                        detail['ts_update'] = t.get('ts_update')
                        detail['ts_created'] = t.get('ts_created')
                        detail['status'] = t.get('status', 'idle')
                        break
                else:
                    detail['id'] = conv_id
                # Cache
                _monitor_ref._cached_trajectory_details[conv_id] = {
                    'detail': detail, 'ts': int(time.time())
                }
                self._serve_json(detail)
                return
        # Fallback: return basic info from cached trajectories
        for t in (_monitor_ref._cached_trajectories or []):
            if t.get('id') == conv_id:
                self._serve_json({**t, 'steps': t.get('steps', []),
                                  'note': 'detail fetch failed, showing cached summary'})
                return
        self._serve_json({'error': f'conversation {conv_id} not found', 'status': status})

    def _serve_capabilities(self):
        """列出所有可用的用户操作能力 — 万法归宗 · 能力清单"""
        caps = {
            'read': [
                {'action': 'list_conversations', 'method': 'GET', 'path': '/api/conversations'},
                {'action': 'get_conversation', 'method': 'GET', 'path': '/api/conversation/{id}'},
                {'action': 'get_snapshot', 'method': 'GET', 'path': '/api/snapshot'},
                {'action': 'get_health', 'method': 'GET', 'path': '/api/health'},
                {'action': 'get_memories', 'method': 'GET', 'path': '/api/memories'},
                {'action': 'get_mcp', 'method': 'GET', 'path': '/api/mcp'},
                {'action': 'get_settings', 'method': 'GET', 'path': '/api/settings'},
                {'action': 'get_processes', 'method': 'GET', 'path': '/api/processes'},
                {'action': 'get_state_db', 'method': 'GET', 'path': '/api/state'},
                {'action': 'get_all', 'method': 'GET', 'path': '/api/all'},
                {'action': 'get_capabilities', 'method': 'GET', 'path': '/api/capabilities'},
            ],
            'write': [
                {'action': 'rename_conversation', 'method': 'POST', 'path': '/api/conversation/rename',
                 'params': {'id': 'cascade_id', 'title': '新标题'}},
                {'action': 'delete_conversation', 'method': 'POST', 'path': '/api/conversation/delete',
                 'params': {'id': 'cascade_id'}},
                {'action': 'cancel_cascade', 'method': 'POST', 'path': '/api/conversation/cancel',
                 'params': {'id': 'cascade_id'}},
                {'action': 'revert_step', 'method': 'POST', 'path': '/api/conversation/revert',
                 'params': {'id': 'cascade_id', 'step': '步骤号'}},
                {'action': 'new_cascade', 'method': 'POST', 'path': '/api/cascade/new'},
                {'action': 'invoke_grpc', 'method': 'POST', 'path': '/api/grpc',
                 'params': {'method': 'gRPC方法名'}},
            ],
            'grpc_control_methods': [
                'CancelCascadeInvocation', 'CancelCascadeInvocationAndWait',
                'RevertToCascadeStep', 'RenameCascadeTrajectory', 'DeleteCascadeTrajectory',
                'SendActionToChatPanel', 'SendUserCascadeMessage', 'BranchCascade',
                'DeleteCascadeMemory', 'UpdateCascadeMemory', 'RefreshMcpServers',
            ],
        }
        self._serve_json(caps)

    def _handle_conv_action(self, action):
        """处理对话管理操作: rename/delete/cancel/revert"""
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
            conv_id = body.get('id', '')
            if not conv_id:
                self._serve_json({'error': 'id required'})
                return
            if not _monitor_ref or not _monitor_ref.grpc.port:
                self._serve_json({'error': 'gRPC not connected'})
                return
            grpc = _monitor_ref.grpc
            if action == 'rename':
                title = body.get('title', '')
                if not title:
                    self._serve_json({'error': 'title required'})
                    return
                proto_body = encode_proto_string(1, conv_id) + encode_proto_string(2, title)
                status, parsed, raw, size = grpc.call('RenameCascadeTrajectory', body=proto_body)
                if status <= 0:
                    status, parsed, raw, size = grpc.call_connect('RenameCascadeTrajectory', body=proto_body)
            elif action == 'delete':
                proto_body = encode_proto_string(1, conv_id)
                status, parsed, raw, size = grpc.call('DeleteCascadeTrajectory', body=proto_body)
                if status <= 0:
                    status, parsed, raw, size = grpc.call_connect('DeleteCascadeTrajectory', body=proto_body)
            elif action == 'cancel':
                proto_body = encode_proto_string(1, conv_id)
                status, parsed, raw, size = grpc.call('CancelCascadeInvocation', body=proto_body)
                if status <= 0:
                    status, parsed, raw, size = grpc.call_connect('CancelCascadeInvocation', body=proto_body)
            elif action == 'revert':
                step_num = body.get('step', 0)
                proto_body = encode_proto_string(1, conv_id) + encode_proto_varint_field(2, int(step_num))
                status, parsed, raw, size = grpc.call('RevertToCascadeStep', body=proto_body)
                if status <= 0:
                    status, parsed, raw, size = grpc.call_connect('RevertToCascadeStep', body=proto_body)
            else:
                self._serve_json({'error': f'unknown action: {action}'})
                return
            result = {
                'action': action, 'id': conv_id,
                'http_status': status, 'size': size,
                'success': status == 200,
                'data': parsed,
            }
            if status == 200:
                _monitor_ref._cached_trajectory_details.pop(conv_id, None)
            self._serve_json(result)
        except Exception as e:
            self._serve_json({'error': str(e)})

    def _handle_new_cascade(self):
        """创建新Cascade对话 (SendActionToChatPanel: newChat)"""
        try:
            if not _monitor_ref or not _monitor_ref.grpc.port:
                self._serve_json({'error': 'gRPC not connected'})
                return
            grpc = _monitor_ref.grpc
            proto_body = encode_proto_string(1, 'newChat')
            status, parsed, raw, size = grpc.call('SendActionToChatPanel', body=proto_body)
            if status <= 0:
                status, parsed, raw, size = grpc.call_connect('SendActionToChatPanel', body=proto_body)
            self._serve_json({
                'action': 'new_cascade',
                'http_status': status, 'success': status == 200,
                'data': parsed,
            })
        except Exception as e:
            self._serve_json({'error': str(e)})

    def _serve_all(self):
        """万法归宗: 返回所有缓存数据的完整汇总"""
        result = {
            'ts': datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'),
            'health': {},
            'conversations': [],
            'processes': {},
            'memories': {'cascade': [], 'user': []},
            'mcp_servers': [],
            'settings': {},
            'edit_state': {},
            'diagnostics': {},
            'workflows': [],
            'unleash': {},
            'workspaces': [],
            'languages': [],
            'web_origins': [],
        }
        if _monitor_ref:
            result['health'] = {
                'ls_pid': _monitor_ref.ls_pid,
                'grpc_port': _monitor_ref.grpc_port,
                'csrf': bool(_monitor_ref.csrf_token),
                'uptime_sec': int(time.time()) - _monitor_ref._start_epoch,
                'grpc_ok': _monitor_ref._grpc_fail_streak == 0,
            }
            result['conversations'] = _enrich_conversations(_monitor_ref._cached_trajectories)
            result['processes'] = _monitor_ref._cached_processes
            result['memories'] = {
                'cascade': _monitor_ref._cached_memories,
                'user': _monitor_ref._cached_user_memories,
            }
            result['mcp_servers'] = _monitor_ref._cached_mcp_states
            result['settings'] = _monitor_ref._cached_user_settings
            result['edit_state'] = _monitor_ref._cached_edit_state
            result['diagnostics'] = _monitor_ref._cached_diagnostics
            result['workflows'] = _monitor_ref._cached_workflows
            result['unleash'] = _monitor_ref._cached_unleash
            result['workspaces'] = _monitor_ref._cached_workspace_infos
            result['languages'] = _monitor_ref._cached_languages
            result['web_origins'] = _monitor_ref._cached_web_origins
        if _event_log_ref:
            result['event_stats'] = _event_log_ref.get_stats()
        self._serve_json(result)

# ══════════════════════════════════════════════════════════════════
# 主监控引擎
# ══════════════════════════════════════════════════════════════════

class WindsurfHotMonitor:
    def __init__(self, port=MONITOR_PORT, no_dash=False, once=False):
        self.port = port
        self.no_dash = no_dash
        self.once = once
        self.log = EventLog()
        self.scanner = ProcessScanner(self.log)
        self.state_reader = StateReader(self.log)
        self.file_monitor = FileMonitor(self.log)
        self.grpc = GrpcWebClient()
        self.ls_pid = None
        self.grpc_port = None
        self.csrf_token = None
        self._running = True
        self._discovery_cycle = 0
        self._cached_trajectories = []  # API缓存
        self._cached_processes = {}     # API缓存
        self._cached_memories = []      # Cascade记忆
        self._cached_user_memories = [] # 用户记忆
        self._cached_mcp_states = []    # MCP服务器状态
        self._cached_user_settings = {} # 用户设置/模型目录
        self._cached_edit_state = {}    # 工作区编辑状态
        self._cached_diagnostics = {}   # 调试诊断
        self._cached_workflows = []     # 工作流
        self._cached_unleash = {}       # 特性开关
        self._cached_workspace_infos = [] # 工作区信息
        self._cached_languages = []     # 支持语言
        self._cached_web_origins = []   # Web搜索源
        self._cached_grpc_raw = {}      # method -> {parsed, ts, size} 所有gRPC原始结果
        self._cached_state_db = []      # SQLite state.vscdb 完整键值
        self._cached_trajectory_details = {}  # conv_id -> {detail, ts}
        self._grpc_fail_streak = 0      # 连续失败计数
        self._last_conv_hash = ''       # 对话变化检测
        self._conv_deltas = []          # 最近变化记录
        self._start_epoch = int(time.time())

    def discover(self):
        """发现LS进程、端口、CSRF令牌"""
        self._discovery_cycle += 1
        header = '发现' if self._discovery_cycle == 1 else '重新发现'

        print(f'\n{"═"*60}')
        print(f'  {header}阶段 · 寻找Windsurf底层')
        print(f'{"═"*60}')

        # 1. 扫描进程
        procs = self.scanner.scan_processes()
        ws_count = len(procs['windsurf'])
        ls_list = procs['ls']

        self.log.emit('discover', {
            'msg': f'Windsurf进程: {ws_count}个, LS: {len(ls_list)}个',
            'windsurf_count': ws_count,
            'ls_pids': [p['pid'] for p in ls_list]
        })

        if not ls_list:
            self.log.emit('error', {'msg': 'Language Server未运行'})
            return False

        # 选择最大的LS进程(主LS)
        ls = max(ls_list, key=lambda p: p['mem_mb'])
        self.ls_pid = ls['pid']
        self.log.emit('discover', {
            'msg': f'主LS: PID={self.ls_pid} Mem={ls["mem_mb"]}MB CPU={ls["cpu"]}s'
        })

        # 2. 识别gRPC端口
        grpc_port, all_ports = self.scanner.identify_grpc_port(self.ls_pid)
        self.grpc_port = grpc_port
        self.log.emit('discover', {
            'msg': f'LS端口: {all_ports} | gRPC-Web主端口: {grpc_port}'
        })

        if not grpc_port:
            self.log.emit('error', {'msg': '无法识别gRPC端口'})
            return False

        # 3. 提取CSRF令牌
        print(f'\n  🔑 提取CSRF令牌...')

        # 先从LS进程提取
        csrf = read_process_env_var(self.ls_pid, 'WINDSURF_CSRF_TOKEN')
        if csrf:
            self.csrf_token = csrf
            self.log.emit('csrf', {
                'msg': f'从LS进程(PID={self.ls_pid})提取CSRF: {csrf[:8]}...{csrf[-4:]}',
                'source': 'ls_process'
            })
        else:
            # 从Extension Host提取
            ext_pid = self.scanner.find_extension_host_pid()
            if ext_pid:
                csrf = read_process_env_var(ext_pid, 'WINDSURF_CSRF_TOKEN')
                if csrf:
                    self.csrf_token = csrf
                    self.log.emit('csrf', {
                        'msg': f'从ExtHost(PID={ext_pid})提取CSRF: {csrf[:8]}...{csrf[-4:]}',
                        'source': 'ext_host'
                    })

        if not self.csrf_token:
            # 遍历所有Windsurf进程
            for p in procs['windsurf']:
                csrf = read_process_env_var(p['pid'], 'WINDSURF_CSRF_TOKEN')
                if csrf:
                    self.csrf_token = csrf
                    self.log.emit('csrf', {
                        'msg': f'从Windsurf(PID={p["pid"]})提取CSRF: {csrf[:8]}...{csrf[-4:]}',
                        'source': f'windsurf_{p["pid"]}'
                    })
                    break

        if not self.csrf_token:
            self.log.emit('info', {'msg': '⚠ 未提取到CSRF令牌,尝试无认证探测'})

        # 4. 配置gRPC客户端
        self.grpc = GrpcWebClient('127.0.0.1', self.grpc_port, self.csrf_token)

        # 5. 连接测试
        print(f'\n  📡 gRPC连通性测试...')
        status, data, raw, size = self.grpc.call('Heartbeat')
        if status == 200:
            self.log.emit('grpc', {
                'method': 'Heartbeat', 'status': '✅ OK',
                'size': size, 'summary': str(data)[:100]
            })
        else:
            # 尝试connect协议
            status2, data2, raw2, size2 = self.grpc.call_connect('Heartbeat')
            if status2 == 200:
                self.log.emit('grpc', {
                    'method': 'Heartbeat(connect)', 'status': '✅ OK',
                    'size': size2
                })
            else:
                self.log.emit('error', {
                    'msg': f'Heartbeat失败: HTTP {status} / connect HTTP {status2}',
                    'grpc_error': str(data),
                    'connect_error': str(data2),
                })
                # 尝试其他端口
                all_ports_except = [p for p in (self.scanner.scan_ports(self.ls_pid) or []) if p != self.grpc_port]
                for alt_port in all_ports_except:
                    self.grpc.port = alt_port
                    s, d, r, sz = self.grpc.call('Heartbeat')
                    if s == 200:
                        self.grpc_port = alt_port
                        self.log.emit('discover', {'msg': f'备用端口 {alt_port} 连通!'})
                        break
                    s2, d2, r2, sz2 = self.grpc.call_connect('Heartbeat')
                    if s2 == 200:
                        self.grpc_port = alt_port
                        self.log.emit('discover', {'msg': f'备用端口 {alt_port} (connect) 连通!'})
                        break

        # 6. 网络连接概览
        conns = self.scanner.scan_connections(self.ls_pid)
        self.log.emit('discover', {
            'msg': f'LS网络: {len(conns["listening"])}监听, {len(conns["local"])}本地, {len(conns["external"])}外部',
            'external': conns['external'][:10]
        })

        # 7. 文件监控初始化
        self.file_monitor.setup()
        self.log.emit('discover', {'msg': '文件监控已初始化'})

        print(f'\n{"═"*60}')
        print(f'  ✅ 发现完成 · LS={self.ls_pid} Port={self.grpc_port} CSRF={"有" if self.csrf_token else "无"}')
        print(f'{"═"*60}\n')

        return True

    def _compute_conv_delta(self, new_trajs):
        """对话变化检测: 只在真正变化时emit SSE, 去苜留菁"""
        # 生成轻量级hash: id+count+ts_update
        sig = '|'.join(f'{t.get("id","")[:8]}:{t.get("count",0)}:{t.get("ts_update",0)}' for t in (new_trajs or []))
        if sig == self._last_conv_hash:
            return  # 无变化, 静默
        self._last_conv_hash = sig

        # 建立旧映射
        old_map = {t.get('id'): t for t in self._cached_trajectories}
        deltas = []
        for t in (new_trajs or []):
            tid = t.get('id')
            if not tid:
                continue
            old = old_map.get(tid)
            if not old:
                deltas.append({'type': 'new', 'id': tid, 'title': t.get('title', '?')[:60]})
            else:
                changes = []
                if t.get('count', 0) != old.get('count', 0):
                    changes.append(f'steps:{old.get("count",0)}→{t.get("count",0)}')
                if t.get('ts_update', 0) != old.get('ts_update', 0):
                    changes.append('updated')
                if t.get('model') != old.get('model'):
                    changes.append(f'model→{t.get("model","?")}')
                new_files = set(t.get('files', []))
                old_files = set(old.get('files', []))
                added_files = new_files - old_files
                if added_files:
                    changes.append(f'+{len(added_files)}文件')
                if changes:
                    deltas.append({'type': 'change', 'id': tid,
                                   'title': t.get('title', '?')[:60],
                                   'changes': changes})
        if deltas:
            self._conv_deltas = (self._conv_deltas + deltas)[-50:]
            self.log.emit('trajectory', {
                'method': 'ConvDelta', 'desc': '对话变化',
                'status': '✅', 'size': len(deltas),
                'summary': ' | '.join(f'{d["title"][:25]}[{"|".join(d.get("changes",[d["type"]]))}]' for d in deltas[:5])
            })

    def poll_grpc_methods(self, methods, label):
        """批量轮询gRPC方法, 带健康跟踪"""
        for method, desc in methods:
            try:
                status, parsed, raw, size = self.grpc.call(method)
                if status <= 0:
                    status, parsed, raw, size = self.grpc.call_connect(method)

                if status == 200 and parsed:
                    self._grpc_fail_streak = 0  # 重置失败计数
                    summary = extract_text_summary(parsed)
                    cat = 'grpc'

                    # 万法归宗: 缓存所有gRPC原始结果
                    self._cached_grpc_raw[method] = {
                        'parsed': parsed, 'ts': int(time.time()), 'size': size
                    }

                    # 结构化解析 + 分类缓存
                    if method == 'GetAllCascadeTrajectories':
                        cat = 'trajectory'
                        trajs = analyze_trajectories(parsed)
                        if trajs:
                            self._compute_conv_delta(trajs)
                            self._cached_trajectories = trajs
                            summary = f'{len(trajs)}条轨迹'
                    elif method == 'GetWorkspaceEditState':
                        cat = 'edit'
                        self._cached_edit_state = parse_edit_state(parsed)
                        summary = f'{self._cached_edit_state.get("file_count",0)}个文件'
                    elif method == 'GetMcpServerStates':
                        cat = 'mcp'
                        self._cached_mcp_states = parse_mcp_states(parsed)
                        summary = f'{len(self._cached_mcp_states)}个MCP服务器'
                    elif method == 'GetCascadeMemories':
                        self._cached_memories = parse_memories(parsed)
                        summary = f'{len(self._cached_memories)}条记忆'
                    elif method == 'GetUserMemories':
                        self._cached_user_memories = parse_memories(parsed)
                        summary = f'{len(self._cached_user_memories)}条用户记忆'
                    elif method == 'GetUserSettings':
                        self._cached_user_settings = parse_user_settings(parsed)
                        mc = len(self._cached_user_settings.get('models', []))
                        summary = f'{mc}个模型配置'
                    elif method == 'GetDebugDiagnostics':
                        self._cached_diagnostics = parse_diagnostics(parsed)
                        summary = f'{self._cached_diagnostics.get("entry_count",0)}条诊断'
                    elif method == 'GetAllWorkflows':
                        self._cached_workflows = parse_workflows(parsed)
                        summary = f'{len(self._cached_workflows)}个工作流'
                    elif method == 'GetUnleashData':
                        self._cached_unleash = parse_unleash(parsed)
                        summary = f'{len(self._cached_unleash)}个特性开关'
                    elif method == 'GetWorkspaceInfos':
                        self._cached_workspace_infos = parse_workspace_infos(parsed)
                        summary = f'{len(self._cached_workspace_infos)}个工作区'
                    elif method == 'WellSupportedLanguages':
                        langs = []
                        def _cl(o, d=0):
                            if d > 3: return
                            if isinstance(o, str) and len(o) > 1 and not o.startswith('<'): langs.append(o)
                            elif isinstance(o, dict):
                                for v in o.values(): _cl(v, d+1)
                            elif isinstance(o, list):
                                for i in o: _cl(i, d+1)
                        _cl(parsed)
                        self._cached_languages = langs
                        summary = f'{len(langs)}种语言'
                    elif method == 'GetDefaultWebOrigins':
                        origins = []
                        def _co(o, d=0):
                            if d > 3: return
                            if isinstance(o, str) and ('.' in o) and len(o) > 3: origins.append(o)
                            elif isinstance(o, dict):
                                for v in o.values(): _co(v, d+1)
                            elif isinstance(o, list):
                                for i in o: _co(i, d+1)
                        _co(parsed)
                        self._cached_web_origins = origins
                        summary = f'{len(origins)}个搜索源'

                    self.log.emit(cat, {
                        'method': method, 'desc': desc,
                        'status': f'✅ {status}', 'size': size,
                        'summary': summary
                    }, dedup_key=method)

                    if size > 10000:
                        out_path = LOG_DIR / f'{method}_latest.json'
                        try:
                            with open(out_path, 'w', encoding='utf-8') as f:
                                json.dump(parsed, f, ensure_ascii=False, default=str, indent=1)
                        except: pass
                else:
                    self._grpc_fail_streak += 1
                    err = ''
                    if parsed and isinstance(parsed, dict):
                        err = parsed.get('_error', '')[:200]
                    self.log.emit('grpc', {
                        'method': method, 'desc': desc,
                        'status': f'❌ {status}', 'size': size,
                        'summary': err
                    }, dedup_key=method)
            except Exception as e:
                self._grpc_fail_streak += 1
                self.log.emit('error', {'method': method, 'msg': str(e)[:200]})

    def poll_state(self):
        """读取SQLite状态"""
        try:
            state = self.state_reader.read_global_state()
            if state:
                self.log.emit('state', {
                    'msg': f'globalStorage: {len(state)}条记录',
                    'top_keys': [s['key'] for s in state[:10]]
                }, dedup_key='global_state')
        except Exception as e:
            pass

    def poll_trajectory_detail(self):
        """获取完整对话轨迹详情(大数据量, 低频)"""
        try:
            status, parsed, raw, size = self.grpc.call('GetUserTrajectoryDebug')
            if status <= 0:
                status, parsed, raw, size = self.grpc.call_connect('GetUserTrajectoryDebug')
            if status == 200 and size > 100:
                self.log.emit('trajectory', {
                    'method': 'GetUserTrajectoryDebug',
                    'status': '✅', 'size': size,
                    'summary': f'完整轨迹调试数据 {size/1024:.1f}KB'
                }, dedup_key='traj_debug')
                # 保存到文件
                out_path = LOG_DIR / 'GetUserTrajectoryDebug_latest.json'
                try:
                    with open(out_path, 'w', encoding='utf-8') as f:
                        json.dump(parsed, f, ensure_ascii=False, default=str, indent=1)
                except: pass
        except: pass

    def _check_ls_alive(self):
        """快速心跳检测: gRPC Heartbeat, 比tasklist快100倍"""
        try:
            status, _, _, _ = self.grpc.call('Heartbeat')
            return status == 200
        except:
            return False

    def monitor_loop(self):
        """主监控循环 — 永不停止, 自愈"""
        cycle = 0
        now = time.time()
        last_fast = 0
        last_med = 0
        last_slow = 0
        last_state = 0
        last_file = 0
        last_traj_detail = 0

        while self._running:
            now = time.time()
            cycle += 1

            try:
                # gRPC健康检测: 连续失败5次 → 重新发现
                if self._grpc_fail_streak >= 5:
                    self.log.emit('info', {'msg': f'⚠ gRPC连续{self._grpc_fail_streak}次失败, 重新发现...'})
                    self._grpc_fail_streak = 0
                    if not self.discover():
                        self.log.emit('info', {'msg': '重新发现失败, 15s后重试...'})
                        time.sleep(15)
                        continue

                # 快速轮询
                if now - last_fast >= POLL_FAST:
                    self.poll_grpc_methods(GRPC_METHODS_FAST, 'fast')
                    last_fast = now

                # 中速轮询
                if now - last_med >= POLL_MED:
                    self.poll_grpc_methods(GRPC_METHODS_MED, 'med')
                    procs = self.scanner.scan_processes()
                    self._cached_processes = procs
                    self.log.emit('process', {
                        'msg': f'Windsurf:{len(procs["windsurf"])} LS:{len(procs["ls"])}',
                        'ls': procs['ls'],
                    }, dedup_key='process_tree')
                    last_med = now

                # 慢速轮询
                if now - last_slow >= POLL_SLOW:
                    self.poll_grpc_methods(GRPC_METHODS_SLOW, 'slow')
                    last_slow = now

                # 状态数据库
                if now - last_state >= 20:
                    self.poll_state()
                    last_state = now

                # 文件变化
                if now - last_file >= 10:
                    self.file_monitor.check_changes()
                    last_file = now

                # 完整轨迹(低频, 大数据)
                if now - last_traj_detail >= 60:
                    self.poll_trajectory_detail()
                    last_traj_detail = now

            except KeyboardInterrupt:
                raise
            except Exception as e:
                self.log.emit('error', {'msg': f'监控循环异常: {e}'})

            time.sleep(0.5)

    def run(self):
        """启动监控 — 永不停止, 道法自然"""
        global _event_log_ref, _monitor_ref
        _event_log_ref = self.log
        _monitor_ref = self

        print(r"""
  ╔══════════════════════════════════════════════════════╗
  ║  Windsurf 热监控守护进程 · 反者道之动                ║
  ║  不干扰 · 热逆向 · 实时追踪一切底层                  ║
  ║  持久化运行 · 自愈 · 道法自然                    ║
  ╚══════════════════════════════════════════════════════╝
        """)

        if self.once:
            if not self.discover():
                print('\n  ❌ 发现失败, 确保Windsurf正在运行。')
                return
            print('\n  === 单次快照模式 ===')
            self.poll_grpc_methods(GRPC_METHODS_FAST + GRPC_METHODS_MED + GRPC_METHODS_SLOW, 'all')
            self.poll_state()
            self.poll_trajectory_detail()
            self.file_monitor.check_changes()
            print(f'\n  统计: {self.log.get_stats()}')
            print(f'  日志: {LOG_DIR}')
            return

        # 启动仪表盘 (立即启动, 不等发现)
        if not self.no_dash:
            def start_dashboard():
                try:
                    class DualStackHTTPServer(ThreadingHTTPServer):
                        address_family = socket.AF_INET6
                        def server_bind(self):
                            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                            super().server_bind()
                    server = DualStackHTTPServer(('::', self.port), DashboardHandler)
                    server.serve_forever()
                except Exception as e:
                    self.log.emit('error', {'msg': f'仪表盘启动失败: {e}'})
            dash_thread = threading.Thread(target=start_dashboard, daemon=True)
            dash_thread.start()
            print(f'  🌐 仪表盘: http://127.0.0.1:{self.port}')

        # 持久化发现: 无限重试, 指数退避, 永不放弃
        backoff = 3
        while self._running:
            if self.discover():
                break
            self.log.emit('info', {'msg': f'Windsurf未运行, {backoff}s后重试...'})
            print(f'  ⚠ Windsurf未发现, {backoff}s后重试... (仪表盘已就绪)')
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)  # 3→6→12→24→48→60s封顶

        if not self._running:
            return

        print(f'  📡 监控已启动 · 按 Ctrl+C 停止\n')
        print(f'  轮询频率: 快={POLL_FAST}s 中={POLL_MED}s 慢={POLL_SLOW}s')
        print(f'  日志目录: {LOG_DIR}\n')

        # 主循环 — 捕获一切异常, 保持生存
        while self._running:
            try:
                self.monitor_loop()
            except KeyboardInterrupt:
                print(f'\n\n  ⏹ 监控停止')
                print(f'  运行时长: {int(time.time()) - self._start_epoch}s')
                print(f'  总事件: {sum(self.log.get_stats().values())}')
                print(f'  统计: {self.log.get_stats()}')
                print(f'  日志: {LOG_DIR}')
                return
            except Exception as e:
                self.log.emit('error', {'msg': f'监控主循环崩溃, 自愈重启: {e}'})
                print(f'  ⚠ 监控异常: {e}, 5s后自愈...')
                time.sleep(5)

# ══════════════════════════════════════════════════════════════════
# CLI入口
# ══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Windsurf热监控守护进程')
    parser.add_argument('--port', type=int, default=MONITOR_PORT, help='仪表盘端口')
    parser.add_argument('--once', action='store_true', help='单次快照模式')
    parser.add_argument('--no-dash', action='store_true', help='无仪表盘')
    args = parser.parse_args()

    monitor = WindsurfHotMonitor(port=args.port, no_dash=args.no_dash, once=args.once)
    monitor.run()
