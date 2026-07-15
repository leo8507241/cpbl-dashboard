"""
單場比賽「進行中」的即時換投分數（跟 feature_engineering.py 的賽季規劃疲勞分數是不同單位：
這裡是 投手 x 場次 x 累計到第幾局，feature_engineering.py 是 投手 x 場次）。

只適用於「先發」。中繼/後援因為隨時可能中局才上場，「第幾局該長怎樣」這個基準線邏輯對他們不成立，
中繼/後援的疲勞監控請用 feature_engineering.py 的賽季規劃疲勞分數。

方法：
  1. 把每位「先發」投手歷年所有先發，按「累計到第N局為止」切出各項指標的數值
  2. 球速/轉速：不是全部球種混在一起算中位數(會被「這局丟比較多變化球」這種戰術選擇干擾)，
     是先分球種各自算偏離，再用這位投手「球季正常球種使用率」當權重加權平均回一個數字
  3. 靠打席數計算的比率指標(OPS-against/ISO-against/FIP/飛球比例/深遠飛球比例)，樣本數太小時
     (打席數<12)用滾動式權重打折，避免第1局只有3-4個打席就雜訊爆表
  4. 跟這位投手「這個球季前N場出賽」的整場最終累計數據比(不分局數)，算出標準化偏離程度
  5. 加權合成 0-100 的「換投分數」

兩個權重版本(使用者要求都做，之後用驗證結果比較，目前驗證結果保留重複版較貼近真實教練決策)：
  WEIGHTS_WITH_OVERLAP   - OPS-against(含保送) + ISO-against + FIP(含保送/HR) 同時保留，容許同個訊號被算多次
  WEIGHTS_DEDUPLICATED   - 保送/觸身球只算一次(BB%/HBP%獨立)，長打只留ISO-against一個代表，FIP只留K/BB/HBP

驗證方式：對每位投手每一場先發，把「這一局的換投分數」依同一局數分組排序，檢查分數最高的那組
「教練實際上有沒有把他換下場」的比例，是不是真的比分數最低那組高，不需要外部受傷/換投標籤。
"""
import numpy as np
import pandas as pd

FASTBALL_TYPES = {"FF", "SI", "FC"}
BREAKING_TYPES = {"SL", "CU", "CH", "FO", "SW", "FS", "KN", "EP"}

HIT_TYPES = {"1B": 1, "2B": 2, "3B": 3, "HR": 4}
WALK_TYPES = {"uBB", "IBB"}
NOT_AB_TYPES = WALK_TYPES | {"HBP", "SF", "SF_E", "SH", "SH_E", "SH_FC"}

# 靠打席數計算的比率指標，用「滾動式權重」：打席數 < SAMPLE_RAMP_FULL_AT 時權重按比例打折。
# SAMPLE_RAMP_FULL_AT=12，大約是這位投手投完前兩局面對的打席數，取滿權重的下限。
SAMPLE_RAMP_FULL_AT = 12
PA_BASED_METRICS = {"ops_against", "iso_against", "fip_overlap", "fb_traj_pct", "deep_fly_pct"}

WORSE_WHEN_LOW = {"velocity", "rpm", "csw_all", "csw_fb", "csw_br", "k_pct"}

WEIGHTS_WITH_OVERLAP = {
    "velocity": 0.20, "rpm": 0.10, "csw_all": 0.15,
    "ops_against": 0.15, "iso_against": 0.10, "fip_overlap": 0.15,
    "fb_traj_pct": 0.075, "deep_fly_pct": 0.075,
}

WEIGHTS_DEDUPLICATED = {
    "velocity": 0.20, "rpm": 0.10, "csw_all": 0.15,
    "bb_pct": 0.075, "hbp_pct": 0.025, "iso_against": 0.15, "fip_dedup": 0.10,
    "fb_traj_pct": 0.075, "deep_fly_pct": 0.075,
}


def _pitch_group(pitch_type: str) -> str:
    if pitch_type in FASTBALL_TYPES:
        return "FB"
    if pitch_type in BREAKING_TYPES:
        return "BR"
    return "OTHER"


def _mad(s):
    med = s.median()
    return (s - med).abs().median()


def sample_weight_multiplier(n_pa) -> float:
    """打席數 vs 權重乘數：0打席=0，滿SAMPLE_RAMP_FULL_AT(12)打席=1，中間線性內插。"""
    return float(np.clip(n_pa / SAMPLE_RAMP_FULL_AT, 0.0, 1.0))


def sample_weight_multiplier_by_pitch(n_pitch, step: int = 25, cap: float = 1.0) -> float:
    """打幾球 vs 權重乘數的階梯版：每滿step(預設25)球，權重乘數多0.25，封頂cap。
    跟 sample_weight_multiplier(打席數/線性內插)是兩種不同的樣本量代理指標，
    差異：打席數會受單局好壞球比例、保送多寡影響波動；球數是投手實際「投了多少球」，
    不受單局戰局內容干擾，理論上更貼近「這一局的樣本累積到哪裡了」。"""
    bucket = max(1, int(np.ceil(n_pitch / step)))
    return float(min(bucket * (step / 100), cap))


def infer_starters(pitch_df: pd.DataFrame) -> pd.DataFrame:
    """回傳只保留「先發」出賽的 (pitcher_uid, game_date) 對照表。
    判斷方式跟 feature_engineering.infer_role 一致：該場最早一球是不是在第1局。"""
    first_inning = (
        pitch_df.sort_values(["pitcher_uid", "game_date", "inning", "pa_order", "pitch_seq"])
        .groupby(["pitcher_uid", "game_date"])["inning"]
        .first()
    )
    starters = first_inning[first_inning == 1].reset_index()[["pitcher_uid", "game_date"]]
    return starters


def compute_pitch_type_checkpoints(pitch_df: pd.DataFrame) -> pd.DataFrame:
    """逐球種、逐局累計的球速/轉速中位數(long format)，之後用來算「依使用率加權」的球速/轉速偏離。"""
    df = pitch_df.sort_values(["pitcher_uid", "game_date", "inning", "pa_order", "pitch_seq"])
    rows = []
    for (uid, date), g in df.groupby(["pitcher_uid", "game_date"], sort=False):
        pitcher_name = g["pitcher"].iloc[0]
        year = g["year"].iloc[0]
        for inning in sorted(g["inning"].unique()):
            cum = g[g["inning"] <= inning]
            total_pitch = len(cum)
            for pt, sub in cum.groupby("pitch_type"):
                if pd.isna(pt):
                    continue
                rows.append({
                    "pitcher_uid": uid, "pitcher": pitcher_name, "game_date": date, "year": year, "inning": inning,
                    "pitch_type": pt, "n_pitch_type": len(sub), "usage_share": len(sub) / total_pitch,
                    "velocity_type": sub["velocity_clean"].median(), "rpm_type": sub["rpm_clean"].median(),
                })
    return pd.DataFrame(rows)


def build_pitch_type_baseline(pt_checkpoints: pd.DataFrame, baseline_games: int = 10) -> pd.DataFrame:
    """每位投手、每個球種，用該球季前 baseline_games 場出賽的「整場最終」數據，
    算球速/轉速中位數+MAD，以及這個球種在正常情況下的使用率(球季正常球種配比，不受單場戰術調整影響)。"""
    max_inning = pt_checkpoints.groupby(["pitcher_uid", "game_date"])["inning"].transform("max")
    final_per_game = pt_checkpoints[pt_checkpoints["inning"] == max_inning].copy()
    final_per_game = final_per_game.sort_values(["pitcher_uid", "year", "game_date"])

    game_rank = final_per_game.groupby(["pitcher_uid", "year"])["game_date"].rank(method="dense")
    first_n = final_per_game[game_rank <= baseline_games]

    grouped = first_n.groupby(["pitcher_uid", "year", "pitch_type"]).agg(
        velocity_type_baseline=("velocity_type", "median"),
        velocity_type_spread=("velocity_type", _mad),
        rpm_type_baseline=("rpm_type", "median"),
        rpm_type_spread=("rpm_type", _mad),
        usage_rate=("usage_share", "mean"),
    ).reset_index()
    return grouped


def compute_pitch_weighted_deviation(pt_checkpoints: pd.DataFrame, pt_baseline: pd.DataFrame) -> pd.DataFrame:
    """把逐球種的偏離，用「這位投手球季正常使用率」當權重加權平均，回傳
    (pitcher_uid, game_date, inning) 層級的單一 velocity_deviation / rpm_deviation。"""
    df = pt_checkpoints.merge(pt_baseline, on=["pitcher_uid", "year", "pitch_type"], how="left")
    df["velocity_dev_type"] = ((df["velocity_type_baseline"] - df["velocity_type"])
                                / df["velocity_type_spread"].replace(0, np.nan)).clip(lower=0).fillna(0)
    df["rpm_dev_type"] = ((df["rpm_type_baseline"] - df["rpm_type"])
                           / df["rpm_type_spread"].replace(0, np.nan)).clip(lower=0).fillna(0)

    def weighted_avg(g, val_col):
        w = g["usage_rate"].fillna(0)
        if w.sum() == 0:
            return 0.0
        return float((g[val_col] * w).sum() / w.sum())

    # 除了偏離值，現在值/基準值也用同一組使用率權重加權平均回一個數字，
    # 這樣主表才能像其他指標一樣完整顯示「現在值/基準值/偏離值」，不會只有偏離值飄在那裡沒有上下文。
    out = df.groupby(["pitcher_uid", "game_date", "inning"]).apply(
        lambda g: pd.Series({
            "velocity_deviation": weighted_avg(g, "velocity_dev_type"),
            "rpm_deviation": weighted_avg(g, "rpm_dev_type"),
            "velocity_weighted": weighted_avg(g, "velocity_type"),
            "velocity_weighted_baseline": weighted_avg(g, "velocity_type_baseline"),
            "rpm_weighted": weighted_avg(g, "rpm_type"),
            "rpm_weighted_baseline": weighted_avg(g, "rpm_type_baseline"),
        }), include_groups=False
    ).reset_index()
    return out


def compute_inning_checkpoints(pitch_df: pd.DataFrame) -> pd.DataFrame:
    """回傳每個 (pitcher_uid, game_date, inning) 的「累計到這一局結束為止」各項指標
    (球速/轉速這兩欄是全球種混算的簡易版，僅供參考顯示用；正式評分請用
    compute_pitch_weighted_deviation() 算出的 velocity_deviation/rpm_deviation)。"""
    df = pitch_df.copy()
    df["_pgroup"] = df["pitch_type"].map(_pitch_group)
    df["_is_csw"] = df["result_code"].isin(["S", "SW"])
    df["_is_hit"] = df["pa_result_type"].isin(HIT_TYPES)
    df["_tb"] = df["pa_result_type"].map(HIT_TYPES).fillna(0)
    df["_is_walk"] = df["pa_result_type"].isin(WALK_TYPES)
    df["_is_hbp"] = df["pa_result_type"] == "HBP"
    df["_is_so"] = df["pa_result_type"] == "SO"
    df["_is_hr"] = df["pa_result_type"] == "HR"
    df["_is_not_ab"] = df["pa_result_type"].isin(NOT_AB_TYPES)
    df["_is_fb_traj"] = df["trajectory"] == "F"
    dist = np.sqrt(df["coord_x"].astype(float) ** 2 + df["coord_y"].astype(float) ** 2)
    deep_threshold = dist[df["trajectory"] == "F"].quantile(0.67)
    df["_is_deep_fly"] = df["_is_fb_traj"] & (dist >= deep_threshold)

    df = df.sort_values(["pitcher_uid", "game_date", "inning", "pa_order", "pitch_seq"])

    rows = []
    for (uid, date), g in df.groupby(["pitcher_uid", "game_date"], sort=False):
        pitcher_name = g["pitcher"].iloc[0]
        year = g["year"].iloc[0]
        for inning in sorted(g["inning"].unique()):
            cum = g[g["inning"] <= inning]
            pa_level = cum.sort_values("pitch_seq").groupby(["inning", "pa_order"], as_index=False).last()

            n_pitch = len(cum)
            csw_all = cum["_is_csw"].mean() if n_pitch else np.nan
            csw_fb = cum.loc[cum["_pgroup"] == "FB", "_is_csw"].mean() if (cum["_pgroup"] == "FB").any() else np.nan
            csw_br = cum.loc[cum["_pgroup"] == "BR", "_is_csw"].mean() if (cum["_pgroup"] == "BR").any() else np.nan
            breaking_pct = (cum["_pgroup"] == "BR").mean() if n_pitch else np.nan

            n_pa = len(pa_level)
            hits = pa_level["_is_hit"].sum()
            tb = pa_level["_tb"].sum()
            walks = pa_level["_is_walk"].sum()
            hbp = pa_level["_is_hbp"].sum()
            so = pa_level["_is_so"].sum()
            hr = pa_level["_is_hr"].sum()
            not_ab = pa_level["_is_not_ab"].sum()
            at_bats = n_pa - not_ab

            obp_denom = at_bats + walks + hbp
            obp = (hits + walks + hbp) / obp_denom if obp_denom > 0 else np.nan
            slg = tb / at_bats if at_bats > 0 else np.nan
            avg = hits / at_bats if at_bats > 0 else np.nan
            ops_against = (obp or 0) + (slg or 0) if obp_denom > 0 or at_bats > 0 else np.nan
            iso_against = (slg - avg) if (at_bats > 0 and slg is not None and avg is not None) else np.nan

            outs = cum["end_outs"].iloc[-1] if len(cum) else 0
            ip_estimate = max((inning - 1) + outs / 3, 1 / 3)
            whip = (hits + walks) / ip_estimate if ip_estimate > 0 else np.nan
            k_pct = so / n_pa if n_pa else np.nan
            bb_pct = walks / n_pa if n_pa else np.nan
            hbp_pct = hbp / n_pa if n_pa else np.nan

            fip_dedup = (3 * (walks + hbp) - 2 * so) / ip_estimate if ip_estimate > 0 else np.nan
            fip_overlap = (13 * hr + 3 * (walks + hbp) - 2 * so) / ip_estimate if ip_estimate > 0 else np.nan

            fb_traj_pct = cum["_is_fb_traj"].mean() if n_pitch else np.nan
            deep_fly_pct = cum["_is_deep_fly"].mean() if n_pitch else np.nan

            velocity_med = cum["velocity_clean"].median()
            rpm_med = cum["rpm_clean"].median()

            rows.append({
                "pitcher_uid": uid, "pitcher": pitcher_name, "game_date": date, "year": year, "inning": inning,
                "n_pitch": n_pitch, "n_pa": n_pa, "velocity": velocity_med, "rpm": rpm_med,
                "csw_all": csw_all, "csw_fb": csw_fb, "csw_br": csw_br, "breaking_pct": breaking_pct,
                "ops_against": ops_against, "iso_against": iso_against, "whip": whip,
                "k_pct": k_pct, "bb_pct": bb_pct, "hbp_pct": hbp_pct,
                "fip_dedup": fip_dedup, "fip_overlap": fip_overlap,
                "fb_traj_pct": fb_traj_pct, "deep_fly_pct": deep_fly_pct,
            })

    return pd.DataFrame(rows)


def build_season_baseline(checkpoint_df: pd.DataFrame, metrics: list[str], baseline_games: int = 10) -> pd.DataFrame:
    """每位投手「這個球季」單一一組基準值(不分局數，球速/轉速不在這裡算，見 build_pitch_type_baseline)，
    用該球季前 baseline_games 場出賽的「整場最終累計數據」算中位數+MAD。"""
    max_inning = checkpoint_df.groupby(["pitcher_uid", "game_date"])["inning"].transform("max")
    final_per_game = checkpoint_df[checkpoint_df["inning"] == max_inning].copy()
    final_per_game = final_per_game.sort_values(["pitcher_uid", "year", "game_date"])

    game_rank = final_per_game.groupby(["pitcher_uid", "year"])["game_date"].rank(method="dense")
    first_n = final_per_game[game_rank <= baseline_games]

    agg = {}
    for m in metrics:
        agg[f"{m}_baseline"] = (m, "median")
        agg[f"{m}_spread"] = (m, _mad)
    grouped = first_n.groupby(["pitcher_uid", "year"]).agg(**agg).reset_index()
    return grouped


def compute_deviations(checkpoint_df: pd.DataFrame, baseline_df: pd.DataFrame, metrics: list[str],
                        join_keys: list[str] = ("pitcher_uid", "year")) -> pd.DataFrame:
    """算球速/轉速以外，其餘子指標的偏離程度(球速/轉速用 compute_pitch_weighted_deviation 另外算好合併進來)。"""
    df = checkpoint_df.merge(baseline_df, on=list(join_keys), how="left")
    for m in metrics:
        base, spread = df[f"{m}_baseline"], df[f"{m}_spread"].replace(0, np.nan)
        if m in WORSE_WHEN_LOW:
            raw = (base - df[m]) / spread
        else:
            raw = (df[m] - base) / spread
        df[f"{m}_deviation"] = raw.clip(lower=0).fillna(0)
    return df


def compute_change_score(deviation_df: pd.DataFrame, weights: dict, weight_by: str = "pa") -> pd.Series:
    """加權合成 0-100 分。PA_BASED_METRICS 的權重會依樣本量動態打折，樣本不足時這些容易被
    小樣本雜訊干擾的指標權重自動降低、其餘指標權重按比例放大。
    weight_by="pa"(預設，打席數線性內插) 或 "pitch"(球數階梯式，見 sample_weight_multiplier_by_pitch)。"""
    if weight_by == "pitch" and "n_pitch" in deviation_df.columns:
        sample_mult = deviation_df["n_pitch"].map(sample_weight_multiplier_by_pitch)
    elif "n_pa" in deviation_df.columns:
        sample_mult = deviation_df["n_pa"].map(sample_weight_multiplier)
    else:
        sample_mult = 1.0

    total = pd.Series(0.0, index=deviation_df.index)
    weight_used = pd.Series(0.0, index=deviation_df.index)
    for m, w in weights.items():
        col = f"{m}_deviation"
        if col not in deviation_df.columns:
            continue
        eff_w = w * sample_mult if m in PA_BASED_METRICS else w
        valid = deviation_df[col].notna()
        total += deviation_df[col].fillna(0) * eff_w
        weight_used += valid * eff_w
    weight_used = weight_used.replace(0, np.nan)
    score = (total / weight_used * 100 / 3).clip(0, 100)
    return score


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from data_cleaning import clean_pitch_tracking

    df = pd.read_csv("pitcher_pitches.csv", low_memory=False)
    if "velocity_clean" not in df.columns:
        df = clean_pitch_tracking(df)

    print("篩選只保留先發出賽(中繼/後援排除，回feature_engineering.py的賽季疲勞分數評估)...")
    starters = infer_starters(df)
    df = df.merge(starters, on=["pitcher_uid", "game_date"], how="inner")
    print(f"先發出賽場次: {len(starters)}")

    print("計算逐局累計指標(除了球速轉速)...")
    checkpoints = compute_inning_checkpoints(df)
    print(f"共 {len(checkpoints)} 個 (投手,場次,局數) 檢查點")

    print("計算逐球種、逐局累計球速/轉速...")
    pt_checkpoints = compute_pitch_type_checkpoints(df)

    metrics_all = ["csw_all", "csw_fb", "csw_br", "ops_against", "iso_against",
                   "whip", "k_pct", "bb_pct", "hbp_pct", "fip_dedup", "fip_overlap",
                   "fb_traj_pct", "deep_fly_pct"]

    print("建立個人球季基準線(不分局數，用該季前10場出賽的最終累計數據)...")
    baseline = build_season_baseline(checkpoints, metrics_all, baseline_games=10)
    pt_baseline = build_pitch_type_baseline(pt_checkpoints, baseline_games=10)

    print("計算偏離程度...")
    dev = compute_deviations(checkpoints, baseline, metrics_all, join_keys=["pitcher_uid", "year"])

    print("計算依使用率加權的球速/轉速偏離...")
    v_dev = compute_pitch_weighted_deviation(pt_checkpoints, pt_baseline)
    dev = dev.merge(v_dev, on=["pitcher_uid", "game_date", "inning"], how="left")
    dev["velocity_deviation"] = dev["velocity_deviation"].fillna(0)
    dev["rpm_deviation"] = dev["rpm_deviation"].fillna(0)

    dev["score_with_overlap"] = compute_change_score(dev, WEIGHTS_WITH_OVERLAP)
    dev["score_dedup"] = compute_change_score(dev, WEIGHTS_DEDUPLICATED)

    dev.to_csv("intra_game_checkpoints_scored.csv", index=False)
    pt_checkpoints.merge(pt_baseline, on=["pitcher_uid", "year", "pitch_type"], how="left").to_csv(
        "intra_game_pitch_type_detail.csv", index=False)
    print(f"\n已存檔 intra_game_checkpoints_scored.csv ({len(dev)} 列)")
    print("已存檔 intra_game_pitch_type_detail.csv (逐球種明細，供儀表板顯示用)")
    print("\nscore_with_overlap 描述統計:")
    print(dev["score_with_overlap"].describe())
    print("\nscore_dedup 描述統計:")
    print(dev["score_dedup"].describe())
