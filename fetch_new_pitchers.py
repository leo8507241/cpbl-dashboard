"""
抓取 2026 新增投手的逐球資料，並對現有投手做 2026 增量更新，合併至 pitcher_pitches.csv

條件（新投手）：
  先發 ≥ 2 場  OR  牛棚 ≥ 6 場（2026年）
  且尚未在 pitcher_pitches.csv 中

增量更新（現有投手）：
  只抓 2026 賽季中比該投手在 CSV 最新 game_date 更新的場次

資料來源：rebas.tw（野球革命），涵蓋 2023–2026 年。

用法：
  python fetch_new_pitchers.py [--dry-run]
"""

import argparse
import time
import sys
import requests
import pandas as pd

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

CSV_PATH = "pitcher_pitches.csv"
BASE_URL  = "https://www.rebas.tw"

SEASONS = [
    ("CPBL-2023-sk", 2023),
    ("CPBL-2024-xa", 2024),
    ("CPBL-2025-JO", 2025),
    ("CPBL-2026-oB", 2026),
]

HIT_RESULTS = {"1B", "2B", "3B", "HR", "IH"}

STARTER_MIN_GS  = 2   # 先發≥2 場（原 5，調低以涵蓋季中加入的先發）
RELIEVER_MIN_RP = 6   # 牛棚≥6 場（原 10，調低以涵蓋使用次數少的牛棚）


def get(path, **kwargs):
    r = requests.get(BASE_URL + path, headers={"User-Agent": UA}, timeout=20, **kwargs)
    r.raise_for_status()
    return r.json()


def fetch_2026_leaders():
    data = get("/api/seasons/CPBL-2026-oB/leaders?type=pitcher&section=standard&pa=undefined")
    return data.get("data", [])


def qualifying_pitchers(leaders):
    result = []
    for p in leaders:
        gs   = p.get("SP", 0) or 0
        rp   = (p.get("games", 0) or 0) - gs
        if gs >= STARTER_MIN_GS or rp >= RELIEVER_MIN_RP:
            pl = p["player"]
            result.append({
                "name":  pl["name"],
                "uid":   pl["uniqid"],
                "team":  pl.get("team_abbr", ""),
                "gs":    gs,
                "rp":    rp,
            })
    return result


def fetch_season_logs(uid, season_uid):
    path = f"/api/formal/players/{uid}/seasons/{season_uid}/logs"
    try:
        data = get(path)
        return data.get("data", [])
    except Exception as e:
        print(f"    ⚠  {season_uid}: {e}")
        return []


def logs_to_rows(pitcher_name, pitcher_uid, season_uid, year, games):
    rows = []
    for game in games:
        gdate = game.get("date", "")
        for pa in game.get("PA_list", []):
            pa_ord     = pa.get("PA_order", 0)
            inning     = pa.get("inning", "")
            bases      = pa.get("bases", "")
            end_outs   = pa.get("endOuts", "")
            LI         = pa.get("LI", "")
            RE24       = pa.get("RE24", "")
            WPA        = pa.get("WPA", "")
            away_score = pa.get("away_score", "")
            home_score = pa.get("home_score", "")
            batter_info = pa.get("batter") or {}
            batter_name = batter_info.get("name", "")
            batter_uid  = batter_info.get("uniqid", "")
            b_hand      = pa.get("b_hand", "")
            pa_result   = pa.get("result", "")
            pa_traj     = pa.get("trajectory", "")
            pa_loc_x    = pa.get("location_coord_x", "")
            pa_loc_y    = pa.get("location_coord_y", "")
            is_hit_pa   = pa_result in HIT_RESULTS

            balls = 0
            strikes = 0
            pitch_seq = 0
            last_code = ""

            events = pa.get("events", [])
            pitch_events = [ev for ev in events if ev.get("type") == "PITCH"]

            for idx, ev in enumerate(pitch_events):
                pitch_seq += 1
                pitch  = ev.get("pitch") or {}
                ptype  = pitch.get("type", "")
                vel    = pitch.get("velocity", "")
                rpm    = pitch.get("RPM", "")
                cx     = pitch.get("coord_x", "")
                cy     = pitch.get("coord_y", "")
                code   = pitch.get("code", "")
                is_str = ev.get("is_strike", False)
                is_bl  = ev.get("is_ball", False)
                in_pl  = ev.get("in_play", False)

                # trajectory / location: only on ball-in-play pitch
                traj  = ""
                loc_x = ""
                loc_y = ""
                if in_pl:
                    traj  = pa_traj
                    loc_x = pa_loc_x
                    loc_y = pa_loc_y

                row = {
                    "pitcher":        pitcher_name,
                    "pitcher_uid":    pitcher_uid,
                    "year":           year,
                    "season_uid":     season_uid,
                    "game_date":      gdate,
                    "inning":         inning,
                    "bases":          bases,
                    "end_outs":       end_outs,
                    "LI":             LI,
                    "RE24":           RE24,
                    "WPA":            WPA,
                    "away_score":     away_score,
                    "home_score":     home_score,
                    "batter":         batter_name,
                    "batter_uid":     batter_uid,
                    "b_hand":         b_hand,
                    "pa_order":       pa_ord,
                    "pitch_seq":      pitch_seq,
                    "balls_before":   balls,
                    "strikes_before": strikes,
                    "is_first_pitch": pitch_seq == 1,
                    "pitch_type":     ptype,
                    "velocity":       vel,
                    "rpm":            rpm,
                    "coord_x":        cx,
                    "coord_y":        cy,
                    "is_strike":      is_str,
                    "is_ball":        is_bl,
                    "in_play":        in_pl,
                    "result_code":    code,
                    "pa_result":      code,        # last pitch code of the PA (same convention as original CSV)
                    "pa_result_type": pa_result,
                    "is_hit":         is_hit_pa,
                    "trajectory":     traj,
                    "pitch_loc_x":    loc_x,
                    "pitch_loc_y":    loc_y,
                }
                rows.append(row)
                last_code = code

                # update count AFTER recording row
                if is_bl:
                    balls += 1
                elif is_str and not in_pl:
                    # foul ball with 2 strikes doesn't add a strike
                    if not (code in ("F", "FT", "BUNT", "FOUL_BUNT") and strikes >= 2):
                        strikes += 1

            # back-fill pa_result (last pitch code) for all pitches in this PA
            if rows and last_code:
                pa_start = len(rows) - pitch_seq
                for i in range(pa_start, len(rows)):
                    rows[i]["pa_result"] = last_code

    return rows


def main(dry_run=False):
    existing = pd.read_csv(CSV_PATH, low_memory=False)
    existing_uids = set(existing["pitcher_uid"].unique())
    print(f"現有 CSV：{len(existing):,} 筆，{len(existing_uids)} 位投手")

    leaders = fetch_2026_leaders()
    qual    = qualifying_pitchers(leaders)
    print(f"2026 符合資格的投手：{len(qual)} 位（先發≥{STARTER_MIN_GS} 或 牛棚≥{RELIEVER_MIN_RP}）")

    new_pitchers      = [p for p in qual if p["uid"] not in existing_uids]
    existing_pitchers = [p for p in qual if p["uid"] in existing_uids]
    print(f"需要新增：{len(new_pitchers)} 位，需增量更新：{len(existing_pitchers)} 位\n")
    for p in new_pitchers:
        role = f"先發{p['gs']}場" if p['gs'] >= STARTER_MIN_GS else f"牛棚{p['rp']}場"
        print(f"  [新] {p['name']}（{p['team']}）{role}  uid={p['uid']}")

    # 計算每位現有投手在 2026 的最新 game_date（用於增量過濾）
    latest_2026 = {}
    if not existing.empty and "game_date" in existing.columns and "year" in existing.columns:
        e2026 = existing[existing["year"].astype(str) == "2026"]
        if not e2026.empty:
            ld = e2026.groupby("pitcher_uid")["game_date"].max()
            latest_2026 = ld.to_dict()

    if dry_run:
        print("\n--dry-run 模式，不實際抓取資料。")
        return

    all_new_rows = []

    # ── 新投手：抓全年度歷史資料 ──────────────────────────────────────
    for pi, pitcher in enumerate(new_pitchers, 1):
        name = pitcher["name"]
        uid  = pitcher["uid"]
        print(f"\n[新增 {pi}/{len(new_pitchers)}] {name} (uid={uid})")

        for season_uid, year in SEASONS:
            print(f"  抓取 {year} ({season_uid})...", end=" ", flush=True)
            games = fetch_season_logs(uid, season_uid)
            if not games:
                print("（無資料）")
                time.sleep(0.3)
                continue
            rows = logs_to_rows(name, uid, season_uid, year, games)
            print(f"{len(games)} 場賽事，{len(rows)} 球")
            all_new_rows.extend(rows)
            time.sleep(0.4)

    # ── 現有投手：只抓 2026 中比最新 game_date 更新的場次 ────────────
    SEASON_2026 = "CPBL-2026-oB"
    updated_pitchers = []
    for pi, pitcher in enumerate(existing_pitchers, 1):
        name      = pitcher["name"]
        uid       = pitcher["uid"]
        last_date = latest_2026.get(uid, "")
        print(f"\n[更新 {pi}/{len(existing_pitchers)}] {name} (uid={uid}, 2026最新={last_date or '無'})",
              end=" ", flush=True)
        games = fetch_season_logs(uid, SEASON_2026)
        if not games:
            print("（無資料）")
            time.sleep(0.3)
            continue

        new_games = [g for g in games if (g.get("date") or "")[:10] > last_date] if last_date else games
        if not new_games:
            print("— 無新場次")
            time.sleep(0.2)
            continue

        rows = logs_to_rows(name, uid, SEASON_2026, 2026, new_games)
        print(f"→ 新增 {len(new_games)} 場，{len(rows)} 球")
        all_new_rows.extend(rows)
        updated_pitchers.append({
            "name":       name,
            "team":       pitcher["team"],
            "rows":       len(rows),
            "date_range": f"{new_games[0].get('date','')[:10]}–{new_games[-1].get('date','')[:10]}",
        })
        time.sleep(0.4)

    if not all_new_rows:
        print("\n沒有抓到任何新資料。")
        return {"new_rows": 0, "pitchers": []}

    new_df = pd.DataFrame(all_new_rows)
    # Ensure column order matches existing CSV
    for col in existing.columns:
        if col not in new_df.columns:
            new_df[col] = ""
    new_df = new_df[existing.columns]

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.to_csv(CSV_PATH, index=False)
    print(f"\n完成！新增 {len(new_df):,} 筆，合計 {len(combined):,} 筆 → {CSV_PATH}")

    # 整理新投手統計（供報告用）
    pitcher_summary = []
    for p in new_pitchers:
        sub = new_df[new_df["pitcher_uid"] == p["uid"]]
        dates = pd.to_datetime(sub["game_date"], errors="coerce").dropna()
        date_range = (f"{dates.min().strftime('%m/%d')}–{dates.max().strftime('%m/%d')}"
                      if len(dates) else "–")
        pitcher_summary.append({
            "name":       p["name"],
            "team":       p["team"],
            "rows":       len(sub),
            "date_range": date_range,
        })
    pitcher_summary.extend(updated_pitchers)

    return {"new_rows": len(new_df), "pitchers": pitcher_summary}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只列出要新增的投手，不實際抓取")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
