"""
探查 rebas.tw batter game log API 結構，為切換做準備。
執行：python probe_rebas_batter.py
"""
import json
import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
BASE = "https://www.rebas.tw"

def get(path):
    res = requests.get(BASE + path, headers={"User-Agent": UA}, timeout=20)
    res.raise_for_status()
    return res.json()

# ── 1. 抓打者排行榜，取前 3 位做測試 ─────────────────────────────────
print("=== 1. 取打者 UID ===")
leaders = get("/api/seasons/CPBL-2026-oB/leaders?type=batter&section=standard&pa=undefined")
batters = leaders.get("data", [])[:3]
for b in batters:
    pl = b["player"]
    print(f"  {pl['name']}  uid={pl['uniqid']}  team={pl.get('team_abbr')}")

# ── 2. 抓第一位打者的 2026 season logs ───────────────────────────────
if batters:
    pl = batters[0]["player"]
    uid = pl["uniqid"]
    name = pl["name"]
    print(f"\n=== 2. {name} 的逐場紀錄（2026） ===")
    logs = get(f"/api/formal/players/{uid}/seasons/CPBL-2026-oB/logs")
    games = logs.get("data", [])
    print(f"  回傳場次：{len(games)}")

    if games:
        g0 = games[0]
        print(f"\n--- 第一場 game object 所有 key ---")
        print(f"  {list(g0.keys())}")

        print(f"\n--- 第一場完整內容（去掉 PA_list）---")
        summary = {k: v for k, v in g0.items() if k != "PA_list"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        pa_list = g0.get("PA_list", [])
        print(f"\n--- PA 數量：{len(pa_list)} ---")
        if pa_list:
            print("\n--- 第一個 PA 完整結構 ---")
            print(json.dumps(pa_list[0], ensure_ascii=False, indent=2))
            if len(pa_list) > 1:
                print("\n--- 第二個 PA（看看 result 有沒有不同型態）---")
                print(json.dumps(pa_list[1], ensure_ascii=False, indent=2))

        # ── 3. 彙總這一場的打擊資料，模擬 game-level aggregation ───────
        HIT = {"1B", "IH"}
        TWO_B, THREE_B, HR_SET = {"2B"}, {"3B"}, {"HR"}
        BB_SET = {"BB", "IBB"}
        NOT_AB = {"BB", "IBB", "HBP", "SF", "SH", "BUNT"}

        stats = dict(pa=0, ab=0, h=0, b2=0, b3=0, hr=0, bb=0, ibb=0,
                     hbp=0, sf=0, sac=0, so=0)
        rbi_sum = 0
        for pa in pa_list:
            r = pa.get("result", "")
            stats["pa"] += 1
            if r not in NOT_AB:
                stats["ab"] += 1
            if r in HIT or r in TWO_B or r in THREE_B or r in HR_SET:
                stats["h"] += 1
            if r in TWO_B: stats["b2"] += 1
            if r in THREE_B: stats["b3"] += 1
            if r in HR_SET: stats["hr"] += 1
            if r in BB_SET: stats["bb"] += 1
            if r == "IBB": stats["ibb"] += 1
            if r == "HBP": stats["hbp"] += 1
            if r == "SF": stats["sf"] += 1
            if r in {"SH", "BUNT"}: stats["sac"] += 1
            if r in {"SO", "K"}: stats["so"] += 1
            rbi_sum += int(pa.get("RBI") or 0)

        print(f"\n--- 這場彙總（用 PA_list 計算）---")
        print(f"  PA={stats['pa']} AB={stats['ab']} H={stats['h']} "
              f"2B={stats['b2']} 3B={stats['b3']} HR={stats['hr']}")
        print(f"  BB={stats['bb']} IBB={stats['ibb']} HBP={stats['hbp']} "
              f"SF={stats['sf']} SAC={stats['sac']} SO={stats['so']}")
        print(f"  RBI（從 PA.RBI 加總）={rbi_sum}")

        # ── 4. 看 PA 物件有沒有 RBI / R 欄位 ──────────────────────────
        all_keys = set()
        for pa in pa_list:
            all_keys.update(pa.keys())
        print(f"\n--- 所有 PA 欄位 ---")
        print(f"  {sorted(all_keys)}")
