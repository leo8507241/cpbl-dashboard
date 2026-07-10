import os
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from supabase import create_client

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "每日追蹤"))
from archetypes import add_archetype, batter_season_stats_from_matchup, MIN_PA as ARCHETYPE_MIN_PA  # noqa: E402

SUPABASE_URL = 'https://vxgtgqlqukexpvnnvslf.supabase.co'
SUPABASE_KEY = 'sb_publishable_9DDMVwHFIMCdBxN12jaWkQ_gMeJkRuI'

MIN_PA_WARNING = 30  # 樣本數低於這個門檻時顯示小樣本警語

PITCH_NAMES = {
    "FF": "四縫線", "SI": "二縫線", "FT": "二縫線",
    "SL": "滑球", "CU": "曲球", "KC": "指節曲球",
    "CH": "變速球", "SP": "指叉球", "FS": "快速指叉",
    "CT": "卡特球", "FC": "卡特球", "SW": "掃射曲球",
    "KN": "蝴蝶球",
}

HIT_RESULTS = {"1B", "2B", "3B", "HR"}
PITCHER_2026_MIN_PA = 30  # 投手在 2026 年至少要面對這多打席才列入選單


@st.cache_data
def load_table(table_name):
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    all_data = []
    page_size = 1000
    offset = 0
    while True:
        result = (supabase.table(table_name)
                  .select('*')
                  .range(offset, offset + page_size - 1)
                  .execute())
        all_data.extend(result.data)
        if len(result.data) < page_size:
            break
        offset += page_size
    return pd.DataFrame(all_data)


@st.cache_data
def load_matchup():
    df = load_table('cpbl_matchup_log')
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'])
    df['year'] = df['date'].dt.year
    return df


@st.cache_data
def load_pitches():
    csv_path = os.path.join(os.path.dirname(_HERE), "pitcher_pitches.csv")
    if not os.path.exists(csv_path):
        return pd.DataFrame()
    df = pd.read_csv(csv_path, low_memory=False)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["year"] = df["game_date"].dt.year
    for col in ["in_play", "is_hit", "is_strike", "is_ball", "is_first_pitch"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().map(
                {"true": True, "false": False, "1": True, "0": False}
            )
    return df


@st.cache_data
def build_archetype_table(matchup_df):
    """打者原型改成直接從 cpbl_matchup_log 自己聚合算球季 iso/BB%/KK%，不再依賴 cpbl_batting_2020_2026。
    原因：cpbl_batting_2020_2026 只有 2020 年起的資料，但對戰紀錄回溯到 2018 年，
    2018-2019 年的打者用那張表一定查不到、全部變成「無資料」。
    這裡自己聚合還有個附帶好處：用 batter_acnt 分組，不必再靠姓名比對，不會有同名球員對錯的風險。
    """
    season_stats = batter_season_stats_from_matchup(matchup_df)
    return add_archetype(season_stats)


def archetype_lookup_dict(archetype_table):
    """{(batter_acnt, 年度): 原型} 查找表。"""
    lookup = {}
    for _, row in archetype_table.dropna(subset=['原型']).iterrows():
        lookup[(row['batter_acnt'], int(row['年度']))] = row['原型']
    return lookup


def bucket_stats(group):
    """算這一組打席的完整拆解：AVG/OBP/SLG/ISO/OPS 全部是直接定義出來的比率公式，
    不需要任何回歸權重——這樣才看得出『為什麼』某個分組難打，是長打被炸、還是保送太多、還是安打率本身高。"""
    n = lambda result: (group['result'] == result).sum()
    bb = n('BB')
    ibb = n('IBB')
    hbp = n('HBP')
    sf = n('SF')
    sac = n('SAC')
    one_b, two_b, three_b, hr = n('1B'), n('2B'), n('3B'), n('HR')
    so = n('SO')
    pa = len(group)
    ab = pa - bb - ibb - hbp - sf - sac
    h = one_b + two_b + three_b + hr
    bb_total = bb + ibb
    avg = h / ab if ab > 0 else None
    obp_denom = ab + bb_total + hbp + sf
    obp = (h + bb_total + hbp) / obp_denom if obp_denom > 0 else None
    slg = (one_b + 2 * two_b + 3 * three_b + 4 * hr) / ab if ab > 0 else None
    ops = (obp + slg) if (obp is not None and slg is not None) else None
    return {
        '打席數': pa,
        '1B': one_b, '2B': two_b, '3B': three_b, 'HR': hr,
        'BB': bb_total, 'SO': so,
        'K%': round(so / pa, 3) if pa > 0 else None,
        'BB%': round(bb_total / pa, 3) if pa > 0 else None,
        'HR%': round(hr / pa, 3) if pa > 0 else None,
        'AVG': round(avg, 3) if avg is not None else None,
        'OBP': round(obp, 3) if obp is not None else None,
        'SLG': round(slg, 3) if slg is not None else None,
        'ISO': round(slg - avg, 3) if (slg is not None and avg is not None) else None,
        'OPS': round(ops, 3) if ops is not None else None,
    }


def explain_gap(bucket_row, baseline_row):
    """拿一個原型的數字跟這位投手『不分原型的整體表現』比，
    自動找出差距最大的分量，給一句話的白話解釋（不是只丟數字）。"""
    diffs = {
        'K%': (bucket_row['K%'] or 0) - (baseline_row['K%'] or 0),
        'BB%': (bucket_row['BB%'] or 0) - (baseline_row['BB%'] or 0),
        'HR%': (bucket_row['HR%'] or 0) - (baseline_row['HR%'] or 0),
        'AVG': (bucket_row['AVG'] or 0) - (baseline_row['AVG'] or 0),
    }
    label_map = {
        'K%': ('三振抓得比平常多', '三振抓得比平常少'),
        'BB%': ('保送放得比平常多', '保送放得比平常少'),
        'HR%': ('被炸全壘打的比例比平常高', '被炸全壘打的比例比平常低'),
        'AVG': ('被打擊率比平常高', '被打擊率比平常低'),
    }
    key = max(diffs, key=lambda k: abs(diffs[k]))
    val = diffs[key]
    direction = 0 if val >= 0 else 1
    return f"主要差在 **{key}**（{label_map[key][direction]}，{'+' if val >= 0 else ''}{val:.3f}）"


st.set_page_config(page_title='投手剋星分析', layout='wide')
st.title('🎯 投手剋星分析')
st.caption(
    '個人對戰紀錄可以拉長年份區間累積樣本，樣本數夠大時比原型分桶更精準——但拉的年份越多，'
    '也要留意打者/投手本身這幾年可能有變化（年輕變老、球種改變），不是一成不變的同一個人。'
)

with st.expander('ℹ️ 這裡為什麼用 AVG/OBP/SLG/ISO/OPS，不是 wOBA？點開看說明'):
    st.markdown("""
    上一版這裡有一個自己近似出來的「wOBA-ish」，被問到「幹嗎自創數值」——確實，wOBA 的權重要拿全聯盟真實比賽結果
    回歸算出來，我這邊沒有管道抓到 rebas.tw 官方逐年校正的權重表。所以全部改用 **AVG/OBP/SLG/ISO/OPS**——
    這些都是直接定義出來的比率公式（OPS = OBP + SLG），不需要任何回歸權重，用我們自己爬的逐打席資料就能
    100% 準確算出來，沒有「自創」的部分，也跟你熟悉的 Day 1 手冊指標是同一套。
    """)

matchup_df = load_matchup()
if matchup_df.empty:
    st.warning('cpbl_matchup_log 目前沒有資料。請先執行 每日追蹤/run_daily.py，並確認 Supabase 讀取權限（見 SQL 檔案註解）。')
    st.stop()

archetype_table = build_archetype_table(matchup_df)
archetype_lookup = archetype_lookup_dict(archetype_table)
matchup_df['原型'] = matchup_df.apply(
    lambda r: archetype_lookup.get((r['batter_acnt'], int(r['year']))), axis=1
)

all_years = sorted(matchup_df['year'].dropna().unique().astype(int))
st.sidebar.header('篩選條件')
default_start = max(2023, all_years[0])  # 野球革命逐球資料最早回溯至 2023
year_range = st.sidebar.slider(
    '對戰資料涵蓋年份區間', min_value=max(2023, all_years[0]), max_value=all_years[-1],
    value=(default_start, all_years[-1]),
)
range_df = matchup_df[(matchup_df['year'] >= year_range[0]) & (matchup_df['year'] <= year_range[1])]
st.caption(f'目前涵蓋 {year_range[0]}–{year_range[1]} 年，共 {len(range_df):,} 個打席的對戰紀錄。')

# 投手選單：只顯示 2026 年有出賽（被打席數 ≥ 30）的投手
_pa_2026 = (
    matchup_df[matchup_df['year'] == 2026]
    .groupby('pitcher_acnt')
    .size()
    .reset_index(name='pa_2026')
)
_qualifying_acnts = set(_pa_2026[_pa_2026['pa_2026'] >= PITCHER_2026_MIN_PA]['pitcher_acnt'])

# 用最新年份的最後一筆記錄取球隊（確保伍鐸之類轉隊後顯示現任球隊）
pitchers_all = (
    matchup_df[matchup_df['pitcher_acnt'].isin(_qualifying_acnts)]
    [['pitcher_acnt', 'pitcher_name', 'pitcher_team', 'year', 'date']]
    .sort_values(['year', 'date'], ascending=True)
    .drop_duplicates(subset=['pitcher_acnt'], keep='last')
    [['pitcher_acnt', 'pitcher_name', 'pitcher_team']]
    .sort_values('pitcher_team')
)

search_text = st.sidebar.text_input('🔍 搜尋投手（姓名或球隊，選填）', help='輸入關鍵字縮小下面下拉選單的範圍，不分大小寫。')
if search_text.strip():
    mask = (pitchers_all['pitcher_name'].str.contains(search_text, case=False, na=False)
            | pitchers_all['pitcher_team'].str.contains(search_text, case=False, na=False))
    pitchers = pitchers_all[mask]
    if pitchers.empty:
        st.sidebar.caption(f'找不到符合「{search_text}」的投手，顯示全部選項。')
        pitchers = pitchers_all
else:
    pitchers = pitchers_all

pitcher_labels = pitchers.apply(lambda r: f"{r['pitcher_name']}（{r['pitcher_team']}）", axis=1)
choice = st.sidebar.selectbox(f'選投手（{len(pitchers)} 位）', sorted(pitcher_labels))
selected = pitchers.iloc[list(pitcher_labels).index(choice)]

min_pa_individual = st.sidebar.slider('個人對戰表：最低打席數門檻', 0, 30, 10)

pitcher_all_df = range_df[range_df['pitcher_acnt'] == selected['pitcher_acnt']]
st.subheader(f"{selected['pitcher_name']}（{selected['pitcher_team']}）{year_range[0]}–{year_range[1]} 年對戰紀錄")

if pitcher_all_df.empty:
    st.info('這位投手在這個年份區間沒有對戰紀錄。')
    st.stop()

# ── ① 個人對戰紀錄：這才是樣本夠大之後真正有意義的東西 ──────────
st.markdown('### 👤 對戰個別打者紀錄')
st.caption(f'依對戰打席數排序，打席數 ≥ {min_pa_individual} 才列出（門檻可在左側調整）。'
           '1B/2B/3B/HR/BB/SO 是實際支數/次數，其餘是比率。')

individual_rows = []
for (batter_acnt, batter_name), g in pitcher_all_df.groupby(['batter_acnt', 'batter_name']):
    stats = bucket_stats(g)
    if stats['打席數'] < min_pa_individual:
        continue
    stats['打者'] = batter_name
    stats['球隊'] = g['batter_team'].iloc[-1]
    stats['原型'] = g['原型'].dropna().iloc[-1] if g['原型'].notna().any() else '（無資料）'
    stats['小樣本警語'] = '⚠️' if stats['打席數'] < MIN_PA_WARNING else ''
    individual_rows.append(stats)

if individual_rows:
    individual_df = pd.DataFrame(individual_rows).sort_values('打席數', ascending=False)
    individual_df = individual_df.set_index('打者')[
        ['球隊', '原型', '打席數', '1B', '2B', '3B', 'HR', 'BB', 'SO',
         'K%', 'BB%', 'HR%', 'AVG', 'OBP', 'SLG', 'ISO', 'OPS', '小樣本警語']
    ]
    st.dataframe(individual_df, use_container_width=True, height=min(600, 45 * (len(individual_df) + 1)))

    valid_ind = individual_df.dropna(subset=['OPS'])
    valid_ind_enough = valid_ind[valid_ind['打席數'] >= MIN_PA_WARNING]
    if len(valid_ind_enough) > 0:
        nemesis = valid_ind_enough['OPS'].idxmax()
        st.markdown(f"🔥 **打不贏的對象**：{nemesis}（{valid_ind_enough.loc[nemesis, '打席數']} 打席、"
                    f"OPS = {valid_ind_enough.loc[nemesis, 'OPS']:.3f}）")
    elif len(valid_ind) > 0:
        st.caption('目前沒有任何一位打者的對戰打席數 ≥ 30，還無法有信心地說「誰是他的剋星」，僅列出目前累積的數字參考。')
else:
    st.info(f'這個年份區間內，沒有打者對戰打席數達到 {min_pa_individual}，試著拉長年份區間或調低門檻。')

# ── ② 逐球分析：投手弱點 × 打者習性 ─────────────────────────────────────
st.markdown('---')
st.markdown('### 🔬 逐球分析：投手弱點 × 打者習性')

pitches_all_df = load_pitches()
pitcher_pitch_data = (
    pitches_all_df[pitches_all_df['pitcher'] == selected['pitcher_name']].copy()
    if not pitches_all_df.empty else pd.DataFrame()
)

if pitcher_pitch_data.empty:
    st.info(
        f"目前 **{selected['pitcher_name']}** 沒有逐球資料。"
        "（逐球資料需要從野球革命抓取，目前涵蓋的投手持續擴充中）"
    )
else:
    pitcher_pitch_data_yr = pitcher_pitch_data[
        (pitcher_pitch_data['year'] >= year_range[0]) &
        (pitcher_pitch_data['year'] <= year_range[1])
    ]

    if individual_rows:
        batter_opts = [r['打者'] for r in sorted(individual_rows, key=lambda x: -(x.get('打席數') or 0))]
        sel_batter = st.selectbox(
            '選擇打者進行逐球分析',
            batter_opts,
            help='從上方「個人對戰紀錄」中選一位打者，分析這位投手對他投的球種組成與落點。',
        )

        batter_data = pitcher_pitch_data_yr[pitcher_pitch_data_yr['batter'] == sel_batter].copy()

        if batter_data.empty:
            st.info(
                f'沒有找到 {selected["pitcher_name"]} 對 {sel_batter} 的逐球紀錄'
                f'（逐球資料涵蓋年份：{year_range[0]}–{year_range[1]}）。'
            )
        else:
            batter_data['is_hit_pitch'] = (
                batter_data['in_play'].fillna(False).astype(bool)
            ) & (
                batter_data['pa_result_type'].isin(HIT_RESULTS)
            )

            col_left, col_right = st.columns([1, 1])

            # ── 左欄：球種命中率表 ──────────────────────────────────────
            with col_left:
                st.markdown(f'**球種命中率**（共 {len(batter_data)} 球）')
                pt_rows = []
                for pt, g in batter_data.groupby('pitch_type', observed=True):
                    n = len(g)
                    in_play_n = int(g['in_play'].fillna(False).astype(bool).sum())
                    hit_n = int(g['is_hit_pitch'].sum())
                    sw_n = int((g['result_code'] == 'SW').sum()) if 'result_code' in g.columns else 0
                    pt_rows.append({
                        '球種': f"{PITCH_NAMES.get(pt, pt)} ({pt})",
                        '_pt_code': pt,
                        '球數': n,
                        '佔比': f'{n / len(batter_data) * 100:.0f}%',
                        '揮空': sw_n,
                        '揮空率': f'{sw_n / n * 100:.0f}%' if n > 0 else '-',
                        '打入場': in_play_n,
                        '安打': hit_n,
                        '接觸命中率': f'{hit_n / in_play_n * 100:.0f}%' if in_play_n > 0 else '-',
                    })
                pt_df = pd.DataFrame(pt_rows).sort_values('球數', ascending=False)
                display_cols = ['球種', '球數', '佔比', '揮空率', '打入場', '安打', '接觸命中率']
                st.dataframe(pt_df[display_cols], use_container_width=True, hide_index=True)
                st.caption(
                    '**接觸命中率** = 安打 ÷ 打入場次數（只計被打入場內的球；'
                    '揮空、界外、球被接殺不算「接觸命中」）'
                )

                dangerous = [r for r in pt_rows if r['安打'] > 0]
                most_hit = max(dangerous, key=lambda r: r['安打']) if dangerous else None

            # ── 右欄：落點散點圖 + 熱點分布（含球種篩選） ──────────────
            with col_right:
                st.markdown('**投球落點分析**')

                # 球種篩選 radio
                _avail_pt_labels = ['全部'] + list(pt_df['球種'].values)
                sel_pt_label = st.radio(
                    '篩選球種', _avail_pt_labels, horizontal=True,
                    help='切換後散點圖與熱點分布皆依此球種篩選',
                )
                if sel_pt_label == '全部':
                    plot_data = batter_data
                else:
                    _sel_code = pt_df.loc[pt_df['球種'] == sel_pt_label, '_pt_code'].iloc[0]
                    plot_data = batter_data[batter_data['pitch_type'] == _sel_code]

                plot_df = plot_data.dropna(subset=['coord_x', 'coord_y'])
                plot_df = plot_df[
                    (plot_df['coord_x'].astype(str) != '') &
                    (plot_df['coord_y'].astype(str) != '')
                ].copy()

                _zone_shape = dict(
                    type='rect', x0=-50, x1=50, y0=-50, y1=50,
                    line=dict(color='black', width=1.5),
                    fillcolor='rgba(100,149,237,0.10)', layer='below',
                )
                _layout_base = dict(
                    height=400,
                    xaxis=dict(range=[-120, 120], zeroline=False, showgrid=False, showticklabels=False),
                    yaxis=dict(range=[-120, 120], zeroline=False, showgrid=False, showticklabels=False,
                               scaleanchor='x', scaleratio=1),
                    plot_bgcolor='rgba(248,248,248,1)',
                    margin=dict(l=5, r=5, t=10, b=5),
                )

                if not plot_df.empty:
                    zone_slider = st.slider(
                        '◄ 散點圖　　　　　熱點分布 ►', 0, 100, 20,
                        key='zone_sl',
                        help='向右拖動淡化散點、顯現密度熱圖；左側為純散點，右側為純熱圖',
                    )

                    fig_zone = go.Figure()

                    # 散點層（slider < 65 時顯示，越往右越透明）
                    if zone_slider < 65:
                        dot_op = max(0.12, 1 - zone_slider / 65)
                        nh = plot_df[~plot_df['is_hit_pitch']].copy()
                        h_pts = plot_df[plot_df['is_hit_pitch']]
                        # 分好球帶內外（對齊 rebas.tw 逐球紀律）
                        _iz = nh['coord_x'].between(-50, 50) & nh['coord_y'].between(-50, 50)
                        nh_in, nh_out = nh[_iz], nh[~_iz]
                        if not nh_in.empty:
                            fig_zone.add_scatter(
                                x=nh_in['coord_x'], y=nh_in['coord_y'],
                                mode='markers', name='好球帶內（非安打）',
                                marker=dict(color='steelblue', size=6, opacity=dot_op),
                            )
                        if not nh_out.empty:
                            fig_zone.add_scatter(
                                x=nh_out['coord_x'], y=nh_out['coord_y'],
                                mode='markers', name='好球帶外（非安打）',
                                marker=dict(color='#adb5bd', size=5, opacity=dot_op * 0.75),
                            )
                        if not h_pts.empty:
                            fig_zone.add_scatter(
                                x=h_pts['coord_x'], y=h_pts['coord_y'],
                                mode='markers', name='安打',
                                marker=dict(color='orangered', size=10, symbol='star',
                                            opacity=min(1.0, dot_op + 0.3),
                                            line=dict(color='white', width=0.8)),
                            )

                    # 熱點密度層（slider > 35 時顯示，越往右越深）
                    hit_pts = plot_df[plot_df['is_hit_pitch']]
                    if zone_slider > 35 and not hit_pts.empty:
                        heat_op = min(1.0, (zone_slider - 35) / 65)
                        fig_zone.add_trace(go.Histogram2dContour(
                            x=hit_pts['coord_x'], y=hit_pts['coord_y'],
                            colorscale='Reds', showscale=False,
                            opacity=heat_op,
                            contours=dict(showlines=False),
                            ncontours=14,
                            name='安打熱點',
                        ))

                    # 無效區背景 + 邊框（±105）
                    fig_zone.add_shape(
                        type='rect', x0=-105, x1=105, y0=-105, y1=105,
                        line=dict(color='#888888', width=1.2, dash='dash'),
                        fillcolor='rgba(180,180,180,0.15)', layer='below')
                    # 追打區背景 + 邊框（±80）
                    fig_zone.add_shape(
                        type='rect', x0=-80, x1=80, y0=-80, y1=80,
                        line=dict(color='#cc8000', width=1.5, dash='dot'),
                        fillcolor='rgba(255,180,0,0.12)', layer='below')
                    # 好球帶外框（±50）
                    fig_zone.add_shape(**_zone_shape)
                    # 九宮格格線
                    _fz3 = 100 / 3
                    for _fxi in [-50 + _fz3, -50 + 2 * _fz3]:
                        fig_zone.add_shape(type='line', x0=_fxi, x1=_fxi, y0=-50, y1=50,
                                           line=dict(color='gray', width=1, dash='dot'))
                    for _fyi in [-50 + _fz3, -50 + 2 * _fz3]:
                        fig_zone.add_shape(type='line', x0=-50, x1=50, y0=_fyi, y1=_fyi,
                                           line=dict(color='gray', width=1, dash='dot'))
                    # 九宮格編號 1-9（從左上→右下，投手視角）
                    for _fzi in range(9):
                        _fzr, _fzc = divmod(_fzi, 3)
                        fig_zone.add_annotation(
                            x=-50 + _fzc * _fz3 + _fz3 / 2,
                            y=50 - _fzr * _fz3 - _fz3 / 2,
                            text=str(_fzi + 1), showarrow=False,
                            font=dict(color='rgba(0,0,0,0.22)', size=16))
                    # 標籤
                    fig_zone.add_annotation(
                        x=86, y=105, text='無效區', showarrow=False, yanchor='bottom',
                        font=dict(size=10, color='#777777'))
                    fig_zone.add_annotation(
                        x=66, y=80, text='追打區', showarrow=False, yanchor='bottom',
                        font=dict(size=10, color='#cc8000'))
                    fig_zone.update_layout(
                        **_layout_base,
                        showlegend=True,
                        legend=dict(orientation='h', y=-0.05, font_size=11),
                    )
                    st.plotly_chart(fig_zone, use_container_width=True)
                    st.caption('紅星 = 安打，深藍 = 好球帶內非安打，灰點 = 好球帶外非安打；拖動上方 slider 可切換至安打密度熱圖。')
                else:
                    st.caption('這位投手在此年份區間的投球資料沒有座標資訊。')

            # ── 深度解析：各被打安打球種（全部列出）───────────────────────────
            _hit_pt_rows = [row for _, row in pt_df.iterrows() if int(row.get('安打', 0)) > 0]
            if _hit_pt_rows and not pitches_all_df.empty:
                st.markdown('---')
                st.markdown(f'#### 🧠 深度解析：{sel_batter} 對 {selected["pitcher_name"]} 各被打安打球種')
                st.caption(
                    f'以下列出所有被打出安打的球種，從「打者全聯盟視角」和「投手全聯盟視角」各自拆解，'
                    f'判斷是打者強項、投手弱點，還是兩者共同造成。'
                )

                def _chr(df_sub):
                    if len(df_sub) == 0:
                        return None, 0, 0
                    ip = int(df_sub['in_play'].fillna(False).astype(bool).sum())
                    h  = int(df_sub['is_hit_pitch'].sum())
                    return (h / ip if ip > 0 else None), h, ip

                for _hpt_i, _hpt in enumerate(_hit_pt_rows):
                    _hpt_code = _hpt['_pt_code']
                    _hpt_name = _hpt['球種']
                    _hpt_hits = int(_hpt['安打'])

                    st.markdown(f'**{_hpt_name}** — 此對決被打 {_hpt_hits} 支安打')

                    _b_all = pitches_all_df[
                        (pitches_all_df['batter'] == sel_batter) &
                        (pitches_all_df['pitch_type'] == _hpt_code) &
                        (pitches_all_df['year'] >= year_range[0])
                    ].copy()
                    _b_all['is_hit_pitch'] = (
                        _b_all['in_play'].fillna(False).astype(bool)
                    ) & (_b_all['pa_result_type'].isin(HIT_RESULTS))
                    _b_rate, _b_hits, _b_in_play = _chr(_b_all)

                    _p_all = pitches_all_df[
                        (pitches_all_df['pitcher'] == selected['pitcher_name']) &
                        (pitches_all_df['pitch_type'] == _hpt_code) &
                        (pitches_all_df['year'] >= year_range[0])
                    ].copy()
                    _p_all['is_hit_pitch'] = (
                        _p_all['in_play'].fillna(False).astype(bool)
                    ) & (_p_all['pa_result_type'].isin(HIT_RESULTS))
                    _p_rate, _p_hits, _p_in_play = _chr(_p_all)

                    _dc_b, _dc_p = st.columns(2)
                    with _dc_b:
                        st.markdown(f'🏏 **打者習性**：{sel_batter} 對全聯盟的 {_hpt_name}')
                        if _b_in_play > 0 and _b_rate is not None:
                            st.metric(
                                '接觸命中率（全聯盟）',
                                f'{_b_rate * 100:.1f}%',
                                help=f'{_b_in_play} 打入場 / {_b_hits} 安打',
                            )
                            st.caption(f'樣本：{len(_b_all)} 球 / {_b_in_play} 打入場 / {_b_hits} 安打')
                            if _b_rate >= 0.35:
                                st.info(f'{sel_batter} 整體就擅長打 {_hpt_name}（{_b_rate*100:.1f}%）。')
                            elif _b_rate >= 0.20:
                                st.info(f'{sel_batter} 對 {_hpt_name} 屬中等水準（{_b_rate*100:.1f}%）。')
                            else:
                                st.info(f'{sel_batter} 面對全聯盟的 {_hpt_name} 並非強項（{_b_rate*100:.1f}%）。')
                        elif len(_b_all) > 0:
                            st.caption(f'{len(_b_all)} 球但無打入場記錄。')
                        else:
                            st.caption(f'逐球資料無 {sel_batter} 面對此球種的紀錄。')

                    with _dc_p:
                        st.markdown(f'🎯 **投手弱點**：{selected["pitcher_name"]} 的 {_hpt_name} 對全聯盟')
                        if _p_in_play > 0 and _p_rate is not None:
                            st.metric(
                                '接觸命中率（全聯盟打者）',
                                f'{_p_rate * 100:.1f}%',
                                help=f'{_p_in_play} 打入場 / {_p_hits} 安打',
                            )
                            st.caption(f'樣本：{len(_p_all)} 球 / {_p_in_play} 打入場 / {_p_hits} 安打')
                            if _p_rate >= 0.35:
                                st.warning(f'{selected["pitcher_name"]} 的 {_hpt_name} 被全聯盟打 {_p_rate*100:.1f}%，是共同弱點。')
                            elif _p_rate >= 0.20:
                                st.info(f'{selected["pitcher_name"]} 的 {_hpt_name} 被打命中率中等（{_p_rate*100:.1f}%）。')
                            else:
                                st.info(f'{selected["pitcher_name"]} 的 {_hpt_name} 對全聯盟並非弱點（{_p_rate*100:.1f}%）。')
                        elif len(_p_all) > 0:
                            st.caption(f'{len(_p_all)} 球但無打入場記錄。')
                        else:
                            st.caption(f'逐球資料無 {selected["pitcher_name"]} 投此球種的紀錄。')

                    if _b_rate is not None and _p_rate is not None:
                        if _b_rate >= 0.30 and _p_rate >= 0.30:
                            _conc = (
                                f'**雙重危機**：{sel_batter} 本就擅長打 {_hpt_name}（全聯盟 {_b_rate*100:.1f}%），'
                                f'且 {selected["pitcher_name"]} 的 {_hpt_name} 也是全聯盟弱點（{_p_rate*100:.1f}%）。'
                            )
                        elif _b_rate >= 0.30 and _p_rate < 0.25:
                            _conc = (
                                f'**打者優勢**：{sel_batter} 本身擅長 {_hpt_name}（全聯盟 {_b_rate*100:.1f}%）；'
                                f'{selected["pitcher_name"]} 此球對全聯盟尚可（{_p_rate*100:.1f}%）。'
                            )
                        elif _b_rate < 0.25 and _p_rate >= 0.30:
                            _conc = (
                                f'**投手弱點**：{selected["pitcher_name"]} 的 {_hpt_name} 是全聯盟弱點（{_p_rate*100:.1f}%），'
                                f'{sel_batter} 抓住機會（全聯盟 CHR {_b_rate*100:.1f}%）。'
                            )
                        else:
                            _conc = (
                                f'打者 CHR {_b_rate*100:.1f}%、投手被打 CHR {_p_rate*100:.1f}%，'
                                f'兩者均屬中等，此對決受球數 / 壘包情境等因素影響較大。'
                            )
                        st.success(_conc)

                    if _hpt_i < len(_hit_pt_rows) - 1:
                        st.markdown('---')

            # ── 進壘點重疊：打者安打熱區 vs 投手投球習慣 ───────────────────────
            if not pitches_all_df.empty:
                st.markdown('---')
                st.markdown('#### 📐 進壘點重疊：打者安打熱區 vs 投手投球習慣')
                st.caption(
                    '**藍** = 投手對全聯盟打者的投球分布；**紅** = 打者對全聯盟投手的安打落點。'
                    ' 兩色重疊區域 = 「投手習慣投此位置，打者恰好也在此出安打」。'
                )

                _bpts = sorted(pitches_all_df[pitches_all_df['batter'] == sel_batter]['pitch_type'].dropna().unique())
                _ppts = sorted(pitches_all_df[pitches_all_df['pitcher'] == selected['pitcher_name']]['pitch_type'].dropna().unique())
                _shared = sorted(set(_bpts) & set(_ppts))

                if not _shared:
                    st.caption('找不到共同球種座標資料。')
                else:
                    _pt_labels = {p: f"{PITCH_NAMES.get(p,p)} ({p})" for p in _shared}
                    _opts_z = ['全部'] + [_pt_labels[p] for p in _shared]
                    _def_idx = 0
                    _mh_code = most_hit.get('_pt_code') if most_hit else None
                    if _mh_code in _shared:
                        _def_idx = list(_shared).index(_mh_code) + 1

                    _sel_z_pt = st.selectbox(
                        '選擇球種（打者安打熱區 vs 投手投球分布）',
                        _opts_z, index=_def_idx, key='zone_overlap_sel',
                    )
                    _sel_z_code = None if _sel_z_pt == '全部' else next(p for p, l in _pt_labels.items() if l == _sel_z_pt)

                    _bz = pitches_all_df[pitches_all_df['batter'] == sel_batter].copy()
                    _pz = pitches_all_df[pitches_all_df['pitcher'] == selected['pitcher_name']].copy()
                    if _sel_z_code:
                        _bz = _bz[_bz['pitch_type'] == _sel_z_code].copy()
                        _pz = _pz[_pz['pitch_type'] == _sel_z_code].copy()

                    _bz['coord_x'] = pd.to_numeric(_bz['coord_x'], errors='coerce')
                    _bz['coord_y'] = pd.to_numeric(_bz['coord_y'], errors='coerce')
                    _pz['coord_x'] = pd.to_numeric(_pz['coord_x'], errors='coerce')
                    _pz['coord_y'] = pd.to_numeric(_pz['coord_y'], errors='coerce')
                    _bz = _bz.dropna(subset=['coord_x', 'coord_y'])
                    _pz = _pz.dropna(subset=['coord_x', 'coord_y'])
                    _bz['_hit'] = _bz['in_play'].fillna(False).astype(bool) & _bz['pa_result_type'].isin(HIT_RESULTS)
                    _bz_hits = _bz[_bz['_hit']].copy()

                    _SZX1, _SZX2 = -50, 50    # 對齊 rebas.tw / page 4 座標系
                    _SZY1, _SZY2 = -50, 50
                    _XZ3 = (_SZX2 - _SZX1) / 3   # ≈ 33.3
                    _YZ3 = (_SZY2 - _SZY1) / 3   # ≈ 33.3

                    def _az(x, y):
                        if _SZX1 <= x <= _SZX2 and _SZY1 <= y <= _SZY2:
                            return min(2, int((x - _SZX1) / _XZ3)) + min(2, int((_SZY2 - y) / _YZ3)) * 3 + 1
                        return 0

                    # 固定顯示範圍（對齊 rebas.tw / page 4：好球帶占圖表寬約 42%）
                    _CZ_H = 80    # 追打區正方形半邊，對齊 page 4 的 ±80
                    _DZ_H = 105   # 無效區正方形半邊
                    _chart_half = 120   # 圖表半邊，對齊 page 4 的 ±120 範圍

                    _ov_sl = st.slider(
                        '◄ 散點圖　　　　　熱點分布 ►', 0, 100, 15, key='zone_ov_sl',
                    )

                    _fig_ov = go.Figure()

                    # ── 散點層（此對決實際進壘點，依球種下拉篩選）────────────────
                    # 先計算「此球種在 batter_data 裡」的總球數與安打數，供後面一致性核對
                    _ms_all_base = batter_data.copy()
                    if _sel_z_code:
                        _ms_all_base = _ms_all_base[_ms_all_base['pitch_type'] == _sel_z_code]
                    _ms_total_n    = len(_ms_all_base)
                    _ms_total_hits = int(_ms_all_base['is_hit_pitch'].sum())

                    if _ov_sl < 65:
                        _sc_op = max(0.12, 1 - _ov_sl / 65)
                        _ms = _ms_all_base.copy()
                        _ms['coord_x'] = pd.to_numeric(_ms['coord_x'], errors='coerce')
                        _ms['coord_y'] = pd.to_numeric(_ms['coord_y'], errors='coerce')
                        _ms = _ms.dropna(subset=['coord_x', 'coord_y'])
                        _ms_no = _ms[~_ms['is_hit_pitch']].copy()
                        _ms_hit = _ms[_ms['is_hit_pitch']]
                        # 分好球帶內外
                        _iz_no = _ms_no['coord_x'].between(-50, 50) & _ms_no['coord_y'].between(-50, 50)
                        _ms_no_in, _ms_no_out = _ms_no[_iz_no], _ms_no[~_iz_no]
                        _ht = (
                            '球種：%{customdata[0]}<br>'
                            '日期：%{customdata[1]}<br>'
                            '座標：(%{x:.1f}, %{y:.1f})<br>'
                            '結果：%{customdata[2]}<extra></extra>'
                        )
                        if not _ms_no_in.empty:
                            _fig_ov.add_trace(go.Scatter(
                                x=_ms_no_in['coord_x'], y=_ms_no_in['coord_y'],
                                mode='markers',
                                marker=dict(color='steelblue', size=7, opacity=_sc_op),
                                name=f'好球帶內（非安打，{len(_ms_no_in)} 球）',
                                hovertemplate=_ht,
                                customdata=list(zip(
                                    _ms_no_in['pitch_type'].apply(lambda p: PITCH_NAMES.get(p, p)),
                                    _ms_no_in['game_date'].astype(str),
                                    _ms_no_in['pa_result_type'].astype(str),
                                )),
                            ))
                        if not _ms_no_out.empty:
                            _fig_ov.add_trace(go.Scatter(
                                x=_ms_no_out['coord_x'], y=_ms_no_out['coord_y'],
                                mode='markers',
                                marker=dict(color='#adb5bd', size=6, opacity=_sc_op * 0.75),
                                name=f'好球帶外（非安打，{len(_ms_no_out)} 球）',
                                hovertemplate=_ht,
                                customdata=list(zip(
                                    _ms_no_out['pitch_type'].apply(lambda p: PITCH_NAMES.get(p, p)),
                                    _ms_no_out['game_date'].astype(str),
                                    _ms_no_out['pa_result_type'].astype(str),
                                )),
                            ))
                        if not _ms_hit.empty:
                            _fig_ov.add_trace(go.Scatter(
                                x=_ms_hit['coord_x'], y=_ms_hit['coord_y'],
                                mode='markers',
                                marker=dict(color='orangered', size=10, symbol='star',
                                            opacity=_sc_op, line=dict(color='white', width=0.8)),
                                name=f'安打（此對決，共 {len(_ms_hit)} 球）',
                                hovertemplate=(
                                    '球種：%{customdata[0]}<br>'
                                    '日期：%{customdata[1]}<br>'
                                    '座標：(%{x:.1f}, %{y:.1f})<br>'
                                    '結果：%{customdata[2]}<extra></extra>'
                                ),
                                customdata=list(zip(
                                    _ms_hit['pitch_type'].apply(lambda p: PITCH_NAMES.get(p, p)),
                                    _ms_hit['game_date'].astype(str),
                                    _ms_hit['pa_result_type'].astype(str),
                                )),
                            ))

                    # ── 熱點層（全聯盟趨勢，拖到右邊時顯示）────────────────────
                    if _ov_sl > 35:
                        _ht_op = min(1.0, (_ov_sl - 35) / 65)
                        if not _pz.empty:
                            _fig_ov.add_trace(go.Histogram2dContour(
                                x=_pz['coord_x'], y=_pz['coord_y'],
                                colorscale='Blues', showscale=False, opacity=_ht_op * 0.70,
                                contours=dict(showlines=False), ncontours=10,
                                name=f'{selected["pitcher_name"]} 投球習慣（全聯盟）',
                            ))
                        if not _bz_hits.empty:
                            _fig_ov.add_trace(go.Histogram2dContour(
                                x=_bz_hits['coord_x'], y=_bz_hits['coord_y'],
                                colorscale='Oranges', showscale=False, opacity=_ht_op * 0.80,
                                contours=dict(showlines=False), ncontours=8,
                                name=f'{sel_batter} 安打習慣（全聯盟）',
                            ))

                    # ── 追打區 / 無效區正方形（以 (0,0) 為中心對稱延伸）──────────

                    # 無效區背景 + 邊框
                    _fig_ov.add_shape(
                        type='rect', x0=-_DZ_H, x1=_DZ_H, y0=-_DZ_H, y1=_DZ_H,
                        line=dict(color='#888888', width=1.2, dash='dash'),
                        fillcolor='rgba(180,180,180,0.15)', layer='below')
                    # 追打區背景 + 邊框
                    _fig_ov.add_shape(
                        type='rect', x0=-_CZ_H, x1=_CZ_H, y0=-_CZ_H, y1=_CZ_H,
                        line=dict(color='#cc8000', width=1.5, dash='dot'),
                        fillcolor='rgba(255,180,0,0.12)', layer='below')

                    # 好球帶外框 + 九宮格格線（原有）
                    _fig_ov.add_shape(type='rect', x0=_SZX1, x1=_SZX2, y0=_SZY1, y1=_SZY2,
                                      line=dict(color='black', width=2),
                                      fillcolor='rgba(100,149,237,0.10)', layer='below')
                    for _xi in [_SZX1 + _XZ3, _SZX1 + 2 * _XZ3]:
                        _fig_ov.add_shape(type='line', x0=_xi, x1=_xi, y0=_SZY1, y1=_SZY2,
                                          line=dict(color='gray', width=1, dash='dot'))
                    for _yi in [_SZY1 + _YZ3, _SZY1 + 2 * _YZ3]:
                        _fig_ov.add_shape(type='line', x0=_SZX1, x1=_SZX2, y0=_yi, y1=_yi,
                                          line=dict(color='gray', width=1, dash='dot'))
                    for _zi2 in range(9):
                        _zr2, _zc2 = divmod(_zi2, 3)
                        _fig_ov.add_annotation(
                            x=_SZX1 + _zc2 * _XZ3 + _XZ3 / 2,
                            y=_SZY2 - _zr2 * _YZ3 - _YZ3 / 2,
                            text=str(_zi2 + 1), showarrow=False,
                            font=dict(color='rgba(0,0,0,0.22)', size=16),
                        )

                    # 區域標示
                    _fig_ov.add_annotation(
                        x=_DZ_H * 0.82, y=_DZ_H, text='無效區',
                        showarrow=False, yanchor='bottom',
                        font=dict(size=10, color='#777777'))
                    _fig_ov.add_annotation(
                        x=_CZ_H * 0.82, y=_CZ_H, text='追打區',
                        showarrow=False, yanchor='bottom',
                        font=dict(size=10, color='#cc8000'))

                    _fig_ov.update_layout(
                        height=400, margin=dict(l=5, r=5, t=10, b=5),
                        xaxis=dict(range=[-_chart_half, _chart_half], showgrid=False,
                                   zeroline=False, showticklabels=False),
                        yaxis=dict(range=[-_chart_half, _chart_half], showgrid=False,
                                   zeroline=False, showticklabels=False,
                                   scaleanchor='x', scaleratio=1),
                        plot_bgcolor='rgba(248,248,248,1)',
                        legend=dict(orientation='h', y=-0.05, font_size=11),
                    )

                    _ov_col, _grid_col = st.columns([3, 2])
                    with _ov_col:
                        # 一致性核對：計算圖中實際顯示的安打星星數
                        _ms_disp_hits = _ms_all_base.copy()
                        _ms_disp_hits['coord_x'] = pd.to_numeric(_ms_disp_hits['coord_x'], errors='coerce')
                        _ms_disp_hits['coord_y'] = pd.to_numeric(_ms_disp_hits['coord_y'], errors='coerce')
                        _ms_disp_hits = _ms_disp_hits[_ms_disp_hits['is_hit_pitch'] == True].dropna(subset=['coord_x', 'coord_y'])
                        _disp_hit_n  = len(_ms_disp_hits)
                        _label_pt    = _sel_z_pt if _sel_z_pt != '全部' else '全部球種'
                        if _disp_hit_n == _ms_total_hits:
                            _hit_check = f'✅ 圖中安打星星 {_disp_hit_n} 顆 = 表格安打數 {_ms_total_hits} 顆'
                        else:
                            _hit_check = (
                                f'⚠️ 圖中顯示 {_disp_hit_n} / {_ms_total_hits} 顆安打'
                                f'（{_ms_total_hits - _disp_hit_n} 顆無座標資料無法繪製）'
                            )
                        st.caption(
                            f'散點：🔵 非安打 ／ 🔴 安打（此對決，{_label_pt}）　|　'
                            f'熱點：🔵 投手全聯盟投球習慣 ／ 🟠 打者全聯盟安打習慣　｜　{_hit_check}'
                        )
                        st.plotly_chart(_fig_ov, use_container_width=True)
                    with _grid_col:
                        _pz_cnt = {}
                        _bz_cnt = {}
                        if not _pz.empty:
                            _pz2 = _pz.copy()
                            _pz2['_zone'] = _pz2.apply(lambda r: _az(r['coord_x'], r['coord_y']), axis=1)
                            _pz_vc = _pz2[_pz2['_zone'] > 0]['_zone'].value_counts()
                            _pz_tot = _pz_vc.sum()
                            _pz_cnt = {z: _pz_vc.get(z, 0) / _pz_tot * 100 for z in range(1, 10)} if _pz_tot > 0 else {}
                        if not _bz_hits.empty:
                            _bz_hits2 = _bz_hits.copy()
                            _bz_hits2['_zone'] = _bz_hits2.apply(lambda r: _az(r['coord_x'], r['coord_y']), axis=1)
                            _bz_vc = _bz_hits2[_bz_hits2['_zone'] > 0]['_zone'].value_counts()
                            _bz_tot = _bz_vc.sum()
                            _bz_cnt = {z: _bz_vc.get(z, 0) / _bz_tot * 100 for z in range(1, 10)} if _bz_tot > 0 else {}

                        def _cell(pct, mode):
                            if pct <= 0:
                                return '<td style="border:1px solid #ccc;padding:5px 4px;text-align:center;font-size:11px;background:#f9f9f9">—</td>'
                            intensity = min(1.0, pct / 35) * 0.85
                            gv = int(255 * (1 - intensity))
                            bg = f'rgb({gv},{gv},255)' if mode == 'p' else f'rgb(255,{gv},{gv})'
                            return f'<td style="border:1px solid #ccc;padding:5px 4px;text-align:center;font-size:11px;background:{bg}"><b>{pct:.0f}%</b></td>'

                        _hg = ('<div style="display:flex;gap:10px;align-items:flex-start">'
                               '<div><p style="text-align:center;font-size:11px;font-weight:bold;margin-bottom:3px">🔵 投手投球%</p>'
                               '<table style="border-collapse:collapse">')
                        for _r in range(3):
                            _hg += '<tr>' + ''.join(_cell(_pz_cnt.get(_r*3+_c+1, 0), 'p') for _c in range(3)) + '</tr>'
                        _hg += ('</table></div>'
                                '<div><p style="text-align:center;font-size:11px;font-weight:bold;margin-bottom:3px">🔴 安打落點%</p>'
                                '<table style="border-collapse:collapse">')
                        for _r in range(3):
                            _hg += '<tr>' + ''.join(_cell(_bz_cnt.get(_r*3+_c+1, 0), 'b') for _c in range(3)) + '</tr>'
                        _hg += '</table></div></div>'
                        st.markdown(_hg, unsafe_allow_html=True)
                        st.caption('Zone 1-9：左上→右下；從投手視角；顏色深 = 佔比高')

                        _znames = {1:'外角高',2:'高',3:'內角高',4:'外角',5:'正中',6:'內角',7:'外角低',8:'低',9:'內角低'}
                        _ov_zones = [(z, _pz_cnt.get(z, 0), _bz_cnt.get(z, 0))
                                     for z in range(1, 10)
                                     if _pz_cnt.get(z, 0) >= 15 and _bz_cnt.get(z, 0) >= 15]
                        if _ov_zones:
                            _top_ov = sorted(_ov_zones, key=lambda x: x[1] + x[2], reverse=True)[0]
                            oz, op, ob = _top_ov
                            st.warning(f'⚠️ Zone {oz}（{_znames[oz]}）重疊危險：投手 {op:.0f}% 投到此區，打者 {ob:.0f}% 安打也在此區')
                        else:
                            st.caption('未偵測到明顯重疊危險 Zone（各需 ≥15%）。')

            # ── 球數情境：投手配球規律 ────────────────────────────────────────
            st.markdown('---')
            st.markdown('#### 🔢 球數情境：投手對此打者的配球規律')
            _cdf2 = batter_data.copy()
            _cdf2['balls_before']   = pd.to_numeric(_cdf2['balls_before'],   errors='coerce')
            _cdf2['strikes_before'] = pd.to_numeric(_cdf2['strikes_before'], errors='coerce')
            _cdf2 = _cdf2.dropna(subset=['balls_before', 'strikes_before'])
            _cdf2['count_str'] = _cdf2.apply(
                lambda r: f"{int(r['balls_before'])}-{int(r['strikes_before'])}", axis=1
            )
            _COUNT_ORDER2 = ['0-0','0-1','0-2','1-0','1-1','1-2','2-0','2-1','2-2','3-0','3-1','3-2']
            _COUNT_LABEL2 = {
                '0-0':'首球', '1-0':'', '2-0':'打者有利', '3-0':'打者有利',
                '0-1':'', '1-1':'', '2-1':'', '3-1':'打者有利',
                '0-2':'投手有利', '1-2':'投手有利', '2-2':'', '3-2':'滿球數',
            }
            if not _cdf2.empty:
                _all_pts2 = sorted(_cdf2['pitch_type'].dropna().unique())
                _pt_cn = {p: PITCH_NAMES.get(p, p) for p in _all_pts2}
                _crows2 = []
                _pred_insights2 = []
                for cnt in _COUNT_ORDER2:
                    g = _cdf2[_cdf2['count_str'] == cnt]
                    if len(g) == 0:
                        continue
                    total = len(g)
                    hits = int(g['is_hit_pitch'].sum())
                    _hit_pts_in_cnt = g[g['is_hit_pitch'] == True]['pitch_type'].dropna()
                    _hit_pt_str = '、'.join([PITCH_NAMES.get(p, p) for p in _hit_pts_in_cnt]) if len(_hit_pts_in_cnt) > 0 else '—'
                    row = {'球數': cnt, '情境': _COUNT_LABEL2.get(cnt, ''), '總球數': total, '安打': hits, '被打球種': _hit_pt_str}
                    pt_vc2 = g['pitch_type'].value_counts()
                    dom_pct, dom_pt = 0, None
                    for pt in _all_pts2:
                        v = int(pt_vc2.get(pt, 0))
                        pct = v / total * 100
                        row[_pt_cn[pt]] = f'{v}({pct:.0f}%)' if v > 0 else '—'
                        if pct > dom_pct:
                            dom_pct, dom_pt = pct, _pt_cn[pt]
                    _crows2.append(row)
                    if dom_pct >= 55 and hits > 0:
                        _pred_insights2.append((cnt, _COUNT_LABEL2.get(cnt, ''), dom_pt, dom_pct, hits, total))
                if _crows2:
                    st.dataframe(pd.DataFrame(_crows2), hide_index=True, use_container_width=True)
                    st.caption('每格 = 該球數下使用此球種的球數（佔比）；數字加總等於「總球數」欄')
                    if _pred_insights2:
                        st.markdown('**⚡ 配球規律警示**')
                        for cnt, label, pt_name, pt_pct, hits, total in _pred_insights2:
                            st.warning(
                                f'球數 **{cnt}**（{label}）：投手對 {sel_batter} 有 **{pt_pct:.0f}%** 機率投 **{pt_name}**'
                                f'（共 {total} 球），此球數被打出 {hits} 支安打'
                                f' → 配球規律明顯，{sel_batter} 可能已掌握此球數傾向'
                            )
                    else:
                        st.caption('無明顯單一球種支配的球數（≥55% 為判定門檻）。')
            else:
                st.caption('球數欄位缺失，無法分析。')
    else:
        st.info('上方沒有符合門檻的個別打者資料，試著降低左側的「最低打席數門檻」。')
