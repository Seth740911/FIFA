#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026世界杯观赛指南 - 后端服务器 (端口8086)
纯静态网页服务，双栈监听，后台定时同步比分和红黄牌
新增：/proxy/ 端点代理 HLS 请求，解决 bufferStalledError
"""

import os
import sys
import socket
import socketserver
import subprocess
import threading
import time
import urllib.parse
import json
import urllib.request
from http.server import SimpleHTTPRequestHandler


WEB_DIR = os.path.dirname(os.path.abspath(__file__))
FETCH_SCRIPT = os.path.join(WEB_DIR, "fetch_scores.py")
VIDEO_DIR = r"D:\迅雷下载"


def _urlopen(url, headers=None, timeout=15):
    """统一 urlopen"""
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=timeout)


class FIFAHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def end_headers(self):
        if self.path.endswith('.json') or self.path.endswith('.html') or self.path == '/':
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        if self.path.split('?')[0] == '/api/sync':
            self._handle_sync()
            return
        if self.path.split('?')[0] == '/api/video':
            self._handle_api_video()
            return
        if self.path.split('?')[0] == '/api/videos':
            self._handle_api_videos()
            return
        # /proxy/?url=ENCODED_URL -> 代理该 URL（用于 HLS 代理）
        if self.path.startswith('/proxy/?'):
            self._handle_proxy()
            return
        if self.path.startswith('/video/'):
            self._handle_video()
            return
        super().do_GET()

    def do_POST(self):
        if self.path == '/api/video/register':
            self._handle_video_register()
            return
        self.send_error(404)

    def _handle_proxy(self):
        """代理任意 URL（用于 HLS 片段代理，避免 CORS/网络问题）"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        url = qs.get('url', [''])[0]
        if not url:
            self.send_error(400, 'Missing url parameter')
            return
        self._proxy_remote(url)

    def _handle_video(self):
        """提供本地视频文件 or 代理远端 URL"""
        rel = self.path[len('/video/'):]
        rel = urllib.parse.unquote(rel)
        if rel.startswith('http://') or rel.startswith('https://'):
            self._proxy_remote(rel)
            return
        fpath = os.path.normpath(os.path.join(VIDEO_DIR, rel))
        if not fpath.startswith(os.path.normpath(VIDEO_DIR)):
            self.send_error(403)
            return
        if not os.path.isfile(fpath):
            self.send_error(404)
            return
        ext = os.path.splitext(fpath)[1].lower()
        ct = {
            '.m3u8': 'application/vnd.apple.mpegurl',
            '.ts': 'video/mp2t',
            '.key': 'application/octet-stream',
            '.mp4': 'video/mp4',
        }.get(ext, 'application/octet-stream')
        try:
            with open(fpath, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', len(data))
            self.send_header('Access-Control-Allow-Origin', '*')
            super().end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(500, str(e))

    def _proxy_remote(self, url):
        """代理远端 URL"""
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        ct = {
            '.m3u8': 'application/vnd.apple.mpegurl',
            '.ts': 'video/mp2t',
            '.key': 'application/octet-stream',
            '.mp4': 'video/mp4',
        }.get(ext, 'application/octet-stream')
        try:
            with _urlopen(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': '*/*',
                'Referer': 'https://www.fifa.com/',
            }) as resp:
                data = resp.read()
            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', len(data))
            self.send_header('Access-Control-Allow-Origin', '*')
            super().end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(502, f'Proxy error: {e}')

    def _handle_api_videos(self):
        video_map_path = os.path.join(WEB_DIR, 'wc-videos.json')
        try:
            with open(video_map_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._json_response(data)
        except Exception as e:
            self._json_response({'error': str(e)}, 500)

    def _handle_video_register(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
        except Exception:
            self._json_response({'error': 'invalid JSON body'}, 400)
            return

        url = body.get('url', '')
        entry_id = body.get('entryId', '')

        if url and not entry_id:
            import re
            m = re.search(r'/watch/([a-zA-Z0-9]+)', url)
            if m:
                entry_id = m.group(1)
            else:
                self._json_response({'error': 'Cannot extract entryId from URL'}, 400)
                return

        if not entry_id:
            self._json_response({'error': 'Missing url or entryId'}, 400)
            return

        try:
            details_url = f'https://cxm-api.fifa.com/fifaplusweb/api/sections/videoDetails/{entry_id}?locale=en'
            with _urlopen(details_url, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
                details = json.loads(resp.read().decode('utf-8'))

            title = details.get('title', '')
            tags = details.get('semanticTags', [])
            match_tag = next((t for t in tags if t.get('sourceCategory') == 'Match'), None)
            if not match_tag:
                self._json_response({'error': 'No Match tag found', 'title': title}, 404)
                return

            match_id = match_tag.get('id', '')
            video_map_path = os.path.join(WEB_DIR, 'wc-videos.json')
            vmap = {}
            if os.path.exists(video_map_path):
                try:
                    with open(video_map_path, 'r', encoding='utf-8') as f:
                        vmap = json.load(f)
                except Exception:
                    pass

            vmap[match_id] = {'entryId': entry_id, 'title': title}
            with open(video_map_path, 'w', encoding='utf-8') as f:
                json.dump(vmap, f, ensure_ascii=False, indent=2)

            print(f"  [video] Registered: {match_id} -> {entry_id} ({title})")
            self._json_response({'matchId': match_id, 'entryId': entry_id, 'title': title, 'registered': True})

        except Exception as e:
            print(f"  [video] Register error: {e}")
            self._json_response({'error': str(e)}, 500)

    def _handle_api_video(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        fifa_id = qs.get('fifaId', [''])[0]
        if not fifa_id:
            self.send_error(400, 'Missing fifaId')
            return
        try:
            video_map_path = os.path.join(WEB_DIR, 'wc-videos.json')
            if not os.path.exists(video_map_path):
                self._json_response({'error': 'no video mapping'}, 404)
                return
            with open(video_map_path, 'r', encoding='utf-8') as f:
                vmap = json.load(f)
            entry = vmap.get(fifa_id)
            if not entry:
                self._json_response({'error': 'no video for this match'}, 404)
                return
            entry_id = entry.get('entryId') if isinstance(entry, dict) else entry
            if not entry_id:
                self._json_response({'error': 'video not available'}, 404)
                return

            api_url = f'https://cxm-api.fifa.com/fifaplusweb/api/videoPlayerData/{entry_id}?locale=en&personalizedAds=false'
            with _urlopen(api_url, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
                pdata = json.loads(resp.read().decode('utf-8'))
            pp = pdata.get('preplayParameters', {})
            asset_guid = pdata.get('verizonAssetGuid', '')
            query_str = pp.get('queryStr', '')
            signature = pp.get('signature', '')
            if not asset_guid or not query_str or not signature:
                self._json_response({'error': 'missing preplay params'}, 502)
                return

            preplay_url = f'https://content.uplynk.com/preplay/{asset_guid}.json?{query_str}&sig={signature}'
            with _urlopen(preplay_url, headers={'User-Agent': 'Mozilla/5.0'}) as resp2:
                preplay = json.loads(resp2.read().decode('utf-8'))
            play_url = preplay.get('playURL', '')
            poster = pdata.get('videoPosterImage', {}).get('src', '')
            duration = pdata.get('duration', 0)
            if not play_url:
                self._json_response({'error': 'no playURL'}, 502)
                return

            # 返回真实 playURL，前端通过 /proxy/ 代理所有 HLS 请求
            print(f"  [video] play_url = {play_url[:100]}")
            self._json_response({'url': play_url, 'poster': poster, 'duration': duration})
        except Exception as e:
            print(f"  [video] Error: {e}")
            self._json_response({'error': str(e)}, 500)

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _handle_sync(self):
        if hasattr(self.server, '_sync_running') and self.server._sync_running:
            self._return_scores()
            return
        self.server._sync_running = True
        def _run_and_clear():
            try:
                _run_fetch()
            finally:
                self.server._sync_running = False
        threading.Thread(target=_run_and_clear, daemon=True).start()
        self._return_scores()

    def _return_scores(self):
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
        pass


def _run_fetch():
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
