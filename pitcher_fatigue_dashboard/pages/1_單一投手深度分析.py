import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from common import ROLE_LABEL, data_source_caption, load_scored

st.set_page_config(page_title="單一投手深度分析", page_icon="🔍", layout="wide")
st.title("🔍 單一投手深度分析")
data_source_caption()

with st.sidebar:
    baseline_games = st.slider("個人基準線場數", min_value=5, max_value=15, value=10)
    rolling_window = st.slider("滾動趨勢視窗(場)", min_value=3, max_value=10, value=5)

scored = load_scored(baseline_games, rolling_window)

with st.sidebar:
    years = sorted(scored["year"].unique())
    season = st.selectbox("球季", years, index=len(years) - 1)
    pitchers = sorted(scored[scored["year"] == season]["pitcher"].unique())
    pitcher = st.selectbox("投手", pitchers)

df = scored[(scored["year"] == season) & (scored["pitcher"] == pitcher)].sort_values("game_date")

if df.empty:
    st.info("這位投手在這個球季沒有資料。")
    st.stop()

st.caption(f"{pitcher}　{season}賽季　共 {len(df)} 場出賽　基準線取本季前{baseline_games}場中位數")

# 綜合疲勞分數走勢
fig_score = go.Figure()
fig_score.add_trace(go.Scatter(x=df["game_date"], y=df["fatigue_score"], mode="lines+markers",
                                name="疲勞分數", line=dict(color="crimson")))
fig_score.add_hline(y=30, line_dash="dash", line_color="orange", annotation_text="關注門檻(30)")
fig_score.update_layout(title="綜合疲勞分數走勢 (0-100)", yaxis_title="疲勞分數", height=350)
st.plotly_chart(fig_score, use_container_width=True)

col1, col2 = st.columns(2)

with col1:
    fig_v = make_subplots(specs=[[{"secondary_y": False}]])
    fig_v.add_trace(go.Scatter(x=df["game_date"], y=df["velocity_med"], mode="markers", name="單場球速中位數",
                                marker=dict(color="steelblue", size=6)))
    fig_v.add_trace(go.Scatter(x=df["game_date"], y=df["rolling_velocity"], mode="lines",
                                name=f"滾動{rolling_window}場中位數", line=dict(color="steelblue")))
    fig_v.add_hline(y=df["baseline_velocity"].iloc[0], line_dash="dot", line_color="gray",
                     annotation_text="個人基準線")
    fig_v.update_layout(title="球速趨勢 (km/h)", height=320)
    st.plotly_chart(fig_v, use_container_width=True)

with col2:
    fig_r = go.Figure()
    fig_r.add_trace(go.Scatter(x=df["game_date"], y=df["rpm_med"], mode="markers", name="單場轉速中位數",
                                marker=dict(color="seagreen", size=6)))
    fig_r.add_trace(go.Scatter(x=df["game_date"], y=df["rolling_rpm"], mode="lines",
                                name=f"滾動{rolling_window}場中位數", line=dict(color="seagreen")))
    fig_r.add_hline(y=df["baseline_rpm"].iloc[0], line_dash="dot", line_color="gray",
                     annotation_text="個人基準線")
    fig_r.update_layout(title="轉速趨勢 (rpm) — 涵蓋率不足的視窗已自動降權", height=320)
    st.plotly_chart(fig_r, use_container_width=True)

col3, col4 = st.columns(2)

with col3:
    fig_ops = go.Figure()
    fig_ops.add_trace(go.Scatter(x=df["game_date"], y=df["ops_against"], mode="markers", name="單場OPS-against",
                                  marker=dict(color="darkorange", size=6)))
    fig_ops.add_trace(go.Scatter(x=df["game_date"], y=df["rolling_ops_against"], mode="lines",
                                  name=f"滾動{rolling_window}場中位數", line=dict(color="darkorange")))
    fig_ops.add_hline(y=df["baseline_ops_against"].iloc[0], line_dash="dot", line_color="gray",
                       annotation_text="個人基準線")
    fig_ops.update_layout(title="OPS-against 趨勢", height=320)
    st.plotly_chart(fig_ops, use_container_width=True)

with col4:
    fig_ra = go.Figure()
    fig_ra.add_trace(go.Scatter(x=df["game_date"], y=df["runs_allowed"], mode="markers", name="單場失分(RA)",
                                 marker=dict(color="indianred", size=6)))
    fig_ra.add_trace(go.Scatter(x=df["game_date"], y=df["rolling_runs_allowed"], mode="lines",
                                 name=f"滾動{rolling_window}場中位數", line=dict(color="indianred")))
    fig_ra.add_hline(y=df["baseline_runs_allowed"].iloc[0], line_dash="dot", line_color="gray",
                      annotation_text="個人基準線")
    fig_ra.update_layout(title="失分率(RA)趨勢 — 未區分自責分/非自責分", height=320)
    st.plotly_chart(fig_ra, use_container_width=True)

st.subheader("逐場明細")
show_cols = ["game_date", "role", "velocity_med", "rpm_med", "ops_against", "runs_allowed", "fatigue_score"]
display_df = df[show_cols].rename(columns={
    "game_date": "日期", "role": "角色", "velocity_med": "球速中位數", "rpm_med": "轉速中位數",
    "ops_against": "OPS-against", "runs_allowed": "失分", "fatigue_score": "疲勞分數",
})
display_df["角色"] = display_df["角色"].map(ROLE_LABEL)
st.dataframe(display_df, use_container_width=True, hide_index=True)
