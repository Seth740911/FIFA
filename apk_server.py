#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""APK 下载服务器 - 端口8888"""

import os, sys, socket, socketserver
from http.server import HTTPServer, BaseHTTPRequestHandler

APK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "releases", "FIFA2026_v3.0.apk")

LANDING_HTML = '''<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FIFA 2026 下载</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e0e0e0;font-family:-apple-system,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:40px 20px}
.icon{font-size:72px;margin-bottom:20px}
h1{font-size:1.4em;color:#c9a028;margin-bottom:16px}
p{color:#888;font-size:.95em;margin-bottom:20px;line-height:1.6}
.btn{display:inline-block;background:#c9a028;color:#000;border:none;border-radius:24px;padding:14px 40px;font-size:1.05em;font-weight:700;text-decoration:none;margin:10px}
.btn:active{opacity:.8}
.tip{background:#1a1a2e;padding:16px 20px;border-radius:12px;margin-top:24px;max-width:320px}
.tip p{color:#aaa;font-size:.85em;margin:0}
.arrow{font-size:24px;color:#c9a028;margin-bottom:8px}
</style>
</head><body>
<div class="icon">&#9917;</div>
<h1>FIFA 2026 v3.0</h1>
<p>2026世界杯观赛指南</p>
<a class="btn" href="/FIFA2026_v3.0.apk">&#11015; 下载安装</a>
<div class="tip">
  <div class="arrow">&#8593;</div>
  <p>如无法下载，请点击右上角<br><b>「在浏览器中打开」</b></p>
</div>
</body></html>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(LANDING_HTML.encode('utf-8'))
            return
        if not os.path.isfile(APK_FILE):
            self.send_error(404, "APK not found")
            return
        size = os.path.getsize(APK_FILE)
        self.send_response(200)
        self.send_header('Content-Type', 'application/vnd.android.package-archive')
        self.send_header('Content-Disposition', 'attachment; filename="FIFA2026_v3.0.apk"')
        self.send_header('Content-Length', size)
        self.end_headers()
        with open(APK_FILE, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def log_message(self, fmt, *args):
        print(f"  [apk] {self.client_address[0]} {fmt % args}")

class DualStack(socketserver.ThreadingTCPServer):
    address_family = socket.AF_INET6
    allow_reuse_address = True
    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

if __name__ == '__main__':
    port = 8888
    print(f"APK Server - 端口 {port}")
    print(f"APK: {APK_FILE} ({os.path.getsize(APK_FILE)/1024/1024:.1f} MB)")
    srv = DualStack(('::', port), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
