import os
import streamlit as st
import pandas as pd
from supabase import create_client
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 支援 HF Spaces secrets（st.secrets）或環境變數，本地開發 fallback 到 hardcoded
def _get_secret(key, default):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

SUPABASE_URL = _get_secret('SUPABASE_URL', 'https://vxgtgqlqukexpvnnvslf.supabase.co')
SUPABASE_KEY = _get_secret('SUPABASE_KEY', 'sb_publishable_9DDMVwHFIMCdBxN12jaWkQ_gMeJkRuI')

TEAM_COLORS = {
    '中信兄弟': '#C8102E',
    '台鋼雄鷹': '#1A1A1A',
    '台鋼隊':   '#555555',  # 台鋼二軍時期（2022 以前）
    '統一獅':   '#FFB81C',
    '味全龍':   '#005DAA',
    '富邦悍將': '#003DA5',
    '樂天桃猿': '#BF0D3E',
}

@st.cache_data
def load_data():
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Supabase 預設只回傳 1000 筆，用分頁把全部資料都抓回來
    all_data = []
    page_size = 1000
    offset = 0
    while True:
        result = (supabase.table('cpbl_batting_2020_2026')
                  .select('*')
                  .range(offset, offset + page_size - 1)
                  .execute())
        all_data.extend(result.data)
        if len(result.data) < page_size:
            break
        offset += page_size

    df = pd.DataFrame(all_data)

    # 年度一定要是 int，否則 df['年度'] == 2026 比較時會因型別不符全部失敗
    if '年度' in df.columns:
        df['年度'] = pd.to_numeric(df['年度'], errors='coerce').astype('Int64')

    # 其餘數字欄位統一轉 float
    float_cols = ['pa', 'avg', 'obp', 'slg', 'ops',
                  'babip', 'iso', 'wrc_plus', 'BB%', 'KK%', 'woba_r',
                  'bb_pct', 'k_pct',
                  'hr', 'bb', 'so', 'r', 'rbi', 'g', 'ab', 'h', 'sf']
    for c in float_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')

    # 如果 Supabase 表格的欄位名稱是 BB% / KK%（不是 bb_pct / k_pct），
    # 或者這些欄位根本不存在，就從原始數據自動算出來
    if 'bb_pct' not in df.columns:
        if 'BB%' in df.columns:
            df['bb_pct'] = df['BB%']                       # 欄位換名就好
        elif 'bb' in df.columns and 'pa' in df.columns:
            df['bb_pct'] = (df['bb'] / df['pa'].replace(0, float('nan'))).round(3)

    if 'k_pct' not in df.columns:
        if 'KK%' in df.columns:
            df['k_pct'] = df['KK%']
        elif 'so' in df.columns and 'pa' in df.columns:
            df['k_pct'] = (df['so'] / df['pa'].replace(0, float('nan'))).round(3)

    if 'iso' not in df.columns and 'slg' in df.columns and 'avg' in df.columns:
        df['iso'] = (df['slg'] - df['avg']).round(3)

    if 'babip' not in df.columns and all(c in df.columns for c in ['h', 'hr', 'ab', 'so', 'sf']):
        denom = df['ab'] - df['so'] - df['hr'] + df['sf']
        df['babip'] = ((df['h'] - df['hr']) / denom.replace(0, float('nan'))).clip(0, 1).round(3)

    return df

df = load_data()

# 資料品質防護：台鋼雄鷹 2024 才加入一軍，移除不合理的歷史記錄
df = df[~((df['球隊'].isin(['台鋼雄鷹', '台鋼隊'])) & (df['年度'] < 2024))]
df = df[df['年度'] >= 2021]
# 移除「魔鷹」虛假球員紀錄
df = df[df['球員'] != '魔鷹']
df = df.reset_index(drop=True)

st.set_page_config(page_title='被低估打者預測', layout='wide')
st.title('⚾ 被低估打者預測')
st.caption('打擊率說謊，誰才是中職真正最有價值的打者？')

# ── 側邊欄篩選器 ──────────────────────────────────────────────
st.sidebar.header('篩選條件')
year   = st.sidebar.selectbox('年度', sorted(df['年度'].dropna().unique().astype(int), reverse=True))
min_pa = st.sidebar.slider('最低打席數', 0, 500, 100, step=10)

data = df[(df['年度'] == year) & (df['pa'] >= min_pa)].copy()
wrc_threshold = 110

avg_median = data['avg'].median()
if pd.isna(avg_median):
    avg_median = 0.280  # 中職歷年約略平均值

st.caption(f'{year} 年，打席 ≥ {min_pa}，共 {len(data)} 位球員')

# ── 側邊欄：資料診斷（幫助確認資料是否正確讀取）──────────────
with st.sidebar.expander('資料診斷'):
    st.write(f'總筆數：{len(df)}')
    st.write(f'已篩選：{len(data)} 筆')
    if len(data) > 0:
        st.write(f'avg：{data["avg"].min():.3f} ~ {data["avg"].max():.3f}（非空：{data["avg"].notna().sum()}）')
        st.write(f'wrc_plus：{data["wrc_plus"].min():.1f} ~ {data["wrc_plus"].max():.1f}（非空：{data["wrc_plus"].notna().sum()}）')
        st.write(f'babip：{data["babip"].min():.3f} ~ {data["babip"].max():.3f}（非空：{data["babip"].notna().sum()}）')

# ── 圖一：wRC+ 排行榜 ─────────────────────────────────────────
st.subheader('① wRC+ 排行榜：誰最強？')

bar_data = data.dropna(subset=['wrc_plus']).sort_values('wrc_plus', ascending=True)
if len(bar_data) > 0:
    fig1 = px.bar(
        bar_data,
        x='wrc_plus', y='球員',
        orientation='h',
        color='球隊',
        color_discrete_map=TEAM_COLORS,
        title=f'{year} 中職打者 wRC+ 排行（聯盟平均 = 100）',
        labels={'wrc_plus': 'wRC+', '球員': ''},
        hover_data={'pa': True, 'avg': ':.3f', 'obp': ':.3f', 'ops': ':.3f', 'wrc_plus': ':.1f'},
        height=max(500, len(bar_data) * 22),
    )
    fig1.add_vline(x=100, line_dash='dash', line_color='gray',
                   annotation_text='聯盟平均(100)', annotation_position='top right')
    fig1.add_vline(x=130, line_dash='dot', line_color='gold',
                   annotation_text='精英門檻(130)', annotation_position='top right')
    fig1.update_layout(plot_bgcolor='white', xaxis_range=[50, None])
    st.plotly_chart(fig1, use_container_width=True)
else:
    st.info('此篩選條件下沒有 wRC+ 資料，請確認資料是否正確上傳到 Supabase。')

# ── 圖二：打擊率 vs wRC+ ──────────────────────────────────────
st.subheader('② 打擊率 vs wRC+：為什麼他被忽視？')

def classify(row):
    hi_wrc = row['wrc_plus'] >= wrc_threshold
    hi_avg = row['avg'] >= avg_median
    if hi_wrc and hi_avg:     return '名副其實的強打者'
    if hi_wrc and not hi_avg: return '被低估的隱藏寶藏 ⭐'
    if not hi_wrc and hi_avg: return '被高估（注意）'
    return '確實普通'

data2 = data.dropna(subset=['wrc_plus', 'avg']).copy()
if len(data2) > 0:
    data2['類型'] = data2.apply(classify, axis=1)
    fig2 = px.scatter(
        data2,
        x='avg', y='wrc_plus',
        color='類型',
        size='pa', size_max=30,
        hover_name='球員',
        hover_data={'球隊': True, 'avg': ':.3f', 'obp': ':.3f',
                    'babip': ':.3f', 'wrc_plus': ':.1f', '類型': False, 'pa': True},
        title='打擊率 vs wRC+：誰被傳統數據低估了？',
        labels={'avg': '打擊率 (AVG)', 'wrc_plus': 'wRC+（聯盟平均=100）'},
        color_discrete_map={
            '名副其實的強打者':     '#2ECC71',
            '被低估的隱藏寶藏 ⭐': '#E74C3C',
            '被高估（注意）':       '#F39C12',
            '確實普通':             '#BDC3C7',
        },
        height=600,
    )
    fig2.add_hline(y=wrc_threshold, line_dash='dot', line_color='gray',
                   annotation_text=f'wRC+ = {wrc_threshold}')
    fig2.add_vline(x=avg_median, line_dash='dot', line_color='gray',
                   annotation_text=f'AVG 中位數 ({avg_median:.3f})')
    for _, row in data2[data2['類型'] == '被低估的隱藏寶藏 ⭐'].iterrows():
        fig2.add_annotation(
            x=row['avg'], y=row['wrc_plus'], text=row['球員'],
            showarrow=True, arrowhead=2, ax=25, ay=-25,
            font=dict(size=11, color='#E74C3C'),
        )
    fig2.update_layout(plot_bgcolor='white')
    st.plotly_chart(fig2, use_container_width=True)
else:
    st.info('此篩選條件下沒有同時具備打擊率和 wRC+ 的資料。')

# ── 圖三：BABIP 分析 ──────────────────────────────────────────
st.subheader('③ BABIP 分析：明年誰會反彈？')

def babip_label(b):
    if pd.isna(b):          return '正常 BABIP .270~.330'
    if b > 0.330:           return '幸運 BABIP > .330（預測明年退步）'
    if b < 0.270:           return '倒楣 BABIP < .270（預測明年反彈）'
    return '正常 BABIP .270~.330'

data3 = data.dropna(subset=['babip']).copy()
if len(data3) > 0:
    # 用 astype(str) 確保 BABIP狀態 一定是字串型別（data3 為空時 apply 會保留 float dtype）
    data3['BABIP狀態'] = data3['babip'].apply(babip_label).astype(str)

    fig3 = px.scatter(
        data3,
        x='babip', y='avg',
        color='BABIP狀態',
        size='pa', size_max=30,
        hover_name='球員',
        hover_data={'球隊': True, 'avg': ':.3f', 'babip': ':.3f',
                    'wrc_plus': ':.1f', 'BABIP狀態': False},
        title='BABIP 分析：誰的打擊率明年會改變？',
        labels={'babip': 'BABIP（場內球安打率）', 'avg': '打擊率 (AVG)'},
        color_discrete_map={
            '幸運 BABIP > .330（預測明年退步）': '#E74C3C',
            '倒楣 BABIP < .270（預測明年反彈）': '#3498DB',
            '正常 BABIP .270~.330':              '#BDC3C7',
        },
        height=600,
    )
    fig3.add_vline(x=0.290, line_dash='dash', line_color='black',
                   annotation_text='聯盟平均 BABIP (.290)')
    rebounds = data3[
        (data3['BABIP狀態'].str.startswith('倒楣')) & (data3['wrc_plus'] >= 100)
    ]
    for _, row in rebounds.iterrows():
        fig3.add_annotation(
            x=row['babip'], y=row['avg'], text=f"{row['球員']} ↑",
            showarrow=True, arrowhead=2, ax=-30, ay=-20,
            font=dict(size=11, color='#3498DB'),
        )
    fig3.update_layout(plot_bgcolor='white')
    st.plotly_chart(fig3, use_container_width=True)
else:
    st.info('此篩選條件下沒有 BABIP 資料。')

# ── 圖四：三合一英雄圖 ────────────────────────────────────────
st.subheader('④ 三合一英雄圖：終極被低估的明日之星')

if len(data3) > 0:
    y_max = data3['wrc_plus'].max() if data3['wrc_plus'].notna().any() else 150

    fig4 = px.scatter(
        data3,
        x='avg', y='wrc_plus',
        color='BABIP狀態',
        size='pa', size_max=35,
        hover_name='球員',
        hover_data={'球隊': True, 'avg': ':.3f', 'obp': ':.3f',
                    'babip': ':.3f', 'wrc_plus': ':.1f', 'BABIP狀態': False, 'pa': True},
        title='🏆 三合一英雄圖：打擊率 × wRC+ × BABIP 運氣',
        labels={'avg': '打擊率 (AVG)', 'wrc_plus': 'wRC+（聯盟平均=100）'},
        color_discrete_map={
            '幸運 BABIP > .330（預測明年退步）': '#E74C3C',
            '倒楣 BABIP < .270（預測明年反彈）': '#2980B9',
            '正常 BABIP .270~.330':              '#95A5A6',
        },
        height=650,
    )
    fig4.add_hline(y=100, line_dash='dash', line_color='lightgray')
    fig4.add_vline(x=avg_median, line_dash='dash', line_color='lightgray')
    fig4.add_annotation(x=avg_median * 0.97, y=y_max, text='← 被低估區',
                        showarrow=False, font=dict(size=12, color='#2980B9'))
    fig4.add_annotation(x=avg_median * 1.02, y=y_max, text='真強區 →',
                        showarrow=False, font=dict(size=12, color='#27AE60'))
    gems = data3[
        (data3['BABIP狀態'].str.startswith('倒楣')) &
        (data3['wrc_plus'] >= 105) &
        (data3['avg'] < avg_median)
    ]
    for _, row in gems.iterrows():
        fig4.add_annotation(
            x=row['avg'], y=row['wrc_plus'],
            text=f"⭐ {row['球員']}",
            showarrow=True, arrowhead=2, ax=30, ay=-30,
            font=dict(size=12, color='#2980B9'),
            bgcolor='rgba(255,255,255,0.8)',
        )
    fig4.update_layout(
        plot_bgcolor='white',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
    )
    st.plotly_chart(fig4, use_container_width=True)
else:
    st.info('此篩選條件下沒有足夠資料繪製三合一英雄圖。')

st.markdown('---')
st.caption('⭐ 三合一英雄圖標記的球員 = 真實能力強（wRC+ ≥ 105）＋ 打擊率低於中位數 ＋ BABIP 今年偏低 → 明年最可能反彈的隱藏寶藏')

# ── ⑤ BABIP 反彈理論驗證 ────────────────────────────────────────
st.markdown('---')
st.subheader('⑤ BABIP 反彈理論驗證')

_HAS = all(c in df.columns for c in ['bb_pct', 'k_pct', 'iso', 'babip'])

if not _HAS:
    st.warning('⚠️ 資料表缺少 bb_pct、k_pct 或 iso 欄位，BABIP 驗證無法執行。請確認 Supabase 資料已包含這些欄位。')
else:
    _avail_yrs = sorted(df['年度'].dropna().unique().astype(int))
    _max_yr    = max(_avail_yrs)

    with st.expander('⚙️ 調整驗證參數', expanded=True):
        vc1, vc2, vc3 = st.columns(3)
        with vc1:
            v_babip  = st.slider('BABIP 倒楣門檻', 0.220, 0.300, 0.270, 0.005, format='%.3f', key='v_babip')
            v_min_pa = st.slider('最低打席數', 50, 200, 100, 10, key='v_pa')
        with vc2:
            v_bb_min = st.slider('BB% 最低門檻', 0.040, 0.150, 0.080, 0.005, format='%.3f', key='v_bb')
            v_k_max  = st.slider('K% 最高門檻', 0.150, 0.350, 0.220, 0.005, format='%.3f', key='v_k')
        with vc3:
            v_iso    = st.checkbox('加入 ISO ≥ 0.100（過濾假性選球眼）', value=False, key='v_iso')
            v_bounce = st.slider('反彈成功門檻（AVG提升）', 0.010, 0.040, 0.015, 0.005, format='%.3f', key='v_bounce')

    # 年度直接跟左側邊欄的年度選擇同步，不需要額外下拉選單
    v_year  = int(year)
    is_pred = (v_year + 1) not in _avail_yrs

    if is_pred:
        st.caption(f'🔮 **預測模式** — 篩選 {v_year} 年倒楣球員，預測 {v_year+1} 年最可能反彈的候選人')
    else:
        nxt_label = f'{v_year+1} 年（進行中）' if v_year + 1 == _max_yr else f'{v_year+1} 年'
        st.caption(f'📊 **回測模式** — {v_year} 年篩選到的倒楣球員，看他們 {nxt_label} 是否真的反彈了')

    def _v_find(yr, use_iso):
        yr_df = df[(df['年度'] == yr) & (df['pa'] >= v_min_pa)].copy()
        cond  = (yr_df['babip'] < v_babip) & (yr_df['bb_pct'] >= v_bb_min) & (yr_df['k_pct'] <= v_k_max)
        if use_iso:
            cond = cond & (yr_df['iso'] >= 0.100)
        return yr_df[cond]

    def _v_diag(curr, nxt):
        reasons = []
        if pd.notna(curr['k_pct']) and pd.notna(nxt['k_pct']) and nxt['k_pct'] - curr['k_pct'] > 0.030:
            reasons.append(f"K%大增({curr['k_pct']:.1%}→{nxt['k_pct']:.1%})")
        if pd.notna(curr['bb_pct']) and pd.notna(nxt['bb_pct']) and curr['bb_pct'] - nxt['bb_pct'] > 0.030:
            reasons.append(f"BB%大降({curr['bb_pct']:.1%}→{nxt['bb_pct']:.1%})")
        if pd.notna(curr['iso']) and pd.notna(nxt['iso']) and curr['iso'] - nxt['iso'] > 0.030:
            reasons.append(f"ISO大降({curr['iso']:.3f}→{nxt['iso']:.3f})")
        if pd.notna(nxt['babip']) and nxt['babip'] < 0.275:
            reasons.append(f"BABIP仍低({nxt['babip']:.3f})")
        if nxt['pa'] < curr['pa'] * 0.60:
            reasons.append(f"打席大減({int(curr['pa'])}→{int(nxt['pa'])})")
        return '、'.join(reasons) if reasons else '原因不明'

    def _v_run(use_iso):
        rows = []
        for _, curr in _v_find(v_year, use_iso).iterrows():
            name = curr['球員']
            rec  = {
                '球員': name, '球隊': curr['球隊'],
                '倒楣年度': v_year,
                '當年PA':    int(curr['pa']),
                '當年AVG':   round(float(curr['avg']),     3) if pd.notna(curr['avg'])     else None,
                '當年BABIP': round(float(curr['babip']),   3),
                '當年BB%':   round(float(curr['bb_pct']),  3) if pd.notna(curr['bb_pct'])  else None,
                '當年K%':    round(float(curr['k_pct']),   3) if pd.notna(curr['k_pct'])   else None,
                '當年ISO':   round(float(curr['iso']),     3) if pd.notna(curr['iso'])     else None,
                '當年wRC+':  round(float(curr['wrc_plus']),1) if pd.notna(curr['wrc_plus']) else None,
            }
            if is_pred:
                rec['結果'] = '🔮 預測反彈候選'
            else:
                rec['反彈年度'] = v_year + 1
                nxt_df = df[(df['年度'] == v_year + 1) & (df['球員'] == name)]
                if len(nxt_df) == 0:
                    rec.update({'結果': '⚠️ 隔年無出賽紀錄', '原因診斷': 'N/A',
                                'AVG變化': None, 'BABIP變化': None, 'wRC+變化': None})
                else:
                    nxt = nxt_df.iloc[0]
                    if nxt['pa'] < v_min_pa:
                        rec.update({'隔年PA': int(nxt['pa']),
                                    '結果': f'⚠️ 隔年打席不足（{int(nxt["pa"])}席，需≥{v_min_pa}）',
                                    '原因診斷': '傷病/出賽不足',
                                    'AVG變化': None, 'BABIP變化': None, 'wRC+變化': None})
                    else:
                        a_chg = (float(nxt['avg'])      - float(curr['avg']))      if pd.notna(nxt['avg'])      and pd.notna(curr['avg'])      else None
                        b_chg = (float(nxt['babip'])    - float(curr['babip']))    if pd.notna(nxt['babip'])    and pd.notna(curr['babip'])    else None
                        w_chg = (float(nxt['wrc_plus']) - float(curr['wrc_plus'])) if pd.notna(nxt['wrc_plus']) and pd.notna(curr['wrc_plus']) else None
                        babip_ok = (float(nxt['babip']) >= 0.270) if pd.notna(nxt['babip']) else None
                        is_partial = (v_year + 1 == _max_yr)
                        if a_chg is None:
                            status = '⚠️ 資料不完整'
                        elif a_chg >= v_bounce and (w_chg is None or w_chg >= 0):
                            status = '✅ 反彈成功'
                        elif a_chg >= v_bounce and w_chg is not None and w_chg < 0:
                            status = '➡️ 小幅改善'
                        elif a_chg >= 0:
                            status = '➡️ 小幅改善'
                        else:
                            status = '❌ 沒有反彈'
                        if status == '✅ 反彈成功':
                            diag = '反彈成功，AVG 與 wRC+ 雙雙改善'
                        elif a_chg is not None and a_chg >= v_bounce and w_chg is not None and w_chg < 0:
                            diag = f'打擊率達標但 wRC+ 下降（{w_chg:+.1f}），製造得分能力未完全回升'
                        else:
                            diag = _v_diag(curr, nxt)
                        rec.update({
                            '隔年PA':    int(nxt['pa']),
                            '隔年AVG':   round(float(nxt['avg']),     3) if pd.notna(nxt['avg'])     else None,
                            '隔年BABIP': round(float(nxt['babip']),   3) if pd.notna(nxt['babip'])   else None,
                            '隔年BB%':   round(float(nxt['bb_pct']),  3) if pd.notna(nxt['bb_pct'])  else None,
                            '隔年K%':    round(float(nxt['k_pct']),   3) if pd.notna(nxt['k_pct'])   else None,
                            '隔年wRC+':  round(float(nxt['wrc_plus']),1) if pd.notna(nxt['wrc_plus']) else None,
                            'AVG變化':   round(a_chg, 3) if a_chg is not None else None,
                            'BABIP變化': round(b_chg, 3) if b_chg is not None else None,
                            'BABIP回正': '✅ 是（≥.270）' if babip_ok else ('❌ 否（仍<.270）' if babip_ok is not None else None),
                            'wRC+變化':  round(w_chg, 1) if w_chg is not None else None,
                            '結果':      status,
                            '備註':      f'⚠️ {v_year+1} 進行中' if is_partial else '',
                            '原因診斷':  diag,
                        })
            rows.append(rec)
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    dv1 = _v_run(False)
    dv2 = _v_run(True) if v_iso else None

    if len(dv1) == 0:
        st.info('找不到符合條件的球員，請調整參數（放寬 BABIP 門檻或降低 BB% 要求）。')
    else:
        _ac = list(dv1.columns)
        _sc = lambda cols: [c for c in cols if c in _ac]

        if is_pred:
            # ── 預測模式 ──────────────────────────────────────────
            pc1, pc2 = st.columns(2)
            pc1.metric('反彈預測候選人數', f'{len(dv1)} 人')
            pc2.metric('篩選條件',
                       f'BABIP < {v_babip:.3f}｜BB% ≥ {v_bb_min:.1%}｜K% ≤ {v_k_max:.1%}')
            if v_iso and dv2 is not None and len(dv2) > 0:
                st.info(f'加入 ISO ≥ 0.100 條件後：{len(dv2)} 人（更嚴格篩選）')

            st.markdown(f'### 圖① {v_year} 年倒楣球員 — {v_year+1} 年反彈預測候選')
            pred_plot = (dv2 if (v_iso and dv2 is not None and len(dv2) > 0) else dv1)
            pred_plot = pred_plot.dropna(subset=['當年wRC+']).sort_values('當年wRC+', ascending=True)
            if len(pred_plot) > 0:
                fig_pred = px.bar(
                    pred_plot, x='當年wRC+', y='球員', orientation='h',
                    color='當年BABIP',
                    color_continuous_scale='RdBu_r',
                    range_color=[0.200, v_babip],
                    title=f'{v_year} 年倒楣球員 → {v_year+1} 年反彈候選（依 wRC+ 強弱排序）',
                    labels={'當年wRC+': f'{v_year} 年 wRC+（聯盟平均=100）', '球員': ''},
                    hover_data={'當年BABIP': ':.3f', '當年AVG': ':.3f',
                                '當年BB%': ':.3f', '當年K%': ':.3f', '當年PA': True},
                    height=max(350, len(pred_plot) * 40),
                )
                fig_pred.add_vline(x=100, line_dash='dash', line_color='gray',
                                   annotation_text='聯盟平均(100)')
                fig_pred.update_layout(plot_bgcolor='white')
                st.plotly_chart(fig_pred, use_container_width=True)
                st.caption(f'📌 顏色越深（偏紅）= BABIP 越低 = 越倒楣。wRC+ 越高 = 技術越強。'
                           f'兩者兼具者在 {v_year+1} 年最有反彈潛力。')

            st.markdown('### 📋 反彈預測候選名單')
            pred_cols = ['球員', '球隊', '當年PA', '當年AVG', '當年BABIP',
                         '當年BB%', '當年K%', '當年ISO', '當年wRC+']
            st.dataframe(
                dv1[_sc(pred_cols)].sort_values('當年wRC+', ascending=False).reset_index(drop=True),
                use_container_width=True,
            )
            st.caption(f'📌 依 {v_year} 年 wRC+ 由高到低排序。wRC+ 高 + BABIP 低 + BB% 高 = 最值得關注的反彈候選。')

        else:
            # ── 回測模式 ──────────────────────────────────────────
            def _calc(dvx):
                j = dvx[~dvx['結果'].str.startswith('⚠️', na=False)]
                t = len(j)
                if t == 0:
                    return 0, 0, 0, 0
                return (t,
                        len(j[j['結果'] == '✅ 反彈成功']),
                        len(j[j['結果'].str.startswith('➡️', na=False)]),
                        len(j[j['結果'].str.startswith('❌', na=False)]))

            t1, s1, p1, f1 = _calc(dv1)

            st.markdown('### 📊 驗證結果摘要')
            if v_iso and dv2 is not None and len(dv2) > 0:
                t2, s2, p2, f2 = _calc(dv2)
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric('篩選球員數', f'{t1} 人 → {t2} 人', help='原始版 vs 加 ISO 條件後')
                _delta = (f'+{(s2/t2-s1/t1)*100:.1f}%' if t2 and s2/t2 >= s1/t1
                          else (f'{(s2/t2-s1/t1)*100:.1f}%' if t2 else None))
                mc2.metric('✅ 反彈率',
                           f'{s1/t1*100:.1f}% → {s2/t2*100:.1f}%' if t2 else f'{s1/t1*100:.1f}%',
                           delta=_delta)
                mc3.metric('✅+➡️ 有改善',
                           f'{(s1+p1)/t1*100:.1f}% → {(s2+p2)/t2*100:.1f}%' if t2
                           else f'{(s1+p1)/t1*100:.1f}%')
            else:
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric('有效案例', f'{t1} 人')
                mc2.metric('✅ 反彈成功', f'{s1} 人',
                           delta=f'{s1/t1*100:.1f}%' if t1 else None)
                mc3.metric('➡️ 小幅改善', f'{p1} 人',
                           delta=f'{p1/t1*100:.1f}%' if t1 else None)
                mc4.metric('❌ 沒有反彈', f'{f1} 人',
                           delta=f'-{f1/t1*100:.1f}%' if t1 else None, delta_color='inverse')

            # ── 圖①：倒楣年 → 反彈年 軌跡圖 ────────────────────
            st.markdown(f'### 圖① {v_year} → {v_year+1} 年：BABIP 與打擊率的實際軌跡')
            _slope_cols = ['AVG變化', '隔年BABIP', '隔年AVG']
            if all(c in dv1.columns for c in _slope_cols):
                plot_dv = dv1[
                    dv1['AVG變化'].notna() & dv1['隔年BABIP'].notna() & dv1['隔年AVG'].notna() &
                    ~dv1['結果'].str.startswith('⚠️', na=False)
                ].copy()
            else:
                plot_dv = pd.DataFrame()
            if len(plot_dv) > 0:
                color_map = {'✅ 反彈成功': '#2ECC71', '➡️ 小幅改善': '#F39C12', '❌ 沒有反彈': '#E74C3C'}
                fig_v1 = make_subplots(
                    rows=1, cols=2,
                    subplot_titles=[f'BABIP 軌跡（{v_year}→{v_year+1}）',
                                    f'打擊率 AVG 軌跡（{v_year}→{v_year+1}）'],
                    horizontal_spacing=0.18,
                )
                legend_added = set()
                for _, row in plot_dv.iterrows():
                    color    = color_map.get(row['結果'], '#95A5A6')
                    name_lbl = row['球員']
                    show_leg = row['結果'] not in legend_added
                    legend_added.add(row['結果'])
                    w_str = (f"  wRC+: {row['wRC+變化']:+.1f}"
                             if pd.notna(row.get('wRC+變化')) else '')
                    # 用整數年份做 x 軸（數值軸，相容性最好）；hover 顯示球員與數值
                    fig_v1.add_trace(go.Scatter(
                        x=[v_year, v_year + 1],
                        y=[row['當年BABIP'], row['隔年BABIP']],
                        mode='lines+markers',
                        line=dict(color=color, width=2),
                        marker=dict(size=9, color=color),
                        legendgroup=row['結果'], name=row['結果'], showlegend=False,
                        hovertemplate=(f"<b>{name_lbl}</b><br>"
                                       f"{v_year} BABIP: {row['當年BABIP']:.3f}<br>"
                                       f"{v_year+1} BABIP: {row['隔年BABIP']:.3f}<extra></extra>"),
                    ), row=1, col=1)
                    fig_v1.add_trace(go.Scatter(
                        x=[v_year, v_year + 1],
                        y=[row['當年AVG'], row['隔年AVG']],
                        mode='lines+markers',
                        line=dict(color=color, width=2),
                        marker=dict(size=9, color=color),
                        legendgroup=row['結果'], name=row['結果'], showlegend=show_leg,
                        hovertemplate=(f"<b>{name_lbl}</b><br>"
                                       f"{v_year} AVG: {row['當年AVG']:.3f}<br>"
                                       f"{v_year+1} AVG: {row['隔年AVG']:.3f}"
                                       f"{w_str}<extra></extra>"),
                    ), row=1, col=2)
                # BABIP=.270 參考線：用獨立 trace，避免 add_hline(row=) 版本相容問題
                fig_v1.add_trace(go.Scatter(
                    x=[v_year, v_year + 1], y=[0.270, 0.270],
                    mode='lines', line=dict(dash='dash', color='steelblue', width=1.5),
                    showlegend=False, hoverinfo='skip',
                ), row=1, col=1)
                fig_v1.update_layout(
                    title=(f'{v_year} → {v_year+1} 年：BABIP 與打擊率軌跡<br>'
                           '<sub>線條往上 = 數值改善 ｜ 綠色=反彈成功 ｜ 橘色=小幅改善 ｜ 紅色=沒有反彈</sub>'),
                    height=530, plot_bgcolor='white',
                    legend=dict(orientation='h', yanchor='bottom', y=-0.18, xanchor='center', x=0.5),
                    xaxis=dict(tickmode='array', tickvals=[v_year, v_year + 1],
                               ticktext=[f'{v_year}（倒楣年）', f'{v_year+1}（反彈年）']),
                    xaxis2=dict(tickmode='array', tickvals=[v_year, v_year + 1],
                                ticktext=[f'{v_year}（倒楣年）', f'{v_year+1}（反彈年）']),
                )
                st.plotly_chart(fig_v1, use_container_width=True)
                st.caption(f'📖 左圖：藍虛線 = BABIP .270 正常下限，線條升到虛線以上 = 運氣回歸。'
                           f'右圖：線條往上 = 打擊率反彈。滑鼠懸停可看球員姓名與具體數值。')
            else:
                st.info('有效數據不足（需要同時具備倒楣年與反彈年的 BABIP、AVG 資料）。')

            # ── 個案明細（分頁籤）────────────────────────────────
            st.markdown('### 📋 個案明細')
            if v_year + 1 == _max_yr:
                st.warning(f'⚠️ {v_year+1} 年賽季仍在進行中，成績為截至目前的部分累積，僅供參考。')
            st.caption('📌 看「BABIP回正」比看「AVG變化」更直接——運氣有沒有回歸才是理論核心。'
                       '「wRC+變化 > 0」表示製造得分能力也確實提升。')

            story = ['球員', '球隊', '備註',
                     '倒楣年度', '當年AVG', '當年BABIP', '當年BB%', '當年K%', '當年wRC+',
                     '反彈年度', '隔年AVG', '隔年BABIP', '隔年BB%', '隔年K%', '隔年wRC+',
                     'AVG變化', 'BABIP變化', 'BABIP回正', 'wRC+變化']

            t_s, t_p, t_f, t_i = st.tabs(['✅ 反彈成功', '➡️ 小幅改善', '❌ 沒有反彈', '⚠️ 傷病/無資料'])
            with t_s:
                sub = dv1[dv1['結果'] == '✅ 反彈成功'].reset_index(drop=True)
                if len(sub) > 0:
                    st.dataframe(sub[_sc(story)], use_container_width=True)
                else:
                    st.info('目前條件下無完全反彈案例。')
            with t_p:
                sub = dv1[dv1['結果'].str.startswith('➡️', na=False)].reset_index(drop=True)
                if len(sub) > 0:
                    st.dataframe(sub[_sc(story)], use_container_width=True)
                else:
                    st.info('目前條件下無小幅改善案例。')
            with t_f:
                sub = dv1[dv1['結果'].str.startswith('❌', na=False)].reset_index(drop=True)
                if len(sub) > 0:
                    st.dataframe(sub[_sc(story + ['原因診斷'])], use_container_width=True)
                else:
                    st.info('目前條件下無沒有反彈案例。')
            with t_i:
                sub = dv1[dv1['結果'].str.startswith('⚠️', na=False)].reset_index(drop=True)
                if len(sub) > 0:
                    st.dataframe(sub[_sc(['倒楣年度', '反彈年度', '球員', '球隊', '當年AVG', '當年BABIP', '結果'])], use_container_width=True)
                else:
                    st.info('沒有傷病/無資料案例。')

            # ── 結論 ─────────────────────────────────────────────
            st.markdown('### 🔬 修正後的理論')
            st.markdown(f"""
**分析年度**：{v_year} → {v_year+1} ｜ **有效案例**：{t1} 人
**篩選條件**：BABIP < {v_babip:.3f}＋BB% ≥ {v_bb_min:.1%}＋K% ≤ {v_k_max:.1%}＋PA ≥ {v_min_pa}

| 結果 | 人數 | 比例 |
|---|---|---|
| ✅ 反彈成功（AVG ≥ +{v_bounce:.3f} 且 wRC+ 未下降） | {s1} 人 | {s1/t1*100:.1f}% |
| ➡️ 小幅改善 | {p1} 人 | {p1/t1*100:.1f}% |
| ❌ 沒有反彈 | {f1} 人 | {f1/t1*100:.1f}% |

> **修正後理論**：BABIP 低 且 BB%/K% 穩健的球員，隔年有 **{(s1+p1)/t1*100:.0f}%** 的機率改善打擊率。
> 沒有反彈的主要風險因子：K% 大增、傷病、wRC+ 同步下降。
""")
