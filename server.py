#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026世界杯观赛指南 - 后端服务器 (端口8086)
纯静态网页服务，双栈监听，后台定时同步比分和红黄牌
"""

import os
import sys
import socket
import socketserver
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler


WEB_DIR = os.path.dirname(os.path.abspath(__file__))
FETCH_SCRIPT = os.path.join(WEB_DIR, "fetch_scores.py")


class FIFAHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def end_headers(self):
        # 禁止缓存 JSON 和 HTML，确保比分数据实时更新
        if self.path.endswith('.json') or self.path.endswith('.html') or self.path == '/':
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        # /api/sync: 触发 fetch_scores.py 同步后返回最新数据
        if self.path.split('?')[0] == '/api/sync':
            self._handle_sync()
            return
        super().do_GET()

    def _handle_sync(self):
        """Run fetch_scores.py on-demand and return wc-scores.json"""
        try:
            result = subprocess.run(
                [sys.executable, FETCH_SCRIPT],
                capture_output=True, text=True, timeout=120,
                cwd=WEB_DIR, encoding='utf-8', errors='replace',
            )
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    print(f"  [sync] {line.strip()}")
            if result.returncode != 0:
                print(f"  [sync] ERROR: {result.stderr.strip()[:200]}")
        except Exception as e:
            print(f"  [sync] Exception: {e}")
        # Return the (now updated) wc-scores.json
        try:
            with open(os.path.join(WEB_DIR, 'wc-scores.json'), 'r', encoding='utf-8') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(data.encode('utf-8'))
        except Exception as e:
            self.send_error(500, str(e))

    def log_message(self, format, *args):
        pass  # 静默日志


def _run_fetch():
    """Run fetch_scores.py to sync scores and cards from FIFA API"""
    try:
        result = subprocess.run(
            [sys.executable, FETCH_SCRIPT],
            capture_output=True, text=True, timeout=120,
            cwd=WEB_DIR, encoding='utf-8', errors='replace',
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    print(f"  [sync] {line.strip()}")
        else:
            print(f"  [sync] ERROR: {result.stderr.strip()[:200]}")
    except Exception as e:
        print(f"  [sync] Exception: {e}")


def _startup_sync():
    """Run fetch_scores.py once on startup"""
    time.sleep(3)
    print("[sync] 启动同步比分和红黄牌...")
    _run_fetch()
    print("[sync] 启动同步完成")


def main():
    listen_port = 8086
    for i, arg in enumerate(sys.argv):
        if arg == '--port' and i + 1 < len(sys.argv):
            listen_port = int(sys.argv[i + 1])

    print(f"2026世界杯观赛指南 - 端口 {listen_port}")
    print(f"http://127.0.0.1:{listen_port}")

    # Startup sync (one-time, no loop)
    sync_thread = threading.Thread(target=_startup_sync, daemon=True)
    sync_thread.start()

    class DualStackTCPServer(socketserver.ThreadingTCPServer):
        address_family = socket.AF_INET6
        def server_bind(self):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    server = DualStackTCPServer(('::', listen_port), FIFAHandler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")


if __name__ == '__main__':
    main()
