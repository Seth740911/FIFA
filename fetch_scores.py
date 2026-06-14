#!/usr/bin/env python3
"""
2026 World Cup Live Scores Fetcher
Fetches match results from FIFA official API and writes to wc-scores.json
Called periodically by scheduler (every 10 minutes)

Usage:
  python fetch_scores.py
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "wc-scores.json")
CARDS_FILE = os.path.join(SCRIPT_DIR, "wc-cards.json")
EVENTS_FILE = os.path.join(SCRIPT_DIR, "wc-events.json")
VIDEOS_FILE = os.path.join(SCRIPT_DIR, "wc-videos.json")


def _urlopen(url, headers=None, timeout=15):
    """统一 urlopen"""
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=timeout)

# FIFA official API - 104 matches, includes group info and scores
FIFA_API = "https://api.fifa.com/api/v3/calendar/matches?language=en&count=500&idSeason=285023"
# Per-match Live API - has Bookings (cards) + Players roster
FIFA_LIVE_API = "https://api.fifa.com/api/v3/live/football/{match_id}?language=en"

# FIFA match status: 0=played, 1=scheduled, 2-9=live stages
PLAYED_STATUS = 0
LIVE_STATUSES = {2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13}


def load_teams():
    """Load team data from wc-roster-data.js for matching.
    Returns (teams_map, teams_ordered) where teams_ordered preserves
    the original WC_TEAMS array order (same as JS forEach push order).
    """
    js_path = os.path.join(SCRIPT_DIR, "wc-roster-data.js")
    teams = {}
    teams_ordered = []
    if not os.path.exists(js_path):
        return teams, teams_ordered
    with open(js_path, "r", encoding="utf-8") as f:
        text = f.read()
    start = text.find("const WC_TEAMS = [")
    if start < 0:
        return teams, teams_ordered
    start = text.index("[", start)
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    arr = json.loads(text[start:end])
    for t in arr:
        info = {
            "cn": t["cn"],
            "group": t["group"],
            "code": t["code"],
            "fifa_id": t.get("teamId", ""),
        }
        teams[t["code"]] = info
        teams_ordered.append(info)
    return teams, teams_ordered


def fetch_fifa():
    """Fetch match data from FIFA API"""
    try:
        with _urlopen(FIFA_API, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("Results", [])
    except Exception as e:
        print(f"[ERROR] Failed to fetch FIFA API: {e}")
        return []


# ---- Bracket definition (must match JS) ----

def _ph_to_bracket_slot(ph_text):
    """Convert FIFA PlaceHolder text to our bracket slot format.
    FIFA: '2A', '1C', '3ABCDF', 'W73', 'RU101'
    Ours: '2A', '1C', '3rd(ABDF)', 'W73', 'RU101'
    """
    if not ph_text:
        return ""
    text = ph_text.strip()
    if text.startswith("W"):
        return text
    if text.startswith("RU"):
        return text
    # Third place group: 3ABCDF -> 3rd(ABDF)
    if text.startswith("3") and len(text) > 2 and text[1:2].isalpha():
        groups_str = text[1:]
        return "3rd(" + groups_str + ")"
    return text


R32 = [
    {"n": 1, "t1": "2B", "t2": "2A"},
    {"n": 2, "t1": "1E", "t2": "3rd(ABCDF)"},
    {"n": 3, "t1": "1F", "t2": "2C"},
    {"n": 4, "t1": "1C", "t2": "2F"},
    {"n": 5, "t1": "1I", "t2": "3rd(CDFGH)"},
    {"n": 6, "t1": "2E", "t2": "2I"},
    {"n": 7, "t1": "1A", "t2": "3rd(CEFHI)"},
    {"n": 8, "t1": "1L", "t2": "3rd(EHIJK)"},
    {"n": 9, "t1": "1D", "t2": "3rd(BEFIJ)"},
    {"n": 10, "t1": "1G", "t2": "3rd(AEHIJ)"},
    {"n": 11, "t1": "2K", "t2": "2L"},
    {"n": 12, "t1": "1H", "t2": "2J"},
    {"n": 13, "t1": "1B", "t2": "3rd(EFGIJ)"},
    {"n": 14, "t1": "1J", "t2": "2H"},
    {"n": 15, "t1": "1K", "t2": "3rd(DEIJL)"},
    {"n": 16, "t1": "2D", "t2": "2G"},
]
R16 = [
    {"n": 17, "from": [2, 5]}, {"n": 18, "from": [1, 3]},
    {"n": 19, "from": [4, 6]}, {"n": 20, "from": [7, 8]},
    {"n": 21, "from": [11, 12]}, {"n": 22, "from": [9, 10]},
    {"n": 23, "from": [14, 16]}, {"n": 24, "from": [13, 15]},
]
QF = [
    {"n": 25, "from": [17, 18]}, {"n": 26, "from": [21, 22]},
    {"n": 27, "from": [19, 20]}, {"n": 28, "from": [23, 24]},
]
SF = [
    {"n": 29, "from": [25, 26]}, {"n": 30, "from": [27, 28]},
]
FL = {"n": 31, "from": [29, 30]}
TP = {"n": 32, "from": [29, 30], "losers": True}
ALL_BRACKET = {m["n"]: m for m in R32 + R16 + QF + SF + [FL, TP]}


def _build_fifa_to_bracket_map(matches):
    """Build mapping from FIFA MatchNumber to our bracket match number (n).
    Uses PlaceHolderA/B to identify which bracket slot each FIFA match corresponds to.
    For R32: uses team slot names directly.
    For later rounds: converts FIFA W<n> references to our W< bracket_n> using the mapping chain.
    """
    # Step 1: Build R32 mapping using PlaceHolder team slots
    bracket_slot_to_n_r32 = {}
    for m in R32:
        key = frozenset([m["t1"], m["t2"]])
        bracket_slot_to_n_r32[key] = m["n"]

    # Step 2: Parse R32 from FIFA data
    fifa_num_to_n = {}  # FIFA MatchNumber -> our bracket n
    for match in matches:
        stage_info = match.get("StageName")
        if not stage_info or not isinstance(stage_info, list) or len(stage_info) == 0:
            continue
        stage = stage_info[0].get("Description", "")
        if stage != "Round of 32":
            continue

        ph_a = match.get("PlaceHolderA")
        ph_b = match.get("PlaceHolderB")
        pa = ph_a.get("Description", "") if isinstance(ph_a, dict) else (str(ph_a) if ph_a else "")
        pb = ph_b.get("Description", "") if isinstance(ph_b, dict) else (str(ph_b) if ph_b else "")
        if not pa or not pb:
            continue

        slot_a = _ph_to_bracket_slot(pa)
        slot_b = _ph_to_bracket_slot(pb)
        key = frozenset([slot_a, slot_b])
        bracket_n = bracket_slot_to_n_r32.get(key)
        if bracket_n is not None:
            fifa_num_to_n[match.get("MatchNumber", 0)] = bracket_n

    # Step 3: Build full W<n> and RU<n> conversion maps
    # Start with R32 mappings, then iteratively add R16, QF, SF, etc.
    fifa_winner_to_our = {}
    fifa_runnerup_to_our = {}
    for fifa_num, our_n in fifa_num_to_n.items():
        fifa_winner_to_our["W" + str(fifa_num)] = "W" + str(our_n)
        fifa_runnerup_to_our["RU" + str(fifa_num)] = "RU" + str(our_n)

    # Now iteratively process each subsequent round
    # Build bracket slot -> n for ALL rounds
    bracket_slot_to_n = {}
    for m in R32:
        key = frozenset([m["t1"], m["t2"]])
        bracket_slot_to_n[key] = m["n"]
    for m in R16 + QF + SF + [FL, TP]:
        slots = []
        for f in m["from"]:
            if m.get("losers"):
                slots.append("RU" + str(f))
            else:
                slots.append("W" + str(f))
        key = frozenset(slots)
        bracket_slot_to_n[key] = m["n"]

    # Process matches round by round: R16 -> QF -> SF -> Final
    stage_order = ["Round of 16", "Quarter-final", "Semi-final", "Play-off for third place", "Final"]
    for stage in stage_order:
        for match in matches:
            si = match.get("StageName")
            if not si or not isinstance(si, list) or len(si) == 0:
                continue
            if si[0].get("Description", "") != stage:
                continue

            ph_a = match.get("PlaceHolderA")
            ph_b = match.get("PlaceHolderB")
            pa = ph_a.get("Description", "") if isinstance(ph_a, dict) else (str(ph_a) if ph_a else "")
            pb = ph_b.get("Description", "") if isinstance(ph_b, dict) else (str(ph_b) if ph_b else "")
            if not pa or not pb:
                continue

            # Convert FIFA W/RU references to our numbering
            slot_a = fifa_winner_to_our.get(pa, fifa_runnerup_to_our.get(pa, pa))
            slot_b = fifa_winner_to_our.get(pb, fifa_runnerup_to_our.get(pb, pb))

            key = frozenset([slot_a, slot_b])
            bracket_n = bracket_slot_to_n.get(key)
            if bracket_n is not None:
                fifa_num = match.get("MatchNumber", 0)
                fifa_num_to_n[fifa_num] = bracket_n
                # Add new conversion entries for this round's matches
                fifa_winner_to_our["W" + str(fifa_num)] = "W" + str(bracket_n)
                fifa_runnerup_to_our["RU" + str(fifa_num)] = "RU" + str(bracket_n)

    # Step 4: Build final output: FIFA MatchNumber -> bracket info for ALL knockout matches
    fifa_to_bracket = {}
    # Re-process all knockout matches with updated conversion maps
    for match in matches:
        stage_info = match.get("StageName")
        if not stage_info or not isinstance(stage_info, list) or len(stage_info) == 0:
            continue
        stage = stage_info[0].get("Description", "")
        if stage == "First Stage":
            continue

        ph_a = match.get("PlaceHolderA")
        ph_b = match.get("PlaceHolderB")
        pa = ph_a.get("Description", "") if isinstance(ph_a, dict) else (str(ph_a) if ph_a else "")
        pb = ph_b.get("Description", "") if isinstance(ph_b, dict) else (str(ph_b) if ph_b else "")
        if not pa or not pb:
            continue

        if stage == "Round of 32":
            slot_a = _ph_to_bracket_slot(pa)
            slot_b = _ph_to_bracket_slot(pb)
        else:
            slot_a = fifa_winner_to_our.get(pa, fifa_runnerup_to_our.get(pa, pa))
            slot_b = fifa_winner_to_our.get(pb, fifa_runnerup_to_our.get(pb, pb))

        key = frozenset([slot_a, slot_b])
        bracket_n = bracket_slot_to_n.get(key)
        if bracket_n is not None:
            fifa_to_bracket[match.get("MatchNumber", 0)] = {
                "n": bracket_n,
                "slot_a": slot_a,
                "slot_b": slot_b,
            }

    return fifa_to_bracket


def parse_matches(matches, teams_map, teams_ordered):
    """Parse FIFA API results into our score format"""
    group_scores = {}
    bracket_scores = {}
    match_details = []

    code_to_cn = {code: info["cn"] for code, info in teams_map.items()}

    # Build group fixtures using ORIGINAL WC_TEAMS order
    groups = {}
    for info in teams_ordered:
        g = info["group"]
        if g not in groups:
            groups[g] = []
        groups[g].append(info)

    gk = sorted(groups.keys())
    fixture_lookup = {}
    for g in gk:
        t = groups[g]
        fx_list = [
            {"id": f"{g}1", "t1": t[0]["cn"], "t2": t[1]["cn"]},
            {"id": f"{g}2", "t1": t[2]["cn"], "t2": t[3]["cn"]},
            {"id": f"{g}3", "t1": t[0]["cn"], "t2": t[2]["cn"]},
            {"id": f"{g}4", "t1": t[1]["cn"], "t2": t[3]["cn"]},
            {"id": f"{g}5", "t1": t[0]["cn"], "t2": t[3]["cn"]},
            {"id": f"{g}6", "t1": t[1]["cn"], "t2": t[2]["cn"]},
        ]
        for fx in fx_list:
            key = tuple(sorted([fx["t1"], fx["t2"]]))
            fixture_lookup[key] = fx

    # Build FIFA -> bracket mapping
    fifa_to_bracket = _build_fifa_to_bracket_map(matches)
    if fifa_to_bracket:
        print(f"[INFO] Mapped {len(fifa_to_bracket)} bracket matches from FIFA PlaceHolders")

    abbr_to_code = {code: code for code in teams_map}

    for m in matches:
        home = m.get("Home") or {}
        away = m.get("Away") or {}
        h_abbr = home.get("Abbreviation", "")
        a_abbr = away.get("Abbreviation", "")
        h_score = m.get("HomeTeamScore")
        a_score = m.get("AwayTeamScore")
        match_status = m.get("MatchStatus", -1)
        group_info = m.get("GroupName")
        group_name = ""
        if group_info and isinstance(group_info, list) and len(group_info) > 0:
            group_name = group_info[0].get("Description", "")
        stage_info = m.get("StageName", [])
        stage = ""
        if stage_info and isinstance(stage_info, list) and len(stage_info) > 0:
            stage = stage_info[0].get("Description", "")

        is_finished = match_status == PLAYED_STATUS
        is_live = match_status in LIVE_STATUSES

        h_code = abbr_to_code.get(h_abbr, h_abbr)
        a_code = abbr_to_code.get(a_abbr, a_abbr)
        h_cn = code_to_cn.get(h_code, "")
        a_cn = code_to_cn.get(a_code, "")

        # Group stage matching
        matched = False
        if h_cn and a_cn and "Group" in group_name:
            key = tuple(sorted([h_cn, a_cn]))
            fx = fixture_lookup.get(key)
            if fx:
                if is_finished or is_live:
                    hs = h_score if h_score is not None else 0
                    as_ = a_score if a_score is not None else 0
                    if fx["t1"] == h_cn:
                        group_scores[fx["id"]] = {"s1": int(hs), "s2": int(as_)}
                    else:
                        group_scores[fx["id"]] = {"s1": int(as_), "s2": int(hs)}
                matched = True

        # Knockout stage matching
        bracket_matched = False
        if stage != "First Stage" and (is_finished or is_live):
            fifa_num = m.get("MatchNumber", 0)
            bracket_info = fifa_to_bracket.get(fifa_num)
            if bracket_info:
                bracket_n = bracket_info["n"]
                slot_a = bracket_info["slot_a"]
                hs = h_score if h_score is not None else 0
                as_ = a_score if a_score is not None else 0
                bm = ALL_BRACKET.get(bracket_n)
                if bm and "t1" in bm:
                    # R32: determine s1 by checking if slot_a matches t1
                    if slot_a == bm["t1"]:
                        bracket_scores[bracket_n] = {"s1": int(hs), "s2": int(as_)}
                    else:
                        bracket_scores[bracket_n] = {"s1": int(as_), "s2": int(hs)}
                else:
                    # Later rounds: slot_a is home -> s1
                    bracket_scores[bracket_n] = {"s1": int(hs), "s2": int(as_)}
                bracket_matched = True

        venue = ""
        stadium = m.get("Stadium")
        if stadium and isinstance(stadium, dict):
            names = stadium.get("Name")
            if names and isinstance(names, list) and len(names) > 0:
                venue = names[0].get("Description", "")

        match_details.append({
            "fifa_id": m.get("IdMatch", ""),
            "home": h_abbr,
            "away": a_abbr,
            "h_cn": h_cn,
            "a_cn": a_cn,
            "score_h": h_score,
            "score_a": a_score,
            "status": match_status,
            "finished": is_finished,
            "live": is_live,
            "date": m.get("Date", ""),
            "group": group_name,
            "stage": stage,
            "attendance": m.get("Attendance", ""),
            "venue": venue,
            "group_matched": matched,
            "bracket_matched": bracket_matched,
        })

    return group_scores, bracket_scores, match_details


def write_output(group_scores, bracket_scores, match_details):
    """Write the combined output"""
    local_group = {}
    local_bracket = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            local_group = existing.get("local_group_scores", {})
            local_bracket = existing.get("local_bracket_scores", {})
        except Exception:
            pass

    output = {
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "FIFA",
        "group_scores": group_scores,
        "bracket_scores": bracket_scores,
        "match_details": match_details,
        "local_group_scores": local_group,
        "local_bracket_scores": local_bracket,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)

    finished = sum(1 for m in match_details if m["finished"])
    live = sum(1 for m in match_details if m["live"])
    group_matched = sum(1 for m in match_details if m["group_matched"])
    bracket_matched = sum(1 for m in match_details if m["bracket_matched"])
    print(f"[OK] Written {len(match_details)} matches to {OUTPUT_FILE}")
    print(f"     Finished: {finished}, Live: {live}")
    print(f"     Group matched: {group_matched}, Bracket mapped: {bracket_matched}")
    print(f"     Group scores: {len(group_scores)}, Bracket scores: {len(bracket_scores)}")


def load_player_id_map():
    """Build mapping: FIFA IdPlayer -> (team_code, jersey) from wc-roster-data.js"""
    js_path = os.path.join(SCRIPT_DIR, "wc-roster-data.js")
    pid_map = {}  # IdPlayer -> {"code": team_code, "jersey": jersey}
    if not os.path.exists(js_path):
        return pid_map
    with open(js_path, "r", encoding="utf-8") as f:
        text = f.read()
    start = text.find("const WC_TEAMS = [")
    if start < 0:
        return pid_map
    start = text.index("[", start)
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    arr = json.loads(text[start:end])
    for t in arr:
        code = t["code"]
        for p in t.get("players", []):
            fifa_id = str(p.get("id", ""))
            jersey = p.get("jersey", 0)
            if fifa_id:
                pid_map[fifa_id] = {"code": code, "jersey": jersey}
    return pid_map


def fetch_live_match(match_id):
    """Fetch Live API for a single match to get Bookings + Players"""
    url = FIFA_LIVE_API.format(match_id=match_id)
    try:
        with _urlopen(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[WARN] Failed to fetch Live API for match {match_id}: {e}")
        return None


def _compute_stage_flags(match_details):
    """Determine if group stage and QF stage are finished.
    Returns {"group_finished": bool, "qf_finished": bool}
    """
    GROUP_STAGES = {"First Stage"}
    QF_STAGE = "Quarter-final"
    
    group_total = sum(1 for m in match_details if m.get("stage") in GROUP_STAGES)
    group_finished = sum(1 for m in match_details if m.get("stage") in GROUP_STAGES and m.get("finished"))
    qf_total = sum(1 for m in match_details if m.get("stage") == QF_STAGE)
    qf_finished = sum(1 for m in match_details if m.get("stage") == QF_STAGE and m.get("finished"))
    
    return {
        "group_finished": group_total > 0 and group_finished >= group_total,
        "qf_finished": qf_total > 0 and qf_finished >= qf_total,
    }


def fetch_and_aggregate_cards(match_details, pid_map):
    """Fetch card data + match events from FIFA Live API.
    Incremental: only fetches matches not already in wc-events.json.
    Returns (cards, stage_info, events).
    """
    import re

    # Load existing events
    existing_events = {}
    _event_meta = {}  # metadata keys like __processed__
    if os.path.exists(EVENTS_FILE):
        try:
            with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
            raw_events = old.get("events", {})
            # Separate metadata keys (prefixed __) from match data
            existing_events = {k: v for k, v in raw_events.items() if not k.startswith("__")}
            _event_meta = {k: v for k, v in raw_events.items() if k.startswith("__")}
        except Exception:
            pass

    # Load existing cards
    existing_cards = {}
    processed_match_ids = set()
    if os.path.exists(CARDS_FILE):
        try:
            with open(CARDS_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
            existing_cards = old.get("cards", {})
            processed_match_ids = set(old.get("processed_matches", []))
        except Exception:
            pass

    # Find finished/live match IDs that need fetching
    all_finished = []
    for m in match_details:
        mid = m.get("fifa_id", "")
        if (m.get("finished") or m.get("live")) and mid:
            all_finished.append(mid)

    # --- Separate delta logic for cards vs events ---
    # Cards: incremental, skip already-processed matches
    new_card_ids = [mid for mid in all_finished if mid not in processed_match_ids]

    # Events: own processed list; a match is "event-done" only if finished AND events are complete
    # (has substitution = full-time data). Mid-game snapshots get re-fetched until complete.
    event_processed = set(_event_meta.get("__processed__", []))
    event_fetch_ids = []
    for m in match_details:
        mid = m.get("fifa_id", "")
        if not mid:
            continue
        if m.get("live") and mid:
            # Live match: fetch if not already in events (avoid hammering live API)
            if mid not in existing_events or mid in event_processed:
                event_fetch_ids.append(mid)
        elif m.get("finished") and mid not in event_processed:
            # Finished match not yet event-done → always fetch
            event_fetch_ids.append(mid)

    # Combine: fetch union of card-new + event-needed, but card processing only for new_card_ids
    fetch_ids = list(dict.fromkeys(new_card_ids + event_fetch_ids))
    print(f"[INFO] Cards: {len(processed_match_ids)} processed, {len(new_card_ids)} new | "
          f"Events: {len(event_processed)} done, {len(event_fetch_ids)} to fetch | "
          f"Total API calls: {len(fetch_ids)}")

    if not all_finished:
        stage_info = _compute_stage_flags(match_details)
        return existing_cards, stage_info, existing_events

    # Build lookups
    match_stage = {}
    match_info = {}
    for m in match_details:
        mid = m.get("fifa_id", "")
        match_stage[mid] = m.get("stage", "")
        match_info[mid] = m

    # Fetch matches (union of card-new + event-needed)
    fetched = 0
    for mid in fetch_ids:
        need_cards = mid in new_card_ids
        live = fetch_live_match(mid)
        if not live:
            continue
        fetched += 1

        stage = match_stage.get(mid, "")
        is_group = stage == "First Stage"
        mi = match_info.get(mid, {})

        # Build IdPlayer -> key mapping
        player_key_map = {}
        for side in ["HomeTeam", "AwayTeam"]:
            team = live.get(side, {})
            for p in team.get("Players", []):
                fifa_pid = str(p.get("IdPlayer", ""))
                if fifa_pid in pid_map:
                    info = pid_map[fifa_pid]
                    player_key_map[fifa_pid] = f"{info['code']}-{info['jersey']}"

        # Process Bookings for cards (only for new matches to avoid double-counting)
        if need_cards:
            # Collect per-player first, then deduplicate 2-yellow-to-red
            _mb = {}  # key -> [{card, minute}]
            for side in ["HomeTeam", "AwayTeam"]:
                team = live.get(side, {})
                for b in team.get("Bookings", []):
                    fifa_pid = str(b.get("IdPlayer", ""))
                    card_type = b.get("Card", 0)
                    if not fifa_pid or card_type not in (1, 2):
                        continue
                    key = player_key_map.get(fifa_pid)
                    if not key:
                        info = pid_map.get(fifa_pid)
                        if info:
                            key = f"{info['code']}-{info['jersey']}"
                    if key:
                        _mb.setdefault(key, []).append({
                            "card": card_type,
                            "minute": b.get("Minute", 0)
                        })

            for key, bk in _mb.items():
                if key not in existing_cards:
                    existing_cards[key] = {"y_group": 0, "r_group": 0, "y_ko": 0, "r_ko": 0}
                # Find yellows absorbed by reds (same minute → 2nd yellow turned red)
                red_mins = [x["minute"] for x in bk if x["card"] == 2]
                absorbed = 0
                for rm in red_mins:
                    for x in bk:
                        if x["card"] == 1 and x["minute"] == rm:
                            absorbed += 1
                            break
                t_y = sum(1 for x in bk if x["card"] == 1) - absorbed
                t_r = len(red_mins)
                if is_group:
                    existing_cards[key]["y_group"] += t_y
                    existing_cards[key]["r_group"] += t_r
                else:
                    existing_cards[key]["y_ko"] += t_y
                    existing_cards[key]["r_ko"] += t_r

        # Extract timeline events
        ev_list = []
        home_team = live.get("HomeTeam", {})
        away_team = live.get("AwayTeam", {})
        h_abbr = home_team.get("Abbreviation", mi.get("home", ""))
        a_abbr = away_team.get("Abbreviation", mi.get("away", ""))
        h_score = home_team.get("Score", 0)
        a_score = away_team.get("Score", 0)

        # Goals (from HomeTeam.Goals + AwayTeam.Goals)
        for side_name in ["HomeTeam", "AwayTeam"]:
            team = live.get(side_name, {})
            side = "home" if side_name == "HomeTeam" else "away"
            for g in team.get("Goals", []):
                ev_list.append({
                    "min": g.get("Minute", ""),
                    "type": "goal",
                    "id_player": str(g.get("IdPlayer", "")),
                    "id_assist": str(g.get("IdAssistPlayer", "")) if g.get("IdAssistPlayer") else "",
                    "side": side,
                })

        # Bookings
        for side_name in ["HomeTeam", "AwayTeam"]:
            team = live.get(side_name, {})
            side = "home" if side_name == "HomeTeam" else "away"
            for b in team.get("Bookings", []):
                card_type = b.get("Card", 0)
                ctype = "yellow" if card_type == 1 else "red" if card_type == 2 else ""
                if not ctype:
                    continue
                ev_list.append({
                    "min": b.get("Minute", ""),
                    "type": ctype,
                    "id_player": str(b.get("IdPlayer", "")),
                    "side": side,
                })

        # Substitutions
        for side_name in ["HomeTeam", "AwayTeam"]:
            team = live.get(side_name, {})
            side = "home" if side_name == "HomeTeam" else "away"
            for s in team.get("Substitutions", []):
                ev_list.append({
                    "min": s.get("Minute", ""),
                    "type": "sub",
                    "id_off": str(s.get("IdPlayerOff", "")),
                    "id_on": str(s.get("IdPlayerOn", "")),
                    "side": side,
                })

        if ev_list:
            def sort_key(ev):
                m_str = ev.get("min", "")
                nums = re.findall(r'\d+', m_str)
                if len(nums) >= 2:
                    return int(nums[0]) + int(nums[1]) * 0.1
                return int(nums[0]) if nums else 0
            ev_list.sort(key=sort_key)

        existing_events[mid] = {
            "home": h_abbr,
            "away": a_abbr,
            "h_cn": mi.get("h_cn", ""),
            "a_cn": mi.get("a_cn", ""),
            "score_h": h_score,
            "score_a": a_score,
            "timeline": ev_list,
        }

        # Mark event as processed if match is finished and data looks complete
        # If any event has minute >= 90' (e.g. 90', 90'+1', 90'+5'), it means
        # we have full-time data. Mid-game snapshots won't have 90' events.
        if mi.get("finished"):
            has_90 = any("90" in (e.get("min") or "") for e in ev_list)
            if has_90:
                event_processed.add(mid)

        time.sleep(0.3)

    print(f"[INFO] Fetched {fetched}/{len(fetch_ids)} matches from Live API")

    # Compute stage flags
    stage_info = _compute_stage_flags(match_details)

    # Save cards
    output = {
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "processed_matches": sorted(all_finished),
        "stages": stage_info,
        "cards": existing_cards,
    }
    with open(CARDS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)

    total_yg = sum(c["y_group"] for c in existing_cards.values())
    total_rg = sum(c["r_group"] for c in existing_cards.values())
    total_yk = sum(c["y_ko"] for c in existing_cards.values())
    total_rk = sum(c["r_ko"] for c in existing_cards.values())
    print(f"[OK] Cards: {len(existing_cards)} players "
          f"(group: {total_yg}Y {total_rg}R, ko: {total_yk}Y {total_rk}R)")
    print(f"     Stages: group={'done' if stage_info['group_finished'] else 'active'}, "
          f"qf={'done' if stage_info['qf_finished'] else 'active'}")

    # Save events (include __processed__ list so next run knows what's complete)
    ev_data = dict(existing_events)
    ev_data["__processed__"] = sorted(event_processed)
    ev_output = {
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "events": ev_data,
    }
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(ev_output, f, ensure_ascii=False, indent=1)
    print(f"[OK] Events: {len(existing_events)} matches, {len(event_processed)} processed")

    return existing_cards, stage_info, existing_events


def discover_videos():
    """Auto-discover WC2026 highlight videos from FIFA+ carousel feeds.
    
    Strategy:
    1. Poll promoCarousel/2Q6UcV6pn5i5Zmiwto9gwD for latest WC2026 highlights
    2. For each video entryId, call videoDetails to get semanticTags → matchId
    3. Merge new entries into wc-videos.json
    
    Returns number of newly discovered videos.
    """
    CXM_API = "https://cxm-api.fifa.com/fifaplusweb/api"
    HEADERS = {"User-Agent": "Mozilla/5.0"}
    
    # Carousel IDs that carry WC2026 video content
    # These come from the WC2026 tournament page sections
    CAROUSEL_IDS = [
        "2Q6UcV6pn5i5Zmiwto9gwD",  # WC2026 highlights promoCarousel
        "1klF18lgpe12FFtd1IoTSs",  # WC2026 highlights news section
    ]
    
    # Load existing video mapping
    vmap = {}
    if os.path.exists(VIDEOS_FILE):
        try:
            with open(VIDEOS_FILE, "r", encoding="utf-8") as f:
                vmap = json.load(f)
        except Exception:
            pass
    
    # Collect all entryIds from carousels
    seen_entry_ids = set()
    for cid in CAROUSEL_IDS:
        url = f"{CXM_API}/sections/promoCarousel/{cid}?locale=en"
        try:
            with _urlopen(url, headers=HEADERS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items", [])
            for item in items:
                eid = item.get("entryId", "")
                if eid and item.get("programmeType") == 3:
                    seen_entry_ids.add(eid)
            print(f"[video] Carousel {cid}: {len(items)} items, {len([i for i in items if i.get('programmeType')==3])} videos")
        except Exception as e:
            print(f"[video] Carousel {cid} error: {e}")
    
    if not seen_entry_ids:
        print("[video] No video entries found in carousels")
        return 0
    
    # Also collect from news section (uses /sections/news/ endpoint)
    # Only use the main highlights feed, NOT the alt-cast/Gamified one
    NEWS_IDS = [
        "1klF18lgpe12FFtd1IoTSs",  # WC2026 match highlights news (standard Highlights only)
    ]
    for nid in NEWS_IDS:
        url = f"{CXM_API}/sections/news/{nid}?locale=en&limit=50"
        try:
            with _urlopen(url, headers=HEADERS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items", [])
            for item in items:
                eid = item.get("entryId", "")
                if eid and item.get("programmeType") == 3:
                    seen_entry_ids.add(eid)
            print(f"[video] News {nid}: {len([i for i in items if i.get('programmeType')==3])} videos")
        except Exception as e:
            print(f"[video] News {nid} error: {e}")
    
    # Check which entryIds are already mapped
    existing_entry_ids = set()
    for mid, entry in vmap.items():
        if isinstance(entry, dict):
            existing_entry_ids.add(entry.get("entryId", ""))
        else:
            existing_entry_ids.add(entry)
    
    new_count = 0
    for eid in seen_entry_ids:
        if eid in existing_entry_ids:
            continue
        
        # Call videoDetails to find matchId via semanticTags
        try:
            details_url = f"{CXM_API}/sections/videoDetails/{eid}?locale=en"
            with _urlopen(details_url, headers=HEADERS) as resp:
                details = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[video] videoDetails {eid} error: {e}")
            continue
        
        title = details.get("title", "")
        tags = details.get("semanticTags", [])
        match_tag = next((t for t in tags if t.get("sourceCategory") == "Match"), None)
        
        if not match_tag:
            # Not a match highlight video (could be alt cast, promo, etc.)
            print(f"[video] Skip {eid}: no Match tag (title: {title[:60]})")
            continue
        
        match_id = match_tag.get("id", "")
        if not match_id:
            continue
        
        # Determine video type: only standard match Highlights are accepted
        # Exclude: "Gamified Highlights", "International Sign Language (IS)", "Alt Cast"
        is_standard_highlights = (
            "Highlights" in title
            and "Gamified" not in title
            and "International Sign Language" not in title
            and "Alt Cast" not in title
            and "|" in title  # Standard format: "XXX v YYY | Group Z | FIFA World Cup 2026™ | Highlights"
        )
        video_type = "highlights" if is_standard_highlights else "other"
        
        # Build entry - support multiple videos per match
        existing = vmap.get(match_id, {})
        if isinstance(existing, str):
            # Legacy format: just an entryId string
            existing = {"entryId": existing, "title": "", "type": "highlights"}
        
        if not isinstance(existing, dict):
            existing = {}
        
        # Always keep standard "highlights" as primary, skip non-standard videos
        if video_type == "highlights":
            vmap[match_id] = {"entryId": eid, "title": title}
            new_count += 1
            print(f"[video] NEW: {match_id} -> {eid} ({title})")
        else:
            # Skip Gamified Highlights, ISL, Alt Cast, etc.
            print(f"[video] Skip {eid}: non-standard type (title: {title[:80]})")
    
    # Save updated mapping
    if new_count > 0:
        with open(VIDEOS_FILE, "w", encoding="utf-8") as f:
            json.dump(vmap, f, ensure_ascii=False, indent=2)
        print(f"[video] Saved {len(vmap)} video mappings ({new_count} new)")
    else:
        print(f"[video] No new videos (total: {len(vmap)})")
    
    return new_count


def main():
    teams_map, teams_ordered = load_teams()
    if not teams_map:
        print("[ERROR] No team data loaded from wc-roster-data.js")
        sys.exit(1)

    print(f"[INFO] Loaded {len(teams_map)} teams")

    matches = fetch_fifa()
    if not matches:
        print("[WARN] No matches fetched from FIFA API")
        sys.exit(0)

    print(f"[INFO] Fetched {len(matches)} matches from FIFA API")

    group_scores, bracket_scores, match_details = parse_matches(matches, teams_map, teams_ordered)
    write_output(group_scores, bracket_scores, match_details)

    # Fetch card data
    pid_map = load_player_id_map()
    print(f"[INFO] Loaded {len(pid_map)} player ID mappings")
    fetch_and_aggregate_cards(match_details, pid_map)

    # Auto-discover WC2026 highlight videos
    discover_videos()


if __name__ == "__main__":
    main()
