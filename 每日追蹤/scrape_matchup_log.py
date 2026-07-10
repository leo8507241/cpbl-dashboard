"""
全聯盟每日打席對戰爬蟲：解析 LiveLogJson（CPBL 逐球紀錄），
把「這一球打席最後結果、誰投誰打」轉成一列，寫進 cpbl_matchup_log（投手剋星分析的資料來源）。

PA 切分規則：LiveLogJson 裡同一個打席的所有逐球紀錄，
(InningSeq, VisitingHomeType, BattingOrder) 三者都相同，且 BattingActionName 在整個打席
一開始就已經是最終結果（不是逐球累積），所以取每組「最後一列」當作這個打席的結果即可。
（已用 2024 年例行賽第 1 場的真實資料驗證過這個假設。）

內建對帳：解析完一場比賽後，跟同一場 BattingJson 的官方 PA/H/BB/SO 總數對一次帳，
對不上就印警告——不代表資料完全沒用，但代表 BattingActionName 分類表可能有沒涵蓋到的字串，
需要回頭檢查、補上映射規則。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, "matchup_log_cache.csv")
TABLE_NAME = "cpbl_matchup_log"

FIELDS = [
    "year", "game_sno", "date", "inning_seq",
    "pitcher_acnt", "pitcher_name", "pitcher_team",
    "batter_acnt", "batter_name", "batter_team",
    "result", "main_event_no",
]

# BattingActionName -> 標準化結果代碼。涵蓋 2024 年例行賽前 3 場實測看到的所有字串，
# 遇到沒看過的字串一律歸 OUT（多半是各種守備位置的出局，例如 XX滾/XX飛/界飛）。
_EXACT_MAP = {
    "三振": "SO",
    "不死三振": "SO",  # 捕逸/暴投造成的不死三振，官方 StrikeOutCnt 仍算三振，但打者上壘
    "一安": "1B",
    "內安": "1B",
    "場安": "1B",  # 場地安打（游擊方向/內野深處的一壘安打變體）
    "二安": "2B",
    "場二": "2B",  # 場地二壘安打
    "三安": "3B",
    "場三": "3B",  # 場地三壘安打
    "全打": "HR",
    "死球": "HBP",
    "犧短": "SAC",
    "犧飛": "SF",
    "雙殺": "GIDP",
}


def classify_result(batting_action_name, content):
    ban = (batting_action_name or "").strip()
    content = content or ""
    if ban == "故四":
        return "IBB"
    if ban == "四壞":
        return "IBB" if "故意" in content else "BB"
    if ban in _EXACT_MAP:
        return _EXACT_MAP[ban]
    if ban.endswith("誤") or ban.endswith("失"):
        return "OTHER"  # 守備失誤上壘等邊緣情況，先歸 OTHER，用對帳機制去抓漏
    if ban:
        return "OUT"  # 各種飛球/滾地球/界外球接殺出局
    return "OTHER"


def parse_game(game_meta, livelog):
    """把逐球紀錄依 (InningSeq, VisitingHomeType, BattingOrder) 分組成打席，回傳每個打席一列。"""
    groups = {}
    order = []
    for row in livelog:
        key = (row["InningSeq"], row["VisitingHomeType"], row["BattingOrder"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    records = []
    for key in order:
        last = groups[key][-1]
        if not (last.get("BattingActionName") or "").strip():
            # 半局因跑壘出局（例如盜壘刺）結束、打者當時還在打擊中——
            # CPBL 官方不算這個打者一個打席，這裡也跳過，不當成一次打席結果
            continue
        is_visiting_batter = str(last["VisitingHomeType"]) == "1"
        batter_team = game_meta["VisitingTeamName"] if is_visiting_batter else game_meta["HomeTeamName"]
        pitcher_team = game_meta["HomeTeamName"] if is_visiting_batter else game_meta["VisitingTeamName"]
        records.append({
            "year": game_meta["Year"],
            "game_sno": game_meta["GameSno"],
            "date": game_meta["GameDate"][:10],
            "inning_seq": last["InningSeq"],
            "pitcher_acnt": last.get("PitcherAcnt"),
            "pitcher_name": last.get("PitcherName"),
            "pitcher_team": pitcher_team,
            "batter_acnt": last.get("HitterAcnt"),
            "batter_name": last.get("HitterName"),
            "batter_team": batter_team,
            "result": classify_result(last.get("BattingActionName"), last.get("Content")),
            "main_event_no": last.get("MainEventNo"),
        })
    return records


def reconcile(game_meta, matchup_records, battings):
    """跟官方 BattingJson 對帳，對不上回傳警告文字列表（不會擋下寫入，只是提醒要檢查）。"""
    by_batter = {}
    for r in matchup_records:
        d = by_batter.setdefault(r["batter_acnt"], {"pa": 0, "h": 0, "bb": 0, "so": 0})
        d["pa"] += 1
        if r["result"] in ("1B", "2B", "3B", "HR"):
            d["h"] += 1
        if r["result"] in ("BB", "IBB"):
            d["bb"] += 1
        if r["result"] == "SO":
            d["so"] += 1

    warnings = []
    for b in battings:
        acnt = b.get("HitterAcnt")
        official = (
            int(b.get("PlateAppearances") or 0),
            int(b.get("HittingCnt") or 0),
            int(b.get("BasesONBallsCnt") or 0),  # 官方這欄已經內含故意四壞，不用再加 IntentionalBasesONBallsCnt
            int(b.get("StrikeOutCnt") or 0),
        )
        parsed = by_batter.get(acnt, {"pa": 0, "h": 0, "bb": 0, "so": 0})
        parsed_tuple = (parsed["pa"], parsed["h"], parsed["bb"], parsed["so"])
        if parsed_tuple != official:
            warnings.append(
                f"{game_meta['GameDate'][:10]} {game_meta['GameSno']} {b.get('HitterName')}："
                f"解析 PA/H/BB/SO={parsed_tuple} vs 官方={official}"
            )
    return warnings


def main(start_year=None, end_year=None):
    start_year = start_year or common.START_YEAR_DEFAULT
    end_year = end_year or __import__("datetime").datetime.now().year

    cache = common.load_cache(CACHE_PATH, key_fields=["year", "game_sno", "main_event_no"])
    rows = list(cache.values())
    processed_games = {(y, s) for (y, s, _meno) in cache.keys()}

    new_games = 0
    new_rows = 0
    all_warnings = []

    for game_meta, game_detail, battings, livelog in common.iter_finished_games(
        start_year, end_year, processed_games
    ):
        game_records = parse_game(game_meta, livelog)
        warnings = reconcile(game_meta, game_records, battings)
        if warnings:
            print(f"  ⚠️  對帳不吻合（{len(warnings)} 位打者）：")
            for w in warnings:
                print(f"      {w}")
            all_warnings.extend(warnings)

        rows.extend(game_records)
        new_games += 1
        new_rows += len(game_records)
        print(f"  + {game_meta['GameDate'][:10]} "
              f"{game_meta['VisitingTeamName']} vs {game_meta['HomeTeamName']} "
              f"（{len(game_records)} 個打席）")

    common.save_cache(
        CACHE_PATH, FIELDS, rows,
        sort_key=lambda r: (r["year"], int(r["game_sno"]), r["main_event_no"] or ""),
    )

    client = common.get_supabase_client()
    if client is None:
        print("⚠️  沒有 SUPABASE_WRITE_KEY，只更新了本地 CSV cache，沒有寫入 Supabase。")
    else:
        common.upsert_batches(client, TABLE_NAME, rows, on_conflict="year,game_sno,main_event_no")

    print(f"\n對戰紀錄：新增 {new_games} 場、{new_rows} 個打席，"
          f"cache 總計 {len(rows)} 筆，存於 {CACHE_PATH}")
    if all_warnings:
        print(f"⚠️  共 {len(all_warnings)} 筆對帳不吻合，建議檢查 classify_result() 的映射表。")
    return {"new_games": new_games, "new_rows": new_rows, "total_rows": len(rows), "warnings": all_warnings}


if __name__ == "__main__":
    main()
