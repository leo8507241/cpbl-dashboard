"""
特徵工程層：把逐球資料聚合成「投手 x 場次」單位，計算滾動趨勢、個人基準線，
最後產出 0-100 的綜合疲勞分數。

三層架構的中間層：
  data_cleaning.py      -> 逐球層級的型別/髒值清理 (velocity_clean/rpm_clean)
  feature_engineering.py -> 本檔案，投手x場次聚合 + 疲勞分數 (本檔案)
  視覺化層(之後的 Streamlit/Dash app) -> 只負責畫圖，不算任何統計

設計原則（對應原始需求）：
  - 單位是「投手 x 場次」，不是逐球即時計算
  - 先發/中繼用「該場首次登板的局數」判斷：inning==1 起登板 -> 先發，否則中繼
  - 沒有自責分/非自責分區別，ERA 改用 RA（失分率，非自責分開）+ OPS-against 兩個子指標一起納入
  - 個人基準線 = 該投手「本季」前 N 場的中位數（預設 N=10，可調整參數，不寫死）
  - 滾動趨勢用中位數（不是平均），避免單場暴投/離群值污染整體趨勢
  - 權重全部集中在 DEFAULT_WEIGHTS，可覆寫，不寫死在計算邏輯深處
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ── 打席結果分類（用來算 OPS-against，未出現在資料裡的代碼視為不影響AB/H統計）──
HIT_TYPES = {"1B": 1, "2B": 2, "3B": 3, "HR": 4}
WALK_TYPES = {"uBB", "IBB"}
NOT_AB_TYPES = WALK_TYPES | {"HBP", "SF", "SF_E", "SH", "SH_E", "SH_FC"}

# ── 疲勞分數各子指標的預設權重，總和為1，之後可依球團回饋調整 ──
# 理由：球速/轉速下滑是最直接的生理疲勞訊號，佔比最高；
#       表現面(失分率+OPS-against)是結果面訊號，容易受隊友守備/運氣干擾，權重次之；
#       穩定度(滾動標準差)是輔助訊號，用來抓「亂了」而非「變差」，權重最低。
DEFAULT_WEIGHTS = {
    "velocity_decline": 0.30,
    "rpm_decline": 0.15,
    "ra_decline": 0.20,
    "ops_against_decline": 0.20,
    "instability": 0.15,
}


@dataclass
class FatigueScoreConfig:
    baseline_games: int = 10       # 個人基準線用本季前幾場
    rolling_window: int = 5        # 滾動趨勢視窗大小(場數)
    rpm_min_coverage: float = 0.5  # 該滾動視窗內rpm非缺值比例低於此門檻，rpm子指標權重歸零、其餘權重按比例放大
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


def infer_role(pitch_df: pd.DataFrame) -> pd.DataFrame:
    """回傳 (pitcher_uid, game_date) -> role ('SP'先發/'RP'中繼) 的對照表。
    判斷方式：該場最早出現的一球是不是在第1局 -> 是則先發，否則中繼。"""
    first_inning = (
        pitch_df.sort_values(["pitcher_uid", "game_date", "inning", "pa_order", "pitch_seq"])
        .groupby(["pitcher_uid", "game_date"])["inning"]
        .first()
    )
    role = np.where(first_inning == 1, "SP", "RP")
    return pd.DataFrame({"role": role}, index=first_inning.index).reset_index()


def aggregate_to_game_level(pitch_df: pd.DataFrame) -> pd.DataFrame:
    """把逐球資料聚合成「投手 x 場次」一列，包含球速/轉速中位數、失分率、OPS-against等。"""
    df = pitch_df.copy()

    is_hit = df["pa_result_type"].isin(HIT_TYPES)
    total_bases = df["pa_result_type"].map(HIT_TYPES).fillna(0)
    is_walk = df["pa_result_type"].isin(WALK_TYPES)
    is_hbp = df["pa_result_type"] == "HBP"
    is_not_ab = df["pa_result_type"].isin(NOT_AB_TYPES)
    is_pa_end = df["pa_result_type"].notna()  # pa_result_type 已回填整個打席，用它標記「這球屬於哪個打席」

    df = df.assign(_is_hit=is_hit, _tb=total_bases, _is_walk=is_walk, _is_hbp=is_hbp,
                    _is_not_ab=is_not_ab, _is_pa_row=is_pa_end)

    # 每個打席只取一列(pitch_seq最大那球)算打席結果，避免同一打席的多顆球重複計入AB/H
    pa_level = (
        df.sort_values("pitch_seq")
        .groupby(["pitcher_uid", "game_date", "inning", "pa_order"], as_index=False)
        .last()
    )

    role_map = infer_role(df)

    game_group = pa_level.groupby(["pitcher_uid", "pitcher", "game_date", "year"], as_index=False)
    game_agg = game_group.agg(
        batters_faced=("pa_order", "count"),
        hits=("_is_hit", "sum"),
        total_bases=("_tb", "sum"),
        walks=("_is_walk", "sum"),
        hbp=("_is_hbp", "sum"),
        not_ab=("_is_not_ab", "sum"),
        max_inning=("inning", "max"),
    )
    game_agg["at_bats"] = game_agg["batters_faced"] - game_agg["not_ab"]
    game_agg["obp_denom"] = game_agg["at_bats"] + game_agg["walks"] + game_agg["hbp"]
    game_agg["obp"] = (game_agg["hits"] + game_agg["walks"] + game_agg["hbp"]) / game_agg["obp_denom"].replace(0, np.nan)
    game_agg["slg"] = game_agg["total_bases"] / game_agg["at_bats"].replace(0, np.nan)
    game_agg["ops_against"] = game_agg["obp"].fillna(0) + game_agg["slg"].fillna(0)

    # 球速/轉速：用中位數(穩健統計量)，並記錄該場rpm非缺值覆蓋率供之後動態降權
    pitch_stats = df.groupby(["pitcher_uid", "game_date"], as_index=False).agg(
        velocity_med=("velocity_clean", "median"),
        rpm_med=("rpm_clean", "median"),
        rpm_coverage=("rpm_clean", lambda s: s.notna().mean()),
        pitch_count=("pitch_seq", "count"),
    )

    # 失分率(RA proxy)：這場出賽期間，對方比分的變化量(用away/home_score在他投球期間的差)
    ra = _compute_runs_allowed(df)

    out = (
        game_agg
        .merge(pitch_stats, on=["pitcher_uid", "game_date"], how="left")
        .merge(ra, on=["pitcher_uid", "game_date"], how="left")
        .merge(role_map, on=["pitcher_uid", "game_date"], how="left")
    )
    out = out.sort_values(["pitcher_uid", "game_date"]).reset_index(drop=True)
    return out


def _compute_runs_allowed(df: pd.DataFrame) -> pd.DataFrame:
    """用該投手在場上時，對方比分從開始到結束的變化量，近似「失分率(RA)」。
    不區分自責分/非自責分(資料沒有官方責失分判定)，只反映"這段期間對方拿了幾分"。"""
    rows = []
    for (uid, date), g in df.groupby(["pitcher_uid", "game_date"]):
        g = g.sort_values(["inning", "pa_order", "pitch_seq"])
        first, last = g.iloc[0], g.iloc[-1]
        # 該投手屬於防守方，對方得分 = 進攻方分數。用 VisitingHomeType 邏輯已內化在原始away/home_score，
        # 這裡簡化：不管主客，直接看「總分變化」中屬於進攻方的分數變化最保險的方式是
        # 比較 away_score+home_score 的總和變化(不管誰進攻，只要有得分livelog就會反映在對應欄位)
        runs = (last["away_score"] + last["home_score"]) - (first["away_score"] + first["home_score"])
        rows.append({"pitcher_uid": uid, "game_date": date, "runs_allowed": max(runs, 0)})
    return pd.DataFrame(rows)


def _rolling_median(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=1).median()


def _rolling_std(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=2).std()


def compute_fatigue_scores(game_df: pd.DataFrame, config: FatigueScoreConfig | None = None) -> pd.DataFrame:
    """輸入 aggregate_to_game_level() 的輸出，回傳加上每場疲勞分數(0-100)的DataFrame。
    每個投手獨立計算：本季前 config.baseline_games 場的中位數當基準線，
    之後每一場用最近 config.rolling_window 場的滾動中位數/標準差跟基準線比較。"""
    cfg = config or FatigueScoreConfig()
    df = game_df.sort_values(["pitcher_uid", "year", "game_date"]).copy()

    out_frames = []
    for (uid, year), g in df.groupby(["pitcher_uid", "year"]):
        g = g.reset_index(drop=True)
        baseline_slice = g.iloc[: cfg.baseline_games]
        baseline = {
            "velocity": baseline_slice["velocity_med"].median(),
            "rpm": baseline_slice["rpm_med"].median(),
            "runs_allowed": baseline_slice["runs_allowed"].median(),
            "ops_against": baseline_slice["ops_against"].median(),
        }

        g["rolling_velocity"] = _rolling_median(g["velocity_med"], cfg.rolling_window)
        g["rolling_rpm"] = _rolling_median(g["rpm_med"], cfg.rolling_window)
        g["rolling_runs_allowed"] = _rolling_median(g["runs_allowed"], cfg.rolling_window)
        g["rolling_ops_against"] = _rolling_median(g["ops_against"], cfg.rolling_window)
        g["rolling_velocity_std"] = _rolling_std(g["velocity_med"], cfg.rolling_window)
        g["rolling_rpm_coverage"] = g["rpm_coverage"].rolling(cfg.rolling_window, min_periods=1).mean()

        g["baseline_velocity"] = baseline["velocity"]
        g["baseline_rpm"] = baseline["rpm"]
        g["baseline_runs_allowed"] = baseline["runs_allowed"]
        g["baseline_ops_against"] = baseline["ops_against"]

        out_frames.append(g)

    result = pd.concat(out_frames, ignore_index=True)

    # 各子指標的「衰退程度」，正值代表比基準線差，負值代表比基準線好，之後裁切到[0,1]再乘權重
    result["velocity_decline_pct"] = (result["baseline_velocity"] - result["rolling_velocity"]) / result["baseline_velocity"]
    result["rpm_decline_pct"] = (result["baseline_rpm"] - result["rolling_rpm"]) / result["baseline_rpm"]
    result["ra_decline_pct"] = (result["rolling_runs_allowed"] - result["baseline_runs_allowed"]) / result["baseline_runs_allowed"].replace(0, np.nan)
    result["ops_against_decline_pct"] = (result["rolling_ops_against"] - result["baseline_ops_against"]) / result["baseline_ops_against"].replace(0, np.nan)
    # 穩定度：滾動標準差相對基準線球速的比例(變異係數)，越大代表球速越飄忽不定
    result["instability_pct"] = result["rolling_velocity_std"] / result["baseline_velocity"]

    weights = dict(cfg.weights)
    # rpm涵蓋率太低的視窗，rpm子指標不可信 -> 權重歸零、其餘子指標按比例放大
    low_rpm_coverage = result["rolling_rpm_coverage"] < cfg.rpm_min_coverage
    remaining_weight_sum = sum(v for k, v in weights.items() if k != "rpm_decline")

    def _score_row(row, sub_weights):
        components = {
            "velocity_decline": row["velocity_decline_pct"],
            "rpm_decline": row["rpm_decline_pct"],
            "ra_decline": row["ra_decline_pct"],
            "ops_against_decline": row["ops_against_decline_pct"],
            "instability": row["instability_pct"],
        }
        total, weight_used = 0.0, 0.0
        for k, w in sub_weights.items():
            v = components[k]
            if pd.isna(v):
                continue
            total += np.clip(v, 0, 1) * w
            weight_used += w
        if weight_used == 0:
            return np.nan
        return total / weight_used * 100

    scores = []
    for idx, row in result.iterrows():
        sub_weights = dict(weights)
        if low_rpm_coverage.loc[idx]:
            sub_weights["rpm_decline"] = 0
        scores.append(_score_row(row, sub_weights))
    result["fatigue_score"] = scores

    return result


if __name__ == "__main__":
    from data_cleaning import clean_pitch_tracking

    CSV_PATH = "/Users/leochen/Desktop/線上課程教材/pythan基礎觀念/爬蟲/pitcher_pitches.csv"
    pitch_df = pd.read_csv(CSV_PATH, low_memory=False)
    if "velocity_clean" not in pitch_df.columns:
        pitch_df = clean_pitch_tracking(pitch_df)

    game_df = aggregate_to_game_level(pitch_df)
    print(f"聚合後投手x場次列數: {len(game_df)}")
    print(game_df[["pitcher", "game_date", "role", "velocity_med", "rpm_med", "ops_against", "runs_allowed"]].head(10))

    scored = compute_fatigue_scores(game_df)
    print("\n疲勞分數描述統計:")
    print(scored["fatigue_score"].describe())

    sample_pitcher = scored["pitcher"].iloc[0]
    print(f"\n=== {sample_pitcher} 疲勞分數趨勢範例 ===")
    print(scored[scored["pitcher"] == sample_pitcher][
        ["game_date", "role", "velocity_med", "rolling_velocity", "baseline_velocity", "fatigue_score"]
    ].to_string(index=False))
