import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import create_client

SUPABASE_URL = 'https://vxgtgqlqukexpvnnvslf.supabase.co'
SUPABASE_KEY = 'sb_publishable_9DDMVwHFIMCdBxN12jaWkQ_gMeJkRuI'

# 固定關注名單：目前是從你原本專案裡本來就有的球員筆記本挑出來的
# （2024王威晨打擊率.ipynb、林立2425打擊成績.ipynb、張育成基本資料.ipynb 等），
# 純粹是「猜你原本就在關注這幾位」，改這行 list 就能加減人，不需要改其他程式碼。
WATCHLIST = ["林立", "陳傑憲", "王威晨", "張育成"]

CURRENT_YEAR = pd.Timestamp.now().year  # 這個頁面只看「這一季」——過去已結束的球季沒有「近況」可言


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
def load_gamelog():
    df = load_table('cpbl_batter_game_log')
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'])
    df['year'] = df['date'].dt.year
    int_cols = ['pa', 'ab', 'h', '1b', '2b', '3b', 'hr', 'bb', 'ibb', 'hbp', 'sf', 'sac', 'so', 'rbi', 'r', 'sb', 'cs']
    for c in int_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    return df


@st.cache_data
def load_official_season_stats(year):
    """cpbl_batting_2020_2026 是你既有專案從 CPBL 官網 + rebas.tw 爬回來、已經算好的官方球季數據
    （woba_r、wrc_plus 都是 rebas.tw 逐年校正過的真實權重，不是本頁自己近似算的），
    這裡只當作「參考對照」秀出來，不會拿來混進下面的滾動排名計算。"""
    df = load_table('cpbl_batting_2020_2026')
    if df.empty:
        return {}
    df['年度'] = pd.to_numeric(df['年度'], errors='coerce')
    df = df[df['年度'] == year]
    lookup = {}
    for _, row in df.iterrows():
        lookup[row['球員']] = {'官方wOBA': row.get('woba_r'), '官方wRC+': row.get('wrc_plus')}
    return lookup


def compute_metrics(sums):
    """給一組加總後的計數型數據（dict），算出比率型指標。全部都是 AVG/OBP/SLG 這種標準公式，
    沒有用到任何自己近似出來的權重——OPS = OBP + SLG，是公認公式，不是自創數值。"""
    ab, h, pa = sums['ab'], sums['h'], sums['pa']
    bb, hbp, sf = sums['bb'], sums['hbp'], sums['sf']
    one_b, two_b, three_b, hr, so = sums['1b'], sums['2b'], sums['3b'], sums['hr'], sums['so']

    avg = h / ab if ab > 0 else None
    obp_denom = ab + bb + hbp + sf
    obp = (h + bb + hbp) / obp_denom if obp_denom > 0 else None
    slg = (one_b + 2 * two_b + 3 * three_b + 4 * hr) / ab if ab > 0 else None
    iso = (slg - avg) if (slg is not None and avg is not None) else None
    ops = (obp + slg) if (obp is not None and slg is not None) else None
    bb_pct = bb / pa if pa > 0 else None
    k_pct = so / pa if pa > 0 else None
    babip_denom = ab - so - hr + sf
    babip = (h - hr) / babip_denom if babip_denom > 0 else None

    return {'avg': avg, 'obp': obp, 'slg': slg, 'iso': iso, 'ops': ops,
            'bb_pct': bb_pct, 'k_pct': k_pct, 'babip': babip, 'pa': pa}


def r3(x):
    """四捨五入到小數點第三位，None 保持 None。"""
    return round(x, 3) if x is not None else None


def ops_plus(obp, slg, lg_obp, lg_slg):
    """標準 OPS+ 公式（沒有球場因子校正，CPBL 各球場差異資料不足，這裡當中性球場處理）：
    100 × [(OBP/聯盟OBP) + (SLG/聯盟SLG) − 1]。100 = 聯盟平均，公開公式、不是自創權重。"""
    if obp is None or slg is None or not lg_obp or not lg_slg:
        return None
    return round(100 * ((obp / lg_obp) + (slg / lg_slg) - 1), 1)


SUM_COLS = ['pa', 'ab', 'h', '1b', '2b', '3b', 'hr', 'bb', 'ibb', 'hbp', 'sf', 'sac', 'so']


def build_player_summary(year_df, rolling_n, official_lookup):
    """回傳每位打者一列：球季至今 vs 近 N 場滾動的 AVG/OBP/SLG/ISO/BABIP/OPS/OPS+，
    用 OPS+ 的漲跌當『近況變化』排序依據——OPS+ 是公開公式（100×((OBP/聯盟OBP)+(SLG/聯盟SLG)-1)），不是自創權重。
    year_df 進來之前應該已經濾掉太少打席的『客串』場次（見下方 min_pa_per_game）。"""
    league_sums = {c: year_df[c].sum() for c in SUM_COLS}
    league_metrics = compute_metrics(league_sums)
    lg_obp, lg_slg = league_metrics['obp'], league_metrics['slg']

    rows = []
    for (acnt, name, team), g in year_df.sort_values('date').groupby(['batter_acnt', 'batter_name', 'team']):
        season_sums = {c: g[c].sum() for c in SUM_COLS}
        season_metrics = compute_metrics(season_sums)

        recent = g.tail(rolling_n)
        recent_sums = {c: recent[c].sum() for c in SUM_COLS}
        recent_metrics = compute_metrics(recent_sums)

        season_ops_plus = ops_plus(season_metrics['obp'], season_metrics['slg'], lg_obp, lg_slg)
        recent_ops_plus = ops_plus(recent_metrics['obp'], recent_metrics['slg'], lg_obp, lg_slg)
        delta = (round(recent_ops_plus - season_ops_plus, 1)
                 if (season_ops_plus is not None and recent_ops_plus is not None) else None)

        official = official_lookup.get(name, {})

        rows.append({
            '球員': name, '球隊': team,
            '球季至今_PA': season_metrics['pa'], '球季至今_AVG': r3(season_metrics['avg']),
            '球季至今_OBP': r3(season_metrics['obp']), '球季至今_SLG': r3(season_metrics['slg']),
            '球季至今_ISO': r3(season_metrics['iso']), '球季至今_BABIP': r3(season_metrics['babip']),
            '球季至今_OPS': r3(season_metrics['ops']), '球季至今_OPS+': season_ops_plus,
            f'近{rolling_n}場_PA': recent_metrics['pa'], f'近{rolling_n}場_AVG': r3(recent_metrics['avg']),
            f'近{rolling_n}場_OBP': r3(recent_metrics['obp']), f'近{rolling_n}場_SLG': r3(recent_metrics['slg']),
            f'近{rolling_n}場_ISO': r3(recent_metrics['iso']), f'近{rolling_n}場_BABIP': r3(recent_metrics['babip']),
            f'近{rolling_n}場_OPS': r3(recent_metrics['ops']), f'近{rolling_n}場_OPS+': recent_ops_plus,
            '近況變化(OPS+)': delta,
            '官方球季wOBA': official.get('官方wOBA'), '官方球季wRC+': official.get('官方wRC+'),
            '納入場次': g['game_sno'].nunique(),
        })
    return pd.DataFrame(rows), lg_obp, lg_slg


st.set_page_config(page_title='打者趨勢雷達', layout='wide')
st.title('📈 打者趨勢雷達')
st.caption(
    '資料來源：每天從 CPBL 官網逐場 boxscore API（box/getlive）一場一場抓，'
    '不是看官網排行榜，是每位打者「每一場」的計數型數據（打數、安打、保送…），累加起來才變成下面的統計。'
)

df = load_gamelog()
if df.empty:
    st.warning('cpbl_batter_game_log 目前沒有資料。請先執行 每日追蹤/run_daily.py，並確認 Supabase 讀取權限（見 SQL 檔案註解）。')
    st.stop()

st.sidebar.header('篩選條件')
st.sidebar.caption(f'固定看 {CURRENT_YEAR} 年（進行中的球季）——已結束的球季沒有「近況」可比，不提供年度切換。')
rolling_n = st.sidebar.slider('滾動場數', 5, 30, 15)
min_pa_per_game = st.sidebar.slider(
    '單場最低打席數（濾掉代打/客串場次）', 0, 6, 3,
    help='一場只有 1、2 個打席的通常是代打或臨時上場，不是真正的「一場先發表現」，先濾掉再算滾動指標比較準。'
)
min_recent_pa = rolling_n * min_pa_per_game
st.sidebar.caption(f'近況最低打席數門檻 = 滾動場數 × 單場最低打席數 = {rolling_n} × {min_pa_per_game} = **{min_recent_pa}**'
                    '（這個數字會跟著上面兩個滑桿自動變動，不用手動調）')

year_df = df[(df['year'] == CURRENT_YEAR) & (df['pa'] >= min_pa_per_game)].copy()
st.caption(
    f'{CURRENT_YEAR} 年（進行中），濾掉單場打席數 < {min_pa_per_game} 之後，共 '
    f'{year_df["batter_acnt"].nunique()} 位打者、{year_df["game_sno"].nunique()} 場比賽。'
)

official_lookup = load_official_season_stats(CURRENT_YEAR)
summary, lg_obp, lg_slg = build_player_summary(year_df, rolling_n, official_lookup)

with st.expander('ℹ️ 為什麼這頁改用 OPS+，不是 wOBA/wRC+？點開看說明'):
    st.markdown(f"""
    - 上一版這裡曾經用一組自己近似出來的線性權重算「類 wOBA」，被問到「幹嗎自創數值，這樣還很難解釋」——確實，
      wOBA 的權重是每年拿全聯盟真實比賽結果回歸算出來的，rebas.tw 才有官方逐年校正的權重，我這邊沒有管道抓到那組真正的權重表。
    - 所以這頁改成用 **AVG / OBP / SLG / ISO / BABIP / OPS / OPS+**——這幾個全部是直接定義出來的公開公式，
      不需要任何回歸出來的權重：OPS = OBP + SLG；**OPS+ = 100 × [(OBP÷聯盟OBP) + (SLG÷聯盟SLG) − 1]**（100 = 聯盟平均，
      沒有球場因子校正，CPBL 各球場差異的資料還不夠完整，這裡當中性球場處理）。用我們自己爬的逐場資料就能算出來，沒有「自創」的部分。
    - {CURRENT_YEAR} 年球季至今，聯盟平均 OBP = **{lg_obp:.3f}**、SLG = **{lg_slg:.3f}**（近況變化排序、下面的圖都是用 OPS+）。
    - **官方球季 wOBA / wRC+** 這兩欄，是直接從你既有的 `cpbl_batting_2020_2026` 表拉出來的——那張表本來就是從 rebas.tw
      爬回來的真實官方數字，這裡原封不動顯示，方便你對照，但**沒有**混進「近況變化」的排序計算，
      因為 rebas.tw 只公布球季累計數字，沒有逐場資料，沒辦法算「近 15 場」這種滾動窗口的官方 wOBA。
    """)

# ── 固定關注名單 ──────────────────────────────────────────────
st.subheader('⭐ 關注名單')
st.caption('目前寫死在 `pages/1_打者趨勢雷達.py` 檔案最上面的 WATCHLIST 這個 list，'
           '是從你專案裡本來就有的球員筆記本（林立、陳傑憲、王威晨、張育成）猜的，想換人直接改那行文字就好。')
watch_df = summary[summary['球員'].isin(WATCHLIST)].sort_values('球員')
if len(watch_df) > 0:
    st.dataframe(watch_df.set_index('球員'), use_container_width=True)
else:
    st.info('關注名單裡的球員這個年度沒有資料。')

# ── 近期漲最多 / 跌最多 ───────────────────────────────────────
eligible = summary[summary[f'近{rolling_n}場_PA'] >= min_recent_pa].dropna(subset=['近況變化(OPS+)'])

col1, col2 = st.columns(2)
with col1:
    st.subheader('🔥 近期漲最多 Top 10')
    risers = eligible.sort_values('近況變化(OPS+)', ascending=False).head(10)
    st.dataframe(risers.set_index('球員'), use_container_width=True)
with col2:
    st.subheader('🧊 近期跌最多 Top 10')
    fallers = eligible.sort_values('近況變化(OPS+)', ascending=True).head(10)
    st.dataframe(fallers.set_index('球員'), use_container_width=True)

# ── 圖：球季至今 vs 近況 OPS+ 散佈圖 ─────────────────────
st.subheader('球季至今 vs 近況：誰正在轉變？')
st.markdown(f"""
**怎麼看這張圖**：每個點是一位打者。
- **橫軸**＝他 {CURRENT_YEAR} 年球季至今的 OPS+（100 = 聯盟平均，比較穩定、看整體實力）
- **縱軸**＝他最近 {rolling_n} 場的 OPS+（比較容易受手感/運氣影響，但反映近況）
- **灰色虛線**是「近況＝球季至今」的參考線：點落在**線的上方**＝最近表現比他至今平均更好（正在變熱）；落在**線的下方**＝最近比至今平均差（正在變冷）
- **顏色**跟灰線是同一件事，藍色＝近況變化是正的（變熱）、紅色＝負的（變冷），深淺代表變化幅度
- **點的大小**＝最近 {rolling_n} 場的打席數，點越大代表這個近況數字的樣本越可靠，小點看看就好、不用太當真
""")
plot_df = eligible.dropna(subset=['球季至今_OPS+', f'近{rolling_n}場_OPS+'])
if len(plot_df) > 0:
    fig = px.scatter(
        plot_df, x='球季至今_OPS+', y=f'近{rolling_n}場_OPS+',
        color='近況變化(OPS+)', color_continuous_scale='RdBu', color_continuous_midpoint=0,
        size=f'近{rolling_n}場_PA', hover_name='球員', hover_data={'球隊': True},
        labels={'球季至今_OPS+': f'{CURRENT_YEAR}年球季至今 OPS+', f'近{rolling_n}場_OPS+': f'近{rolling_n}場 OPS+'},
        height=600,
    )
    max_v = max(plot_df['球季至今_OPS+'].max(), plot_df[f'近{rolling_n}場_OPS+'].max())
    min_v = min(plot_df['球季至今_OPS+'].min(), plot_df[f'近{rolling_n}場_OPS+'].min())
    fig.add_shape(type='line', x0=min_v, y0=min_v, x1=max_v, y1=max_v,
                  line=dict(dash='dot', color='gray'))
    fig.add_vline(x=100, line_dash='dot', line_color='lightgray', annotation_text='聯盟平均(100)')
    fig.add_hline(y=100, line_dash='dot', line_color='lightgray')
    fig.add_annotation(x=min_v, y=max_v, text='↑ 線的上方：近況比至今平均更好（變熱）',
                        showarrow=False, xanchor='left', yanchor='top', font=dict(size=12, color='#2166AC'))
    fig.add_annotation(x=max_v, y=min_v, text='↓ 線的下方：近況比至今平均差（變冷）',
                        showarrow=False, xanchor='right', yanchor='bottom', font=dict(size=12, color='#B2182B'))
    fig.update_layout(plot_bgcolor='white')
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info('目前篩選條件下沒有足夠資料畫圖。')
