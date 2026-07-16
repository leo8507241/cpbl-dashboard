"""
CPBL 每日更新主流程 + LINE Notify 報告
執行順序：
  1. daily_update.py      → CPBL 打者累計 + rebas.tw 進階指標 → Supabase
                            （內部自動呼叫 每日追蹤/run_daily.py）
  2. fetch_new_pitchers.py → 抓新投手逐球資料 → pitcher_pitches.csv
  3. enrich_pitches.py    → 補充打席結果欄位
  4. 發送 LINE Notify 日報
"""
import os
import sys
import time
import requests
from datetime import datetime
import pytz

# 讓 daily_update 內部可以 import 每日追蹤 的模組
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "每日追蹤"))

GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
NOTIFY_EMAIL   = os.environ.get("NOTIFY_EMAIL", GMAIL_USER)
HF_USER        = os.environ.get("HF_USER", "leo88888")
SPACE_URL      = f"https://huggingface.co/spaces/{HF_USER}/cpbl-dashboard"
CSV_PATH       = "pitcher_pitches.csv"


def send_email(subject: str, body: str):
    if not GMAIL_USER or not GMAIL_PASSWORD:
        print("[Email] 未設定 GMAIL_USER / GMAIL_APP_PASSWORD，跳過通知")
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
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.send_message(msg)
        print(f"[Email] ✅ 已寄出至 {NOTIFY_EMAIL}")
    except Exception as e:
        print(f"[Email] ❌ 寄送失敗：{e}")


def csv_row_count(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f) - 1  # 扣掉 header
    except FileNotFoundError:
        return 0


def main():
    tz_taipei = pytz.timezone("Asia/Taipei")
    tz_van    = pytz.timezone("America/Vancouver")
    date_tp   = datetime.now(tz_taipei).strftime("%Y-%m-%d")
    time_van  = datetime.now(tz_van).strftime("%Y-%m-%d %H:%M")

    errors = []

    # ─── ① 打者累計 + 每日追蹤逐場 ──────────────────────────────
    print("\n" + "="*55)
    print("① 打者累計數據 & 逐場追蹤")
    print("="*55)
    batting_updated  = False
    new_games        = 0
    new_matchup_rows = 0
    try:
        import daily_update
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = daily_update.main()
        output = buf.getvalue()
        print(output)
        batting_updated = (result is True)   # main() 只有成功寫入 Supabase 才回傳 True

        # 從 stdout 解析逐場更新數字（run_daily.py 用 print，可被捕捉）
        for line in output.splitlines():
            if "打者逐場資料：新增" in line:
                try:
                    new_games = int(
                        line.split("新增")[1].split("場")[0]
                        .strip().replace("，", "").replace(",", "")
                    )
                except Exception:
                    pass
            if "對戰紀錄：新增" in line or "打席對戰紀錄：新增" in line:
                try:
                    new_matchup_rows = int(
                        line.split("新增")[1].split("場")[0]
                        .strip().replace("，", "").replace(",", "")
                    )
                except Exception:
                    pass
    except Exception as e:
        errors.append(f"打者資料：{e}")
        print(f"⚠️  打者資料更新失敗：{e}")

    # ─── ② 投手逐球 ──────────────────────────────────────────────
    print("\n" + "="*55)
    print("② 投手逐球 CSV")
    print("="*55)
    rows_before        = csv_row_count(CSV_PATH)
    pitch_new_rows     = 0
    pitch_new_pitchers = []
    try:
        import fetch_new_pitchers
        result = fetch_new_pitchers.main()
        rows_after = csv_row_count(CSV_PATH)
        pitch_new_rows = max(0, rows_after - rows_before)
        if result and isinstance(result, dict):
            pitch_new_pitchers = result.get("pitchers", [])

        if pitch_new_rows > 0:
            print(f"\n新增 {pitch_new_rows:,} 球，執行 enrich_pitches.py...")
            import enrich_pitches
            enrich_pitches.main()
    except Exception as e:
        errors.append(f"投手逐球：{e}")
        print(f"⚠️  投手逐球更新失敗：{e}")

    # ─── 組報告 ──────────────────────────────────────────────────
    # batting_updated=True 代表打者累計數據每日都有寫入 Supabase，不算「無更新」
    no_update = (not batting_updated and pitch_new_rows == 0 and not errors)

    if no_update:
        msg = (
            f"\n📊 CPBL 每日更新報告"
            f"\n📅 台北 {date_tp}｜溫哥華 {time_van}"
            f"\n\n😴 今日無比賽，資料無更新"
            f"\n🔗 {SPACE_URL}"
        )
    else:
        lines = [
            "📊 CPBL 每日更新報告",
            f"📅 台北 {date_tp}｜溫哥華 {time_van}",
            "=" * 40,
        ]

        # ① 打者資料
        if batting_updated:
            lines.append("【打者資料更新】")
            lines.append("  ✅ 累計打擊數據：已同步至 Supabase")
            if new_games > 0:
                lines.append(f"  ⚾ 逐場比賽記錄：新增 {new_games} 場")
            else:
                lines.append("  ⚾ 逐場比賽記錄：今日無新場次（或 CPBL 官網未回應）")
            if new_matchup_rows > 0:
                lines.append(f"  📋 打席對戰紀錄：新增 {new_matchup_rows:,} 筆")
            lines.append("  → 影響頁面：被低估打者預測、打者趨勢雷達、")
            lines.append("              投手剋星分析、林立效應分析")
            lines.append("")

        # ② 投手逐球
        if pitch_new_rows > 0:
            lines.append("【投手逐球更新】")
            lines.append(f"  🎯 新增球數：{pitch_new_rows:,} 球")
            if pitch_new_pitchers:
                lines.append(f"  📌 新增投手（{len(pitch_new_pitchers)} 位）：")
                for p in pitch_new_pitchers:
                    lines.append(f"     • {p['name']}（{p['team']}）"
                                 f"  {p['rows']:,} 球  {p['date_range']}")
            lines.append("  → HuggingFace Space 已同步更新")
            lines.append("  → 影響頁面：投手弱點分析")
            lines.append("")

        # 錯誤
        if errors:
            lines.append("【⚠️ 注意事項】")
            for err in errors:
                lines.append(f"  ⚠️ {err}")
            lines.append("")

        lines += ["=" * 40, f"🔗 {SPACE_URL}"]
        msg = "\n".join(lines)

    subject = f"📊 CPBL 每日更新報告 {date_tp}"
    print("\n" + "="*55)
    print(f"Email 報告 → {NOTIFY_EMAIL}")
    print("="*55)
    print(msg)
    send_email(subject, msg)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
