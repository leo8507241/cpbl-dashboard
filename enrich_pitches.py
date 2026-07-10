"""
補充 pitcher_pitches.csv 的打席結果欄位：
  pa_result_type  : 打席結果代碼（HR/1B/2B/3B/FO/GO/SO/BB/HBP 等）
  is_hit          : 是否安打（1B/2B/3B/HR）
  trajectory      : 球路軌跡（F=飛球, G=滾地球, L=平飛, P=內野高飛, "")
  pitch_loc_x     : 落點 X 座標（打入場才有）
  pitch_loc_y     : 落點 Y 座標（打入場才有）

用法：python enrich_pitches.py
"""

import time
import requests
import pandas as pd

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
CSV_IN  = "pitcher_pitches.csv"
CSV_OUT = "pitcher_pitches.csv"   # 原地覆蓋

HIT_RESULTS = {"1B", "2B", "3B", "HR"}


def fetch_logs(pitcher_uid, season_uid):
    url = (f"https://www.rebas.tw/api/formal/players/"
           f"{pitcher_uid}/seasons/{season_uid}/logs")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}")
        return []
    return r.json().get("data", [])


def build_lookup(logs):
    """
    回傳兩個 dict:
      pa_lookup  : (date, pa_order, batter_uid) → {result, trajectory, loc_x, loc_y}
      pitch_lookup: (date, pa_order, batter_uid, pitch_seq) → {trajectory, loc_x, loc_y}
    """
    pa_lu = {}
    pitch_lu = {}

    for game in logs:
        date = game.get("date", "")
        for pa in game.get("PA_list", []):
            pa_ord  = pa.get("PA_order", 0)
            batter_uid = (pa.get("batter") or {}).get("uniqid", "")

            pa_key = (date, pa_ord, batter_uid)
            pa_lu[pa_key] = {
                "result":     pa.get("result", ""),
                "trajectory": pa.get("trajectory", ""),
                "loc_x":      pa.get("location_coord_x", ""),
                "loc_y":      pa.get("location_coord_y", ""),
            }

            # 逐球
            pitch_seq = 1
            for ev in pa.get("events", []):
                if ev.get("type") != "PITCH":
                    continue
                p = ev.get("pitch") or {}
                pitch_lu[(date, pa_ord, batter_uid, pitch_seq)] = {
                    "trajectory": p.get("trajectory", ""),
                    "loc_x":      p.get("location_coord_x", ""),
                    "loc_y":      p.get("location_coord_y", ""),
                }
                pitch_seq += 1

    return pa_lu, pitch_lu


def main():
    df = pd.read_csv(CSV_IN)
    print(f"讀入 {len(df)} 筆")

    combos = (df[["pitcher", "pitcher_uid", "season_uid"]]
              .drop_duplicates()
              .values.tolist())
    print(f"共 {len(combos)} 個 pitcher×season 組合\n")

    all_pa   = {}
    all_pitch = {}

    for pitcher, uid, season in combos:
        print(f"  抓取 {pitcher} | {season} ...", end=" ", flush=True)
        try:
            logs = fetch_logs(uid, season)
            pa_lu, pitch_lu = build_lookup(logs)
            all_pa.update(pa_lu)
            all_pitch.update(pitch_lu)
            print(f"{len(pa_lu)} 打席, {len(pitch_lu)} 球")
        except Exception as e:
            print(f"失敗: {e}")
        time.sleep(0.4)

    print(f"\n合計 PA lookup: {len(all_pa)}, pitch lookup: {len(all_pitch)}")

    # 對應回 CSV
    def enrich(row):
        pa_key    = (row["game_date"], int(row["pa_order"]), str(row["batter_uid"]))
        pitch_key = (row["game_date"], int(row["pa_order"]), str(row["batter_uid"]),
                     int(row["pitch_seq"]))

        pa_info    = all_pa.get(pa_key, {})
        pitch_info = all_pitch.get(pitch_key, {})

        result  = pa_info.get("result", "")
        traj    = pitch_info.get("trajectory") or pa_info.get("trajectory", "")
        loc_x   = pitch_info.get("loc_x") or pa_info.get("loc_x", "")
        loc_y   = pitch_info.get("loc_y") or pa_info.get("loc_y", "")

        return pd.Series({
            "pa_result_type": result,
            "is_hit":         result in HIT_RESULTS,
            "trajectory":     traj,
            "pitch_loc_x":    loc_x,
            "pitch_loc_y":    loc_y,
        })

    print("合併中...", flush=True)
    enriched = df.apply(enrich, axis=1)
    df2 = pd.concat([df, enriched], axis=1)

    # 比對率
    mapped = (df2["pa_result_type"] != "").sum()
    print(f"成功對應: {mapped}/{len(df2)} ({mapped/len(df2)*100:.1f}%)")
    print("pa_result_type 值分布:")
    print(df2["pa_result_type"].value_counts().head(20))

    df2.to_csv(CSV_OUT, index=False)
    print(f"\n儲存完成 → {CSV_OUT}")


if __name__ == "__main__":
    main()
