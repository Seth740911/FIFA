#!/usr/bin/env python3
"""测试：通过比赛ID直接查询FIFA官网视频"""

import json
import urllib.request

CXM_API = "https://cxm-api.fifa.com/fifaplusweb/api"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# 加载已完赛比赛
with open('wc-scores.json', encoding='utf-8') as f:
    data = json.load(f)

finished = [m for m in data['match_details'] if m.get('finished')]
print(f'已完赛: {len(finished)} 场\n')

# 加载现有视频映射
try:
    with open('wc-videos.json', encoding='utf-8') as f:
        existing_videos = json.load(f)
except:
    existing_videos = {}

print(f'现有视频映射: {len(existing_videos)} 个\n')

# 方法：通过search API搜索每个比赛的视频
new_videos = {}

for match in finished:
    fifa_id = match['fifa_id']
    home = match['h_cn']
    away = match['a_cn']
    score = f"{match['score_h']}-{match['score_a']}"
    
    # 如果已经有视频，跳过
    if fifa_id in existing_videos:
        print(f'✓ {home} {score} {away} - 已有视频')
        continue
    
    print(f'\n查找: {home} {score} {away} (ID: {fifa_id})')
    
    # 尝试通过search API查找
    search_url = f"{CXM_API}/search?locale=en&q={home}+{away}&limit=20"
    try:
        req = urllib.request.Request(search_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            search_data = json.loads(resp.read().decode('utf-8'))
        
        items = search_data.get('items', [])
        print(f'  搜索结果: {len(items)} 个')
        
        for item in items:
            entry_id = item.get('entryId', '')
            title = item.get('title', '')
            programme_type = item.get('programmeType', 0)
            
            # 只处理视频类型
            if programme_type != 3:
                continue
            
            # 过滤条件
            if 'Highlights' not in title:
                continue
            if 'Gamified' in title:
                print(f'  ✗ 跳过 (Gamified): {title[:60]}')
                continue
            if 'International Sign Language' in title:
                print(f'  ✗ 跳过 (ISL): {title[:60]}')
                continue
            if 'Alt Cast' in title:
                print(f'  ✗ 跳过 (Alt Cast): {title[:60]}')
                continue
            
            # 验证是否是这个比赛的视频
            details_url = f"{CXM_API}/sections/videoDetails/{entry_id}?locale=en"
            try:
                req2 = urllib.request.Request(details_url, headers=HEADERS)
                with urllib.request.urlopen(req2, timeout=10) as resp2:
                    details = json.loads(resp2.read().decode('utf-8'))
                
                tags = details.get('semanticTags', [])
                match_tag = next((t for t in tags if t.get('sourceCategory') == 'Match'), None)
                
                if match_tag and match_tag.get('id') == fifa_id:
                    print(f'  ✓ 找到视频: {title[:80]}')
                    new_videos[fifa_id] = {
                        'entryId': entry_id,
                        'title': title
                    }
                    break
                else:
                    print(f'  ? 不匹配: {title[:60]}')
            except Exception as e:
                print(f'  ✗ videoDetails错误: {e}')
                
    except Exception as e:
        print(f'  ✗ 搜索错误: {e}')

print(f'\n\n新发现视频: {len(new_videos)} 个')
if new_videos:
    print('\n新视频列表:')
    for fifa_id, video in new_videos.items():
        print(f'  {fifa_id}: {video["title"][:80]}')
    
    # 保存到测试文件
    test_file = 'wc-videos-test.json'
    all_videos = {**existing_videos, **new_videos}
    with open(test_file, 'w', encoding='utf-8') as f:
        json.dump(all_videos, f, ensure_ascii=False, indent=2)
    print(f'\n已保存到 {test_file} (共 {len(all_videos)} 个视频)')
else:
    print('\n没有新视频')
