import streamlit as st
import pandas as pd
import plotly.express as px

from common import ROLE_LABEL, data_source_caption, load_scored

st.set_page_config(page_title="投手疲勞監控儀表板", page_icon="⚾", layout="wide")

st.title("⚾ 投手疲勞監控儀表板 — 球隊總覽")
data_source_caption("疲勞分數為賽季規劃參考指標，不是換投建議。")

with st.sidebar:
    st.header("篩選條件")
    baseline_games = st.slider("個人基準線場數", min_value=5, max_value=15, value=10)
    rolling_window = st.slider("滾動趨勢視窗(場)", min_value=3, max_value=10, value=5)

scored = load_scored(baseline_games, rolling_window)

years = sorted(scored["year"].unique())
with st.sidebar:
    season = st.selectbox("球季", years, index=len(years) - 1)
    role_filter = st.multiselect("角色", options=["SP", "RP"], default=["SP", "RP"],
                                  format_func=lambda r: ROLE_LABEL[r])

season_df = scored[(scored["year"] == season) & (scored["role"].isin(role_filter))]

# 每位投手取「最新一場」的疲勞分數當作目前排行依據
latest = (
    season_df.sort_values("game_date")
    .groupby("pitcher_uid", as_index=False)
    .last()
    .sort_values("fatigue_score", ascending=False)
)
latest["角色"] = latest["role"].map(ROLE_LABEL)
latest["最近一場日期"] = latest["game_date"]

st.subheader(f"{season} 賽季 — 全隊投手疲勞分數排行（依最新一場）")

if latest.empty:
    st.info("這個篩選條件下沒有資料。")
else:
    high_risk = latest[latest["fatigue_score"] >= 30]
    if len(high_risk):
        st.warning(f"⚠️ {len(high_risk)} 位投手最新一場疲勞分數 ≥ 30，建議優先關注：" +
                   "、".join(high_risk["pitcher"].tolist()))

    fig = px.bar(
        latest.head(30), x="pitcher", y="fatigue_score", color="角色",
        hover_data=["最近一場日期", "velocity_med", "rpm_med", "ops_against"],
        labels={"fatigue_score": "疲勞分數", "pitcher": "投手"},
        title="疲勞分數排行（前30名，依分數高到低）",
    )
    fig.update_layout(xaxis_tickangle=-45)
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        latest[["pitcher", "角色", "最近一場日期", "fatigue_score", "velocity_med", "rpm_med",
                "ops_against", "runs_allowed"]]
        .rename(columns={
            "pitcher": "投手", "fatigue_score": "疲勞分數", "velocity_med": "球速中位數",
            "rpm_med": "轉速中位數", "ops_against": "OPS-against", "runs_allowed": "失分",
        }),
        use_container_width=True, hide_index=True,
    )

st.divider()
st.caption("點選左側頁面選單，可查看單一投手深度分析、賽季負荷總覽。")
