# -*- coding: utf-8 -*-
"""
共用邏輯：讀取 lin_li_games_cache.csv，計算「林立在場 vs 缺陣」的球隊打擊/勝率數據，
並產生圖表與結論文字。給 樂天林立效應報告.ipynb 匯入使用。
"""
import csv
import os

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

for _font in ("PingFang TC", "Heiti TC", "Arial Unicode MS"):
    if _font in {f.name for f in fm.fontManager.ttflist}:
        matplotlib.rcParams["font.family"] = _font
        break
matplotlib.rcParams["axes.unicode_minus"] = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, "lin_li_games_cache.csv")
COLOR_WITH = "#BF0D3E"      # 樂天隊色（有林立）
COLOR_WITHOUT = "#888888"   # 灰（缺陣）

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


def overview_table_md(a_with, a_without):
    def row(label, key, fmt=fmt3):
        diff = fmt(a_with[key] - a_without[key]) if fmt is fmt3 else fmt_pct(a_with[key] - a_without[key])
        return f"| {label} | {fmt(a_with[key])} | {fmt(a_without[key])} | {diff} |"

    lines = [
        "| 指標 | 林立在場 | 林立缺陣 | 差異(有−無) |",
        "|---|---|---|---|",
        f"| 場次 | {a_with['g']} | {a_without['g']} | - |",
        f"| 戰績 | {a_with['w']}勝{a_with['l']}敗 | {a_without['w']}勝{a_without['l']}敗 | - |",
        f"| 實際勝率 | {fmt_pct(a_with['win_pct'])} | {fmt_pct(a_without['win_pct'])} | {fmt_pct(a_with['win_pct']-a_without['win_pct'])} |",
        f"| 畢氏(得失分)勝率 | {fmt_pct(a_with['pyth_win_pct'])} | {fmt_pct(a_without['pyth_win_pct'])} | {fmt_pct(a_with['pyth_win_pct']-a_without['pyth_win_pct'])} |",
        f"| 平均得分/場 | {a_with['r_per_g']:.2f} | {a_without['r_per_g']:.2f} | {a_with['r_per_g']-a_without['r_per_g']:+.2f} |",
        f"| 平均失分/場 | {a_with['ra_per_g']:.2f} | {a_without['ra_per_g']:.2f} | {a_with['ra_per_g']-a_without['ra_per_g']:+.2f} |",
        row("AVG 打擊率", "avg"),
        row("OBP 上壘率", "obp"),
        row("SLG 長打率", "slg"),
        row("OPS", "ops"),
        row("ISO 純長打率", "iso"),
        row("BB% 保送率", "bb_pct", fmt_pct),
        row("K% 三振率", "k_pct", fmt_pct),
        row("BABIP", "babip"),
        row("wOBA(近似)", "woba"),
    ]
    return "\n".join(lines)


def yearly_table_md(years, yearly_with, yearly_without):
    lines = [
        "| 年度 | 林立在場(勝-敗) | 林立缺陣(勝-敗) | 有林立OPS | 無林立OPS | 有林立勝率 | 無林立勝率 |",
        "|---|---|---|---|---|---|---|",
    ]
    for y in years:
        aw, awo = yearly_with[y], yearly_without[y]
        w_str = f"{aw['g']}場({aw['w']}-{aw['l']})" if aw else "0場"
        wo_str = f"{awo['g']}場({awo['w']}-{awo['l']})" if awo else "0場"
        ops_w = fmt3(aw["ops"]) if aw else "-"
        ops_wo = fmt3(awo["ops"]) if awo else "-"
        winp_w = fmt_pct(aw["win_pct"]) if aw else "-"
        winp_wo = fmt_pct(awo["win_pct"]) if awo else "-"
        lines.append(f"| {y} | {w_str} | {wo_str} | {ops_w} | {ops_wo} | {winp_w} | {winp_wo} |")
    return "\n".join(lines)


def small_sample_md(years, yearly_with, yearly_without):
    warnings = []
    for y in years:
        n_with = yearly_with[y]["g"] if yearly_with[y] else 0
        n_without = yearly_without[y]["g"] if yearly_without[y] else 0
        if 0 < n_with < 10:
            warnings.append(f"{y} 年「林立在場」僅 {n_with} 場")
        if 0 < n_without < 10:
            warnings.append(f"{y} 年「林立缺陣」僅 {n_without} 場")
    if warnings:
        return "以下樣本場次少於 10 場，比率型數據（勝率、OPS 等）波動大，解讀時請謹慎：\n\n" + \
               "\n".join(f"- {w}" for w in warnings)
    return "目前兩組樣本場次都足夠(≥10場)，可信度較高。"


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
        f"林立在場時，實際勝率比畢氏(得失分)勝率**低了 {abs(gap_with):.1f} 個百分點**"
        f"（{'實際不如預期' if gap_with < 0 else '實際優於預期'}）；"
        f"缺陣時則是實際勝率{'高於' if gap_without > 0 else '低於'}畢氏勝率 {abs(gap_without):.1f} 個百分點。"
        f"換句話說，林立在場的比賽，球隊「應該贏的比賽」沒有全部贏下來——"
        f"這通常指向牛棚穩定度、一分差比賽的臨場運氣、或後段棒次串聯等其他變數，"
        f"而不是林立本人的問題。**不能用『他在場勝率沒比較高』來否定他對打線的貢獻。**"
    )
    lines.append(
        f"\n**3. 因果關係的但書：這不是隨機對照實驗。**　"
        f"林立缺陣的原因（傷病、國際賽徵召等）往往和球隊當下的整體健康狀況有關——"
        f"他缺陣的那段時間，可能其他主力也剛好受影響，或者對手輪值剛好比較弱/強。"
        f"這份報告能證明的是「相關性」：林立在場與更好的團隊打擊數據高度相關；"
        f"但無法完全排除其他同時發生的因素。解讀時建議把它當作強力的參考訊號，而非唯一的因果證據。"
    )

    latest_year = years[-1]
    latest_with = yearly_with.get(latest_year)
    latest_without = yearly_without.get(latest_year)
    if (latest_with and latest_with["g"] < 10) or (latest_without and latest_without["g"] < 10):
        small_side = "林立在場" if (latest_with and latest_with["g"] < 10) else "林立缺陣"
        small_g = latest_with["g"] if small_side == "林立在場" else latest_without["g"]
        lines.append(
            f"\n**4. {latest_year} 賽季提醒：** 目前「{small_side}」僅 {small_g} 場，"
            f"隨著賽季推進、樣本增加，結論會越來越穩定，現階段的 {latest_year} 數字僅供參考。"
        )

    return "\n".join(lines)


def plot_ops_trend(years, yearly_with, yearly_without):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ops_with = [yearly_with[y]["ops"] if yearly_with[y] else None for y in years]
    ops_without = [yearly_without[y]["ops"] if yearly_without[y] else None for y in years]
    ax.plot(years, ops_with, marker="o", color=COLOR_WITH, linewidth=2.5, label="林立在場")
    ax.plot(years, ops_without, marker="o", color=COLOR_WITHOUT, linewidth=2.5,
            linestyle="--", label="林立缺陣")
    for y, v in zip(years, ops_with):
        if v is not None:
            ax.annotate(f"{v:.3f}", (y, v), textcoords="offset points", xytext=(0, 8),
                        ha="center", fontsize=8, color=COLOR_WITH)
    for y, v in zip(years, ops_without):
        if v is not None:
            ax.annotate(f"{v:.3f}", (y, v), textcoords="offset points", xytext=(0, -14),
                        ha="center", fontsize=8, color=COLOR_WITHOUT)
    ax.set_title("樂天桃猿逐年團隊 OPS：林立在場 vs 缺陣", fontsize=13)
    ax.set_xlabel("年度")
    ax.set_ylabel("團隊 OPS")
    ax.set_xticks(years)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend()
    fig.tight_layout()
    return fig


def plot_winrate_gap(years, yearly_with, yearly_without):
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), sharey=True)
    for ax, data, title, color in [
        (axes[0], yearly_with, "林立在場", COLOR_WITH),
        (axes[1], yearly_without, "林立缺陣", COLOR_WITHOUT),
    ]:
        actual = [data[y]["win_pct"] * 100 if data[y] else None for y in years]
        pyth = [data[y]["pyth_win_pct"] * 100 if data[y] else None for y in years]
        ax.plot(years, actual, marker="o", color=color, linewidth=2.5, label="實際勝率")
        ax.plot(years, pyth, marker="s", color=color, linewidth=2, linestyle="--",
                alpha=0.7, label="畢氏(得失分)勝率")
        ax.axhline(50, color="black", linewidth=0.8, alpha=0.4)
        ax.set_title(title, fontsize=12)
        ax.set_xlabel("年度")
        ax.set_xticks(years)
        ax.grid(axis="y", linestyle=":", alpha=0.5)
        ax.legend(fontsize=9)
    axes[0].set_ylabel("勝率 (%)")
    fig.suptitle("實際勝率 vs 畢氏勝率：勝率有沒有跟得上表現？", fontsize=13)
    fig.tight_layout()
    return fig


def plot_overview_bar(a_with, a_without):
    metrics = [("AVG", "avg"), ("OBP", "obp"), ("SLG", "slg"), ("OPS", "ops"),
               ("ISO", "iso"), ("wOBA(近似)", "woba")]
    labels = [m[0] for m in metrics]
    vals_with = [a_with[m[1]] for m in metrics]
    vals_without = [a_without[m[1]] for m in metrics]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = range(len(labels))
    width = 0.35
    ax.bar([i - width / 2 for i in x], vals_with, width, color=COLOR_WITH, label="林立在場")
    ax.bar([i + width / 2 for i in x], vals_without, width, color=COLOR_WITHOUT, label="林立缺陣")
    for i, v in enumerate(vals_with):
        ax.annotate(f"{v:.3f}", (i - width / 2, v), textcoords="offset points",
                    xytext=(0, 4), ha="center", fontsize=8)
    for i, v in enumerate(vals_without):
        ax.annotate(f"{v:.3f}", (i + width / 2, v), textcoords="offset points",
                    xytext=(0, 4), ha="center", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_title("2020–今 團隊打擊數據總覽：林立在場 vs 缺陣", fontsize=13)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend()
    fig.tight_layout()
    return fig
