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
        if self.path.split('?')[0] == '/api/stats':
            self._handle_api_stats()
            return
        if self.path.split('?')[0] == '/api/all-stats':
            self._handle_all_stats()
            return
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
        if self.path.split('?')[0] == '/api/video/discover':
            self._handle_video_discover()
            return
        # /dl → 全部下载页 / /fifa → FIFA专用下载页
        if self.path.split('?')[0] in ('/dl', '/dl/'):
            self.path = '/download.html'
        elif self.path.split('?')[0] in ('/fifa', '/fifa/'):
            self.path = '/fifa-dl.html'
        elif self.path.split('?')[0] in ('/stats', '/stats/'):
            self.path = '/stats.html'
        elif self.path.split('?')[0] in ('/all-stats', '/all-stats/'):
            self.path = '/all-stats.html'
        # /proxy/?url=ENCODED_URL -> 代理该 URL（用于 HLS 代理）
        if self.path.startswith('/proxy/?'):
            self._handle_proxy()
            return
        if self.path.startswith('/video/'):
            self._handle_video()
            return
        if self.path.startswith('/apk/'):
            self._handle_apk()
            self._log_apk_access()
            return
        
        # 记录页面访问（非API、非静态资源）
        if not self.path.startswith('/api/') and not self.path.startswith('/proxy/'):
            self._log_page_view()
        
        super().do_GET()

    def do_POST(self):
        if self.path == '/api/diag':
            self._handle_diag()
            return
        if self.path == '/api/video/register':
            self._handle_video_register()
            return
        if self.path == '/api/heartbeat':
            self._handle_heartbeat()
            return
        if self.path == '/api/video/watch':
            self._handle_video_watch()
            return
        if self.path == '/api/diag':
            self._handle_diag()
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

    def _handle_video_discover(self):
        """实时从FIFA carousel查询单场比赛的视频，找到后写入wc-videos.json并返回播放信息"""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        fifa_id = qs.get('fifaId', [''])[0]
        if not fifa_id:
            self._json_response({'error': 'Missing fifaId'}, 400)
            return

        CXM_API = "https://cxm-api.fifa.com/fifaplusweb/api"
        HEADERS = {"User-Agent": "Mozilla/5.0"}

        # 1. Check if already mapped
        video_map_path = os.path.join(WEB_DIR, 'wc-videos.json')
        vmap = {}
        if os.path.exists(video_map_path):
            try:
                with open(video_map_path, 'r', encoding='utf-8') as f:
                    vmap = json.load(f)
            except Exception:
                pass

        if vmap.get(fifa_id):
            # Already mapped, return video info directly
            entry = vmap[fifa_id]
            entry_id = entry.get('entryId') if isinstance(entry, dict) else entry
            self._json_response({'found': True, 'entryId': entry_id, 'title': entry.get('title', '') if isinstance(entry, dict) else '', 'source': 'cache'})
            return

        # 2. Scan carousels for new entries
        CAROUSEL_IDS = [
            "2Q6UcV6pn5i5Zmiwto9gwD",
            "1klF18lgpe12FFtd1IoTSs",
        ]
        NEWS_IDS = [
            "1klF18lgpe12FFtd1IoTSs",
        ]

        # Collect already-mapped entryIds to skip
        existing_entry_ids = set()
        for mid, entry in vmap.items():
            if isinstance(entry, dict):
                existing_entry_ids.add(entry.get('entryId', ''))
            else:
                existing_entry_ids.add(entry)

        seen_entry_ids = []
        for cid in CAROUSEL_IDS:
            try:
                url = f"{CXM_API}/sections/promoCarousel/{cid}?locale=en"
                with _urlopen(url, headers=HEADERS) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                for item in data.get('items', []):
                    eid = item.get('entryId', '')
                    if eid and item.get('programmeType') == 3 and eid not in existing_entry_ids:
                        seen_entry_ids.append(eid)
            except Exception:
                pass

        for nid in NEWS_IDS:
            try:
                url = f"{CXM_API}/sections/news/{nid}?locale=en&limit=50"
                with _urlopen(url, headers=HEADERS) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                for item in data.get('items', []):
                    eid = item.get('entryId', '')
                    if eid and item.get('programmeType') == 3 and eid not in existing_entry_ids:
                        seen_entry_ids.append(eid)
            except Exception:
                pass

        if not seen_entry_ids:
            self._json_response({'found': False}, 200)
            return

        # 3. For each new entryId, call videoDetails to find matchId
        found_entry = None
        found_title = ''
        for eid in seen_entry_ids:
            try:
                details_url = f"{CXM_API}/sections/videoDetails/{eid}?locale=en"
                with _urlopen(details_url, headers=HEADERS) as resp:
                    details = json.loads(resp.read().decode('utf-8'))

                title = details.get('title', '')
                tags = details.get('semanticTags', [])
                match_tag = next((t for t in tags if t.get('sourceCategory') == 'Match'), None)
                if not match_tag:
                    continue

                match_id = match_tag.get('id', '')

                # Filter: only standard Highlights
                is_standard = (
                    'Highlights' in title
                    and 'Gamified' not in title
                    and 'International Sign Language' not in title
                    and 'Alt Cast' not in title
                    and '|' in title
                )
                if not is_standard:
                    continue

                # Also register other newly found videos while we're at it
                if match_id and match_id not in vmap:
                    vmap[match_id] = {'entryId': eid, 'title': title}
                    print(f"  [video-discover] Found: {match_id} -> {eid} ({title})")

                if match_id == fifa_id:
                    found_entry = eid
                    found_title = title
                    break  # Found our target
            except Exception:
                continue

        if not found_entry:
            # Save any other videos we found along the way
            if len(vmap) > 0:
                try:
                    with open(video_map_path, 'w', encoding='utf-8') as f:
                        json.dump(vmap, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            self._json_response({'found': False}, 200)
            return

        # 4. Save updated vmap
        try:
            with open(video_map_path, 'w', encoding='utf-8') as f:
                json.dump(vmap, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        print(f"  [video-discover] Target found: {fifa_id} -> {found_entry} ({found_title})")
        self._json_response({'found': True, 'entryId': found_entry, 'title': found_title, 'source': 'discovered'})

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
                            'captain': pl.get('Captain', p.get('Captain', False)),
                            'yellow': pl.get('YellowCards', p.get('YellowCards', 0)),
                            'red': pl.get('RedCards', p.get('RedCards', 0))
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

    def _log_apk_access(self):
        """记录APK访问信息到专用日志"""
        # 不再写 user_activity.log，避免外挂硬盘频繁访问
        return
        ip = self.client_address[0]
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        apk_file = self.path[len('/apk/'):]
        user_agent = self.headers.get('User-Agent', 'Unknown')
        
        # 解析User-Agent获取设备信息
        device_info = self._parse_user_agent(user_agent)
        
        # 获取请求来源(Referer)
        referer = self.headers.get('Referer', '')
        
        # 获取接受语言
        accept_language = self.headers.get('Accept-Language', '')
        
        log_entry = {
            'type': 'apk_download',
            'timestamp': timestamp,
            'ip': ip,
            'apk_file': apk_file,
            'user_agent': user_agent,
            'device_type': device_info.get('device_type', 'Unknown'),
            'os': device_info.get('os', 'Unknown'),
            'browser': device_info.get('browser', 'Unknown'),
            'is_mobile': device_info.get('is_mobile', False),
            'referer': referer,
            'language': accept_language
        }
        
        stats_file = os.path.join(WEB_DIR, 'user_activity.log')
        try:
            with open(stats_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"  [stats] APK access log error: {e}")

    def _log_page_view(self):
        """记录页面访问"""
        # 不再写 user_activity.log，避免外挂硬盘频繁访问
        return
        ip = self.client_address[0]
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        page = self.path.split('?')[0]
        user_agent = self.headers.get('User-Agent', 'Unknown')
        device_info = self._parse_user_agent(user_agent)
        referer = self.headers.get('Referer', '')
        
        # 跳过静态资源
        if page.endswith(('.png', '.jpg', '.css', '.js', '.svg', '.json')):
            return
        
        log_entry = {
            'type': 'page_view',
            'timestamp': timestamp,
            'ip': ip,
            'page': page,
            'user_agent': user_agent,
            'device_type': device_info.get('device_type', 'Unknown'),
            'os': device_info.get('os', 'Unknown'),
            'browser': device_info.get('browser', 'Unknown'),
            'is_mobile': device_info.get('is_mobile', False),
            'referer': referer
        }
        
        stats_file = os.path.join(WEB_DIR, 'user_activity.log')
        try:
            with open(stats_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            pass  # 静默失败，不影响页面加载

    def _parse_user_agent(self, ua):
        """解析User-Agent获取设备信息"""
        import re
        info = {
            'device_type': 'Unknown',
            'os': 'Unknown',
            'browser': 'Unknown',
            'is_mobile': False
        }
        
        ua_lower = ua.lower()
        
        # 判断设备类型
        if 'mobile' in ua_lower or 'android' in ua_lower or 'iphone' in ua_lower or 'ipad' in ua_lower:
            info['is_mobile'] = True
            if 'android' in ua_lower:
                info['device_type'] = 'Android手机'
                match = re.search(r'Android\s+([\d.]+)', ua)
                if match:
                    info['os'] = f'Android {match.group(1)}'
            elif 'iphone' in ua_lower:
                info['device_type'] = 'iPhone'
                match = re.search(r'OS\s+([\d_]+)', ua)
                if match:
                    info['os'] = f'iOS {match.group(1).replace("_", ".")}'
            elif 'ipad' in ua_lower:
                info['device_type'] = 'iPad'
                match = re.search(r'OS\s+([\d_]+)', ua)
                if match:
                    info['os'] = f'iOS {match.group(1).replace("_", ".")}'
        elif 'windows' in ua_lower:
            info['device_type'] = 'Windows电脑'
            match = re.search(r'Windows\s+NT\s+([\d.]+)', ua)
            if match:
                win_ver = {'10.0': '10/11', '6.3': '8.1', '6.2': '8', '6.1': '7'}
                info['os'] = f'Windows {win_ver.get(match.group(1), match.group(1))}'
        elif 'macintosh' in ua_lower or 'mac os' in ua_lower:
            info['device_type'] = 'Mac电脑'
            match = re.search(r'Mac\s+OS\s+X\s+([\d_.]+)', ua)
            if match:
                info['os'] = f'macOS {match.group(1).replace("_", ".")}'
        elif 'linux' in ua_lower:
            info['device_type'] = 'Linux电脑'
            info['os'] = 'Linux'
        
        # 判断浏览器
        if 'edg' in ua_lower:
            info['browser'] = 'Edge'
        elif 'chrome' in ua_lower and 'chromium' not in ua_lower:
            info['browser'] = 'Chrome'
        elif 'firefox' in ua_lower:
            info['browser'] = 'Firefox'
        elif 'safari' in ua_lower and 'chrome' not in ua_lower:
            info['browser'] = 'Safari'
        elif 'powershell' in ua_lower:
            info['browser'] = 'PowerShell'
        elif 'curl' in ua_lower:
            info['browser'] = 'curl'
        elif 'wget' in ua_lower:
            info['browser'] = 'wget'
        
        return info

    def _handle_heartbeat(self):
        """处理心跳请求，记录在线状态"""
        import datetime
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
        except Exception:
            body = {}
        
        ip = self.client_address[0]
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        page = body.get('page', 'Unknown')
        
        # 更新在线用户缓存
        online_file = os.path.join(WEB_DIR, '.temp', 'online_users.json')
        try:
            os.makedirs(os.path.dirname(online_file), exist_ok=True)
            online_users = {}
            if os.path.exists(online_file):
                with open(online_file, 'r', encoding='utf-8') as f:
                    online_users = json.load(f)
            
            online_users[ip] = {
                'ip': ip,
                'last_seen': timestamp,
                'page': page,
                'device_type': body.get('device_type', 'Unknown'),
                'is_mobile': body.get('is_mobile', False)
            }
            
            # 清理5分钟未活动的用户
            now = datetime.datetime.now()
            to_remove = []
            for user_ip, data in online_users.items():
                last_time = datetime.datetime.strptime(data['last_seen'], '%Y-%m-%d %H:%M:%S')
                if (now - last_time).total_seconds() > 300:  # 5分钟
                    to_remove.append(user_ip)
            
            for user_ip in to_remove:
                del online_users[user_ip]
            
            with open(online_file, 'w', encoding='utf-8') as f:
                json.dump(online_users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [heartbeat] Error: {e}")
        
        self._json_response({'status': 'ok', 'online_count': len(online_users)})

    def _handle_diag(self):
        """接收前端诊断日志，追加写入文件"""
        import datetime
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
        except Exception:
            body = {}
        ip = self.client_address[0]
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{ip}] {json.dumps(body, ensure_ascii=False)}\n"
        diag_file = os.path.join(WEB_DIR, '.temp', 'diag.log')
        os.makedirs(os.path.dirname(diag_file), exist_ok=True)
        with open(diag_file, 'a', encoding='utf-8') as f:
            f.write(line)
        self._json_response({'status': 'ok'})

    def _handle_video_watch(self):
        """记录视频观看行为"""
        import datetime
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
        except Exception:
            body = {}
        
        ip = self.client_address[0]
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user_agent = self.headers.get('User-Agent', 'Unknown')
        device_info = self._parse_user_agent(user_agent)
        
        log_entry = {
            'type': 'video_watch',
            'timestamp': timestamp,
            'ip': ip,
            'fifa_id': body.get('fifaId', ''),
            'match_title': body.get('title', ''),
            'duration': body.get('duration', 0),
            'user_agent': user_agent,
            'device_type': device_info.get('device_type', 'Unknown'),
            'is_mobile': device_info.get('is_mobile', False)
        }
        
        stats_file = os.path.join(WEB_DIR, 'user_activity.log')
        try:
            with open(stats_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"  [stats] Video watch log error: {e}")
        
        self._json_response({'status': 'ok'})

    def _handle_api_stats(self):
        """返回完整的用户行为统计数据"""
        stats_file = os.path.join(WEB_DIR, 'user_activity.log')
        
        if not os.path.exists(stats_file):
            self._json_response({
                'total_downloads': 0,
                'unique_ips': 0,
                'online_users': 0,
                'visitors': [],
                'daily_stats': [],
                'apk_files': [],
                'page_views': [],
                'video_watches': []
            })
            return
        
        try:
            # 读取所有日志
            entries = []
            with open(stats_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except:
                            pass
            
            if not entries:
                self._json_response({
                    'total_downloads': 0,
                    'unique_ips': 0,
                    'online_users': 0,
                    'visitors': [],
                    'daily_stats': [],
                    'apk_files': [],
                    'page_views': [],
                    'video_watches': []
                })
                return
            
            # 统计总下载量
            total_downloads = len([e for e in entries if e.get('type') == 'apk_download'])
            
            # 统计页面访问
            page_views = {}
            for e in entries:
                if e.get('type') == 'page_view':
                    page = e.get('page', '')
                    if page not in page_views:
                        page_views[page] = {'page': page, 'count': 0}
                    page_views[page]['count'] += 1
            
            page_list = list(page_views.values())
            page_list.sort(key=lambda x: x['count'], reverse=True)
            
            # 统计视频观看
            video_watches = {}
            for e in entries:
                if e.get('type') == 'video_watch':
                    fifa_id = e.get('fifa_id', '')
                    if fifa_id not in video_watches:
                        video_watches[fifa_id] = {
                            'fifa_id': fifa_id,
                            'title': e.get('match_title', ''),
                            'count': 0,
                            'total_duration': 0
                        }
                    video_watches[fifa_id]['count'] += 1
                    video_watches[fifa_id]['total_duration'] += e.get('duration', 0)
            
            video_list = list(video_watches.values())
            video_list.sort(key=lambda x: x['count'], reverse=True)
            
            # 统计独立IP
            ip_stats = {}
            for entry in entries:
                ip = entry.get('ip', 'Unknown')
                if ip not in ip_stats:
                    ip_stats[ip] = {
                        'ip': ip,
                        'count': 0,
                        'first_seen': entry.get('timestamp', ''),
                        'last_seen': entry.get('timestamp', ''),
                        'apk_files': set(),
                        'pages_visited': set(),
                        'videos_watched': set(),
                        'device_types': set(),
                        'is_mobile': False
                    }
                ip_stats[ip]['count'] += 1
                ip_stats[ip]['last_seen'] = entry.get('timestamp', '')
                
                if entry.get('type') == 'apk_download':
                    ip_stats[ip]['apk_files'].add(entry.get('apk_file', ''))
                elif entry.get('type') == 'page_view':
                    ip_stats[ip]['pages_visited'].add(entry.get('page', ''))
                elif entry.get('type') == 'video_watch':
                    ip_stats[ip]['videos_watched'].add(entry.get('fifa_id', ''))
                
                if entry.get('device_type'):
                    ip_stats[ip]['device_types'].add(entry.get('device_type'))
                if entry.get('is_mobile'):
                    ip_stats[ip]['is_mobile'] = True
            
            unique_ips = len(ip_stats)
            
            # 获取在线用户数
            online_file = os.path.join(WEB_DIR, '.temp', 'online_users.json')
            online_count = 0
            if os.path.exists(online_file):
                try:
                    with open(online_file, 'r', encoding='utf-8') as f:
                        online_users = json.load(f)
                    # 清理过期用户
                    import datetime
                    now = datetime.datetime.now()
                    to_remove = []
                    for user_ip, data in online_users.items():
                        last_time = datetime.datetime.strptime(data['last_seen'], '%Y-%m-%d %H:%M:%S')
                        if (now - last_time).total_seconds() > 300:
                            to_remove.append(user_ip)
                    for user_ip in to_remove:
                        del online_users[user_ip]
                    with open(online_file, 'w', encoding='utf-8') as f:
                        json.dump(online_users, f, ensure_ascii=False, indent=2)
                    online_count = len(online_users)
                except:
                    pass
            
            # 转换visitors格式
            visitors = []
            for ip, stats in ip_stats.items():
                visitors.append({
                    'ip': ip,
                    'count': stats['count'],
                    'first_seen': stats['first_seen'],
                    'last_seen': stats['last_seen'],
                    'apk_downloads': list(stats['apk_files']),
                    'pages_visited': list(stats['pages_visited']),
                    'videos_watched': list(stats['videos_watched']),
                    'device_types': list(stats['device_types']),
                    'is_mobile': stats['is_mobile']
                })
            
            # 按IP访问次数排序
            visitors.sort(key=lambda x: x['count'], reverse=True)
            
            # 按日期统计
            daily_stats = {}
            for entry in entries:
                date = entry.get('timestamp', '')[:10]  # YYYY-MM-DD
                if date not in daily_stats:
                    daily_stats[date] = {'date': date, 'count': 0, 'ips': set(), 'downloads': 0, 'videos': 0}
                daily_stats[date]['count'] += 1
                daily_stats[date]['ips'].add(entry.get('ip', ''))
                if entry.get('type') == 'apk_download':
                    daily_stats[date]['downloads'] += 1
                elif entry.get('type') == 'video_watch':
                    daily_stats[date]['videos'] += 1
            
            daily_list = []
            for date, stats in daily_stats.items():
                daily_list.append({
                    'date': date,
                    'count': stats['count'],
                    'unique_ips': len(stats['ips']),
                    'downloads': stats['downloads'],
                    'videos': stats['videos']
                })
            
            daily_list.sort(key=lambda x: x['date'])
            
            # 统计APK文件
            apk_files = {}
            for entry in entries:
                if entry.get('type') == 'apk_download':
                    apk = entry.get('apk_file', 'Unknown')
                    if apk not in apk_files:
                        apk_files[apk] = {'file': apk, 'count': 0}
                    apk_files[apk]['count'] += 1
            
            apk_list = list(apk_files.values())
            apk_list.sort(key=lambda x: x['count'], reverse=True)
            
            self._json_response({
                'total_downloads': total_downloads,
                'unique_ips': unique_ips,
                'online_users': online_count,
                'visitors': visitors,
                'daily_stats': daily_list,
                'apk_files': apk_list,
                'page_views': page_list[:10],  # 前10个
                'video_watches': video_list[:10]  # 前10个
            })
            
        except Exception as e:
            print(f"  [stats] Error generating stats: {e}")
            self._json_response({'error': str(e)}, 500)

    def _handle_all_stats(self):
        """聚合8081-8085所有端口的统计数据"""
        import urllib.request
        
        all_stats = {
            'ports': {},
            'summary': {
                'total_downloads': 0,
                'total_visitors': 0,
                'total_online': 0,
                'ports_active': 0
            }
        }
        
        # 查询8081-8085端口
        for port in range(8081, 8086):
            try:
                url = f'http://127.0.0.1:{port}/api/stats'
                req = urllib.request.Request(url, method='GET')
                
                try:
                    with urllib.request.urlopen(req, timeout=2) as resp:
                        data = json.loads(resp.read().decode('utf-8'))
                        
                        all_stats['ports'][str(port)] = {
                            'status': 'online',
                            'data': data
                        }
                        
                        # 累加汇总
                        all_stats['summary']['total_downloads'] += data.get('total_downloads', 0)
                        all_stats['summary']['total_visitors'] += data.get('unique_ips', 0)
                        all_stats['summary']['total_online'] += data.get('online_users', 0)
                        all_stats['summary']['ports_active'] += 1
                        
                except Exception as e:
                    all_stats['ports'][str(port)] = {
                        'status': 'offline',
                        'error': str(e)
                    }
                    
            except Exception as e:
                all_stats['ports'][str(port)] = {
                    'status': 'error',
                    'error': str(e)
                }
        
        self._json_response(all_stats)

    def log_message(self, format, *args):
        # 不再写 access.log，避免外挂硬盘频繁访问
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


def _regen_photo_map():
    """扫描 wc-photos 目录，生成英文名+位置→文件名的映射表"""
    import re as _re
    photos_dir = os.path.join(WEB_DIR, "wc-photos")
    output = os.path.join(WEB_DIR, "wc-photo-map.json")
    photo_map = {}
    total = 0
    if not os.path.isdir(photos_dir):
        return
    for team in os.listdir(photos_dir):
        team_dir = os.path.join(photos_dir, team)
        if not os.path.isdir(team_dir):
            continue
        team_map = {}
        for f in os.listdir(team_dir):
            if not f.endswith('.png'):
                continue
            m = _re.match(r'^(\d+)_(.+)_(GK|DF|MF|FW)\.png$', f)
            if m:
                name, pos = m.group(2), m.group(3)
                team_map[f"{name}|{pos}"] = f
                total += 1
        photo_map[team] = team_map
    try:
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(photo_map, f, ensure_ascii=False, indent=1)
        print(f"  [photo-map] {len(photo_map)} teams, {total} photos")
    except Exception as e:
        print(f"  [photo-map] Error: {e}")


def _run_photo_update():
    """运行照片更新脚本（每日执行一次）"""
    # 检查是否有照片更新脚本
    photo_script = os.path.join(WEB_DIR, ".temp", "download_photos.py")
    if not os.path.exists(photo_script):
        # 如果没有专门的脚本，跳过
        print("  [photo] 未找到照片更新脚本，跳过")
        return
    
    try:
        result = subprocess.run(
            [sys.executable, photo_script],
            capture_output=True, text=True, timeout=300,  # 照片下载可能需要更长时间
            cwd=WEB_DIR, encoding='utf-8', errors='replace',
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    print(f"  [photo] {line.strip()}")
        else:
            print(f"  [photo] ERROR: {result.stderr.strip()[:200]}")
    except Exception as e:
        print(f"  [photo] Exception: {e}")

    # 照片更新后重新生成映射表
    _regen_photo_map()


def _daily_photo_thread():
    """每日照片更新线程（每天凌晨 3 点执行）"""
    import datetime
    
    # 启动时先检查今天是否已执行过
    last_run_file = os.path.join(WEB_DIR, ".temp", "last_photo_update.txt")
    today = datetime.date.today().isoformat()
    
    if os.path.exists(last_run_file):
        try:
            with open(last_run_file, 'r') as f:
                last_date = f.read().strip()
            if last_date == today:
                print("[photo] 今日已更新过照片，等待明天")
        except:
            pass
    
    while True:
        # 每 30 分钟检查一次是否到了凌晨 3 点
        time.sleep(1800)
        
        now = datetime.datetime.now()
        if now.hour == 3 and now.minute < 30:  # 3:00-3:30 之间执行
            today = now.date().isoformat()
            
            # 检查今天是否已执行
            if os.path.exists(last_run_file):
                try:
                    with open(last_run_file, 'r') as f:
                        last_date = f.read().strip()
                    if last_date == today:
                        continue  # 今天已执行过
                except:
                    pass
            
            print(f"[photo] 开始每日照片更新 ({now.strftime('%Y-%m-%d %H:%M:%S')})")
            _run_photo_update()
            
            # 记录执行日期
            try:
                os.makedirs(os.path.dirname(last_run_file), exist_ok=True)
                with open(last_run_file, 'w') as f:
                    f.write(today)
            except:
                pass
            
            print("[photo] 每日照片更新完成")


def _startup_sync():
    time.sleep(3)
    print("[sync] 启动同步比分和红黄牌...")
    _run_fetch()
    print("[sync] 启动同步完成")
    _regen_photo_map()


def main():
    listen_port = 8086
    for i, arg in enumerate(sys.argv):
        if arg == '--port' and i + 1 < len(sys.argv):
            listen_port = int(sys.argv[i + 1])

    print(f"2026世界杯观赛指南 - 端口 {listen_port}")
    print(f"http://127.0.0.1:{listen_port}")

    # 启动比分同步线程
    sync_thread = threading.Thread(target=_startup_sync, daemon=True)
    sync_thread.start()

    # 启动每日照片更新线程
    photo_thread = threading.Thread(target=_daily_photo_thread, daemon=True)
    photo_thread.start()

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
