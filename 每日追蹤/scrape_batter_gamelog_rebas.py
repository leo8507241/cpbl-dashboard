"""
全聯盟逐場打者資料（rebas.tw 版）
取代 scrape_batter_gamelog.py（CPBL 官網版，已被 bot 封鎖）。

資料來源：rebas.tw /api/formal/players/{uid}/seasons/{season_uid}/logs
每場 game 物件內有 batting 彙總（無需自行加總 PA_list）。
game.seq = CPBL game_sno（驗證：2026-07-12 CPBL=202、rebas.tw=202，一致）。

Supabase table: cpbl_batter_game_log
衝突鍵: year, game_sno, batter_acnt（batter_uid 充當 batter_acnt）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
BASE = "https://www.rebas.tw"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, "batter_gamelog_rebas_cache.csv")
TABLE_NAME = "cpbl_batter_game_log"

FIELDS = [
    "year", "game_sno", "date", "team", "opponent", "batter_acnt", "batter_name",
    "pa", "ab", "h", "1b", "2b", "3b", "hr", "bb", "ibb", "hbp", "sf", "sac",
    "so", "rbi", "r", "sb", "cs",
]

SEASONS = [
    ("CPBL-2025-JO", 2025),
    ("CPBL-2026-oB", 2026),
]

TEAM_ABBR_MAP = {
    "象": "中信兄弟",
    "鷹": "台鋼雄鷹",
    "獅": "統一7-ELEVEn獅",
    "龍": "味全龍",
    "悍": "富邦悍將",
    "猿": "樂天桃猿",
}


def _get(path):
    r = requests.get(BASE + path, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return r.json()


def _fetch_leaders(season_uid):
    return _get(f"/api/seasons/{season_uid}/leaders?type=batter&section=standard&pa=undefined").get("data", [])


def _fetch_logs(uid, season_uid):
    try:
        return _get(f"/api/formal/players/{uid}/seasons/{season_uid}/logs").get("data", [])
    except Exception as e:
        print(f"    ⚠ {uid}: {e}")
        return []


def _game_to_record(g, uid, name, team_full, year):
    b   = g.get("batting", {})
    h   = int(b.get("H") or 0)
    h2b = int(b.get("Double") or 0)
    h3b = int(b.get("Triple") or 0)
    hr  = int(b.get("HR") or 0)
    return {
        "year":        year,
        "game_sno":    str(g.get("seq", "")),
        "date":        (g.get("date") or "")[:10],
        "team":        team_full,
        "opponent":    TEAM_ABBR_MAP.get(g.get("opponent_abbr", ""), g.get("opponent_abbr", "")),
        "batter_acnt": uid,
        "batter_name": name,
        "pa":  int(b.get("PA")  or 0),
        "ab":  int(b.get("AB")  or 0),
        "h":   h,
        "1b":  max(0, h - h2b - h3b - hr),
        "2b":  h2b,
        "3b":  h3b,
        "hr":  hr,
        "bb":  int(b.get("BB")  or 0),
        "ibb": int(b.get("IBB") or 0),
        "hbp": int(b.get("HBP") or 0),
        "sf":  int(b.get("SF")  or 0),
        "sac": int(b.get("SH")  or 0),
        "so":  int(b.get("SO")  or 0),
        "rbi": int(b.get("RBI") or 0),
        "r":   int(b.get("R")   or 0),
        "sb":  int(b.get("SB")  or 0),
        "cs":  int(b.get("CS")  or 0),
    }


def main(start_year=None, end_year=None):
    from datetime import datetime
    start_year = start_year or 2025
    end_year   = end_year   or datetime.now().year

    cache = common.load_cache(CACHE_PATH, key_fields=["year", "game_sno", "batter_acnt"])
    rows  = list(cache.values())
    done  = set(cache.keys())

    new_game_set = set()
    new_rows     = 0

    for season_uid, year in SEASONS:
        if not (start_year <= year <= end_year):
            continue
        print(f"=== rebas.tw {year} 打者逐場 ===")
        leaders = _fetch_leaders(season_uid)
        print(f"  共 {len(leaders)} 位打者，開始抓取逐場紀錄…")

        for i, entry in enumerate(leaders, 1):
            pl        = entry["player"]
            uid       = pl["uniqid"]
            name      = pl["name"]
            team_full = TEAM_ABBR_MAP.get(pl.get("team_abbr", ""), pl.get("team_abbr", ""))

            for g in _fetch_logs(uid, season_uid):
                seq = str(g.get("seq", ""))
                if not seq or not g.get("batting"):
                    continue
                key = (str(year), seq, uid)
                if key in done:
                    continue
                rec = _game_to_record(g, uid, name, team_full, year)
                rows.append(rec)
                done.add(key)
                new_game_set.add((year, seq))
                new_rows += 1

            if i % 20 == 0:
                print(f"    {i}/{len(leaders)} 位完成")
            time.sleep(0.2)

    new_games = len(new_game_set)

    common.save_cache(
        CACHE_PATH, FIELDS, rows,
        sort_key=lambda r: (r["year"], int(r["game_sno"] or 0), r["batter_acnt"]),
    )

    client = common.get_supabase_client()
    if client is None:
        print("⚠️  沒有 SUPABASE_WRITE_KEY，僅更新本地 CSV cache")
    else:
        common.upsert_batches(client, TABLE_NAME, rows, on_conflict="year,game_sno,batter_acnt")

    print(f"\n打者逐場資料（rebas.tw）：新增 {new_games} 場、{new_rows} 筆打者紀錄，"
          f"cache 總計 {len(rows)} 筆")
    return {"new_games": new_games, "new_rows": new_rows, "total_rows": len(rows)}


if __name__ == "__main__":
    main()
