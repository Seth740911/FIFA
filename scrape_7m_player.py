#!/usr/bin/env python3
"""
7m体育网 球员数据爬虫 v4
- 自动解密字段名（解析 getinfofun.php JS代码）
- 自动转换 position 数字→中文
- 抓取基本信息、比赛统计（解析为结构化数据）、实时数据
用法:
  python scrape_7m_player.py 97901
  python scrape_7m_player.py 97901 -o 97901.json
  python scrape_7m_player.py 97901 45 100 -o players.json
"""

import json
import re
import sys
import time
from urllib.request import Request, urlopen

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}

# 全局缓存：从 const/gb.js 获取的常量
_LINEUP_ARR = None
_PLAYER_DATA_TITLE = None


def http_get(url, referer=None, timeout=10):
    headers = dict(HEADERS)
    if referer:
        headers['Referer'] = referer
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', errors='ignore')


def load_constants(lang='gb'):
    global _LINEUP_ARR, _PLAYER_DATA_TITLE
    if _LINEUP_ARR is not None:
        return
    try:
        js = http_get(f'https://static.7m.com.cn/js/player/const/{lang}.js')
        # 提取 LINEUP_ARR = ["前锋",...]  （匹配方括号内容）
        m = re.search(r'LINEUP_ARR\s*=\s*(\[.*?\])\s*;', js, re.DOTALL)
        if m:
            _LINEUP_ARR = json.loads(m.group(1))
        # 提取 PLAYER_DATA_TITLE = ["国籍：",...]
        m2 = re.search(r'PLAYER_DATA_TITLE\s*=\s*(\[.*?\])\s*;', js, re.DOTALL)
        if m2:
            _PLAYER_DATA_TITLE = json.loads(m2.group(1))
    except Exception as e:
        print(f'  [警告] 加载常量失败: {e}', file=sys.stderr)
        _LINEUP_ARR = ["前锋", "中场", "后卫", "守门员", "", "", "", "", "", "其它"]
        _PLAYER_DATA_TITLE = ["国籍", "生日", "生物周期", "身高", "体重",
                              "效力球队", "场上位置", "加盟日期", "转会费",
                              "前度效力球队", "曾经效力球队", "俱乐部球衣", "球员身价"]


def extract_json_var(js_text, var_name):
    """用括号计数法从JS中提取 var xxx = {...} 的JSON对象"""
    prefix = f'var {var_name} ='
    idx = js_text.find(prefix)
    if idx < 0:
        return None
    start = idx + len(prefix)
    while start < len(js_text) and js_text[start] in ' \t\r\n':
        start += 1
    if start >= len(js_text) or js_text[start] != '{':
        return None
    depth = 0
    in_str = False
    str_ch = None
    i = start
    while i < len(js_text):
        c = js_text[i]
        if in_str:
            if c == '\\' and i + 1 < len(js_text):
                i += 1
            elif c == str_ch:
                in_str = False
        else:
            if c in ('"', "'"):
                in_str = True
                str_ch = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return json.loads(js_text[start:i + 1])
        i += 1
    return None


def parse_info_fun_js(js):
    """
    解析 getinfofun.php 返回的JS，建立 加密key → 字段名 的映射。
    通过分析 playerName 赋值和 PLAYER_DATA_TITLE 下标来推断。
    """
    mapping = {}

    # 1) 中文名：var playerName = playerInfo["..."]
    m = re.search(r'var playerName = playerInfo\["(.*?)"\]', js)
    if m:
        mapping[m.group(1)] = 'name_cn'

    # 2) 英文名：playerName += "(" + playerInfo["..."] + ")"
    m2 = re.search(r'playerName \+=.*?playerInfo\["(.*?)"\]', js)
    if m2:
        mapping[m2.group(1)] = 'name_en'

    # 3) 通过 PLAYER_DATA_TITLE 下标建立映射
    title_field = {
        0: 'nationality',
        1: 'birthday',
        3: 'height',
        4: 'weight',
        5: 'club',
        6: 'position',
        7: 'join_date',
        8: 'transfer_fee',
        9: 'former_club',
        10: 'once_club',
        11: 'shirt_no',
        12: 'market_value',
    }
    positions = [(m.start(), int(m.group(1)))
                 for m in re.finditer(r'PLAYER_DATA_TITLE\[(\d+)\]', js)]
    positions.sort()

    key_occs = [(m.start(), m.group(1))
                  for m in re.finditer(r'playerInfo\["(.*?)"\]', js)]

    for pos, tidx in positions:
        if tidx not in title_field:
            continue
        best = None
        best_pos = -1
        for kpos, k in key_occs:
            if kpos < pos and kpos > best_pos and k not in mapping:
                best = k
                best_pos = kpos
        if best:
            mapping[best] = title_field[tidx]

    # 4) profile / honours
    for km in re.finditer(r'playerInfo\["(.*?)"\]', js):
        k = km.group(1)
        if k in mapping:
            continue
        rest = js[km.end():km.end() + 200]
        if 'profile_td' in rest:
            mapping[k] = 'profile'
        elif 'golry_td' in rest:
            mapping[k] = 'honours'

    return mapping


def fetch_player_info(pid, lang='gb'):
    """抓取并解密球员基本信息"""
    load_constants(lang)
    nc = str(int(time.time() * 1000))
    url = f'https://player.7m.com.cn/v2/encrypt/fun/getinfo.php?id={pid}&lang={lang}&nc={nc}'
    referer = f'https://player.7m.com.cn/{pid}/index_{lang}.shtml'
    js = http_get(url, referer=referer)
    info_enc = extract_json_var(js, 'playerInfo')
    if not info_enc:
        return None, f'无法解析playerInfo，响应前300字: {js[:300]}'
    eindex = info_enc.get('e_index', '')
    field_map = {}
    if eindex:
        try:
            js_fun = http_get(
                f'https://player.7m.com.cn/v2/encrypt/fun/getinfofun.php?eindex={eindex}&lang={lang}'
            )
            field_map = parse_info_fun_js(js_fun)
        except Exception as e:
            print(f'  [警告] 获取字段映射失败: {e}', file=sys.stderr)

    result = {}
    for k, v in info_enc.items():
        if k in ('e_index', 'link'):
            result[k] = v
        elif k in field_map:
            result[field_map[k]] = v
        else:
            guessed = _guess_field(k, v, result)
            if guessed:
                result[guessed] = v
            else:
                result[f'_raw_{k}'] = v

    # 转换 position 数字 → 中文
    if 'position' in result and _LINEUP_ARR:
        try:
            idx = int(result['position'])
            if 0 <= idx < len(_LINEUP_ARR):
                result['position'] = _LINEUP_ARR[idx]
        except (ValueError, TypeError):
            pass

    # 转换 birthday 格式
    if 'birthday' in result:
        result['birthday'] = result['birthday'].replace(',', '-').strip()

    return result, None


def _guess_field(k, v, result_so_far):
    """按值特征猜测字段名"""
    if not isinstance(v, str):
        return None
    s = v.strip()
    if re.match(r'^\d{4}[-,]\d{1,2}[-,]\d{1,2}$', s):
        return 'birthday' if 'birthday' not in result_so_far else None
    if re.match(r'^\d+cm$', s):
        return 'height' if 'height' not in result_so_far else None
    if re.match(r'^\d+kg$', s):
        return 'weight' if 'weight' not in result_so_far else None
    return None


def _parse_vs(vs_str):
    """解析 vs 字段：赛事ID, 主队ID, 客队ID, 球员所在队ID, 主客场?, 主队进球, 客队进球"""
    parts = vs_str.split(',')
    return {
        'competition_id': parts[1] if len(parts) > 1 else '',
        'team1_id': parts[2] if len(parts) > 2 else '',
        'team2_id': parts[3] if len(parts) > 3 else '',
        'player_team_id': parts[4] if len(parts) > 4 else '',
        'venue_flag': parts[5] if len(parts) > 5 else '',
        'score1': parts[6] if len(parts) > 6 else '',
        'score2': parts[7] if len(parts) > 7 else '',
    }


def _parse_s(s_str):
    """解析 s 字段：进球,点球,乌龙,黄牌,红牌,上场时间"""
    parts = s_str.split(',')
    return {
        'goals': parts[0] if len(parts) > 0 else '',
        'penalties': parts[1] if len(parts) > 1 else '',
        'own_goals': parts[2] if len(parts) > 2 else '',
        'yellow_cards': parts[3] if len(parts) > 3 else '',
        'red_cards': parts[4] if len(parts) > 4 else '',
        'minutes': parts[5] if len(parts) > 5 else '',
    }


def decrypt_stats(stats_enc, lang='gb'):
    """解析比赛统计数据为结构化格式"""
    if not stats_enc:
        return None
    result = {}
    for k, v in stats_enc.items():
        if k in ('e_index', 'link'):
            result[k] = v
        elif isinstance(v, dict):
            # 判断是赛事映射还是球队映射
            first_val = next(iter(v.values()), '')
            if isinstance(first_val, dict) and 'n' in first_val:
                result['competitions'] = v
            else:
                result['teams'] = v
        elif isinstance(v, list):
            matches = []
            for rec in v:
                vs_info = _parse_vs(rec.get('vs', ''))
                s_info = _parse_s(rec.get('s', ''))
                date_str = rec.get('t', '').replace(',', '-')
                matches.append({
                    'match_id': rec.get('vs', '').split(',')[0] if rec.get('vs') else '',
                    'date': date_str,
                    **vs_info,
                    **s_info,
                })
            result['matches'] = matches
    return result


def fetch_player_stats(pid, lang='gb'):
    """抓取比赛统计"""
    nc = str(int(time.time() * 1000))
    url = f'https://player.7m.com.cn/v2/encrypt/fun/getstats.php?id={pid}&lang={lang}&nc={nc}'
    referer = f'https://player.7m.com.cn/{pid}/index_{lang}.shtml'
    try:
        js = http_get(url, referer=referer, timeout=15)
        stats_enc = extract_json_var(js, 'playerStats')
        return decrypt_stats(stats_enc, lang) if stats_enc else None
    except Exception as e:
        print(f'  [警告] 获取stats失败: {e}', file=sys.stderr)
        return None


def fetch_intime(pid):
    """抓取球员实时数据（明文）"""
    url = f'https://player.7m.com.cn/{pid}/data/intime.js'
    try:
        js = http_get(url, timeout=10)
        return extract_json_var(js, 'intime')
    except Exception:
        return None


def scrape_player(pid, lang='gb'):
    """抓取一个球员的全部数据"""
    result = {'player_id': pid}
    info, err = fetch_player_info(pid, lang)
    if err:
        result['error'] = err
        return result
    result['info'] = info
    stats = fetch_player_stats(pid, lang)
    if stats:
        result['stats'] = stats
    intime = fetch_intime(pid)
    if intime:
        result['intime'] = intime
    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    pids = []
    out_file = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '-o' and i + 1 < len(args):
            out_file = args[i + 1]
            i += 2
        else:
            pids.append(args[i])
            i += 1
    if not pids:
        print('请提供至少一个球员ID', file=sys.stderr)
        sys.exit(1)

    all_results = []
    for pid in pids:
        print(f'[抓取中] 球员ID: {pid}', file=sys.stderr)
        data = scrape_player(pid)
        all_results.append(data)
        if len(pids) > 1:
            time.sleep(0.5)

    output = all_results[0] if len(all_results) == 1 else all_results
    json_str = json.dumps(output, ensure_ascii=False, indent=2)
    if out_file:
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write(json_str)
        print(f'[完成] 已保存到 {out_file}', file=sys.stderr)
    else:
        print(json_str)


if __name__ == '__main__':
    main()
