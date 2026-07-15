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
"""
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

SCORE_GRID = np.arange(0, 101, 2)
MIN_SAMPLE = 100


def compute_calibration(cp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """輸入 intra_game_checkpoints_scored 同格式的DataFrame(需含score_with_overlap欄)，
    回傳 (summary_table, curve_table, quartile_table) 三張表。"""
    cp = cp.sort_values(["pitcher_uid", "game_date", "inning"]).copy()
    cp["has_next_inning"] = cp.groupby(["pitcher_uid", "game_date"])["inning"].shift(-1).notna()
    cp["removed_after"] = (~cp["has_next_inning"]).astype(int)

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
