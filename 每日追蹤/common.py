"""
兩個每日追蹤專案（打者趨勢雷達、投手剋星分析）共用的爬蟲與 Supabase 工具。
沿用 樂天林立分析/scrape_games.py 的 session/token/idempotent cache 模式，
差別是這裡不篩球隊——CPBL 賽程 API 本來就回傳當天所有球隊的比賽。
"""
import csv
import os
from datetime import datetime
from time import sleep

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
KIND_CODE = "A"  # 一軍例行賽
START_YEAR_DEFAULT = 2025  # 只從最近一個完整年度開始建表（回溯範圍決定）

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://vxgtgqlqukexpvnnvslf.supabase.co")
# 使用者確認這組 key（跟 app.py 用的同一組）在這個專案裡沒有開 RLS 擋寫入，upsert 實測成功，
# 所以直接當預設值；env var SUPABASE_WRITE_KEY 可覆蓋，換專案/换成有 RLS 限制時用得到。
SUPABASE_WRITE_KEY = os.environ.get("SUPABASE_WRITE_KEY", "sb_publishable_9DDMVwHFIMCdBxN12jaWkQ_gMeJkRuI")


def get_supabase_client():
    if not SUPABASE_WRITE_KEY:
        return None
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_WRITE_KEY)


def upsert_batches(client, table, rows, on_conflict, batch_size=500):
    if client is None or not rows:
        return
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        client.table(table).upsert(batch, on_conflict=on_conflict).execute()


def get_token(session, url):
    page = session.get(url, headers={"User-Agent": UA}, timeout=20)
    soup = BeautifulSoup(page.text, "html.parser")
    inp = soup.find("input", {"name": "__RequestVerificationToken"})
    form_token = inp.get("value", "") if inp else ""
    cookie_token = session.cookies.get("__RequestVerificationToken")
    return form_token, f"{cookie_token}:{form_token}"


def fetch_year_games(session, year):
    """抓某一年『所有球隊』的例行賽比賽（含比分）——不像 scrape_games.py 篩單一球隊。"""
    form_token, combined = get_token(session, "https://www.cpbl.com.tw/schedule")
    res = session.post(
        "https://www.cpbl.com.tw/schedule/getgamedatas",
        headers={
            "User-Agent": UA,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.cpbl.com.tw",
            "Referer": "https://www.cpbl.com.tw/schedule",
            "RequestVerificationToken": combined,
        },
        data={"calendar": f"{year}/01/01", "location": "", "kindCode": KIND_CODE},
        timeout=20,
    )
    res.raise_for_status()
    j = res.json()
    if not j.get("Success"):
        return []
    import json
    return json.loads(j["GameDatas"])


def fetch_box(session, year, game_sno):
    """抓單場 boxscore：回傳 (game_detail, battings, livelog)。"""
    import json
    box_url = f"https://www.cpbl.com.tw/box?year={year}&kindCode={KIND_CODE}&gameSno={game_sno}"
    form_token, combined = get_token(session, box_url)
    res = session.post(
        "https://www.cpbl.com.tw/box/getlive",
        headers={
            "User-Agent": UA,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.cpbl.com.tw",
            "Referer": box_url,
            "RequestVerificationToken": combined,
        },
        data={
            "__RequestVerificationToken": form_token,
            "GameSno": str(game_sno), "KindCode": KIND_CODE, "Year": str(year),
            "PrevOrNext": "", "PresentStatus": "",
        },
        timeout=20,
    )
    res.raise_for_status()
    j = res.json()
    if not j.get("Success"):
        return None
    game_detail = json.loads(j["GameDetailJson"])
    battings = json.loads(j["BattingJson"])
    livelog = json.loads(j["LiveLogJson"])
    return game_detail, battings, livelog


def iter_finished_games(start_year, end_year, known_game_keys):
    """
    依序 yield 還沒抓過、且已經打完的比賽 (game_meta, game_detail, battings, livelog)。
    known_game_keys：set of (year_str, game_sno_str)，已抓過的比賽會被跳過。

    ⚠️ 每一年都重開一個新的 requests.Session()（不重用呼叫端傳進來的 session）：
    實測發現同一個 session 連續掃過一整年（幾百次請求）之後，CPBL 官網會開始對後續年度的
    請求回 500，換一個全新 session（等於拿新的 cookie/CSRF token）重新握手就能恢復正常，
    這是目前找到最簡單可靠的繞過方式。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    for year in range(start_year, end_year + 1):
        print(f"=== 抓 {year} 年賽程 ===")
        year_session = requests.Session()
        try:
            games = fetch_year_games(year_session, year)
        except Exception as e:
            print(f"  年度賽程抓取失敗：{e}")
            continue
        print(f"  總場次：{len(games)}")

        for g in games:
            key = (str(year), str(g["GameSno"]))
            if key in known_game_keys:
                continue
            game_date = g["GameDate"][:10]
            if game_date > today:
                continue  # 還沒開打

            try:
                sleep(0.6)
                result = fetch_box(year_session, year, g["GameSno"])
                if result is None:
                    continue
                game_detail, battings, livelog = result
                gd = game_detail[0] if game_detail else {}
                if gd.get("VisitingTotalScore") is None or gd.get("HomeTotalScore") is None:
                    continue  # 比賽未完成或資料不完整
                yield g, game_detail, battings, livelog
                known_game_keys.add(key)
            except Exception as e:
                print(f"  第 {g['GameSno']} 場失敗：{e}")
                continue


def load_cache(cache_path, key_fields):
    if not os.path.exists(cache_path):
        return {}
    cache = {}
    with open(cache_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = tuple(row[k] for k in key_fields)
            cache[key] = row
    return cache


def save_cache(cache_path, fields, rows, sort_key):
    rows = sorted(rows, key=sort_key)
    with open(cache_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
