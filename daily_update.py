"""
CPBL 每日自動更新腳本
功能：
  1. 從 rebas.tw 抓全聯盟累計打擊數據（standard + advanced + new 三個 section）
  2. 計算 wOBA / wRC+ / BABIP / ISO 等進階指標
  3. Upsert 進 Supabase cpbl_batting_2020_2026 表格
  4. 呼叫 每日追蹤/run_daily.py 更新逐場紀錄

用法：
  python daily_update.py          # 只更新當年度（2026）
  python daily_update.py 2025     # 指定年度重新抓
"""
import sys
import os
import logging
from datetime import datetime
from time import sleep

import requests
import pandas as pd
from supabase import create_client

# ── 設定 ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://vxgtgqlqukexpvnnvslf.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'sb_publishable_9DDMVwHFIMCdBxN12jaWkQ_gMeJkRuI')
TABLE = 'cpbl_batting_2020_2026'

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# rebas.tw 球隊縮寫 → 中文全名
TEAM_ABBR_MAP = {
    '象': '中信兄弟',
    '鷹': '台鋼雄鷹',
    '獅': '統一獅',
    '龍': '味全龍',
    '悍': '富邦悍將',
    '猿': '樂天桃猿',
}

# wOBA 線性權重
W_BB, W_HBP, W_1B, W_2B, W_3B, W_HR = 0.69, 0.72, 0.88, 1.24, 1.57, 2.00

# Supabase 表格欄位（排除 id / auto 欄位）
SUPABASE_COLS = {
    '球員', '球隊', '年度', 'g', 'pa', 'ab', 'rbi', 'r', 'h',
    '1B', '2B', '3B', 'hr', 'tb', 'so', 'sb', 'gidp', 'sac', 'sf',
    'bb', 'ibb', 'hbp', 'cs', 'go', 'ao', 'GO/AO', 'SB%', 'obp',
    'slg', 'avg', 'ops', 'babip', 'iso', 'BB%', 'KK%',
    'woba_r', 'whiff_pct', 'gb_pct', 'bip_pct',
    'bb_pct_r', 'k_pct_r', 'babip_r', 'iso_r', 'rc', 'wrc_plus',
}

LOG_PATH = os.path.join(os.path.dirname(__file__), 'daily_update.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)


# ── rebas.tw 打者標準數據（取代 CPBL 官網爬蟲）────────────────────────────
def scrape_rebas_standard(year):
    url = (f'https://www.rebas.tw/api/seasons/CPBL-{year}-oB/leaders'
           f'?type=batter&section=standard&pa=undefined')
    try:
        res = requests.get(url, headers={'User-Agent': UA}, timeout=15)
        if res.status_code != 200:
            log.warning(f'rebas.tw standard HTTP {res.status_code}')
            return []
        data = res.json().get('data', [])
    except Exception as e:
        log.warning(f'rebas.tw standard 失敗：{e}')
        return []

    rows = []
    for p in data:
        pl  = p['player']
        g   = int(p.get('games') or 0)
        pa  = int(p.get('PA') or 0)
        ab  = int(p.get('AB') or 0)
        h   = int(p.get('H') or 0)
        h2b = int(p.get('Double') or 0)
        h3b = int(p.get('Triple') or 0)
        hr  = int(p.get('HR') or 0)
        h1b = max(0, h - h2b - h3b - hr)
        tb  = h1b + 2*h2b + 3*h3b + 4*hr
        bb  = int(p.get('BB') or 0)
        ibb = int(p.get('IBB') or 0)
        hbp = int(p.get('HBP') or 0)
        sf  = int(p.get('SF') or 0)
        sac = int(p.get('SH') or 0)
        so  = int(p.get('SO') or 0)
        rbi = int(p.get('RBI') or 0)
        r   = int(p.get('R') or 0)
        sb  = int(p.get('SB') or 0)
        cs  = int(p.get('CS') or 0)
        gidp = int(p.get('GIDP') or 0)
        avg = float(p.get('AVG') or 0)

        obp_d = ab + bb + hbp + sf
        obp   = round((h + bb + hbp) / obp_d, 3) if obp_d else 0.0
        slg   = round(tb / ab, 3) if ab else 0.0
        ops   = round(obp + slg, 3)
        sb_pct = round(sb / (sb + cs), 3) if (sb + cs) else 0.0

        rows.append({
            '球員': pl['name'],
            '球隊': TEAM_ABBR_MAP.get(pl.get('team_abbr', ''), pl.get('team_abbr', '')),
            '年度': year,
            'g': g, 'pa': pa, 'ab': ab,
            'rbi': rbi, 'r': r, 'h': h,
            '1B': h1b, '2B': h2b, '3B': h3b, 'hr': hr, 'tb': tb,
            'so': so, 'sb': sb,
            'obp': obp, 'slg': slg, 'avg': avg, 'ops': ops,
            'gidp': gidp, 'sac': sac, 'sf': sf,
            'bb': bb, 'ibb': ibb, 'hbp': hbp, 'cs': cs,
            'go': 0, 'ao': 0, 'GO/AO': 0.0, 'SB%': sb_pct,
        })
    log.info(f'rebas.tw standard {year}：{len(rows)} 位打者')
    return rows


# ── rebas.tw 進階指標 ──────────────────────────────────────────────────────
def scrape_rebas(year):
    def _fetch(section):
        url = (f'https://www.rebas.tw/api/seasons/CPBL-{year}-oB/leaders'
               f'?type=batter&section={section}&pa=undefined')
        try:
            res = requests.get(url, headers={'User-Agent': UA}, timeout=15)
            if res.status_code != 200:
                return []
            return res.json().get('data', [])
        except Exception as e:
            log.warning(f'rebas.tw {section} 失敗：{e}')
            return []

    adv_rows, new_rows = [], []
    for p in _fetch('advanced'):
        adv_rows.append({
            '球員': p['player']['name'], '年度': year,
            'woba_r': p.get('wOBA'), 'babip_r': p.get('BABIP'),
            'iso_r': p.get('ISO'),   'whiff_pct': p.get('Whiffp'),
            'gb_pct': p.get('GBp'),  'bb_pct_r': p.get('BBp'),
            'k_pct_r': p.get('Kp'),
        })
    for p in _fetch('new'):
        new_rows.append({
            '球員': p['player']['name'], '年度': year,
            'wrc_plus_r': p.get('wRCplus'), 'wpa': p.get('WPA'),
        })

    df_adv = pd.DataFrame(adv_rows) if adv_rows else pd.DataFrame(columns=['球員', '年度'])
    df_new = pd.DataFrame(new_rows) if new_rows else pd.DataFrame(columns=['球員', '年度'])
    return df_adv.merge(df_new, on=['球員', '年度'], how='outer')


# ── 指標計算 ──────────────────────────────────────────────────────────────
def compute_stats(df):
    # OBP/SLG/AVG 有時是 0.275 格式有時是 275 整數，統一轉小數
    for col in ['obp', 'slg', 'avg', 'ops']:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: x / 1000 if isinstance(x, (int, float)) and x > 1 else x)

    denom = df['ab'] - df['so'] - df['hr'] + df['sf']
    df['babip'] = ((df['h'] - df['hr']) / denom.replace(0, float('nan'))).clip(0, 1).round(3)
    df['iso']   = (df['slg'] - df['avg']).round(3)
    df['BB%']   = (df['bb'] / df['pa'].replace(0, float('nan'))).round(3)
    df['KK%']   = (df['so'] / df['pa'].replace(0, float('nan'))).round(3)

    # 自行計算 wOBA（從原始計數型數據算，不依賴官網欄位）
    h1b = df['1B'].fillna(0).astype(float)
    h2b = df['2B'].fillna(0).astype(float)
    h3b = df['3B'].fillna(0).astype(float)
    hr  = df['hr'].fillna(0).astype(float)
    bb  = df['bb'].fillna(0).astype(float)
    ibb = df['ibb'].fillna(0).astype(float)
    hbp = df['hbp'].fillna(0).astype(float)
    ab  = df['ab'].fillna(0).astype(float)
    sf  = df['sf'].fillna(0).astype(float)
    numer = W_BB*(bb-ibb) + W_HBP*hbp + W_1B*h1b + W_2B*h2b + W_3B*h3b + W_HR*hr
    denom_w = ab + (bb - ibb) + sf + hbp
    df['_woba'] = (numer / denom_w.replace(0, float('nan'))).round(4)

    # 丟棄銀棒指數欄（對分析無用）
    df = df.drop(columns=['_ssa'], errors='ignore')
    return df


def compute_wrc_plus(df):
    WOBA_SCALE = 1.15
    results = []
    for year in df['年度'].unique():
        ydf = df[df['年度'] == year]
        qual = ydf[ydf['pa'] >= 20].dropna(subset=['_woba'])
        if qual.empty or qual['pa'].sum() == 0:
            continue
        lg_woba = (qual['_woba'] * qual['pa']).sum() / qual['pa'].sum()
        lg_r_pa = ydf['r'].sum() / ydf['pa'].sum() if ydf['pa'].sum() > 0 else 0
        for idx, row in ydf.iterrows():
            if pd.notna(row.get('_woba')) and row['pa'] > 0 and lg_r_pa > 0:
                wrc_per_pa = (row['_woba'] - lg_woba) / WOBA_SCALE + lg_r_pa
                wrc_plus = round((wrc_per_pa / lg_r_pa) * 100, 1)
            else:
                wrc_plus = None
            results.append({'index': idx, 'wrc_plus': wrc_plus})
    if results:
        df = df.join(pd.DataFrame(results).set_index('index')['wrc_plus'])
    df = df.drop(columns=['_woba'], errors='ignore')
    return df


# ── Supabase upsert ────────────────────────────────────────────────────────
def _clean_records(df):
    import math
    records = []
    for row in df.to_dict(orient='records'):
        clean = {}
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                clean[k] = None
            elif hasattr(v, 'item'):  # numpy scalar
                item = v.item()
                if isinstance(item, float) and (math.isnan(item) or math.isinf(item)):
                    clean[k] = None
                else:
                    clean[k] = item
            else:
                clean[k] = v
        records.append(clean)
    return records


def upsert_year(sb, year, df_year):
    # 只保留 Supabase 表格中存在的欄位
    valid_cols = [c for c in df_year.columns if c in SUPABASE_COLS]
    records = _clean_records(df_year[valid_cols])
    # 刪舊資料再插入，確保乾淨
    sb.table(TABLE).delete().eq('年度', year).execute()
    for i in range(0, len(records), 200):
        sb.table(TABLE).insert(records[i:i + 200]).execute()
    log.info(f'  {year} 年：上傳 {len(records)} 筆完成')


# ── 主流程 ────────────────────────────────────────────────────────────────
def main():
    target_year = int(sys.argv[1]) if len(sys.argv) > 1 else datetime.now().year
    log.info(f'===== CPBL Daily Update 開始 ({target_year} 年) =====')

    # 1. rebas.tw 標準打擊數據（全聯盟）
    all_rows = scrape_rebas_standard(target_year)
    if not all_rows:
        log.error('rebas.tw standard 資料為空，中止')
        raise RuntimeError('rebas.tw standard 抓取失敗')

    df = pd.DataFrame(all_rows)
    df = compute_stats(df)
    df = compute_wrc_plus(df)

    # 2. rebas.tw 進階指標合併
    df_rebas = scrape_rebas(target_year)
    if not df_rebas.empty:
        df = df.merge(df_rebas, on=['球員', '年度'], how='left')
        log.info(f'rebas.tw advanced/new 合併：{len(df_rebas)} 筆')

    # 3. 存入 Supabase
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    upsert_year(sb, target_year, df)

    # 4. 每日追蹤（逐場紀錄，使用 CPBL 官網；從海外 IP 可能失敗但不中斷主流程）
    gamelog_dir = os.path.join(os.path.dirname(__file__), '每日追蹤')
    if os.path.exists(os.path.join(gamelog_dir, 'run_daily.py')):
        log.info('執行 每日追蹤/run_daily.py...')
        sys.path.insert(0, gamelog_dir)
        try:
            import importlib
            rd = importlib.import_module('run_daily')
            rd.main()
        except Exception as e:
            log.error(f'每日追蹤失敗：{e}')

    log.info(f'===== Daily Update 完成 =====\n')
    return True


if __name__ == '__main__':
    main()
