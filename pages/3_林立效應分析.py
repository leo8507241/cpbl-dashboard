import json
import os
from datetime import datetime

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import stats as st_lib

# 此檔放在 pages/，ROOT 是上一層（HF Space 根目錄）
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAST_UPDATE_PATH = os.path.join(ROOT_DIR, "last_update.json")

COLOR_WITH = "#BF0D3E"
COLOR_WITHOUT = "#888888"

st.set_page_config(page_title="樂天桃猿：林立效應儀表板", layout="wide")
st.title("⚾ 樂天桃猿「林立在場 vs 缺陣」逐場效應儀表板")


@st.cache_data(ttl=3600)
def load_data():
    rows = st_lib.load_rows()
    years, with_lin, without_lin = st_lib.split_rows(rows)
    return rows, years, with_lin, without_lin


rows, all_years, with_lin_all, without_lin_all = load_data()

# ── 今日更新 banner ──────────────────────────────────────────
if os.path.exists(LAST_UPDATE_PATH):
    with open(LAST_UPDATE_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    run_at = meta.get("run_at", "")
    new_games = meta.get("new_games", [])
    if new_games:
        msgs = []
        for g in new_games:
            side = "林立有上場" if g["lin_li_played"] else "林立無上場"
            result = "勝" if g["team_win"] else "負"
            msgs.append(f"**{g['date']}** vs {g['opponent']}（{side}，{result}）")
        st.success(f"📅 最新一次更新（{run_at}）新增了 {len(new_games)} 場比賽：" + "；".join(msgs))
    else:
        st.info(f"📅 最新一次更新：{run_at}（沒有新完賽的比賽）")
else:
    st.warning("尚未執行過每日更新任務。")

st.caption(
    f"資料範圍：{all_years[0]}–{all_years[-1]} 一軍例行賽（不含季後賽/熱身賽/明星賽）　"
    f"總場次：{len(rows)}　"
    "「林立在場」定義：該場 boxscore 打者名單出現林立（含先發/代打/代跑）。"
)

# ── 側邊欄：年度篩選（互動）──────────────────────────────────
st.sidebar.header("篩選條件")
selected_years = st.sidebar.multiselect("選擇年度", all_years, default=all_years)

if not selected_years:
    st.warning("請至少選擇一個年度。")
    st.stop()

with_lin = [r for r in with_lin_all if r["year"] in selected_years]
without_lin = [r for r in without_lin_all if r["year"] in selected_years]
years = [y for y in all_years if y in selected_years]

a_with = st_lib.agg(with_lin)
a_without = st_lib.agg(without_lin)
yearly_with = st_lib.yearly_agg(years, with_lin)
yearly_without = st_lib.yearly_agg(years, without_lin)

if a_with is None or a_without is None:
    st.warning("所選年度中，「林立在場」或「林立缺陣」其中一組沒有比賽資料，請調整篩選。")
    st.stop()

# ── 總覽指標卡片 ──────────────────────────────────────────────
st.subheader("① 總覽：林立在場 vs 缺陣")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("場次", f"{a_with['g']} vs {a_without['g']}")
c2.metric("實際勝率", st_lib.fmt_pct(a_with["win_pct"]), f"{st_lib.fmt_pct(a_with['win_pct']-a_without['win_pct'])} vs 缺陣")
c3.metric("畢氏勝率", st_lib.fmt_pct(a_with["pyth_win_pct"]), f"{st_lib.fmt_pct(a_with['pyth_win_pct']-a_without['pyth_win_pct'])} vs 缺陣")
c4.metric("OPS", st_lib.fmt3(a_with["ops"]), f"{a_with['ops']-a_without['ops']:+.3f} vs 缺陣")
c5.metric("wOBA(近似)", st_lib.fmt3(a_with["woba"]), f"{a_with['woba']-a_without['woba']:+.3f} vs 缺陣")

overview_rows = [
    ("戰績", f"{a_with['w']}勝{a_with['l']}敗", f"{a_without['w']}勝{a_without['l']}敗"),
    ("平均得分/場", f"{a_with['r_per_g']:.2f}", f"{a_without['r_per_g']:.2f}"),
    ("平均失分/場", f"{a_with['ra_per_g']:.2f}", f"{a_without['ra_per_g']:.2f}"),
    ("AVG 打擊率", st_lib.fmt3(a_with["avg"]), st_lib.fmt3(a_without["avg"])),
    ("OBP 上壘率", st_lib.fmt3(a_with["obp"]), st_lib.fmt3(a_without["obp"])),
    ("SLG 長打率", st_lib.fmt3(a_with["slg"]), st_lib.fmt3(a_without["slg"])),
    ("ISO 純長打率", st_lib.fmt3(a_with["iso"]), st_lib.fmt3(a_without["iso"])),
    ("BB% 保送率", st_lib.fmt_pct(a_with["bb_pct"]), st_lib.fmt_pct(a_without["bb_pct"])),
    ("K% 三振率", st_lib.fmt_pct(a_with["k_pct"]), st_lib.fmt_pct(a_without["k_pct"])),
    ("BABIP", st_lib.fmt3(a_with["babip"]), st_lib.fmt3(a_without["babip"])),
]
st.dataframe(
    {"指標": [r[0] for r in overview_rows], "林立在場": [r[1] for r in overview_rows], "林立缺陣": [r[2] for r in overview_rows]},
    hide_index=True, use_container_width=True,
)

# ── 圖一：總覽長條圖（互動）──────────────────────────────────
metrics = [("AVG", "avg"), ("OBP", "obp"), ("SLG", "slg"), ("OPS", "ops"), ("ISO", "iso"), ("wOBA(近似)", "woba")]
fig_bar = go.Figure()
fig_bar.add_bar(name="林立在場", x=[m[0] for m in metrics], y=[a_with[m[1]] for m in metrics], marker_color=COLOR_WITH)
fig_bar.add_bar(name="林立缺陣", x=[m[0] for m in metrics], y=[a_without[m[1]] for m in metrics], marker_color=COLOR_WITHOUT)
fig_bar.update_layout(title="團隊打擊數據總覽", barmode="group", height=450)
st.plotly_chart(fig_bar, use_container_width=True)

# ── 逐年拆解 ──────────────────────────────────────────────────
st.subheader("② 逐年拆解")
table = {
    "年度": years,
    "林立在場(勝-敗)": [f"{yearly_with[y]['g']}場({yearly_with[y]['w']}-{yearly_with[y]['l']})" if yearly_with[y] else "0場" for y in years],
    "林立缺陣(勝-敗)": [f"{yearly_without[y]['g']}場({yearly_without[y]['w']}-{yearly_without[y]['l']})" if yearly_without[y] else "0場" for y in years],
    "有林立OPS": [st_lib.fmt3(yearly_with[y]["ops"]) if yearly_with[y] else "-" for y in years],
    "無林立OPS": [st_lib.fmt3(yearly_without[y]["ops"]) if yearly_without[y] else "-" for y in years],
    "有林立勝率": [st_lib.fmt_pct(yearly_with[y]["win_pct"]) if yearly_with[y] else "-" for y in years],
    "無林立勝率": [st_lib.fmt_pct(yearly_without[y]["win_pct"]) if yearly_without[y] else "-" for y in years],
}
st.dataframe(table, hide_index=True, use_container_width=True)

# ── 圖二：逐年 OPS 趨勢（互動折線圖，滑鼠可查看數值）──────────
fig_ops = go.Figure()
fig_ops.add_scatter(x=years, y=[yearly_with[y]["ops"] if yearly_with[y] else None for y in years],
                     mode="lines+markers", name="林立在場", line=dict(color=COLOR_WITH, width=3))
fig_ops.add_scatter(x=years, y=[yearly_without[y]["ops"] if yearly_without[y] else None for y in years],
                     mode="lines+markers", name="林立缺陣", line=dict(color=COLOR_WITHOUT, width=3, dash="dash"))
fig_ops.update_layout(title="逐年團隊 OPS 趨勢", xaxis_title="年度", yaxis_title="OPS", height=450)
st.plotly_chart(fig_ops, use_container_width=True)

# ── 圖三：實際勝率 vs 畢氏勝率（互動子圖）────────────────────
fig_win = make_subplots(rows=1, cols=2, subplot_titles=("林立在場", "林立缺陣"), shared_yaxes=True)
for col, data, color in [(1, yearly_with, COLOR_WITH), (2, yearly_without, COLOR_WITHOUT)]:
    actual = [data[y]["win_pct"] * 100 if data[y] else None for y in years]
    pyth = [data[y]["pyth_win_pct"] * 100 if data[y] else None for y in years]
    fig_win.add_scatter(x=years, y=actual, mode="lines+markers", name="實際勝率",
                        line=dict(color=color, width=3), legendgroup=f"g{col}",
                        showlegend=(col == 1), row=1, col=col)
    fig_win.add_scatter(x=years, y=pyth, mode="lines+markers", name="畢氏勝率",
                        line=dict(color=color, width=2, dash="dash"), legendgroup=f"g{col}",
                        showlegend=(col == 1), row=1, col=col)
fig_win.update_layout(title="實際勝率 vs 畢氏(得失分)勝率", height=450)
st.plotly_chart(fig_win, use_container_width=True)

# ── 分析結論 ──────────────────────────────────────────────────
st.subheader("📊 分析結論")
st.markdown(st_lib.conclusions_md(years, yearly_with, yearly_without, a_with, a_without))

# ── 小樣本提醒 ────────────────────────────────────────────────
warnings = st_lib.small_sample_warnings(years, yearly_with, yearly_without)
st.subheader("小樣本提醒")
if warnings:
    st.warning("以下樣本場次少於 10 場，比率型數據波動大，解讀時請謹慎：\n\n" + "\n".join(f"- {w}" for w in warnings))
else:
    st.success("目前兩組樣本場次都足夠(≥10場)，可信度較高。")
