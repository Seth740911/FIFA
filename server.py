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
APK_DIR = r"G:\AI\APK"
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
        if self.path.split('?')[0] == '/api/lineup':
            self._handle_api_lineup()
            return
        if self.path.split('?')[0] == '/api/video':
            self._handle_api_video()
            return
        if self.path.split('?')[0] == '/api/videos':
            self._handle_api_videos()
            return
        # /dl → 全部下载页 / /fifa → FIFA专用下载页
        if self.path.split('?')[0] in ('/dl', '/dl/'):
            self.path = '/download.html'
        elif self.path.split('?')[0] in ('/fifa', '/fifa/'):
            self.path = '/fifa-dl.html'
        # /proxy/?url=ENCODED_URL -> 代理该 URL（用于 HLS 代理）
        if self.path.startswith('/proxy/?'):
            self._handle_proxy()
            return
        if self.path.startswith('/video/'):
            self._handle_video()
            return
        if self.path.startswith('/apk/'):
            self._handle_apk()
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

    def _handle_apk(self):
        """提供APK文件下载，浏览器下载完自动弹出安装提示"""
        filename = self.path[len('/apk/'):]
        filename = urllib.parse.unquote(filename)
        fpath = os.path.normpath(os.path.join(APK_DIR, filename))
        if not fpath.startswith(os.path.normpath(APK_DIR)):
            self.send_error(403)
            return
        if not os.path.isfile(fpath):
            self.send_error(404)
            return
        try:
            with open(fpath, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.android.package-archive')
            self.send_header('Content-Length', len(data))
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

            # 过滤：只接受标准 Highlights，排除 Gamified / IS / Alt Cast
            is_standard = (
                'Highlights' in title
                and 'Gamified' not in title
                and 'International Sign Language' not in title
                and 'Alt Cast' not in title
                and '|' in title
            )
            if not is_standard:
                self._json_response({'error': 'Not standard Highlights (Gamified/IS/Alt Cast skipped)', 'title': title}, 400)
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

    def _handle_api_lineup(self):
        """获取首发阵容: /api/lineup?fifaId=400021443"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        fifa_id = qs.get('fifaId', [''])[0]
        if not fifa_id:
            self._json_response({'error': 'fifaId required'}, 400)
            return
        try:
            # 从赛事ID中提取其他参数（从wc-events.json查找）
            events_path = os.path.join(WEB_DIR, 'wc-events.json')
            if not os.path.exists(events_path):
                self._json_response({'error': 'no events data'}, 404)
                return
            with open(events_path, 'r', encoding='utf-8') as f:
                events = json.load(f).get('events', {})
            ev = events.get(fifa_id)
            if not ev:
                self._json_response({'error': 'match not found'}, 404)
                return
            cid = ev.get('cid', '17')
            sid = ev.get('sid', '285023')
            stid = ev.get('stid', '289273')
            # 调用FIFA Live API获取阵容
            api_url = f'https://api.fifa.com/api/v3/live/football/{fifa_id}?language=en'
            try:
                resp = _urlopen(api_url, timeout=10)
                data = json.loads(resp.read().decode('utf-8'))
            except Exception:
                # 回退：用calendar API
                api_url2 = f'https://api.fifa.com/api/v3/calendar/matches?language=en&idCompetition={cid}&idSeason={sid}&idStage={stid}&idMatch={fifa_id}&count=400'
                resp = _urlopen(api_url2, timeout=10)
                cal = json.loads(resp.read().decode('utf-8'))
                data = cal.get('Results', [{}])[0] if cal.get('Results') else {}

            result = {'home': None, 'away': None}
            for side, key in [('HomeTeam', 'home'), ('AwayTeam', 'away')]:
                team = data.get(side, {})
                if not team:
                    # calendar API格式
                    side_key = 'Home' if key == 'home' else 'Away'
                    team = data.get(side_key, {})
                players_raw = team.get('Players', [])
                starters = []
                for p in players_raw:
                    pl = p.get('Player', p)  # live API用Players[].Player, calendar可能不同
                    status = pl.get('Status', p.get('Status', 0))
                    if status == 1:  # Starting XI
                        pos_code = pl.get('Position', p.get('Position', 3))
                        starters.append({
                            'id': pl.get('IdPlayer', p.get('IdPlayer', '')),
                            'name': (pl.get('ShortName') or pl.get('PlayerName', [{}]))[0].get('Description', '') if isinstance(pl.get('ShortName') or pl.get('PlayerName'), list) else str(pl.get('ShortName', '')),
                            'pos': pos_code,
                            'jersey': pl.get('ShirtNumber', p.get('ShirtNumber', 0)),
                            'captain': pl.get('Captain', p.get('Captain', False))
                        })
                # 提取主教练 (Role=0)
                coach_info = None
                coaches_raw = team.get('Coaches', [])
                for c in coaches_raw:
                    if c.get('Role') == 0:
                        nm = c.get('Name', [])
                        al = c.get('Alias', [])
                        coach_info = {
                            'id': c.get('IdCoach', ''),
                            'name': al[0].get('Description', '') if al else (nm[0].get('Description', '') if nm else ''),
                            'photo': c.get('PictureUrl') or ''
                        }
                        break
                result[key] = {
                    'team': team.get('ShortClubName', team.get('TeamName', '')),
                    'idTeam': str(team.get('IdTeam', '')),
                    'formation': team.get('Tactics', ''),
                    'players': starters,
                    'coach': coach_info
                }
            self._json_response(result)
        except Exception as e:
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
            self._json_response({'url': play_url, 'poster': poster, 'duration': duration, 'entryId': entry_id, 'title': entry.get('title', '') if isinstance(entry, dict) else ''})
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
