"""
資料清理層：只處理「型別/髒值/合理範圍」層級的清理，不做任何特徵工程或統計計算。
輸入輸出都是 DataFrame，不直接讀寫 pitcher_pitches.csv，方便單元測試與之後被
特徵工程層 import 使用。

原始 velocity/rpm 欄位保留不動（存檔用途），清理後的數值另外輸出成
velocity_clean / rpm_clean 兩個新欄位（float，範圍外或無法轉換一律為 NaN）。
"""
import re
import pandas as pd

VELOCITY_RANGE = (100, 170)   # km/h，職棒正常球速範圍
RPM_RANGE = (1000, 3000)      # rpm，職棒正常轉速範圍

# 常見 OCR/爬蟲誤植的字元替換（在抽數字之前先修正，避免把 "22O0" 這種誤把 0 打成 O 的值整筆丟掉）
_OCR_FIXES = {"O": "0", "o": "0", "I": "1", "l": "1"}
_NON_DIGIT_RE = re.compile(r"[^\d.]")


def _extract_numeric(raw) -> float | None:
    """修正常見OCR誤字後，去除所有非數字字元只留數字本體；純垃圾值(如'*'/'A'/'q')留空。
    raw理論上是astype(str)轉換後的字串，但不同pandas版本/後端(如PyArrow-backed字串陣列)
    對缺失值的處理不一致，NaN/None有時候會繞過astype(str)直接以float型別傳進來，
    這裡明確擋掉非字串輸入，不能假設一定是str。"""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    for bad, good in _OCR_FIXES.items():
        s = s.replace(bad, good)
    digits = _NON_DIGIT_RE.sub("", s)
    if digits in ("", "."):
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def _to_clean_numeric(series: pd.Series, valid_range: tuple[float, float]) -> pd.Series:
    """抽出數字本體（容錯常見誤植字元），超出合理範圍的視為異常值轉 NaN。"""
    numeric = series.astype(str).map(_extract_numeric)
    numeric = pd.to_numeric(numeric, errors="coerce")
    lo, hi = valid_range
    out_of_range = (numeric < lo) | (numeric > hi)
    numeric = numeric.mask(out_of_range)
    return numeric


def clean_pitch_tracking(df: pd.DataFrame) -> pd.DataFrame:
    """回傳新增了 velocity_clean / rpm_clean 兩欄的 DataFrame（不修改原始欄位）。"""
    out = df.copy()
    out["velocity_clean"] = _to_clean_numeric(out["velocity"], VELOCITY_RANGE)
    out["rpm_clean"] = _to_clean_numeric(out["rpm"], RPM_RANGE)
    return out


if __name__ == "__main__":
    CSV_PATH = "/Users/leochen/Desktop/線上課程教材/pythan基礎觀念/爬蟲/pitcher_pitches.csv"
    df = pd.read_csv(CSV_PATH, low_memory=False)
    before_v_missing = df["velocity"].isna().mean()
    before_r_missing = df["rpm"].isna().mean()

    cleaned = clean_pitch_tracking(df)

    after_v_missing = cleaned["velocity_clean"].isna().mean()
    after_r_missing = cleaned["rpm_clean"].isna().mean()
    n_v_dropped_as_junk = ((df["velocity"].notna()) & (cleaned["velocity_clean"].isna())).sum()
    n_r_dropped_as_junk = ((df["rpm"].notna()) & (cleaned["rpm_clean"].isna())).sum()

    print(f"velocity 缺值率: {before_v_missing:.4f} -> {after_v_missing:.4f} (新增 {n_v_dropped_as_junk} 筆判定為髒值/超出範圍)")
    print(f"rpm 缺值率: {before_r_missing:.4f} -> {after_r_missing:.4f} (新增 {n_r_dropped_as_junk} 筆判定為髒值/超出範圍)")
    print()
    print("velocity_clean 描述統計:")
    print(cleaned["velocity_clean"].describe())
    print()
    print("rpm_clean 描述統計:")
    print(cleaned["rpm_clean"].describe())

    cleaned.to_csv(CSV_PATH, index=False)
    print(f"\n已寫回 {CSV_PATH}（新增 velocity_clean / rpm_clean 兩欄，原始 velocity/rpm 保留不動）")
