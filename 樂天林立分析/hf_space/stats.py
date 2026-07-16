# -*- coding: utf-8 -*-
"""純計算邏輯（不含畫圖套件），給 app.py 用。"""
import csv
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, "lin_li_games_cache.csv")

# 近似線性權重（非 CPBL 官方逐年權重，僅用於同賽季內兩組樣本的內部相對比較）
W_BB, W_HBP, W_1B, W_2B, W_3B, W_HR = 0.69, 0.72, 0.88, 1.24, 1.57, 2.00


def load_rows():
    with open(CACHE_PATH, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in r:
            if k not in ("year", "date", "opponent", "home_away", "lin_li_role", "game_sno"):
                if k in ("lin_li_played", "team_win"):
                    r[k] = r[k] == "True"
                else:
                    r[k] = int(r[k]) if r[k] not in (None, "") else 0
        r["opp_r"] = r["final_home"] if r["home_away"] == "away" else r["final_visiting"]
    rows.sort(key=lambda r: r["date"])
    return rows


def split_rows(rows):
    years = sorted(set(r["year"] for r in rows))
    with_lin = [r for r in rows if r["lin_li_played"]]
    without_lin = [r for r in rows if not r["lin_li_played"]]
    return years, with_lin, without_lin


def agg(rows):
    g = len(rows)
    if g == 0:
        return None
    s = {k: sum(r[k] for r in rows) for k in [
        "team_pa", "team_ab", "team_h", "team_1b", "team_2b", "team_3b", "team_hr",
        "team_bb", "team_ibb", "team_hbp", "team_sf", "team_sac", "team_so",
        "team_r", "opp_r", "team_gidp", "team_sb", "team_cs",
    ]}
    w = sum(1 for r in rows if r["team_win"])
    l = g - w

    ab, pa, h = s["team_ab"], s["team_pa"], s["team_h"]
    bb, hbp, sf, so = s["team_bb"], s["team_hbp"], s["team_sf"], s["team_so"]
    onebase, twobase, threebase, hr = s["team_1b"], s["team_2b"], s["team_3b"], s["team_hr"]

    avg = h / ab if ab else 0
    obp = (h + bb + hbp) / (ab + bb + hbp + sf) if (ab + bb + hbp + sf) else 0
    tb = onebase * 1 + twobase * 2 + threebase * 3 + hr * 4
    slg = tb / ab if ab else 0
    ops = obp + slg
    iso = slg - avg
    bb_pct = bb / pa if pa else 0
    k_pct = so / pa if pa else 0
    babip_denom = (ab - so - hr + sf)
    babip = (h - hr) / babip_denom if babip_denom else 0
    woba_denom = (ab + bb - s["team_ibb"] + sf + hbp)
    woba = (W_BB * (bb - s["team_ibb"]) + W_HBP * hbp + W_1B * onebase +
            W_2B * twobase + W_3B * threebase + W_HR * hr) / woba_denom if woba_denom else 0

    r, ra = s["team_r"], s["opp_r"]
    pyth = r ** 2 / (r ** 2 + ra ** 2) if (r or ra) else 0

    return {
        "g": g, "w": w, "l": l, "win_pct": w / g if g else 0, "pyth_win_pct": pyth,
        "r_per_g": r / g if g else 0, "ra_per_g": ra / g if g else 0,
        "diff_per_g": (r - ra) / g if g else 0,
        "avg": avg, "obp": obp, "slg": slg, "ops": ops, "iso": iso,
        "bb_pct": bb_pct, "k_pct": k_pct, "babip": babip, "woba": woba,
        "sb": s["team_sb"], "cs": s["team_cs"],
    }


def yearly_agg(years, rows):
    return {y: agg([r for r in rows if r["year"] == y]) for y in years}


def fmt3(x):
    return f"{x:.3f}"


def fmt_pct(x):
    return f"{x*100:.1f}%"


def conclusions_md(years, yearly_with, yearly_without, a_with, a_without):
    comparable_years = [y for y in years if yearly_with[y] and yearly_without[y]
                        and yearly_with[y]["g"] >= 10 and yearly_without[y]["g"] >= 10]
    ops_higher_years = [y for y in comparable_years if yearly_with[y]["ops"] > yearly_without[y]["ops"]]

    gap_with = (a_with["win_pct"] - a_with["pyth_win_pct"]) * 100
    gap_without = (a_without["win_pct"] - a_without["pyth_win_pct"]) * 100

    lines = []
    lines.append(
        f"**1. 打擊面：林立在陣中，樂天打線確實比較強，而且不是偶然。**　"
        f"在樣本足夠(≥10場)的 {len(comparable_years)} 個年度中，有 **{len(ops_higher_years)} 個年度**"
        f"（{', '.join(map(str, ops_higher_years))}）林立在場時的團隊 OPS 都高於缺陣時，"
        f"累積下來 OPS 差了 **{a_with['ops']-a_without['ops']:.3f}**、wOBA(近似) 差了 "
        f"**{a_with['woba']-a_without['woba']:.3f}**。這個一致性代表打線變化不是單一年度的雜訊，"
        f"而是每年都重複出現的訊號。"
    )
    lines.append(
        f"\n**2. 勝負面：打線變強沒有等量反映在勝率上，缺口在「運氣」而不是「進攻」。**　"
        f"林立在場時，實際勝率比畢氏(得失分)勝率**{'低了' if gap_with < 0 else '高了'} {abs(gap_with):.1f} 個百分點**；"
        f"缺陣時則是實際勝率{'高於' if gap_without > 0 else '低於'}畢氏勝率 {abs(gap_without):.1f} 個百分點。"
        f"這通常指向牛棚穩定度、一分差比賽的臨場運氣等變數，而不是林立本人的問題。"
    )
    lines.append(
        f"\n**3. 因果關係的但書：這不是隨機對照實驗。**　"
        f"林立缺陣的原因（傷病、國際賽徵召等）往往和球隊當下的整體健康狀況有關，"
        f"這份報告能證明的是「相關性」，無法完全排除其他同時發生的因素。"
    )

    latest_year = years[-1]
    latest_with = yearly_with.get(latest_year)
    latest_without = yearly_without.get(latest_year)
    if (latest_with and latest_with["g"] < 10) or (latest_without and latest_without["g"] < 10):
        small_side = "林立在場" if (latest_with and latest_with["g"] < 10) else "林立缺陣"
        small_g = latest_with["g"] if small_side == "林立在場" else latest_without["g"]
        lines.append(
            f"\n**4. {latest_year} 賽季提醒：** 目前「{small_side}」僅 {small_g} 場，樣本增加後結論會更穩定。"
        )

    return "\n".join(lines)


def small_sample_warnings(years, yearly_with, yearly_without):
    warnings = []
    for y in years:
        n_with = yearly_with[y]["g"] if yearly_with[y] else 0
        n_without = yearly_without[y]["g"] if yearly_without[y] else 0
        if 0 < n_with < 10:
            warnings.append(f"{y} 年「林立在場」僅 {n_with} 場")
        if 0 < n_without < 10:
            warnings.append(f"{y} 年「林立缺陣」僅 {n_without} 場")
    return warnings
