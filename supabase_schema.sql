-- ============================================================================
-- CPBL 投手疲勞分析 — Supabase 新增表格
-- 用法：複製整份貼進 Supabase Dashboard -> SQL Editor -> Run，只需執行一次。
-- 之後的資料同步由 sync_intra_game_to_supabase.py 處理（delete+insert，不需要
-- 重跑這份 schema，除非要重建表格結構）。
--
-- RLS 說明：這個專案沿用現有 cpbl_batting_2020_2026 表格的模式——用
-- anon/publishable key 就能讀寫（沒有另外收緊 RLS）。如果你的 Supabase
-- 專案對新表格預設關閉匿名寫入，請比照 cpbl_batting_2020_2026 現有的
-- RLS policy 設定這6張新表，不然 sync 腳本的 insert/delete 會被拒絕。
-- ============================================================================

-- 1. 原始逐球資料（pitcher_pitches.csv 全量）
create table if not exists cpbl_pitcher_pitches (
    id bigint generated always as identity primary key,
    pitcher text,
    pitcher_uid text not null,
    year integer not null,
    season_uid text,
    game_date text not null,
    inning integer not null,
    bases integer,
    end_outs integer,
    "LI" double precision,
    "RE24" double precision,
    "WPA" double precision,
    away_score double precision,
    home_score double precision,
    batter text,
    batter_uid text,
    b_hand text,
    pa_order integer not null,
    pitch_seq integer not null,
    balls_before integer,
    strikes_before integer,
    is_first_pitch boolean,
    pitch_type text,
    velocity text,          -- 原始欄位，可能含OCR雜訊字元，故用text，乾淨數值請用velocity_clean
    rpm text,               -- 同上，乾淨數值請用rpm_clean
    coord_x double precision,
    coord_y double precision,
    is_strike boolean,
    is_ball boolean,
    in_play text,
    result_code text,
    pa_last_pitch_code text,
    pa_result_type text,
    is_hit boolean,
    trajectory text,
    pitch_loc_x double precision,
    pitch_loc_y double precision,
    velocity_clean double precision,
    rpm_clean double precision,
    unique (pitcher_uid, game_date, inning, pa_order, pitch_seq)
);
create index if not exists idx_pitches_year on cpbl_pitcher_pitches(year);
create index if not exists idx_pitches_pitcher on cpbl_pitcher_pitches(pitcher_uid);

-- 2. 單場逐局換投分數明細（intra_game_checkpoints_scored.csv 全量）
create table if not exists cpbl_intra_game_checkpoints (
    id bigint generated always as identity primary key,
    pitcher_uid text not null,
    pitcher text,
    game_date text not null,
    year integer,
    inning integer not null,
    n_pitch integer,
    n_pa integer,
    velocity double precision,
    rpm double precision,
    csw_all double precision,
    csw_fb double precision,
    csw_br double precision,
    breaking_pct double precision,
    ops_against double precision,
    iso_against double precision,
    whip double precision,
    k_pct double precision,
    bb_pct double precision,
    hbp_pct double precision,
    fip_dedup double precision,
    fip_overlap double precision,
    fb_traj_pct double precision,
    deep_fly_pct double precision,
    csw_all_baseline double precision,
    csw_all_spread double precision,
    csw_fb_baseline double precision,
    csw_fb_spread double precision,
    csw_br_baseline double precision,
    csw_br_spread double precision,
    ops_against_baseline double precision,
    ops_against_spread double precision,
    iso_against_baseline double precision,
    iso_against_spread double precision,
    whip_baseline double precision,
    whip_spread double precision,
    k_pct_baseline double precision,
    k_pct_spread double precision,
    bb_pct_baseline double precision,
    bb_pct_spread double precision,
    hbp_pct_baseline double precision,
    hbp_pct_spread double precision,
    fip_dedup_baseline double precision,
    fip_dedup_spread double precision,
    fip_overlap_baseline double precision,
    fip_overlap_spread double precision,
    fb_traj_pct_baseline double precision,
    fb_traj_pct_spread double precision,
    deep_fly_pct_baseline double precision,
    deep_fly_pct_spread double precision,
    csw_all_deviation double precision,
    csw_fb_deviation double precision,
    csw_br_deviation double precision,
    ops_against_deviation double precision,
    iso_against_deviation double precision,
    whip_deviation double precision,
    k_pct_deviation double precision,
    bb_pct_deviation double precision,
    hbp_pct_deviation double precision,
    fip_dedup_deviation double precision,
    fip_overlap_deviation double precision,
    fb_traj_pct_deviation double precision,
    deep_fly_pct_deviation double precision,
    velocity_deviation double precision,
    rpm_deviation double precision,
    velocity_weighted double precision,
    velocity_weighted_baseline double precision,
    rpm_weighted double precision,
    rpm_weighted_baseline double precision,
    score_with_overlap double precision,
    score_dedup double precision,
    unique (pitcher_uid, game_date, inning)
);
create index if not exists idx_checkpoints_pitcher_game on cpbl_intra_game_checkpoints(pitcher_uid, game_date);

-- 3. 逐球種明細（intra_game_pitch_type_detail.csv 全量）
create table if not exists cpbl_intra_game_pitch_type_detail (
    id bigint generated always as identity primary key,
    pitcher_uid text not null,
    pitcher text,
    game_date text not null,
    year integer,
    inning integer not null,
    pitch_type text not null,
    n_pitch_type integer,
    usage_share double precision,
    velocity_type double precision,
    rpm_type double precision,
    velocity_type_baseline double precision,
    velocity_type_spread double precision,
    rpm_type_baseline double precision,
    rpm_type_spread double precision,
    usage_rate double precision,
    unique (pitcher_uid, game_date, inning, pitch_type)
);
create index if not exists idx_pt_detail_pitcher_game on cpbl_intra_game_pitch_type_detail(pitcher_uid, game_date);

-- 4. 每局換投門檻校準摘要（inning_fatigue_thresholds.csv，中文欄名改英文方便query）
create table if not exists cpbl_inning_fatigue_thresholds (
    inning integer primary key,
    sample_size integer,
    baseline_removal_rate double precision,
    discriminant_coef double precision
);

-- 5. 每局「分數->真實換投機率」連續曲線(inning_removal_curve.csv)
create table if not exists cpbl_inning_removal_curve (
    id bigint generated always as identity primary key,
    inning integer not null,
    score double precision not null,
    removal_prob double precision,
    unique (inning, score)
);

-- 6. 每局分數Q1-Q4粗分層對照表(inning_score_quartiles.csv)
create table if not exists cpbl_inning_score_quartiles (
    id bigint generated always as identity primary key,
    inning integer not null,
    quartile text not null,
    score_low double precision,
    score_high double precision,
    removal_rate double precision,
    unique (inning, quartile)
);
