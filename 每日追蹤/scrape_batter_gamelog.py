"""
全聯盟每日逐場打者資料爬蟲：抓每一場比賽裡『每一位打者』的當場計數型打擊數據，
寫進 cpbl_batter_game_log（打者滾動趨勢雷達的資料來源）。

跟 樂天林立分析/scrape_games.py 的差別：
- 不篩球隊，一次抓全聯盟
- 每場輸出「每位打者一列」，不是球隊總計一列

可重複執行：已經抓過的比賽（year+game_sno）會整場跳過，只抓新場次。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, "batter_gamelog_cache.csv")
TABLE_NAME = "cpbl_batter_game_log"

FIELDS = [
    "year", "game_sno", "date", "team", "opponent", "batter_acnt", "batter_name",
    "pa", "ab", "h", "1b", "2b", "3b", "hr", "bb", "ibb", "hbp", "sf", "sac",
    "so", "rbi", "r", "sb", "cs",
]


def batting_row_to_record(game_meta, b):
    side = str(b.get("VisitingHomeType"))
    is_visiting = side == "1"
    team = game_meta["VisitingTeamName"] if is_visiting else game_meta["HomeTeamName"]
    opponent = game_meta["HomeTeamName"] if is_visiting else game_meta["VisitingTeamName"]

    def n(key):
        return int(b.get(key) or 0)

    return {
        "year": game_meta["Year"],
        "game_sno": game_meta["GameSno"],
        "date": game_meta["GameDate"][:10],
        "team": team,
        "opponent": opponent,
        "batter_acnt": b.get("HitterAcnt"),
        "batter_name": b.get("HitterName"),
        "pa": n("PlateAppearances"),
        "ab": n("HitCnt"),
        "h": n("HittingCnt"),
        "1b": n("OneBaseHitCnt"),
        "2b": n("TwoBaseHitCnt"),
        "3b": n("ThreeBaseHitCnt"),
        "hr": n("HomeRunCnt"),
        "bb": n("BasesONBallsCnt"),
        "ibb": n("IntentionalBasesONBallsCnt"),
        "hbp": n("HitBYPitchCnt"),
        "sf": n("SacrificeFlyCnt"),
        "sac": n("SacrificeHitCnt"),
        "so": n("StrikeOutCnt"),
        "rbi": n("RunBattedINCnt"),
        "r": n("ScoreCnt"),
        "sb": n("StealBaseOKCnt"),
        "cs": n("StealBaseFailCnt"),
    }


def main(start_year=None, end_year=None):
    start_year = start_year or common.START_YEAR_DEFAULT
    end_year = end_year or __import__("datetime").datetime.now().year

    cache = common.load_cache(CACHE_PATH, key_fields=["year", "game_sno", "batter_acnt"])
    rows = list(cache.values())
    # 用「這場比賽有沒有任何一位打者已經在 cache 裡」判斷這場是否已抓過，
    # 避免同一場漏抓了某幾位打者卻誤判成已完成。
    processed_games = {(y, s) for (y, s, _acnt) in cache.keys()}

    new_games = 0
    new_rows = 0

    for game_meta, game_detail, battings, livelog in common.iter_finished_games(
        start_year, end_year, processed_games
    ):
        game_batter_rows = [batting_row_to_record(game_meta, b) for b in battings]
        rows.extend(game_batter_rows)
        new_games += 1
        new_rows += len(game_batter_rows)
        print(f"  + {game_meta['GameDate'][:10]} "
              f"{game_meta['VisitingTeamName']} vs {game_meta['HomeTeamName']} "
              f"（{len(game_batter_rows)} 位打者）")

    common.save_cache(
        CACHE_PATH, FIELDS, rows,
        sort_key=lambda r: (r["year"], int(r["game_sno"]), r["batter_acnt"]),
    )

    client = common.get_supabase_client()
    if client is None:
        print("⚠️  沒有 SUPABASE_WRITE_KEY，只更新了本地 CSV cache，沒有寫入 Supabase。")
    else:
        common.upsert_batches(client, TABLE_NAME, rows, on_conflict="year,game_sno,batter_acnt")

    print(f"\n打者逐場資料：新增 {new_games} 場、{new_rows} 筆打者紀錄，"
          f"cache 總計 {len(rows)} 筆，存於 {CACHE_PATH}")
    return {"new_games": new_games, "new_rows": new_rows, "total_rows": len(rows)}


if __name__ == "__main__":
    main()
