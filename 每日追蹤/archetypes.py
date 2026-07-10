"""
打者原型分桶：給定 cpbl_batting_2020_2026 的球季列（要有 年度/pa/iso/BB%/KK% 欄位），
在「同一年度」內用中位數切成 4 型。

⚠️ 這是表現輪廓分桶（長打 x 選球），不是左右打分桶——CPBL 官網查不到打者慣用手資料。
"""
import pandas as pd

MIN_PA = 100


def add_archetype(df):
    df = df.copy()
    df["原型"] = None
    for year, idx in df.groupby("年度").groups.items():
        sub = df.loc[idx]
        qualified = sub[sub["pa"] >= MIN_PA].dropna(subset=["iso", "BB%", "KK%"])
        if len(qualified) < 4:
            continue  # 樣本太少，這一年不分型
        iso_median = qualified["iso"].median()
        disc_median = (qualified["BB%"] - qualified["KK%"]).median()

        for i in qualified.index:
            row = df.loc[i]
            power = row["iso"] >= iso_median
            discipline = (row["BB%"] - row["KK%"]) >= disc_median
            if power and discipline:
                label = "選球長打型"
            elif power and not discipline:
                label = "重砲長打型"
            elif not power and discipline:
                label = "穩健選球型"
            else:
                label = "積極接觸型"
            df.loc[i, "原型"] = label
    return df


def batter_season_stats_from_matchup(matchup_df):
    """把 cpbl_matchup_log（每打席一列）依 (batter_acnt, batter_name, year) 聚合成球季彙總，
    算出 pa/iso/BB%/KK%，可以直接餵給 add_archetype() 用。

    這樣打者原型分桶就不用依賴 cpbl_batting_2020_2026（那張表只有 2020 年起的資料，
    對戰紀錄卻回溯到 2018 年，2018-2019 年的打者全部會對不到），
    改成直接用我們自己已經爬到的逐打席資料算，年份涵蓋範圍才會跟對戰紀錄一致；
    而且用 batter_acnt 分組，不會有同名球員對錯的問題（cpbl_batting_2020_2026 只能用姓名比對）。
    """
    rows = []
    for (acnt, name, year), g in matchup_df.groupby(["batter_acnt", "batter_name", "year"]):
        n = lambda result: (g["result"] == result).sum()
        bb, ibb, hbp, sf, sac = n("BB"), n("IBB"), n("HBP"), n("SF"), n("SAC")
        one_b, two_b, three_b, hr, so = n("1B"), n("2B"), n("3B"), n("HR"), n("SO")
        pa = len(g)
        ab = pa - bb - ibb - hbp - sf - sac
        h = one_b + two_b + three_b + hr
        bb_total = bb + ibb
        avg = h / ab if ab > 0 else None
        slg = (one_b + 2 * two_b + 3 * three_b + 4 * hr) / ab if ab > 0 else None
        rows.append({
            "batter_acnt": acnt, "batter_name": name, "年度": year,
            "pa": pa,
            "iso": (slg - avg) if (slg is not None and avg is not None) else None,
            "BB%": (bb_total / pa) if pa > 0 else None,
            "KK%": (so / pa) if pa > 0 else None,
        })
    return pd.DataFrame(rows)
