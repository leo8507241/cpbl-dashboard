"""
修復13位投手歷史資料裡「局數標籤被壓縮/截斷」的問題(2026-07稽核發現)。

背景：稽核單場即時換投監控時，發現鋼龍/布雷克/艾速特/羅戈/江承諺/黃子鵬/勝騎士/李東洺/
魔力藍/魔爾曼/獅帝芬/梅賽鍶/蔣銲這13位投手，本地pitcher_pitches.csv裡有異常高比例(64%~100%)
的先發「剛好」都停在同一局(多半是第3局)。實際拿魔力藍/鋼龍的比賽對照rebas.tw API直接查證：
  - API本身回傳的PA_list是完整的(魔力藍2026-07-03這場API有6局、110球)
  - 但本地CSV存的同一場，球數同樣是110球，局數卻被壓縮成只有1-3局
  - 用現在的 fetch_new_pitchers.logs_to_rows() 重新轉換同一筆API資料，局數是正確的(1-6局)
結論：資料來源沒問題，現在的抓取程式也沒問題，是這幾位投手的歷史資料在「當初」被寫入
pitcher_pitches.csv時用了某個已經不存在的舊邏輯，局數欄位算錯。修法：整批重新抓這13位投手
的全部歷史賽季資料，用現在正確的邏輯覆蓋掉舊的壞資料。

用法：
  python backfill_truncated_pitchers.py --dry-run   # 只列出會抓哪些投手/場次，不寫檔
  python backfill_truncated_pitchers.py              # 實際重抓並覆蓋pitcher_pitches.csv
"""
import argparse
import time

import pandas as pd

from fetch_new_pitchers import CSV_PATH, SEASONS, fetch_season_logs, logs_to_rows

FLAGGED_PITCHERS = {
    "KPTzi": "鋼龍", "CvkZv": "布雷克", "mxPQL": "艾速特", "upBvv": "羅戈",
    "3Xh4g": "江承諺", "J4Ca0": "黃子鵬", "PtGe1": "勝騎士", "v1Pw4": "李東洺",
    "QRk3": "魔力藍", "6ePkF": "魔爾曼", "zzvID": "獅帝芬", "7JDl0": "梅賽鍶",
    "QyYQE": "蔣銲",
}


def main(dry_run=False):
    existing = pd.read_csv(CSV_PATH, low_memory=False)
    print(f"現有 CSV：{len(existing):,} 筆")

    old_rows = existing[existing["pitcher_uid"].isin(FLAGGED_PITCHERS)]
    print(f"這13位投手目前的舊資料：{len(old_rows):,} 筆，{old_rows.groupby(['pitcher_uid','game_date']).ngroups} 場\n")

    if dry_run:
        for uid, name in FLAGGED_PITCHERS.items():
            years_present = sorted(existing.loc[existing["pitcher_uid"] == uid, "year"].unique())
            print(f"  {name}(uid={uid})：舊資料涵蓋年度 {years_present}，將重新抓取全部4個球季")
        print("\n--dry-run 模式，不實際抓取或寫檔。")
        return

    all_new_rows = []
    for pi, (uid, name) in enumerate(FLAGGED_PITCHERS.items(), 1):
        print(f"\n[{pi}/{len(FLAGGED_PITCHERS)}] {name} (uid={uid})")
        for season_uid, year in SEASONS:
            print(f"  重新抓取 {year} ({season_uid})...", end=" ", flush=True)
            games = fetch_season_logs(uid, season_uid)
            if not games:
                print("（無資料）")
                time.sleep(0.3)
                continue
            rows = logs_to_rows(name, uid, season_uid, year, games)
            print(f"{len(games)} 場賽事，{len(rows)} 球")
            all_new_rows.extend(rows)
            time.sleep(0.4)

    if not all_new_rows:
        print("\n沒有抓到任何資料，中止(不覆蓋現有CSV)。")
        return

    new_df = pd.DataFrame(all_new_rows)
    for col in existing.columns:
        if col not in new_df.columns:
            new_df[col] = ""
    new_df = new_df[existing.columns]

    kept = existing[~existing["pitcher_uid"].isin(FLAGGED_PITCHERS)]
    combined = pd.concat([kept, new_df], ignore_index=True)
    combined.to_csv(CSV_PATH, index=False)

    print(f"\n完成！移除舊資料 {len(old_rows):,} 筆，寫入重抓資料 {len(new_df):,} 筆")
    print(f"合計 {len(combined):,} 筆 → {CSV_PATH}（原本 {len(existing):,} 筆）")

    print("\n各投手重抓前後場次數對照：")
    new_games_count = new_df.groupby("pitcher_uid")["game_date"].nunique()
    old_games_count = old_rows.groupby("pitcher_uid")["game_date"].nunique()
    for uid, name in FLAGGED_PITCHERS.items():
        print(f"  {name}: 舊資料{old_games_count.get(uid, 0)}場 -> 重抓後{new_games_count.get(uid, 0)}場")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
