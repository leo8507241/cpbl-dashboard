"""
全聯盟打席對戰紀錄（rebas.tw 版）
取代 scrape_matchup_log.py（CPBL 官網版，已被 bot 封鎖）。

資料來源：rebas.tw 投手 player logs → PA_list
每個 PA 包含：打者資訊、打席結果（result）、局數、球數、壘包狀況等。

Supabase table: cpbl_matchup_log
衝突鍵: year, game_sno, main_event_no
  main_event_no 改用 synthetic key："{game_uid}_{pitcher_uid}_{pa_round}"
  (CPBL 原版是數字流水號，rebas.tw 版改為字串 ID，互不衝突)

抓所有投手（無出賽場次門檻），確保對戰紀錄完整。
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
CACHE_PATH = os.path.join(BASE_DIR, "matchup_log_rebas_cache.csv")
TABLE_NAME = "cpbl_matchup_log"

FIELDS = [
    "year", "game_sno", "date", "inning_seq",
    "pitcher_acnt", "pitcher_name", "pitcher_team",
    "batter_acnt", "batter_name", "batter_team",
    "result", "main_event_no",
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

# rebas.tw PA result → 標準化結果（與 CPBL 版對齊）
RESULT_MAP = {
    "1B": "1B", "IH": "1B",
    "2B": "2B",
    "3B": "3B",
    "HR": "HR",
    "BB": "BB",
    "IBB": "IBB",
    "HBP": "HBP",
    "SF": "SF",
    "SH": "SAC",
    "BUNT": "SAC",
    "SO": "SO",
    "K": "SO",
    "GIDP": "GIDP",
    "FC":   "OUT",
    "E":    "OTHER",
    "OUT":  "OUT",
}


def _get(path):
    r = requests.get(BASE + path, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return r.json()


def _fetch_pitcher_leaders(season_uid):
    """抓所有投手，不設場次門檻（pa=undefined 回傳全部）。"""
    return _get(f"/api/seasons/{season_uid}/leaders?type=pitcher&section=standard&pa=undefined").get("data", [])


def _fetch_logs(uid, season_uid):
    try:
        return _get(f"/api/formal/players/{uid}/seasons/{season_uid}/logs").get("data", [])
    except Exception as e:
        print(f"    ⚠ {uid}: {e}")
        return []


def _parse_game(g, pitcher_uid, pitcher_name, pitcher_team, year):
    """從一場投手 game log 的 PA_list 解析出所有打席對戰紀錄。"""
    game_uid  = g.get("uniqid", "")
    game_sno  = str(g.get("seq", ""))
    date      = (g.get("date") or "")[:10]
    home_abbr = (g.get("home") or {}).get("abbr", "")
    away_abbr = (g.get("away") or {}).get("abbr", "")

    records = []
    for pa in g.get("PA_list", []):
        batter_info = pa.get("batter") or {}
        batter_uid  = batter_info.get("uniqid", "")
        batter_name = batter_info.get("name", "")
        if not batter_uid:
            continue

        # 確定打者球隊：side="HOME" 表示打者是主場攻擊
        batter_side = pa.get("side", "")
        if batter_side == "HOME":
            batter_team_abbr = home_abbr
        else:
            batter_team_abbr = away_abbr
        batter_team = TEAM_ABBR_MAP.get(batter_team_abbr, batter_team_abbr)

        raw_result = pa.get("result", "")
        result     = RESULT_MAP.get(raw_result, "OUT") if raw_result else "OTHER"

        pa_round = pa.get("PA_round", 0)
        # 加入 batter_uid 確保同一場中不同打者的 PA_round=1 不會碰撞
        main_event_no = f"{game_uid}_{pitcher_uid}_{batter_uid}_{pa_round}"

        records.append({
            "year":         year,
            "game_sno":     game_sno,
            "date":         date,
            "inning_seq":   pa.get("inning", ""),
            "pitcher_acnt": pitcher_uid,
            "pitcher_name": pitcher_name,
            "pitcher_team": pitcher_team,
            "batter_acnt":  batter_uid,
            "batter_name":  batter_name,
            "batter_team":  batter_team,
            "result":       result,
            "main_event_no": main_event_no,
        })
    return records


def main(start_year=None, end_year=None):
    from datetime import datetime
    start_year = start_year or 2025
    end_year   = end_year   or datetime.now().year

    cache = common.load_cache(CACHE_PATH, key_fields=["year", "game_sno", "main_event_no"])
    rows  = list(cache.values())
    done  = set(cache.keys())

    new_game_set = set()
    new_rows     = 0

    for season_uid, year in SEASONS:
        if not (start_year <= year <= end_year):
            continue
        print(f"=== rebas.tw {year} 投手對戰紀錄 ===")
        leaders = _fetch_pitcher_leaders(season_uid)
        print(f"  共 {len(leaders)} 位投手")

        for i, entry in enumerate(leaders, 1):
            pl           = entry["player"]
            pitcher_uid  = pl["uniqid"]
            pitcher_name = pl["name"]
            pitcher_team = TEAM_ABBR_MAP.get(pl.get("team_abbr", ""), pl.get("team_abbr", ""))

            for g in _fetch_logs(pitcher_uid, season_uid):
                seq      = str(g.get("seq", ""))
                game_uid = g.get("uniqid", "")
                if not seq or not game_uid:
                    continue

                records = _parse_game(g, pitcher_uid, pitcher_name, pitcher_team, year)
                for rec in records:
                    key = (str(year), rec["game_sno"], rec["main_event_no"])
                    if key in done:
                        continue
                    rows.append(rec)
                    done.add(key)
                    new_game_set.add((year, seq))
                    new_rows += 1

            if i % 10 == 0:
                print(f"    {i}/{len(leaders)} 位完成")
            time.sleep(0.3)

    new_games = len(new_game_set)

    common.save_cache(
        CACHE_PATH, FIELDS, rows,
        sort_key=lambda r: (r["year"], int(r["game_sno"] or 0), r["main_event_no"] or ""),
    )

    client = common.get_supabase_client()
    if client is None:
        print("⚠️  沒有 SUPABASE_WRITE_KEY，僅更新本地 CSV cache")
    else:
        common.upsert_batches(client, TABLE_NAME, rows, on_conflict="year,game_sno,main_event_no")

    print(f"\n打席對戰紀錄（rebas.tw）：新增 {new_games} 場、{new_rows} 個打席，"
          f"cache 總計 {len(rows)} 筆")
    return {"new_games": new_games, "new_rows": new_rows, "total_rows": len(rows)}


if __name__ == "__main__":
    main()
