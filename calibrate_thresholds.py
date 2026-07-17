"""
用全部歷年資料(2023-2026)，針對「保留重複版」換投分數，算出每一局「分數 -> 真實換投機率」的
連續曲線(isotonic regression，只保證單調不下降，不強加線性假設)，取代舊版「切3段」的作法。

舊版問題：把每一局切成 不用換/可換可不換/一定要換 三個固定分數區間，隱含假設「分數對換投機率
的鑑別力在每一局都一樣強」。但實測發現到了第7局，分數最低分組換投機率已經65.6%，最高分組只有
75%，相關係數只剩0.125(對照第5局0.237)——分數在晚局的鑑別力明顯變弱，三段式門檻硬套上去，
色帶會暗示一種其實不存在的精準度。改用連續機率曲線 + 相關係數一起呈現，鑑別力弱的局數如實反映
「分數在這裡沒那麼準」，不用假裝有清楚的分界線。

單獨執行(python calibrate_thresholds.py)會從 intra_game_checkpoints_scored.csv 讀資料、
存3個校準CSV。sync_intra_game_to_supabase.py 會直接呼叫 compute_calibration() 重用同一份
邏輯，不用另外讀寫CSV。

資料截斷偵測(detect_truncated_starts)：實測發現原始逐球資料裡，少數投手(2026-07查出13位，
包含鋼龍/布雷克/艾速特等主力洋將)有極高比例(64%~100%)的先發「剛好」都停在同一局(幾乎都是
第3局)，遠超過真實換投決策該有的分散程度(換投時機該隨對手/戰況每場不同)，判斷是這些投手的
部分歷史逐球紀錄本身就沒抓完整(截斷)，不是教練真的都在那一局換投。這種場次的「最後一局」
removed_after標記不可信(可能繼續投也可能真的換了，資料無法判斷)，但截斷之前的局數資料
(球速/轉速/戰績累計)本身是真的，予以保留，只排除最後那一局的removed_after標記。
"""
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

SCORE_GRID = np.arange(0, 101, 2)
MIN_SAMPLE = 100

TRUNCATION_CONCENTRATION_THRESHOLD = 0.6  # 單一投手的先發，>=60%都結束在同一局，判定為截斷
TRUNCATION_MIN_STARTS = 10                # 場次太少統計不穩定，先發數<10的不列入偵測


def detect_truncated_starts(checkpoint_df: pd.DataFrame, concentration_threshold: float = TRUNCATION_CONCENTRATION_THRESHOLD,
                             min_starts: int = TRUNCATION_MIN_STARTS) -> set[tuple]:
    """偵測「同一位投手，異常大比例的先發都剛好結束在同一局」的模式。
    真實換投決策應該隨每場比賽的對手/戰況不同而分散在不同局數；如果某位投手有超過
    concentration_threshold比例的先發都精準停在同一局，統計上不太可能是真實決策造成的，
    更可能是那個局數之後的逐球資料沒被抓完整。回傳 {(pitcher_uid, game_date, inning), ...}
    這組(場次,局數)的removed_after標記不可信，應該從校準用的樣本裡排除。

    反覆(iterative)偵測：排除第一層抓到的截斷場次後，同一位投手剩下的場次有時候會在
    「下一個」局數又出現異常集中(例如某人先在第3局截斷一批，剩下的又有九成集中在第4局)，
    代表截斷不只發生在單一局數。每輪只排掉當輪抓到的，重新檢查剩下的，直到抓不到新的
    異常集中點為止，才能把連續截斷完整清乾淨。"""
    max_inning = checkpoint_df.groupby(["pitcher_uid", "game_date"])["inning"].max().reset_index()
    flagged = set()
    remaining = max_inning.copy()

    while True:
        found_this_round = False
        for uid, g in remaining.groupby("pitcher_uid"):
            if len(g) < min_starts:
                continue
            vc = g["inning"].value_counts()
            top_inning, top_count = vc.idxmax(), vc.max()
            if top_count / len(g) >= concentration_threshold:
                bad_rows = g[g["inning"] == top_inning]
                for _, row in bad_rows.iterrows():
                    flagged.add((row["pitcher_uid"], row["game_date"], top_inning))
                found_this_round = True
        if not found_this_round:
            break
        flagged_keys = {(uid, d) for uid, d, _ in flagged}
        remaining = remaining[~remaining.apply(lambda r: (r["pitcher_uid"], r["game_date"]) in flagged_keys, axis=1)]

    return flagged


def compute_calibration(cp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """輸入 intra_game_checkpoints_scored 同格式的DataFrame(需含score_with_overlap欄)，
    回傳 (summary_table, curve_table, quartile_table) 三張表。"""
    cp = cp.sort_values(["pitcher_uid", "game_date", "inning"]).copy()
    cp["has_next_inning"] = cp.groupby(["pitcher_uid", "game_date"])["inning"].shift(-1).notna()
    cp["removed_after"] = (~cp["has_next_inning"]).astype(int)

    truncated = detect_truncated_starts(cp)
    if truncated:
        mask = cp.apply(lambda r: (r["pitcher_uid"], r["game_date"], r["inning"]) in truncated, axis=1)
        cp.loc[mask, "removed_after"] = np.nan
        print(f"偵測到疑似資料截斷的場次-局數組合: {len(truncated)} 組，"
              f"已從removed_after校準樣本排除(不影響球速/戰績等其他數據)")
    cp = cp.dropna(subset=["removed_after"])
    cp["removed_after"] = cp["removed_after"].astype(int)

    summary_rows, curve_rows, quartile_rows = [], [], []
    for inning in sorted(cp["inning"].unique()):
        sub = cp[cp["inning"] == inning]
        if len(sub) < MIN_SAMPLE:
            continue

        corr = sub["score_with_overlap"].corr(sub["removed_after"])
        overall_rate = sub["removed_after"].mean()

        iso = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip", increasing=True)
        iso.fit(sub["score_with_overlap"], sub["removed_after"])
        probs = iso.predict(SCORE_GRID)

        summary_rows.append({
            "inning": inning, "樣本數": len(sub),
            "該局整體換投基準率": round(overall_rate, 3),
            "鑑別力係數": round(corr, 3),
        })
        for score, prob in zip(SCORE_GRID, probs):
            curve_rows.append({"inning": inning, "score": float(score), "removal_prob": round(float(prob), 4)})

        sub_sorted = sub.sort_values("score_with_overlap").copy()
        sub_sorted["_q"] = pd.qcut(sub_sorted["score_with_overlap"], 4, labels=False, duplicates="drop")
        for q, grp in sub_sorted.groupby("_q"):
            quartile_rows.append({
                "inning": inning, "quartile": f"Q{int(q) + 1}",
                "score_low": round(grp["score_with_overlap"].min(), 1),
                "score_high": round(grp["score_with_overlap"].max(), 1),
                "removal_rate": round(grp["removed_after"].mean(), 3),
            })

    return pd.DataFrame(summary_rows), pd.DataFrame(curve_rows), pd.DataFrame(quartile_rows)


if __name__ == "__main__":
    cp = pd.read_csv("intra_game_checkpoints_scored.csv", low_memory=False)
    summary_table, curve_table, quartile_table = compute_calibration(cp)

    print(summary_table.to_string(index=False))
    summary_table.to_csv("inning_fatigue_thresholds.csv", index=False)
    curve_table.to_csv("inning_removal_curve.csv", index=False)
    quartile_table.to_csv("inning_score_quartiles.csv", index=False)
    print("\n已存檔 inning_fatigue_thresholds.csv, inning_removal_curve.csv, inning_score_quartiles.csv")
