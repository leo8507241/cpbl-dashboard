"""
每日更新入口：依序抓「打者逐場資料」跟「打席對戰紀錄」。
資料來源：rebas.tw（野球革命）—— 不依賴 CPBL 官網，不受 IP 封鎖影響。

用法：
    python run_daily.py            # 預設從 2025 抓到今年
    python run_daily.py 2025 2026  # 指定起訖年
"""
import sys

import scrape_batter_gamelog_rebas as scrape_batter_gamelog
import scrape_matchup_log_rebas    as scrape_matchup_log


def main():
    start_year = int(sys.argv[1]) if len(sys.argv) > 1 else None
    end_year   = int(sys.argv[2]) if len(sys.argv) > 2 else None

    print("========== ① 打者逐場資料（rebas.tw）==========")
    batter_result = scrape_batter_gamelog.main(start_year, end_year)

    print("\n========== ② 打席對戰紀錄（rebas.tw）==========")
    matchup_result = scrape_matchup_log.main(start_year, end_year)

    print("\n========== 每日更新摘要 ==========")
    print(f"打者逐場資料：新增 {batter_result['new_games']} 場、{batter_result['new_rows']} 筆")
    print(f"打席對戰紀錄：新增 {matchup_result['new_games']} 場、{matchup_result['new_rows']} 筆")


if __name__ == "__main__":
    main()
