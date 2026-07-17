"""
把「單場即時換投監控」用到的所有資料同步進 Supabase：
  1. cpbl_pitcher_pitches            - 原始逐球資料(pitcher_pitches.csv全量)
  2. cpbl_intra_game_checkpoints     - 逐局換投分數明細
  3. cpbl_intra_game_pitch_type_detail - 逐球種明細
  4. cpbl_inning_fatigue_thresholds  - 每局校準摘要(樣本數/基準率/鑑別力係數)
  5. cpbl_inning_removal_curve       - 每局「分數->真實換投機率」連續曲線
  6. cpbl_inning_score_quartiles     - 每局Q1-Q4分層對照表

前置條件：先在 Supabase SQL Editor 執行一次 supabase_schema.sql 建表。

用法：
  python sync_intra_game_to_supabase.py            # 全量同步(第一次跑，或人工重建時用)
  python sync_intra_game_to_supabase.py --incremental  # 只同步當年度的逐球資料+全部衍生表
                                                        （衍生表本來就要全量重算，量不大）

環境變數：SUPABASE_URL / SUPABASE_KEY（比照 daily_update.py 的模式，本地未設定時
fallback 到寫死的 publishable key；GitHub Actions 裡由 secrets 注入）。
"""
import math
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cleaning import clean_pitch_tracking
import intra_game_fatigue as igf
from calibrate_thresholds import compute_calibration

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://vxgtgqlqukexpvnnvslf.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_9DDMVwHFIMCdBxN12jaWkQ_gMeJkRuI")

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pitcher_pitches.csv")
BATCH_SIZE = 200
MAX_RETRIES = 4


def _with_retry(fn, desc: str):
    """寬欄位表(如cpbl_pitcher_pitches有37欄)偶爾會頂到Supabase的statement timeout，
    不是邏輯錯誤，重試通常就過了。指數backoff：2s、4s、8s、16s。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            print(f"    ⚠️  {desc} 失敗(第{attempt}次)，{wait}秒後重試：{e}")
            time.sleep(wait)

METRICS_ALL = ["csw_all", "csw_fb", "csw_br", "ops_against", "iso_against",
               "whip", "k_pct", "bb_pct", "hbp_pct", "fip_dedup", "fip_overlap",
               "fb_traj_pct", "deep_fly_pct"]

THRESHOLD_RENAME = {"樣本數": "sample_size", "該局整體換投基準率": "baseline_removal_rate",
                     "鑑別力係數": "discriminant_coef"}


def _clean_records(df: pd.DataFrame) -> list[dict]:
    records = []
    for row in df.to_dict(orient="records"):
        clean = {}
        for k, v in row.items():
            if hasattr(v, "item"):  # numpy scalar -> python native
                v = v.item()
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                clean[k] = None
            elif v is None or (not isinstance(v, (list, dict)) and pd.isna(v)):
                clean[k] = None
            else:
                clean[k] = v
        records.append(clean)
    return records


def _replace_table(sb, table: str, df: pd.DataFrame, delete_filter: dict | None = None):
    """刪掉符合條件的舊資料(沒給filter就整張表清空)，再分批插入新資料。每個request都包了重試，
    寬欄位表(如cpbl_pitcher_pitches)偶爾會頂到Supabase的statement timeout，重試通常就過。"""
    def _delete():
        q = sb.table(table).delete()
        if delete_filter:
            for col, val in delete_filter.items():
                q = q.eq(col, val)
        else:
            # PostgREST的delete要求至少一個filter。5張衍生表都保證有"inning"欄位(cpbl_pitcher_pitches
            # 不會走這個分支，一定帶delete_filter)，用它當全部列永真的條件，不能沿用id(thresholds表沒有id欄位)。
            q = q.gte("inning", -1)
        return q.execute()

    _with_retry(_delete, f"{table} 清空舊資料")

    records = _clean_records(df)
    n_batches = math.ceil(len(records) / BATCH_SIZE)
    for batch_idx, i in enumerate(range(0, len(records), BATCH_SIZE), start=1):
        batch = records[i:i + BATCH_SIZE]
        _with_retry(lambda batch=batch: sb.table(table).insert(batch).execute(),
                    f"{table} 第{i}-{i + len(batch)}筆")
        if batch_idx % 20 == 0 or batch_idx == n_batches:
            print(f"    ...{table} 進度 {batch_idx}/{n_batches} 批")
    print(f"  ✅ {table}：{len(records)} 筆")


def sync_pitcher_pitches(sb, df: pd.DataFrame, incremental: bool):
    cols = [c for c in df.columns if c != "id"]
    years = sorted(df["year"].unique())
    if incremental:
        years = [max(years)]  # 只重算/重傳當年度，往年資料不會變動
    for year in years:
        _replace_table(sb, "cpbl_pitcher_pitches", df.loc[df["year"] == year, cols], delete_filter={"year": int(year)})


def sync_derived_tables(sb, dev: pd.DataFrame, pt_detail: pd.DataFrame,
                         summary_table: pd.DataFrame, curve_table: pd.DataFrame, quartile_table: pd.DataFrame):
    checkpoint_cols = [c for c in dev.columns if c not in ("has_next_inning", "removed_after")]
    _replace_table(sb, "cpbl_intra_game_checkpoints", dev[checkpoint_cols])
    _replace_table(sb, "cpbl_intra_game_pitch_type_detail", pt_detail)

    summary_table = summary_table.rename(columns=THRESHOLD_RENAME)
    _replace_table(sb, "cpbl_inning_fatigue_thresholds", summary_table)
    _replace_table(sb, "cpbl_inning_removal_curve", curve_table)
    _replace_table(sb, "cpbl_inning_score_quartiles", quartile_table)


def build_intra_game_pipeline(df: pd.DataFrame):
    """整套 pipeline：篩先發 -> 逐局checkpoint -> 逐球種checkpoint -> 基準線 -> 偏離 -> 分數。
    跟 intra_game_fatigue.py 的 __main__ 邏輯一致，這裡抽出來給 sync 腳本重用。"""
    print("篩選只保留先發出賽...")
    starters = igf.infer_starters(df)
    sdf = df.merge(starters, on=["pitcher_uid", "game_date"], how="inner")
    print(f"先發出賽場次: {len(starters)}")

    print("計算逐局累計指標(除了球速轉速)...")
    checkpoints = igf.compute_inning_checkpoints(sdf)
    print("計算逐球種、逐局累計球速/轉速...")
    pt_checkpoints = igf.compute_pitch_type_checkpoints(sdf)

    print("建立個人球季基準線...")
    baseline = igf.build_season_baseline(checkpoints, METRICS_ALL, baseline_games=igf.BASELINE_GAMES)
    pt_baseline = igf.build_pitch_type_baseline(pt_checkpoints, baseline_games=igf.BASELINE_GAMES)

    print("計算偏離程度...")
    dev = igf.compute_deviations(checkpoints, baseline, METRICS_ALL, join_keys=["pitcher_uid", "year"])
    v_dev = igf.compute_pitch_weighted_deviation(pt_checkpoints, pt_baseline)
    dev = dev.merge(v_dev, on=["pitcher_uid", "game_date", "inning"], how="left")
    for col in ["velocity_deviation", "rpm_deviation", "velocity_weighted", "velocity_weighted_baseline",
                "rpm_weighted", "rpm_weighted_baseline"]:
        if col in dev.columns:
            dev[col] = dev[col].fillna(0)

    dev["score_with_overlap"] = igf.compute_change_score(dev, igf.WEIGHTS_WITH_OVERLAP)
    dev["score_dedup"] = igf.compute_change_score(dev, igf.WEIGHTS_DEDUPLICATED)
    # game_rank<=BASELINE_GAMES的場次，基準值包含自己或未來比賽，不是嚴格事前基準——
    # dashboard要用這個標示分數可信度，見intra_game_fatigue.compute_game_rank()的說明。
    dev["baseline_game_rank"] = igf.compute_game_rank(dev)

    pt_detail = pt_checkpoints.merge(pt_baseline, on=["pitcher_uid", "year", "pitch_type"], how="left")
    return dev, pt_detail


def main():
    incremental = "--incremental" in sys.argv
    print(f"===== Supabase 同步開始 ({'incremental' if incremental else 'full'}) {datetime.now()} =====")

    df = pd.read_csv(CSV_PATH, low_memory=False)
    if "velocity_clean" not in df.columns:
        df = clean_pitch_tracking(df)

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("\n--- 1. 原始逐球資料 ---")
    sync_pitcher_pitches(sb, df, incremental)

    print("\n--- 2. 疲勞分數 pipeline (用全部歷史資料算基準線，重算量不受incremental影響) ---")
    dev, pt_detail = build_intra_game_pipeline(df)
    summary_table, curve_table, quartile_table = compute_calibration(dev)

    print("\n--- 3. 上傳衍生表 ---")
    sync_derived_tables(sb, dev, pt_detail, summary_table, curve_table, quartile_table)

    print(f"\n===== Supabase 同步完成 {datetime.now()} =====")


if __name__ == "__main__":
    main()
