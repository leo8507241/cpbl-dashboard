"""
比對 rebas.tw 打者逐場 vs 現有 CPBL cache，確認數據差異。
執行：python verify_rebas_vs_cpbl.py
"""
import csv
import os
import time
import requests
from collections import defaultdict

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
BASE = "https://www.rebas.tw"
CPBL_CACHE = "每日追蹤/batter_gamelog_cache.csv"

TEAM_ABBR_MAP = {
    "象": "中信兄弟", "鷹": "台鋼雄鷹", "獅": "統一7-ELEVEn獅",
    "龍": "味全龍", "悍": "富邦悍將", "猿": "樂天桃猿",
}

def _get(path):
    r = requests.get(BASE + path, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return r.json()

# ── 1. 讀現有 CPBL cache ──────────────────────────────────────────────
print("=== 讀取 CPBL cache ===")
cpbl_rows = []
with open(CPBL_CACHE, encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        if row["year"] == "2026":
            cpbl_rows.append(row)
print(f"  2026 筆數：{len(cpbl_rows)}")

# 建立 {(game_sno, batter_name): {...}} 查詢表
cpbl_by_key = {}
for r in cpbl_rows:
    key = (r["game_sno"], r["batter_name"])
    cpbl_by_key[key] = r

# ── 2. 從 rebas.tw 抓同一批打者 ──────────────────────────────────────
print("\n=== 從 rebas.tw 抓 2026 打者逐場（抽查前 20 位） ===")
leaders = _get("/api/seasons/CPBL-2026-oB/leaders?type=batter&section=standard&pa=undefined").get("data", [])
print(f"  排行榜共 {len(leaders)} 位打者")

STATS_COLS = ["pa", "ab", "h", "2b", "3b", "hr", "bb", "so", "rbi", "r", "sb"]

matches      = 0
mismatches   = 0
missing      = 0
mismatch_details = []

for entry in leaders[:20]:   # 只抽查前 20 位避免耗時太久
    pl   = entry["player"]
    uid  = pl["uniqid"]
    name = pl["name"]
    team_full = TEAM_ABBR_MAP.get(pl.get("team_abbr", ""), pl.get("team_abbr", ""))

    games = _get(f"/api/formal/players/{uid}/seasons/CPBL-2026-oB/logs").get("data", [])
    for g in games:
        seq = str(g.get("seq", ""))
        key = (seq, name)
        cpbl = cpbl_by_key.get(key)
        if cpbl is None:
            missing += 1
            continue

        b = g.get("batting", {})
        h   = int(b.get("H") or 0)
        h2b = int(b.get("Double") or 0)
        h3b = int(b.get("Triple") or 0)
        hr  = int(b.get("HR") or 0)

        rebas = {
            "pa":  str(int(b.get("PA")  or 0)),
            "ab":  str(int(b.get("AB")  or 0)),
            "h":   str(h),
            "2b":  str(h2b),
            "3b":  str(h3b),
            "hr":  str(hr),
            "bb":  str(int(b.get("BB")  or 0)),
            "so":  str(int(b.get("SO")  or 0)),
            "rbi": str(int(b.get("RBI") or 0)),
            "r":   str(int(b.get("R")   or 0)),
            "sb":  str(int(b.get("SB")  or 0)),
        }

        diffs = {}
        for col in STATS_COLS:
            if rebas.get(col, "0") != cpbl.get(col, "0"):
                diffs[col] = (cpbl.get(col), rebas.get(col))

        if diffs:
            mismatches += 1
            mismatch_details.append({
                "date":   g.get("date", "")[:10],
                "batter": name,
                "seq":    seq,
                "diffs":  diffs,
            })
        else:
            matches += 1

    time.sleep(0.2)
    print(f"  {name}：{len(games)} 場完成")

# ── 3. 輸出比對結果 ──────────────────────────────────────────────────
print("\n" + "="*60)
print(f"比對結果（前 20 位打者，2026 年，共找到 {matches+mismatches} 筆可比對紀錄）")
print(f"  ✅ 完全相符：{matches}")
print(f"  ❌ 有差異：  {mismatches}")
print(f"  ⚪ 僅 rebas.tw 有（CPBL cache 無此場次）：{missing}")
print()

if mismatch_details:
    print("差異明細（最多顯示 20 筆）：")
    for d in mismatch_details[:20]:
        diff_str = "  ".join(f"{col}: CPBL={v[0]} rebas={v[1]}" for col, v in d["diffs"].items())
        print(f"  {d['date']} {d['batter']} seq={d['seq']}  →  {diff_str}")
else:
    print("🎉 所有可比對紀錄完全吻合！")

if missing > 0:
    print(f"\nℹ️  {missing} 筆在 rebas.tw 有但 CPBL cache 沒有（可能是 7/7-7/15 新比賽或 CPBL 回傳不完整）")
print("="*60)
