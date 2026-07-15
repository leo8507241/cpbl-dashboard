import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px

from common import ROLE_LABEL, data_source_caption, load_game_level

st.set_page_config(page_title="賽季負荷總覽", page_icon="📊", layout="wide")
st.title("📊 賽季負荷總覽")
data_source_caption("用球數/出賽間隔為球團輪值調度參考，非即時比賽資料。")

game_df = load_game_level()

with st.sidebar:
    years = sorted(game_df["year"].unique())
    season = st.selectbox("球季", years, index=len(years) - 1)
    role_filter = st.multiselect("角色", options=["SP", "RP"], default=["SP", "RP"],
                                  format_func=lambda r: ROLE_LABEL[r])

season_df = game_df[(game_df["year"] == season) & (game_df["role"].isin(role_filter))].sort_values(
    ["pitcher_uid", "game_date"]
)

if season_df.empty:
    st.info("這個篩選條件下沒有資料。")
    st.stop()

# 累積用球數
cum_pitch = season_df.groupby("pitcher_uid", as_index=False).agg(
    pitcher=("pitcher", "first"), role=("role", "first"),
    total_pitches=("pitch_count", "sum"), games=("game_date", "nunique"),
)
cum_pitch["角色"] = cum_pitch["role"].map(ROLE_LABEL)
cum_pitch = cum_pitch.sort_values("total_pitches", ascending=False)

st.subheader(f"{season} 賽季 — 累積用球數排行")
fig_cum = px.bar(cum_pitch.head(30), x="pitcher", y="total_pitches", color="角色",
                  labels={"total_pitches": "累積用球數", "pitcher": "投手"})
fig_cum.update_layout(xaxis_tickangle=-45)
st.plotly_chart(fig_cum, use_container_width=True)

st.divider()

# 出賽間隔(休息天數)
season_df["game_date_dt"] = pd.to_datetime(season_df["game_date"])
season_df["prev_date"] = season_df.groupby("pitcher_uid")["game_date_dt"].shift(1)
season_df["rest_days"] = (season_df["game_date_dt"] - season_df["prev_date"]).dt.days

st.subheader("出賽間隔分布（休息天數）")
rest_summary = (
    season_df.dropna(subset=["rest_days"])
    .groupby("pitcher_uid", as_index=False)
    .agg(pitcher=("pitcher", "first"), role=("role", "first"),
         median_rest=("rest_days", "median"), min_rest=("rest_days", "min"), n=("rest_days", "count"))
)
rest_summary["角色"] = rest_summary["role"].map(ROLE_LABEL)
rest_summary = rest_summary.sort_values("median_rest")

short_rest = rest_summary[rest_summary["min_rest"] <= 1]
if len(short_rest):
    st.warning("⚠️ 曾經只休息 0-1 天就再度出賽的投手：" + "、".join(short_rest["pitcher"].tolist()))

fig_rest = px.bar(rest_summary, x="pitcher", y="median_rest", color="角色",
                   labels={"median_rest": "中位數休息天數", "pitcher": "投手"},
                   title="各投手中位數休息天數（越低代表用得越勤）")
fig_rest.update_layout(xaxis_tickangle=-45)
st.plotly_chart(fig_rest, use_container_width=True)

st.divider()

st.subheader("單一投手負荷明細")
pitcher_sel = st.selectbox("選擇投手", sorted(season_df["pitcher"].unique()))
detail = season_df[season_df["pitcher"] == pitcher_sel][
    ["game_date", "role", "pitch_count", "rest_days", "batters_faced", "max_inning"]
].rename(columns={
    "game_date": "日期", "role": "角色", "pitch_count": "用球數",
    "rest_days": "距上一場天數", "batters_faced": "面對打者數", "max_inning": "最高局數",
})
detail["角色"] = detail["角色"].map(ROLE_LABEL)
st.dataframe(detail, use_container_width=True, hide_index=True)
