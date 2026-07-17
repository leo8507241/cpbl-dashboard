"""三個頁面共用的資料載入/快取邏輯。只做載入+呼叫特徵工程層，不在這裡算統計。"""
import os
import sys

import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaning import clean_pitch_tracking
from feature_engineering import FatigueScoreConfig, aggregate_to_game_level, compute_fatigue_scores
import intra_game_fatigue as igf

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT_DIR, "pitcher_pitches.csv")
THRESHOLD_PATH = os.path.join(ROOT_DIR, "inning_fatigue_thresholds.csv")
REMOVAL_CURVE_PATH = os.path.join(ROOT_DIR, "inning_removal_curve.csv")
SCORE_QUARTILE_PATH = os.path.join(ROOT_DIR, "inning_score_quartiles.csv")

INTRA_GAME_METRICS = ["csw_all", "csw_fb", "csw_br", "ops_against", "iso_against",
                       "whip", "k_pct", "bb_pct", "hbp_pct", "fip_dedup", "fip_overlap",
                       "fb_traj_pct", "deep_fly_pct"]

ROLE_LABEL = {"SP": "先發", "RP": "中繼"}


# ── Supabase(單場即時換投監控頁面的資料來源，其餘頁面仍讀本地CSV/即時計算) ──────
def _get_secret(key, default):
    """比照 app.py 的模式：優先讀 st.secrets(HF Space)，本地開發 fallback 到環境變數/寫死值。"""
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
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _fetch_all_from_supabase(table: str, order_col: str | None = None) -> pd.DataFrame:
    """PostgREST單次最多回1000列，用range()分頁抓到底。查失敗直接raise，讓呼叫端決定要不要fallback。"""
    sb = _supabase_client()
    page = 1000
    start = 0
    rows = []
    while True:
        q = sb.table(table).select("*").range(start, start + page - 1)
        if order_col:
            q = q.order(order_col)
        res = q.execute()
        data = res.data
        rows.extend(data)
        if len(data) < page:
            break
        start += page
    df = pd.DataFrame(rows)
    return df.drop(columns=["id"], errors="ignore")


@st.cache_data
def load_pitch_data() -> pd.DataFrame:
    df = pd.read_csv(DATA_PATH, low_memory=False)
    if "velocity_clean" not in df.columns:
        df = clean_pitch_tracking(df)
    return df


@st.cache_data
def load_game_level() -> pd.DataFrame:
    pitch_df = load_pitch_data()
    return aggregate_to_game_level(pitch_df)


@st.cache_data
def load_scored(baseline_games: int, rolling_window: int) -> pd.DataFrame:
    game_df = load_game_level()
    cfg = FatigueScoreConfig(baseline_games=baseline_games, rolling_window=rolling_window)
    return compute_fatigue_scores(game_df, cfg)


def _compute_intra_game_checkpoints_locally() -> pd.DataFrame:
    """live-compute版本，全資料集算一次要約20-30秒。只在Supabase讀取失敗時當fallback用。"""
    pitch_df = load_pitch_data()
    starters = igf.infer_starters(pitch_df)
    starter_pitch_df = pitch_df.merge(starters, on=["pitcher_uid", "game_date"], how="inner")

    checkpoints = igf.compute_inning_checkpoints(starter_pitch_df)
    pt_checkpoints = igf.compute_pitch_type_checkpoints(starter_pitch_df)

    baseline = igf.build_season_baseline(checkpoints, INTRA_GAME_METRICS, baseline_games=igf.BASELINE_GAMES)
    pt_baseline = igf.build_pitch_type_baseline(pt_checkpoints, baseline_games=igf.BASELINE_GAMES)

    dev = igf.compute_deviations(checkpoints, baseline, INTRA_GAME_METRICS, join_keys=["pitcher_uid", "year"])
    v_dev = igf.compute_pitch_weighted_deviation(pt_checkpoints, pt_baseline)
    dev = dev.merge(v_dev, on=["pitcher_uid", "game_date", "inning"], how="left")
    dev["velocity_deviation"] = dev["velocity_deviation"].fillna(0)
    dev["rpm_deviation"] = dev["rpm_deviation"].fillna(0)

    dev["score_with_overlap"] = igf.compute_change_score(dev, igf.WEIGHTS_WITH_OVERLAP)
    dev["score_dedup"] = igf.compute_change_score(dev, igf.WEIGHTS_DEDUPLICATED)
    dev["baseline_game_rank"] = igf.compute_game_rank(dev)
    return dev


@st.cache_data
def load_intra_game_checkpoints() -> pd.DataFrame:
    """單場逐局即時換投分數。優先讀 Supabase(cpbl_intra_game_checkpoints，由
    sync_intra_game_to_supabase.py 每日同步)，查詢失敗才退回本地即時計算(約20-30秒)。"""
    try:
        df = _fetch_all_from_supabase("cpbl_intra_game_checkpoints")
        if not df.empty:
            return df
    except Exception as e:
        st.warning(f"Supabase讀取cpbl_intra_game_checkpoints失敗，改用本地即時計算：{e}")
    return _compute_intra_game_checkpoints_locally()


@st.cache_data
def load_pitch_type_detail() -> pd.DataFrame:
    """逐球種、逐局的球速/轉速現在值 vs 基準值，給單場頁面顯示透明度用。
    優先讀 Supabase(cpbl_intra_game_pitch_type_detail)，失敗才退回本地即時計算。"""
    try:
        df = _fetch_all_from_supabase("cpbl_intra_game_pitch_type_detail")
        if not df.empty:
            return df
    except Exception as e:
        st.warning(f"Supabase讀取cpbl_intra_game_pitch_type_detail失敗，改用本地即時計算：{e}")
    pitch_df = load_pitch_data()
    starters = igf.infer_starters(pitch_df)
    starter_pitch_df = pitch_df.merge(starters, on=["pitcher_uid", "game_date"], how="inner")
    pt_checkpoints = igf.compute_pitch_type_checkpoints(starter_pitch_df)
    pt_baseline = igf.build_pitch_type_baseline(pt_checkpoints, baseline_games=10)
    return pt_checkpoints.merge(pt_baseline, on=["pitcher_uid", "year", "pitch_type"], how="left")


@st.cache_data
def load_inning_thresholds() -> pd.DataFrame:
    """每局的樣本數/整體換投基準率/鑑別力係數(換投分數 vs 真實換投決策的相關係數)。
    係數越低代表分數在這一局對「會不會換投」的解釋力越弱，色帶只能當參考。
    優先讀Supabase(cpbl_inning_fatigue_thresholds)，失敗才退回本地CSV。"""
    try:
        df = _fetch_all_from_supabase("cpbl_inning_fatigue_thresholds")
        if not df.empty:
            return df.rename(columns=THRESHOLD_RENAME_FROM_DB)
    except Exception as e:
        st.warning(f"Supabase讀取cpbl_inning_fatigue_thresholds失敗，改用本地CSV：{e}")
    return pd.read_csv(THRESHOLD_PATH)


@st.cache_data
def load_inning_removal_curve() -> pd.DataFrame:
    """每局「換投分數 -> 真實換投機率」的連續曲線(isotonic regression，只保證單調不下降)。
    用來畫色帶漸層，也用來把分數換算成機率、決定文字狀態，避免分數軸跟機率軸兜不起來。
    優先讀Supabase(cpbl_inning_removal_curve)，失敗才退回本地CSV。"""
    try:
        df = _fetch_all_from_supabase("cpbl_inning_removal_curve")
        if not df.empty:
            return df
    except Exception as e:
        st.warning(f"Supabase讀取cpbl_inning_removal_curve失敗，改用本地CSV：{e}")
    return pd.read_csv(REMOVAL_CURVE_PATH)


@st.cache_data
def load_inning_score_quartiles() -> pd.DataFrame:
    """每局粗顆粒度版：分數切4等分(Q1最低~Q4最高)，各自的分數範圍+真實換投機率。
    給不想看連續曲線、只想抓「大致分幾層、每層機率差多少」的人看。
    優先讀Supabase(cpbl_inning_score_quartiles)，失敗才退回本地CSV。"""
    try:
        df = _fetch_all_from_supabase("cpbl_inning_score_quartiles")
        if not df.empty:
            return df
    except Exception as e:
        st.warning(f"Supabase讀取cpbl_inning_score_quartiles失敗，改用本地CSV：{e}")
    return pd.read_csv(SCORE_QUARTILE_PATH)


def removal_prob_for(curve_df: pd.DataFrame, inning: int, score: float) -> float | None:
    """在指定局數的曲線上，用內插查出這個分數對應的真實換投機率。查無資料回傳None。"""
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
