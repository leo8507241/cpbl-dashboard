"""
每日更新入口：依序抓「打者逐場資料」跟「打席對戰紀錄」。
比照 樂天林立分析/scrape_games.py 的用法，每天手動執行（或自己排程執行）一次即可，
已經抓過的場次會自動跳過，只處理當天新增的比賽。

用法：
    python run_daily.py            # 預設從 common.START_YEAR_DEFAULT 抓到今年
    python run_daily.py 2025 2026  # 指定起訖年
"""
import sys

import scrape_batter_gamelog
import scrape_matchup_log


def main():
    start_year = int(sys.argv[1]) if len(sys.argv) > 1 else None
    end_year = int(sys.argv[2]) if len(sys.argv) > 2 else None

    print("========== ① 打者逐場資料 ==========")
    batter_result = scrape_batter_gamelog.main(start_year, end_year)

    print("\n========== ② 打席對戰紀錄 ==========")
    matchup_result = scrape_matchup_log.main(start_year, end_year)

    print("\n========== 每日更新摘要 ==========")
    print(f"打者逐場資料：新增 {batter_result['new_games']} 場、{batter_result['new_rows']} 筆")
    print(f"打席對戰紀錄：新增 {matchup_result['new_games']} 場、{matchup_result['new_rows']} 筆"
          + (f"，{len(matchup_result['warnings'])} 筆對帳警告" if matchup_result["warnings"] else ""))


if __name__ == "__main__":
    main()
