"""
樂天桃猿逐場資料（rebas.tw 版）
取代舊版 CPBL 官網爬蟲（已被 bot 封鎖）。

資料來源：
  - 比賽清單 + 主客場：rebas.tw /api/formal/players/{uid}/seasons/{season_uid}/logs
  - 打擊總計 + 勝負：Supabase cpbl_batter_game_log（由 scrape_batter_gamelog_rebas.py 每日更新）
  - 林立是否出場：cpbl_batter_game_log.batter_acnt = "RJzVu"

可重複執行：2020-2024 年保留 cache 不動，2025-2026 年每次重建以確保資料準確。
"""
import csv, json, os, sys, time, requests
from datetime import datetime
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, "lin_li_games_cache.csv")
LAST_UPDATE_PATH = os.path.join(BASE_DIR, "last_update.json")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
BASE_URL = "https://www.rebas.tw"

LIN_LI_UID = "RJzVu"
RAKUTEN_ABBR = "猿"
RAKUTEN_FULL = "樂天桃猿"

TEAM_ABBR_MAP = {
    "象": "中信兄弟",
    "鷹": "台鋼雄鷹",
    "獅": "統一7-ELEVEn獅",
    "龍": "味全龍",
    "悍": "富邦悍將",
    "猿": "樂天桃猿",
}

SEASONS = [
    ("CPBL-2025-JO", 2025),
    ("CPBL-2026-oB", 2026),
]

FIELDS = [
    "year", "game_sno", "date", "opponent", "home_away",
    "final_visiting", "final_home", "lin_li_played", "lin_li_role",
    "team_win", "team_r", "team_pa", "team_ab", "team_h",
    "team_1b", "team_2b", "team_3b", "team_hr",
    "team_bb", "team_ibb", "team_hbp", "team_sf", "team_sac", "team_so", "team_gidp",
    "team_sb", "team_cs",
]


def _get(path):
    r = requests.get(BASE_URL + path, headers={"User-Agent": UA}, timeout=20)
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


def _get_top_anchor_uids(season_uid, n=4):
    """取樂天出場最多的 n 位打者作為比賽索引錨點，確保涵蓋整個賽季所有場次"""
    leaders = _fetch_leaders(season_uid)
    rakuten = [
        (p["player"]["uniqid"], int(p.get("games", 0)))
        for p in leaders if p["player"].get("team_abbr") == RAKUTEN_ABBR
    ]
    rakuten.sort(key=lambda x: -x[1])
    return [uid for uid, _ in rakuten[:n]]


def _build_game_index(uids, season_uid):
    """合併多位打者的 logs，建立完整賽季比賽索引 {game_sno: {date, opponent, home_away}}"""
    index = {}
    for uid in uids:
        for g in _fetch_logs(uid, season_uid):
            seq = str(g.get("seq", ""))
            if not seq or seq in index:
                continue
            home_abbr = (g.get("home") or {}).get("abbr", "")
            is_home = (home_abbr == RAKUTEN_ABBR)
            opponent_abbr = g.get("opponent_abbr", "")
            opponent_full = TEAM_ABBR_MAP.get(opponent_abbr, opponent_abbr)
            index[seq] = {
                "date": (g.get("date") or "")[:10],
                "opponent": opponent_full,
                "home_away": "home" if is_home else "away",
            }
        time.sleep(0.2)
    return index


def _query_supabase_season(client, year):
    """從 Supabase 一次查出整賽季的樂天+對手打者資料（避免逐場查詢）"""
    rakuten_rows, opp_rows = [], []
    page_size = 1000  # Supabase 每次最多回傳 1000 筆，需分頁

    for col, val, out in [
        ("team", RAKUTEN_FULL, rakuten_rows),
        ("opponent", RAKUTEN_FULL, opp_rows),
    ]:
        page = 0
        while True:
            batch = (client.table("cpbl_batter_game_log")
                     .select("game_sno,batter_acnt,pa,ab,h,1b,2b,3b,hr,bb,ibb,hbp,sf,sac,so,r,sb,cs")
                     .eq("year", year).eq(col, val)
                     .range(page * page_size, (page + 1) * page_size - 1)
                     .execute().data)
            out.extend(batch)
            if len(batch) < page_size:  # 不足 1000 筆 → 最後一頁
                break
            page += 1

    return rakuten_rows, opp_rows


def _agg(rows):
    keys = ["pa", "ab", "h", "1b", "2b", "3b", "hr", "bb", "ibb", "hbp", "sf", "sac", "so", "r", "sb", "cs"]
    t = {k: 0 for k in keys}
    for b in rows:
        for k in keys:
            t[k] += int(b.get(k) or 0)
    return t


def _build_record(year, game_sno, game_info, rakuten_batters, opp_r):
    """建立 lin_li_games_cache 的一筆記錄"""
    if not rakuten_batters:
        return None

    t = _agg(rakuten_batters)
    team_r = t["r"]
    team_win = team_r > opp_r

    home_away = game_info["home_away"]
    final_home = team_r if home_away == "home" else opp_r
    final_visiting = opp_r if home_away == "home" else team_r

    lin_li = [b for b in rakuten_batters if b.get("batter_acnt") == LIN_LI_UID]
    lin_li_played = len(lin_li) > 0
    lin_li_pa = int(lin_li[0].get("pa") or 0) if lin_li else 0
    lin_li_role = ("先發" if lin_li_pa >= 3 else "代打") if lin_li_played else ""

    return {
        "year": str(year),
        "game_sno": str(game_sno),
        "date": game_info["date"],
        "opponent": game_info["opponent"],
        "home_away": home_away,
        "final_visiting": final_visiting,
        "final_home": final_home,
        "lin_li_played": lin_li_played,
        "lin_li_role": lin_li_role,
        "team_win": team_win,
        "team_r": team_r,
        "team_pa": t["pa"],
        "team_ab": t["ab"],
        "team_h": t["h"],
        "team_1b": t["1b"],
        "team_2b": t["2b"],
        "team_3b": t["3b"],
        "team_hr": t["hr"],
        "team_bb": t["bb"],
        "team_ibb": t["ibb"],
        "team_hbp": t["hbp"],
        "team_sf": t["sf"],
        "team_sac": t["sac"],
        "team_so": t["so"],
        "team_gidp": 0,
        "team_sb": t["sb"],
        "team_cs": t["cs"],
    }


def main():
    # Load existing cache (保留 2020-2024 年資料)
    cache = {}
    historical = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                key = (row["year"], str(row["game_sno"]))
                if int(row["year"]) < 2025:
                    historical[key] = row  # 保留 2020-2024
                else:
                    cache[key] = row      # 2025-2026 會從 Supabase 重建

    print(f"保留歷史資料：{len(historical)} 場（2020-2024）")
    print(f"待重建：{len(cache)} 場（2025-2026，將從 rebas.tw + Supabase 更新）")

    # Connect to Supabase
    _here = os.path.dirname(BASE_DIR)
    sys.path.insert(0, os.path.join(_here, "每日追蹤"))
    try:
        from common import get_supabase_client
        client = get_supabase_client()
    except Exception as e:
        print(f"⚠️ Supabase 連線失敗：{e}")
        client = None

    new_records = {}
    new_games = []  # 本次新增（不在舊 cache 中）的場次

    for season_uid, year in SEASONS:
        print(f"\n=== {year} 年（rebas.tw + Supabase）===")

        # 取比賽清單（使用多位錨點確保涵蓋整個賽季）
        anchor_uids = _get_top_anchor_uids(season_uid, n=4)
        if not anchor_uids:
            print(f"  找不到樂天球員，跳過")
            continue
        game_index = _build_game_index(anchor_uids, season_uid)
        print(f"  比賽清單：{len(game_index)} 場（rebas.tw anchors={anchor_uids}）")
        time.sleep(0.2)

        # 一次查出整賽季打者資料
        if client is None:
            print(f"  ⚠ 無 Supabase 連線，跳過 {year}")
            continue

        rakuten_rows, opp_rows = _query_supabase_season(client, year)
        print(f"  Supabase：樂天 {len(rakuten_rows)} 筆 / 對手 {len(opp_rows)} 筆")

        # 依 game_sno 分組
        rakuten_by_game = defaultdict(list)
        for b in rakuten_rows:
            rakuten_by_game[str(b["game_sno"])].append(b)

        opp_r_by_game = defaultdict(int)
        for b in opp_rows:
            opp_r_by_game[str(b["game_sno"])] += int(b.get("r") or 0)

        # 建立每場記錄
        for game_sno in sorted(game_index.keys(), key=lambda s: game_index[s]["date"]):
            game_info = game_index[game_sno]
            rakuten_batters = rakuten_by_game.get(game_sno, [])
            if not rakuten_batters:
                print(f"  ⏭ {game_info['date']} seq={game_sno} 無打者資料（可能尚未完賽）")
                continue

            opp_r = opp_r_by_game.get(game_sno, 0)
            record = _build_record(year, game_sno, game_info, rakuten_batters, opp_r)
            if record is None:
                continue

            key = (str(year), str(game_sno))
            new_records[key] = record

            # 判斷是否為本次新增（不在原有 cache 中）
            old_cache_key = (str(year), str(game_sno))
            if old_cache_key not in cache:
                new_games.append(record)
                lin = "林立有上場" if record["lin_li_played"] else "林立未上場"
                print(f"  ✅ 新增 {game_info['date']} seq={game_sno} vs {game_info['opponent']} {'勝' if record['team_win'] else '負'}（{lin}）")

    # 合併資料：歷史(2020-2024) + 舊 cache 保底(2025-2026) + 新 Supabase 資料覆蓋
    # cache 作為 fallback（避免 Supabase 尚未有某場次資料時資料丟失）
    all_records = {}
    all_records.update(historical)   # 2020-2024：保留不動
    all_records.update(cache)        # 2025-2026 舊版本（fallback）
    all_records.update(new_records)  # 用 rebas.tw+Supabase 新資料覆蓋
    rows = sorted(all_records.values(), key=lambda r: (r["year"], int(r.get("game_sno") or 0)))

    # 寫入 CSV cache
    with open(CACHE_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # 更新 last_update.json
    lin_li_new = [g for g in new_games if g["lin_li_played"]]
    meta = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "new_games": [
            {k: g[k] for k in [
                "year", "game_sno", "date", "opponent", "home_away",
                "final_visiting", "final_home", "lin_li_played", "lin_li_role",
                "team_win", "team_r", "team_pa", "team_ab", "team_h",
                "team_1b", "team_2b", "team_3b", "team_hr",
                "team_bb", "team_ibb", "team_hbp", "team_sf", "team_sac", "team_so", "team_gidp",
                "team_sb", "team_cs",
            ]}
            for g in new_games if g["lin_li_played"]
        ],
        "total_games": len(rows),
    }
    with open(LAST_UPDATE_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n本次新增 {len(new_games)} 場（含林立上場 {len(lin_li_new)} 場）")
    print(f"cache 總計 {len(rows)} 場")
    return new_games


if __name__ == "__main__":
    main()
