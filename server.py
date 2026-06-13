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
import urllib.parse
import json
import urllib.request
from http.server import SimpleHTTPRequestHandler


WEB_DIR = os.path.dirname(os.path.abspath(__file__))
FETCH_SCRIPT = os.path.join(WEB_DIR, "fetch_scores.py")
VIDEO_DIR = r"D:\迅雷下载"


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
        # /api/video?fifaId=xxx: 获取比赛集锦m3u8播放地址
        if self.path.split('?')[0] == '/api/video':
            self._handle_api_video()
            return
        # /api/videos: 获取所有已注册的视频列表
        if self.path.split('?')[0] == '/api/videos':
            self._handle_api_videos()
            return
        # /video/: 提供迅雷下载的视频文件 or 代理远端URL
        if self.path.startswith('/video/'):
            self._handle_video()
            return
        super().do_GET()

    def do_POST(self):
        if self.path == '/api/video/register':
            self._handle_video_register()
            return
        self.send_error(404)

    def _handle_video(self):
        """Proxy remote m3u8/TS URLs or serve local video files from VIDEO_DIR"""
        rel = self.path[len('/video/'):]
        rel = urllib.parse.unquote(rel)

        # Remote URL proxy: /video/https://...
        if rel.startswith('http://') or rel.startswith('https://'):
            self._proxy_remote(rel)
            return

        # Local file serving: /video/path → VIDEO_DIR/path
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
        """Proxy a remote URL (m3u8/TS/key) to avoid CORS issues"""
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        ct = {
            '.m3u8': 'application/vnd.apple.mpegurl',
            '.ts': 'video/mp2t',
            '.key': 'application/octet-stream',
            '.mp4': 'video/mp4',
        }.get(ext, 'application/octet-stream')
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': '*/*',
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
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
        """Return the full wc-videos.json mapping"""
        video_map_path = os.path.join(WEB_DIR, 'wc-videos.json')
        try:
            with open(video_map_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._json_response(data)
        except Exception as e:
            self._json_response({'error': str(e)}, 500)

    def _handle_video_register(self):
        """Register a FIFA watch URL and auto-map entryId to matchId.
        POST body: {"url": "https://www.fifa.com/en/watch/XXXX"} or {"entryId": "XXXX"}
        Flow:
          1. Extract entryId from URL
          2. Call videoDetails API → get semanticTags with Match category
          3. Save {matchId: {entryId, title}} to wc-videos.json
          4. Return match info
        """
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
        except Exception:
            self._json_response({'error': 'invalid JSON body'}, 400)
            return

        url = body.get('url', '')
        entry_id = body.get('entryId', '')

        # Extract entryId from URL
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
            # 1. Call videoDetails API to get semanticTags
            details_url = f'https://cxm-api.fifa.com/fifaplusweb/api/sections/videoDetails/{entry_id}?locale=en'
            req = urllib.request.Request(details_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                details = json.loads(resp.read().decode('utf-8'))

            title = details.get('title', '')
            tags = details.get('semanticTags', [])

            # 2. Find Match tag to get matchId
            match_tag = next((t for t in tags if t.get('sourceCategory') == 'Match'), None)
            if not match_tag:
                self._json_response({'error': 'No Match tag found in video', 'title': title}, 404)
                return

            match_id = match_tag.get('id', '')

            # 3. Load and update wc-videos.json
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
            self._json_response({
                'matchId': match_id,
                'entryId': entry_id,
                'title': title,
                'registered': True,
            })

        except Exception as e:
            print(f"  [video] Register error: {e}")
            self._json_response({'error': str(e)}, 500)

    def _handle_api_video(self):
        """Get m3u8 play URL for a given FIFA match ID.
        1. Look up video entryId from wc-videos.json (fifaId → entryId)
        2. Call FIFA videoPlayerData API to get preplayParameters
        3. Call uplynk preplay API to get m3u8 URL
        4. Return m3u8 URL + poster + duration to frontend
        """
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        fifa_id = qs.get('fifaId', [''])[0]
        if not fifa_id:
            self.send_error(400, 'Missing fifaId')
            return
        try:
            # 1. Load video mapping
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

            # 2. Call FIFA videoPlayerData API
            api_url = f'https://cxm-api.fifa.com/fifaplusweb/api/videoPlayerData/{entry_id}?locale=en&personalizedAds=false'
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                pdata = json.loads(resp.read().decode('utf-8'))
            pp = pdata.get('preplayParameters', {})
            asset_guid = pdata.get('verizonAssetGuid', '')
            query_str = pp.get('queryStr', '')
            signature = pp.get('signature', '')
            if not asset_guid or not query_str or not signature:
                self._json_response({'error': 'missing preplay params'}, 502)
                return

            # 3. Call uplynk preplay API
            preplay_url = f'https://content.uplynk.com/preplay/{asset_guid}.json?{query_str}&sig={signature}'
            req2 = urllib.request.Request(preplay_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                preplay = json.loads(resp2.read().decode('utf-8'))
            play_url = preplay.get('playURL', '')
            poster = pdata.get('videoPosterImage', {}).get('src', '')
            duration = pdata.get('duration', 0)
            if not play_url:
                self._json_response({'error': 'no playURL'}, 502)
                return
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
        """Run fetch_scores.py in background, return current wc-scores.json immediately.
        If a sync is already running, skip and return current data."""
        # Check if a sync is already in progress
        if hasattr(self.server, '_sync_running') and self.server._sync_running:
            # Return current data without starting another sync
            self._return_scores()
            return
        
        self.server._sync_running = True
        def _run_and_clear():
            try:
                _run_fetch()
            finally:
                self.server._sync_running = False
        
        threading.Thread(target=_run_and_clear, daemon=True).start()
        # Return current data immediately
        self._return_scores()
    
    def _return_scores(self):
        """Return the current wc-scores.json file"""
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
