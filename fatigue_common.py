"""
「單場即時換投監控」頁面專用的資料載入層(獨立部署到 leo88888/cpbl-dashboard 這個Space用)。

跟本機開發版(pitcher_fatigue_dashboard/common.py)的差異：這裡只讀 Supabase，
不含本地CSV/即時計算的fallback——因為部署到HF Space上不會帶著211K列的
pitcher_pitches.csv去現場重算一次checkpoint(改由 sync_intra_game_to_supabase.py
每日離線算好、寫進Supabase，Space只負責查詢+畫圖，開頁面速度更快)。

資料是誰寫進來的、多久更新一次：見repo根目錄的 sync_intra_game_to_supabase.py，
由 .github/workflows/daily_update.yml 每日自動執行。
"""
import os

import numpy as np
import pandas as pd
import streamlit as st
from supabase import create_client

SAMPLE_RAMP_FULL_AT = 12
PA_BASED_METRICS = {"ops_against", "iso_against", "fip_overlap", "fb_traj_pct", "deep_fly_pct"}
WEIGHTS_WITH_OVERLAP = {
    "velocity": 0.20, "rpm": 0.10, "csw_all": 0.15,
    "ops_against": 0.15, "iso_against": 0.10, "fip_overlap": 0.15,
    "fb_traj_pct": 0.075, "deep_fly_pct": 0.075,
}


def sample_weight_multiplier(n_pa) -> float:
    """打席數 vs 權重乘數：0打席=0，滿SAMPLE_RAMP_FULL_AT(12)打席=1，中間線性內插。"""
    return float(np.clip(n_pa / SAMPLE_RAMP_FULL_AT, 0.0, 1.0))


def _get_secret(key, default):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


SUPABASE_URL = _get_secret("SUPABASE_URL", "https://vxgtgqlqukexpvnnvslf.supabase.co")
SUPABASE_KEY = _get_secret("SUPABASE_KEY", "sb_publishable_9DDMVwHFIMCdBxN12jaWkQ_gMeJkRuI")

THRESHOLD_RENAME_FROM_DB = {"sample_size": "樣本數", "baseline_removal_rate": "該局整體換投基準率",
                            "discriminant_coef": "鑑別力係數"}


@st.cache_resource
def _supabase_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _fetch_all(table: str) -> pd.DataFrame:
    sb = _supabase_client()
    page, start, rows = 1000, 0, []
    while True:
        res = sb.table(table).select("*").range(start, start + page - 1).execute()
        data = res.data
        rows.extend(data)
        if len(data) < page:
            break
        start += page
    df = pd.DataFrame(rows)
    return df.drop(columns=["id"], errors="ignore")


@st.cache_data(ttl=3600)
def load_intra_game_checkpoints() -> pd.DataFrame:
    return _fetch_all("cpbl_intra_game_checkpoints")


@st.cache_data(ttl=3600)
def load_pitch_type_detail() -> pd.DataFrame:
    return _fetch_all("cpbl_intra_game_pitch_type_detail")


@st.cache_data(ttl=3600)
def load_inning_thresholds() -> pd.DataFrame:
    return _fetch_all("cpbl_inning_fatigue_thresholds").rename(columns=THRESHOLD_RENAME_FROM_DB)


@st.cache_data(ttl=3600)
def load_inning_removal_curve() -> pd.DataFrame:
    return _fetch_all("cpbl_inning_removal_curve")


@st.cache_data(ttl=3600)
def load_inning_score_quartiles() -> pd.DataFrame:
    return _fetch_all("cpbl_inning_score_quartiles")


def removal_prob_for(curve_df: pd.DataFrame, inning: int, score: float) -> float | None:
    sub = curve_df[curve_df["inning"] == inning]
    if sub.empty:
        return None
    return float(np.interp(score, sub["score"], sub["removal_prob"]))


def zone_for_prob(prob: float | None) -> str:
    if prob is None:
        return "無資料"
    if prob >= 0.6:
        return "一定要換"
    if prob >= 0.3:
        return "可換可不換"
    return "不用換"


def data_source_caption(extra: str = "") -> None:
    st.caption(
        f"資料來源：CPBL官方逐球紀錄 + rebas.tw野球革命追蹤數據，僅供賽季規劃參考，非即時比賽資料。{extra}"
    )
