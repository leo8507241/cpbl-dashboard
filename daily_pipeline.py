"""
CPBL 每日更新主流程 + Email 報告
執行順序：
  1. daily_update.py      → rebas.tw 打者累計 + 進階指標 → Supabase
                            （內部呼叫 每日追蹤/run_daily.py：
                              打者逐場＋投手對戰，全部改用 rebas.tw）
  2. fetch_new_pitchers.py → 抓新投手逐球資料 → pitcher_pitches.csv
  3. enrich_pitches.py    → 補充打席結果欄位
  4. 查詢 Supabase 取得各儀表板更新前後狀態差異
  5. 發送 Email 日報（含六個儀表板的更新追蹤）
"""
import os
import sys
import io
import contextlib
import math
from datetime import datetime

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "每日追蹤"))

GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
NOTIFY_EMAIL   = os.environ.get("NOTIFY_EMAIL", GMAIL_USER)
HF_USER        = os.environ.get("HF_USER", "leo88888")
SPACE_URL      = f"https://huggingface.co/spaces/{HF_USER}/cpbl-dashboard"
CSV_PATH       = "pitcher_pitches.csv"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://vxgtgqlqukexpvnnvslf.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_9DDMVwHFIMCdBxN12jaWkQ_gMeJkRuI")


# ── helpers ──────────────────────────────────────────────────────────────────

def send_email(subject: str, body: str):
    if not GMAIL_USER or not GMAIL_PASSWORD:
        print("[Email] 未設定 GMAIL_USER / GMAIL_APP_PASSWORD，跳過")
        return
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_USER
        msg["To"]      = NOTIFY_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(GMAIL_USER, GMAIL_PASSWORD)
            srv.send_message(msg)
        print(f"[Email] ✅ 已寄出至 {NOTIFY_EMAIL}")
    except Exception as e:
        print(f"[Email] ❌ 寄送失敗：{e}")


def csv_row_count(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f) - 1
    except FileNotFoundError:
        return 0


def _supabase_client():
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        return None


def _q(sb, table, select, filters=None):
    """Simple query helper; returns list of dicts."""
    try:
        q = sb.table(table).select(select)
        if filters:
            for col, val in filters.items():
                q = q.eq(col, val)
        return q.execute().data or []
    except Exception:
        return []


# ── 儀表板快照（更新前後比較用）───────────────────────────────────────────

def _snapshot_batting(sb, year):
    """累計打擊：今年球員數、wRC+ 最高前 3 位。"""
    rows = _q(sb, "cpbl_batting_2020_2026", "球員,球隊,pa,wrc_plus,hr,avg",
              {"年度": year})
    if not rows:
        return None
    qualified = [r for r in rows if (r.get("pa") or 0) >= 100]
    top3 = sorted(qualified, key=lambda r: r.get("wrc_plus") or 0, reverse=True)[:3]
    return {
        "players": len(rows),
        "top_wrc": [(r["球員"], r["球隊"], r.get("wrc_plus")) for r in top3],
        "max_hr":  max((r.get("hr") or 0) for r in rows),
    }


def _snapshot_gamelog(sb, year):
    """逐場記錄：最新場次日期、總筆數。"""
    rows = _q(sb, "cpbl_batter_game_log", "date", {"year": year})
    if not rows:
        return {"latest": None, "total": 0}
    dates = [r["date"] for r in rows if r.get("date")]
    return {"latest": max(dates) if dates else None, "total": len(rows)}


def _snapshot_matchup(sb, year):
    """對戰紀錄：最新日期、總打席數。"""
    rows = _q(sb, "cpbl_matchup_log", "date", {"year": year})
    if not rows:
        return {"latest": None, "total": 0}
    dates = [r["date"] for r in rows if r.get("date")]
    return {"latest": max(dates) if dates else None, "total": len(rows)}


def _snapshot_pitcher_csv():
    """投手逐球 CSV：球數、投手數。"""
    try:
        import pandas as pd
        df = pd.read_csv(CSV_PATH, low_memory=False)
        return {"rows": len(df), "pitchers": df["pitcher"].nunique()}
    except Exception:
        return {"rows": csv_row_count(CSV_PATH), "pitchers": 0}


def _snapshot_fatigue(sb):
    """投手疲勞分析資料：最新日期、筆數。"""
    rows = _q(sb, "cpbl_intra_game_checkpoints", "game_date")
    if not rows:
        return {"latest": None, "total": 0}
    dates = [r["game_date"] for r in rows if r.get("game_date")]
    return {"latest": max(dates) if dates else None, "total": len(rows)}


def take_snapshot(year):
    sb = _supabase_client()
    if sb is None:
        return {}
    return {
        "batting":  _snapshot_batting(sb, year),
        "gamelog":  _snapshot_gamelog(sb, year),
        "matchup":  _snapshot_matchup(sb, year),
        "pitcher":  _snapshot_pitcher_csv(),
        "fatigue":  _snapshot_fatigue(sb),
    }


# ── 組 Email 報告 ─────────────────────────────────────────────────────────────

def _diff_gamelog(before, after, label):
    """比較前後的逐場/對戰快照，回傳狀態行。"""
    if after is None:
        return f"  ⚠️ 查詢失敗"
    b_lat  = before.get("latest") if before else None
    a_lat  = after.get("latest")
    b_tot  = before.get("total",  0) if before else 0
    a_tot  = after.get("total",   0)
    added  = a_tot - b_tot
    if added > 0:
        return f"  ✅ 新增 {added:,} 筆，最新場次 {a_lat}"
    if a_lat and b_lat and a_lat == b_lat:
        return f"  ⚪ 無新增（最新 {a_lat}，共 {a_tot:,} 筆）"
    return f"  ⚪ 無新增（最新 {a_lat or '–'}，共 {a_tot:,} 筆）"


def _diff_batting(before, after, year):
    if not after:
        return ["  ⚠️ 查詢失敗"]
    b_players = before.get("players", 0) if before else 0
    a_players = after.get("players", 0)
    lines = [f"  ✅ 已同步至 Supabase，{a_players} 位球員"]
    if a_players != b_players and b_players > 0:
        lines.append(f"     （比更新前 {'增加' if a_players > b_players else '減少'} "
                     f"{abs(a_players - b_players)} 位）")
    if after.get("top_wrc"):
        lines.append("  📊 wRC+ 前三名：")
        for name, team, wrc in after["top_wrc"]:
            lines.append(f"     {name}（{team}）wRC+={wrc}")
    lines.append(f"  🏠 全隊最高 HR：{after.get('max_hr', '–')}")
    return lines


def _diff_pitcher(before, after):
    if not after:
        return ["  ⚠️ 查詢失敗"]
    b_rows = before.get("rows", 0) if before else 0
    a_rows = after.get("rows", 0)
    added  = a_rows - b_rows
    if added > 0:
        return [f"  ✅ 新增 {added:,} 球，合計 {a_rows:,} 球，{after.get('pitchers',0)} 位投手"]
    return [f"  ⚪ 無新增（共 {a_rows:,} 球，{after.get('pitchers',0)} 位投手）"]


def build_email(date_tp, time_van, year,
                before, after,
                batting_updated, pitch_new_rows, pitch_new_pitchers,
                new_games, new_matchup_rows, errors):

    lines = [
        "📊 CPBL 每日更新報告",
        f"📅 台北 {date_tp}｜溫哥華 {time_van}",
        "=" * 50,
        "",
        "【六個儀表板更新狀況】",
        "",
    ]

    # ① 被低估打者預測 / 打者趨勢雷達（同一資料源）
    lines.append("① 被低估打者預測 & 打者趨勢雷達")
    lines.append(f"   資料源：rebas.tw 累計打擊數據 → Supabase（{year}年）")
    if batting_updated:
        for l in _diff_batting(before.get("batting"), after.get("batting"), year):
            lines.append(l)
    else:
        lines.append("  ❌ 更新失敗（見下方錯誤）")
    lines.append("")

    # ② 林立效應分析
    lines.append("② 林立效應分析")
    lines.append(f"   資料源：rebas.tw 打者逐場紀錄 → Supabase")
    if batting_updated:
        lines.append(_diff_gamelog(before.get("gamelog"), after.get("gamelog"), "gamelog"))
        if new_games > 0:
            lines.append(f"  ⚾ 本次新增 {new_games} 場")
    else:
        lines.append("  ⚠️ 隨打者資料一起執行，請見上方")
    lines.append("")

    # ③ 投手剋星分析
    lines.append("③ 投手剋星分析")
    lines.append(f"   資料源：rebas.tw 投手對戰紀錄 → Supabase")
    if batting_updated:
        lines.append(_diff_gamelog(before.get("matchup"), after.get("matchup"), "matchup"))
        if new_matchup_rows > 0:
            lines.append(f"  📋 本次新增 {new_matchup_rows:,} 個打席")
    else:
        lines.append("  ⚠️ 隨打者資料一起執行，請見上方")
    lines.append("")

    # ④ 投手弱點分析
    lines.append("④ 投手弱點分析")
    lines.append(f"   資料源：rebas.tw 逐球資料 → pitcher_pitches.csv → HuggingFace")
    for l in _diff_pitcher(before.get("pitcher"), after.get("pitcher")):
        lines.append(l)
    if pitch_new_pitchers:
        lines.append(f"  📌 新增投手（{len(pitch_new_pitchers)} 位）：")
        for p in pitch_new_pitchers:
            lines.append(f"     • {p['name']}（{p['team']}）"
                         f"  {p['rows']:,} 球  {p['date_range']}")
    lines.append("")

    # ⑤ 單場即時換投監控
    lines.append("⑤ 單場即時換投監控")
    lines.append(f"   資料源：pitcher_pitches.csv → 疲勞分數 → Supabase")
    fa = after.get("fatigue")
    fb = before.get("fatigue")
    if fa:
        fa_tot = fa.get("total", 0)
        fb_tot = fb.get("total", 0) if fb else 0
        added  = fa_tot - fb_tot
        if added > 0:
            lines.append(f"  ✅ 新增 {added:,} 筆，最新 {fa.get('latest')}")
        else:
            lines.append(f"  ⚪ 無新增（最新 {fa.get('latest') or '–'}，共 {fa_tot:,} 筆）")
    else:
        lines.append("  ⚠️ 資料查詢失敗")
    lines.append("")

    # 錯誤欄
    if errors:
        lines.append("【⚠️ 本次錯誤】")
        for e in errors:
            lines.append(f"  ⚠️  {e}")
        lines.append("")

    lines += ["=" * 50, f"🔗 {SPACE_URL}"]
    return "\n".join(lines)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    tz_taipei = pytz.timezone("Asia/Taipei")
    tz_van    = pytz.timezone("America/Vancouver")
    date_tp   = datetime.now(tz_taipei).strftime("%Y-%m-%d")
    time_van  = datetime.now(tz_van).strftime("%Y-%m-%d %H:%M")
    year      = datetime.now().year

    errors = []

    # ── 更新前快照 ────────────────────────────────────────────────────
    print("\n" + "="*55)
    print("取得更新前 Supabase 快照…")
    print("="*55)
    before = take_snapshot(year)

    # ── ① 打者累計 + 每日追蹤（rebas.tw）──────────────────────────────
    print("\n" + "="*55)
    print("① 打者累計數據 & 逐場追蹤（rebas.tw）")
    print("="*55)
    batting_updated  = False
    new_games        = 0
    new_matchup_rows = 0
    try:
        import daily_update
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = daily_update.main()
        output = buf.getvalue()
        print(output)
        batting_updated = (result is True)

        for line in output.splitlines():
            if "打者逐場資料" in line and "新增" in line:
                try:
                    new_games = int(
                        line.split("新增")[1].split("場")[0]
                        .strip().replace("，", "").replace(",", "")
                    )
                except Exception:
                    pass
            if "打席對戰紀錄" in line and "新增" in line:
                try:
                    new_matchup_rows = int(
                        line.split("新增")[1].split("個")[0]
                        .strip().replace("，", "").replace(",", "")
                    )
                except Exception:
                    pass
    except Exception as e:
        errors.append(f"打者資料：{e}")
        print(f"⚠️  打者資料更新失敗：{e}")

    # ── ② 投手逐球 CSV ────────────────────────────────────────────────
    print("\n" + "="*55)
    print("② 投手逐球 CSV（rebas.tw）")
    print("="*55)
    pitch_new_rows     = 0
    pitch_new_pitchers = []
    try:
        import fetch_new_pitchers
        rows_before = csv_row_count(CSV_PATH)
        result = fetch_new_pitchers.main()
        rows_after = csv_row_count(CSV_PATH)
        pitch_new_rows = max(0, rows_after - rows_before)
        if result and isinstance(result, dict):
            pitch_new_pitchers = result.get("pitchers", [])

        if pitch_new_rows > 0:
            print(f"\n新增 {pitch_new_rows:,} 球，執行 enrich_pitches.py…")
            import enrich_pitches
            enrich_pitches.main()
    except Exception as e:
        errors.append(f"投手逐球：{e}")
        print(f"⚠️  投手逐球更新失敗：{e}")

    # ── 更新後快照 ────────────────────────────────────────────────────
    print("\n" + "="*55)
    print("取得更新後 Supabase 快照…")
    print("="*55)
    after = take_snapshot(year)

    # ── 組 Email ──────────────────────────────────────────────────────
    no_update = (not batting_updated and pitch_new_rows == 0 and not errors)

    if no_update:
        msg = (
            f"\n📊 CPBL 每日更新報告"
            f"\n📅 台北 {date_tp}｜溫哥華 {time_van}"
            f"\n\n😴 今日無比賽或資料無更新"
            f"\n🔗 {SPACE_URL}"
        )
    else:
        msg = build_email(
            date_tp, time_van, year,
            before, after,
            batting_updated, pitch_new_rows, pitch_new_pitchers,
            new_games, new_matchup_rows, errors,
        )

    subject = f"📊 CPBL 每日更新報告 {date_tp}"
    print("\n" + "="*55)
    print(f"Email 報告 → {NOTIFY_EMAIL}")
    print("="*55)
    print(msg)
    send_email(subject, msg)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
