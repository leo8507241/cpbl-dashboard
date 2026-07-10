import os
import requests
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="投手弱點分析", layout="wide")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PITCH_NAMES = {
    "FF":"四縫線速球","SI":"二縫/沉球","FC":"卡特球",
    "SL":"滑球","SW":"掃球","CU":"曲球",
    "CH":"變速球","FO":"指叉球","FS":"分叉球","EP":"慢速球",
}
BASES_LABEL = {0:"空壘",1:"一壘",2:"二壘",3:"一二壘",4:"三壘",5:"一三壘",6:"二三壘",7:"滿壘"}

SZ_X1, SZ_X2 = -50, 50
SZ_Y1, SZ_Y2 = -50, 50
X3 = (SZ_X2 - SZ_X1) / 3
Y3 = (SZ_Y2 - SZ_Y1) / 3

def sector(x, y):
    in_x = SZ_X1 <= x <= SZ_X2
    in_y = SZ_Y1 <= y <= SZ_Y2
    if in_x and in_y:
        c = min(2, int((x - SZ_X1) / X3))
        r = min(2, int((SZ_Y2 - y) / Y3))
        return ("SZ", r * 3 + c + 1)
    if abs(x) <= 80 and abs(y) <= 80:
        if not in_x and not in_y:
            if x < SZ_X1 and y > SZ_Y2: return ("CH_TL", 0)
            if x > SZ_X2 and y > SZ_Y2: return ("CH_TR", 0)
            if x < SZ_X1 and y < SZ_Y1: return ("CH_BL", 0)
            return ("CH_BR", 0)
        if y > SZ_Y2: return ("CH_T", min(2, int((x - SZ_X1) / X3)))
        if y < SZ_Y1: return ("CH_B", min(2, int((x - SZ_X1) / X3)))
        if x < SZ_X1: return ("CH_L", 0)
        return ("CH_R", 0)
    if y > 80:
        if x < -80: return ("WA_TL", 0)
        if x >  80: return ("WA_TR", 0)
        if x < SZ_X1:           return ("WA_T", 0)
        elif x < SZ_X1 + X3:    return ("WA_T", 1)
        elif x < SZ_X1 + 2*X3:  return ("WA_T", 2)
        elif x <= SZ_X2:         return ("WA_T", 3)
        else:                    return ("WA_T", 4)
    if y < -80:
        if x < -80: return ("WA_BL", 0)
        if x >  80: return ("WA_BR", 0)
        if x < SZ_X1:           return ("WA_B", 0)
        elif x < SZ_X1 + X3:    return ("WA_B", 1)
        elif x < SZ_X1 + 2*X3:  return ("WA_B", 2)
        elif x <= SZ_X2:         return ("WA_B", 3)
        else:                    return ("WA_B", 4)
    if x < -80:
        if y > SZ_Y2:            return ("WA_L", 0)
        elif y >= SZ_Y2 - Y3:    return ("WA_L", 1)
        elif y >= SZ_Y2 - 2*Y3:  return ("WA_L", 2)
        elif y >= SZ_Y1:         return ("WA_L", 3)
        else:                    return ("WA_L", 4)
    if y > SZ_Y2:                return ("WA_R", 0)
    elif y >= SZ_Y2 - Y3:        return ("WA_R", 1)
    elif y >= SZ_Y2 - 2*Y3:      return ("WA_R", 2)
    elif y >= SZ_Y1:             return ("WA_R", 3)
    else:                        return ("WA_R", 4)

SWING_CODES = {"SW","F","FT","H","BUNT","FOUL_BUNT","TRY_BUNT"}
HIT_RESULTS  = {"1B","2B","3B","HR","IH"}
GO_RESULTS   = {"GO","GIDP"}
FO_RESULTS   = {"FO","SF"}
SO_RESULTS   = {"SO"}
BB_RESULTS   = {"uBB","IBB","HBP","BB"}

METRIC_FORMULA = {
    "揮空率%": "揮空次數 ÷ 揮棒次數 × 100（揮棒 = 有出棒動作，含揮空/界外/打入場）",
    "接觸率%": "打入場次數 ÷ 揮棒次數 × 100（接觸 = 球被打進場內）",
    "揮棒率%": "揮棒次數 ÷ 投到該區的球數 × 100",
    "投球%":   "該區投球數 ÷ 所有投球數 × 100",
}

METRIC_DETAIL = {
    "投球%": {
        "公式":   "格內落球數 ÷ 全部有座標投球數 × 100",
        "分母說明": "全部有座標的投球總數（固定值，各格加總 = 100%）",
        "格內小字": "落在該格的**投球數**（即分子；各格加總 = 投球總數）",
        "用途":   "看投手最慣用哪個區域，數字越高代表越常投到這裡",
    },
    "揮空率%": {
        "公式":   "該格揮空次數 ÷ 該格揮棒次數 × 100",
        "分母說明": "揮棒 = 打者有出棒（含揮空 / 界外 / 打入場），不含被動看球",
        "格內小字": "落在該格且打者有**出棒**的次數（分母，揮棒次數）",
        "用途":   "哪個區域最容易讓打者揮空，數字越高對投手越有利",
    },
    "揮棒率%": {
        "公式":   "該格揮棒次數 ÷ 該格投球數 × 100",
        "分母說明": "投球數 = 所有投進該格的球（不管打者有沒有揮棒）",
        "格內小字": "落在該格的**投球總數**（分母）",
        "用途":   "哪個區域最容易誘出打者揮棒，數字越高代表打者越容易出棒",
    },
    "接觸率%": {
        "公式":   "該格打入場次數 ÷ 該格揮棒次數 × 100",
        "分母說明": "揮棒次數 = 打者有出棒（含揮空 / 界外 / 打入場）",
        "格內小字": "落在該格且打者有**出棒**的次數（分母，揮棒次數）",
        "用途":   "揮棒後打到球的比例，數字越低代表投手在這區越難被打到",
    },
}

ALL_COUNTS = [(b, s) for b in range(4) for s in range(3)]

def count_type(b, s):
    if (b, s) in {(2,0),(3,0),(3,1)}: return "hitter"
    if (b, s) in {(0,2),(1,2),(2,2)}: return "pitcher"
    return "neutral"

COUNT_BG = {"hitter":"#dbeafe","pitcher":"#fce7f3","neutral":"#f9fafb"}
COUNT_BD = {"hitter":"#2563eb","pitcher":"#db2777","neutral":"#9ca3af"}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
SEASON_IDS = [(2023,"sk"),(2024,"xa"),(2025,"JO"),(2026,"oB")]

def ip_str(ipo):
    if not ipo: return "0"
    return f"{int(ipo)//3}.{int(ipo)%3}"


@st.cache_data
def load():
    df = pd.read_csv(os.path.join(ROOT, "pitcher_pitches.csv"), low_memory=False)
    for c in ["velocity","rpm","coord_x","coord_y","LI","RE24","WPA",
              "balls_before","strikes_before"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["is_swing"]      = df["result_code"].isin(SWING_CODES)
    df["is_whiff"]      = df["result_code"] == "SW"
    df["is_contact"]    = df["in_play"] == True
    df["is_hit_actual"] = df["pa_result_type"].isin(HIT_RESULTS)
    df["is_go"]         = df["pa_result_type"].isin(GO_RESULTS)
    df["is_fo"]         = df["pa_result_type"].isin(FO_RESULTS)
    df["is_so"]         = df["pa_result_type"].isin(SO_RESULTS)
    df["is_bb"]         = df["pa_result_type"].isin(BB_RESULTS)
    return df


@st.cache_data(ttl=3600)
def fetch_pitcher_season_stats(name):
    std_rows, adv_rows = [], []
    for yr, sid in SEASON_IDS:
        try:
            for section, dest in [("standard", std_rows), ("advanced", adv_rows)]:
                url = (f"https://www.rebas.tw/api/seasons/CPBL-{yr}-{sid}/leaders"
                       f"?type=pitcher&section={section}&pa=undefined")
                data = requests.get(url, headers={"User-Agent": UA}, timeout=8).json().get("data",[])
                for p in data:
                    if p.get("player",{}).get("name","") == name:
                        row = {k:v for k,v in p.items() if k!="player" and k!="reach_min"}
                        row["year"] = yr
                        dest.append(row)
                        break
        except Exception:
            pass

    if not std_rows:
        return pd.DataFrame()
    df_s = pd.DataFrame(std_rows).set_index("year")
    df_a = pd.DataFrame(adv_rows).set_index("year") if adv_rows else pd.DataFrame()
    if not df_a.empty:
        # 只加入 advanced 裡 standard 沒有的欄位，避免 suffix 污染欄名
        unique_adv = [c for c in df_a.columns if c not in df_s.columns]
        merged = df_s.join(df_a[unique_adv]) if unique_adv else df_s.copy()
    else:
        merged = df_s
    return merged.reset_index()


df = load()

# ── 側邊欄 ──────────────────────────────────────────────
st.sidebar.header("篩選條件")
pitcher   = st.sidebar.selectbox("投手", sorted(df["pitcher"].unique()))
years_all = sorted(df["year"].unique())
years_sel = st.sidebar.multiselect("年度", years_all, default=years_all)
hand_sel  = st.sidebar.radio("對手打者慣用手", ["全部","右打(R)","左打(L)"])

sub = df[df["pitcher"] == pitcher]
if years_sel: sub = sub[sub["year"].isin(years_sel)]
if hand_sel == "右打(R)": sub = sub[sub["b_hand"] == "R"]
elif hand_sel == "左打(L)": sub = sub[sub["b_hand"] == "L"]

st.title(f"⚾ 投手弱點分析｜{pitcher}")
if sub.empty:
    st.warning("無資料，請調整篩選條件。"); st.stop()

# 球數配球分析 共用資料
sub_c = sub.dropna(subset=["balls_before","strikes_before"]).copy()
sub_c["balls_before"]   = sub_c["balls_before"].astype(int)
sub_c["strikes_before"] = sub_c["strikes_before"].astype(int)
sub_c = sub_c[sub_c["balls_before"].between(0,3) & sub_c["strikes_before"].between(0,2)]

# ── 將 RESULT_MAP 提到此處，供 session_state 預初始化使用 ────
RESULT_MAP = {
    "全部球":    None,
    "安打":      lambda d: d["is_hit_actual"],
    "揮空":      lambda d: d["result_code"] == "SW",
    "滾地球出局": lambda d: d["is_go"] | (d["trajectory"] == "G"),
    "高飛球出局": lambda d: d["is_fo"] | (d["trajectory"] == "F"),
}
RESULT_MAP_FP = {
    "全部球":              None,
    "安打":               lambda d: d["is_hit_actual"],
    "揮空":               lambda d: d["result_code"] == "SW",
    "界外球":             lambda d: d["result_code"].isin({"F","FT","FOUL_BUNT"}),
    "滾地球出局":         lambda d: d["is_go"] | (d["trajectory"] == "G"),
    "高飛球出局":         lambda d: d["is_fo"] | (d["trajectory"] == "F"),
    "其他（好球/壞球/觸身球）": lambda d: (
        ~d["is_hit_actual"] &
        ~(d["result_code"] == "SW") &
        ~d["result_code"].isin({"F","FT","FOUL_BUNT"}) &
        ~(d["is_go"] | (d["trajectory"] == "G")) &
        ~(d["is_fo"] | (d["trajectory"] == "F"))
    ),
}

# ── 預初始化所有 widget session_state，防止首次互動觸發 tab 重置 ──
def _init_ss(key, val):
    if key not in st.session_state:
        st.session_state[key] = val

# 靜態預設
_init_ss("t1_vr",  "球速(km/h)")
_init_ss("z_res",  "全部球")
_init_ss("z_met",  list(METRIC_FORMULA.keys())[0])
_init_ss("z_sl",   25)
_init_ss("sit_pt", "全部球種")
_init_ss("fp_res", "全部球")
_init_ss("fp_sl",  20)
_init_ss("c5_sl",  20)
_init_ss("c5_pt",  "全部球種")
_init_ss("fp_pt",  "全部球種")
_init_ss("cnt_sel_t5", "B0-S0")
_init_ss("c5_cnt",     "B0-S0")

# 動態預設：用最多球種作為 t1_pt、z_pt 預設
_pt_vc_pre = sub["pitch_type"].value_counts()
_active_pts_pre = [p for p in _pt_vc_pre.index if _pt_vc_pre[p] >= 20]
_init_ss("t1_pt", _active_pts_pre[0] if _active_pts_pre else "")
_init_ss("z_pt",  "全部球種")

# 情境弱點 D 區：預算第一個有效（壘包｜出局）選項
_pre_bs = sub.copy()
_pre_bs["壘包"] = (pd.to_numeric(_pre_bs["bases"], errors="coerce")
                   .fillna(0).astype(int).map(lambda x: BASES_LABEL.get(x, "?")))
_pre_bs["出局"] = (pd.to_numeric(_pre_bs["end_outs"], errors="coerce")
                   .fillna(0).astype(int).map({0:"0 出",1:"1 出",2:"2 出"}))
_pre_grp = _pre_bs.groupby(["壘包","出局"]).size().reset_index(name="n")
_pre_grp = _pre_grp[_pre_grp["n"] >= 5]
_first_sit_pre = None
for _o_pre in ["0 出","1 出","2 出"]:
    for _b_pre in [BASES_LABEL[i] for i in range(8)]:
        if not _pre_grp[(_pre_grp["壘包"]==_b_pre) & (_pre_grp["出局"]==_o_pre)].empty:
            _first_sit_pre = f"{_b_pre}｜{_o_pre}"; break
    if _first_sit_pre: break
if _first_sit_pre:
    _init_ss("sit_d_sel", _first_sit_pre)

_TAB_NAMES = ["📊 年度趨勢","🎯 落點分析","⚡ 情境弱點","1️⃣ 首球策略","🔢 球數配球","💡 打擊建議"]
_init_ss("_nav", _TAB_NAMES[0])
_sec = st.radio("分析項目", _TAB_NAMES, horizontal=True,
                key="_nav", label_visibility="collapsed")

st.markdown("---")

# ═══════════════════════════════════════════════════════════
# TAB 1：年度趨勢
# ═══════════════════════════════════════════════════════════
if _sec == "📊 年度趨勢":
    yr_list = sorted(sub["year"].unique())

    # ── A. 投手每年成績（從 rebas.tw 抓）────────────────
    st.subheader("投手歷年成績")
    season_stats = fetch_pitcher_season_stats(pitcher)
    if not season_stats.empty:
        display_cols = {}
        mapping = {
            "year":"年度","R_W":"勝","R_L":"敗","R_SV":"救援","games":"出賽",
            "IPOut":"局數","ERA":"防禦率","ERAplus":"ERA+","H":"被安打","HR":"被全壘打",
            "BB":"保送","SO":"三振","WHIP":"WHIP","FIP":"FIP",
            "Kp":"K%","BBp":"BB%","Whiffp":"Whiff%","BABIP":"BABIP",
        }
        filtered = season_stats[[c for c in mapping if c in season_stats.columns]].copy()
        if "IPOut" in filtered.columns:
            filtered["IPOut"] = filtered["IPOut"].apply(ip_str)
        filtered = filtered.rename(columns=mapping)
        st.dataframe(filtered.reset_index(drop=True), hide_index=True, use_container_width=True)
    else:
        st.info("rebas.tw 找不到此投手成績（外籍球員名稱可能略有差異）")

    # ── B. 球種年度趨勢（下拉選擇球種）─────────────────
    st.markdown("---")
    st.subheader("球種年度趨勢")

    pt_counts = sub["pitch_type"].value_counts()
    pt_opts = [p for p in pt_counts.index if pt_counts[p] >= 20]
    pt_default = pt_opts[0] if pt_opts else None

    col_pt, col_met = st.columns([2, 2])
    with col_pt:
        pt_sel = st.selectbox("選擇球種", pt_opts,
                              format_func=lambda p: f"{PITCH_NAMES.get(p,p)} ({p})",
                              key="t1_pt")
    with col_met:
        velo_rpm = st.radio("球速/轉速", ["球速(km/h)","轉速(RPM)"], horizontal=True, key="t1_vr")

    if pt_sel:
        pt_data = sub[sub["pitch_type"] == pt_sel].groupby("year")
        yr_total = sub.groupby("year").size()

        trend_rows = []
        for yr, g in pt_data:
            sw           = g["is_swing"].sum()
            in_play      = g["is_contact"].sum()
            # 安打率：只看「打入場那顆球」是否被打成安打，避免 PA 結果被記在所有球上
            contact_hits = g[g["is_contact"]]["is_hit_actual"].sum()
            trend_rows.append({
                "年度":        int(yr),
                "使用%":       round(len(g)/yr_total.get(yr,1)*100, 1),
                "球速(km/h)":  round(g["velocity"].mean(), 1),
                "轉速(RPM)":   round(g["rpm"].mean(), 0),
                "揮棒%":       round(sw/len(g)*100, 1),
                "揮空%":       round(g["is_whiff"].sum()/sw*100, 1) if sw else None,
                "接觸%":       round(in_play/sw*100, 1) if sw else None,
                "安打率%":     round(contact_hits/in_play*100, 1) if in_play else None,
            })
        tdf = pd.DataFrame(trend_rows)
        st.dataframe(tdf, hide_index=True, use_container_width=True)
        st.info(
            "**指標說明**  \n"
            "**使用%** = 該球種投球數 ÷ 當年總投球數 × 100  \n"
            "**揮棒%** = 揮棒次數 ÷ 投球數 × 100（揮棒＝有出棒，含揮空／界外／打入場）  \n"
            "**揮空%** = 揮空次數 ÷ 揮棒次數 × 100  \n"
            "**接觸%** = 打入場次數 ÷ 揮棒次數 × 100（揮棒後成功打到球的比率）  \n"
            "**安打率%** = 安打次數 ÷ 打入場次數 × 100（打入場後被打成安打的比率，越高對打者越有利）"
        )

        # 折線圖：球速 or RPM + 揮空%
        metric_col = "球速(km/h)" if velo_rpm == "球速(km/h)" else "轉速(RPM)"
        fig_trend = go.Figure()
        if metric_col in tdf.columns:
            fig_trend.add_scatter(x=tdf["年度"], y=tdf[metric_col],
                                  mode="lines+markers", name=metric_col,
                                  line=dict(color="#2563eb", width=2.5), yaxis="y")
        if "揮空%" in tdf.columns:
            fig_trend.add_scatter(x=tdf["年度"], y=tdf["揮空%"],
                                  mode="lines+markers", name="揮空%",
                                  line=dict(color="#d0021b", width=2.5, dash="dot"), yaxis="y2")
        fig_trend.update_layout(
            height=280,
            xaxis=dict(tickmode="array", tickvals=yr_list, ticktext=[str(y) for y in yr_list]),
            yaxis=dict(title=metric_col, side="left"),
            yaxis2=dict(title="揮空%", side="right", overlaying="y", showgrid=False),
            margin=dict(l=0,r=0,t=10,b=30),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig_trend, use_container_width=True)

    # ── C. 各年度球種使用比例（群組長條圖）─────────────
    st.markdown("---")
    st.subheader("各年度球種使用比例")

    # 只保留有使用的球種
    mix = sub.groupby(["year","pitch_type"]).size().reset_index(name="n")
    mix["pct"] = mix.groupby("year")["n"].transform(lambda x: x/x.sum()*100).round(1)
    active_pts = mix.groupby("pitch_type")["n"].sum()
    active_pts = active_pts[active_pts >= 10].index.tolist()
    mix = mix[mix["pitch_type"].isin(active_pts)].copy()
    mix["球種"] = mix["pitch_type"].map(lambda x: PITCH_NAMES.get(x,x))

    fig_bar = px.bar(mix, x="year", y="pct", color="球種", barmode="group",
                     labels={"year":"年度","pct":"使用率%","球種":"球種"})
    fig_bar.update_xaxes(tickmode="array", tickvals=yr_list, ticktext=[str(y) for y in yr_list])
    fig_bar.update_layout(height=320, margin=dict(l=0,r=0,t=10,b=30),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(fig_bar, use_container_width=True)


# ═══════════════════════════════════════════════════════════
# TAB 2：落點分析
# ═══════════════════════════════════════════════════════════
if _sec == "🎯 落點分析":

    c1, c2, c3, c4 = st.columns([2,2,2,3])
    with c1:
        pt_opts2 = ["全部球種"] + [
            f"{PITCH_NAMES.get(p,p)} ({p})"
            for p in sub["pitch_type"].value_counts().index if
            sub["pitch_type"].value_counts()[p] >= 10]
        pt_sel2 = st.selectbox("球種", pt_opts2, key="z_pt")
    with c2:
        result_sel2 = st.selectbox("顯示情境", list(RESULT_MAP.keys()), key="z_res")
    with c3:
        zone_met = st.selectbox("九宮格指標", list(METRIC_FORMULA.keys()), key="z_met")
    with c4:
        slider_val = st.slider("◄ 逐球散點　　落點熱圖 ►", 0, 100, 25, key="z_sl")

    _d = METRIC_DETAIL[zone_met]
    st.info(
        f"**{zone_met}　公式：** {_d['公式']}  \n"
        f"**分母說明：** {_d['分母說明']}  \n"
        f"**格內小字代表：** {_d['格內小字']}  \n"
        f"**用途：** {_d['用途']}"
    )

    sub_z = sub.copy()
    if pt_sel2 != "全部球種":
        pt_code2 = pt_sel2.split("(")[-1].rstrip(")")
        sub_z = sub_z[sub_z["pitch_type"] == pt_code2]

    filter_fn = RESULT_MAP[result_sel2]
    sub_z_plot = sub_z[filter_fn(sub_z)] if filter_fn else sub_z
    sub_z_plot = sub_z_plot.dropna(subset=["coord_x","coord_y"])

    # 只取有座標的球做分區（NaN 座標不歸入任何區域，避免污染無效區計數）
    _sub_z_c = sub_z.dropna(subset=["coord_x", "coord_y"]).copy()
    _sub_z_c["_sector"] = _sub_z_c.apply(lambda r: sector(r["coord_x"], r["coord_y"]), axis=1)
    _sz_total = len(_sub_z_c)  # 投球% 分母：只計有座標的球

    def sector_metric(key, met):
        g = _sub_z_c[_sub_z_c["_sector"] == key]
        total = len(g)
        if total == 0: return None, 0    # 完全沒球才顯示 "-"
        sw = int(g["is_swing"].sum())
        if met == "揮空率%":
            v = g["is_whiff"].sum()/sw*100 if sw else 0.0   # 0揮棒 → 0%
            return v, sw
        elif met == "接觸率%":
            v = g["is_contact"].sum()/sw*100 if sw else 0.0
            return v, sw
        elif met == "揮棒率%":
            return sw/total*100, total
        else:
            return (total/_sz_total*100 if _sz_total else 0.0), total

    def cell_color(v, met):
        if v is None: return "#e8e8e8"
        intensity = min(1.0, v / (30 if met=="揮空率%" else 60 if met in ("接觸率%","揮棒率%") else 20))
        r,g,b = int(255), int(255*(1-intensity*0.8)), int(255*(1-intensity*0.9))
        return f"rgb({r},{g},{b})"

    def fmt_v(v): return f"{v:.0f}%" if v is not None else "-"

    # ── 散點圖（全寬）─────────────────────────────────
    fig_z = go.Figure()

    if slider_val < 65:
        op = max(0.15, 1 - slider_val/65)
        if not sub_z_plot.empty:
            _iz = (sub_z_plot["coord_x"].between(SZ_X1, SZ_X2) &
                   sub_z_plot["coord_y"].between(SZ_Y1, SZ_Y2))
            _z_in, _z_out = sub_z_plot[_iz], sub_z_plot[~_iz]
            if not _z_in.empty:
                fig_z.add_scatter(x=_z_in["coord_x"], y=_z_in["coord_y"],
                                  mode="markers", name=f"{result_sel2}（好球帶內）",
                                  marker=dict(color="steelblue", size=6, opacity=op))
            if not _z_out.empty:
                fig_z.add_scatter(x=_z_out["coord_x"], y=_z_out["coord_y"],
                                  mode="markers", name=f"{result_sel2}（追打/無效區）",
                                  marker=dict(color="#f97316", size=6, opacity=op))

    if slider_val > 35 and not sub_z_plot.empty:
        op2 = min(1.0, (slider_val-35)/65)
        fig_z.add_trace(go.Histogram2dContour(
            x=sub_z_plot["coord_x"], y=sub_z_plot["coord_y"],
            colorscale="Reds", showscale=False,
            opacity=op2, contours=dict(showlines=False), ncontours=12))

    fig_z.add_shape(type="rect", x0=-105, x1=105, y0=-105, y1=105,
                    line=dict(color="#888888", width=1.2, dash="dash"),
                    fillcolor="rgba(180,180,180,0.15)", layer="below")
    fig_z.add_shape(type="rect", x0=-80, x1=80, y0=-80, y1=80,
                    line=dict(color="#cc8000", width=1.5, dash="dot"),
                    fillcolor="rgba(255,180,0,0.12)", layer="below")
    fig_z.add_shape(type="rect", x0=SZ_X1, x1=SZ_X2, y0=SZ_Y1, y1=SZ_Y2,
                    line=dict(color="black", width=2),
                    fillcolor="rgba(100,149,237,0.10)", layer="below")
    for xv in [SZ_X1+X3, SZ_X1+2*X3]:
        fig_z.add_vline(x=xv, line=dict(color="gray", width=1, dash="dot"))
    for yv in [SZ_Y1+Y3, SZ_Y1+2*Y3]:
        fig_z.add_hline(y=yv, line=dict(color="gray", width=1, dash="dot"))
    for _zi in range(9):
        _zr, _zc = divmod(_zi, 3)
        fig_z.add_annotation(x=SZ_X1+_zc*X3+X3/2, y=SZ_Y2-_zr*Y3-Y3/2,
                              text=str(_zi+1), showarrow=False,
                              font=dict(color="rgba(0,0,0,0.22)", size=16))
    fig_z.add_annotation(x=86, y=105, text="無效區", showarrow=False, yanchor="bottom",
                          font=dict(size=10, color="#777777"))
    fig_z.add_annotation(x=66, y=80, text="追打區", showarrow=False, yanchor="bottom",
                          font=dict(size=10, color="#cc8000"))
    fig_z.update_layout(
        xaxis=dict(range=[-120, 120], zeroline=False, showgrid=False, showticklabels=False),
        yaxis=dict(range=[-120, 120], zeroline=False, showgrid=False, showticklabels=False,
                   scaleanchor="x", scaleratio=1),
        height=480, margin=dict(l=5, r=5, t=10, b=5),
        legend=dict(orientation="h", y=-0.05, font_size=11),
        plot_bgcolor="rgba(248,248,248,1)",
    )
    st.plotly_chart(fig_z, use_container_width=True)
    st.caption("左側散點依「顯示情境」篩選；下方九宮格統計使用全部情境投球（不受情境篩選影響），好球帶外的點為橘色。")

    # ── 九宮格（散點圖下方，置中顯示）────────────────
    st.markdown(f"**九宮格｜{zone_met}**")
    ch_t  = [sector_metric(("CH_T",  i), zone_met) for i in range(3)]
    ch_b  = [sector_metric(("CH_B",  i), zone_met) for i in range(3)]
    ch_l  = sector_metric(("CH_L",  0), zone_met)
    ch_r  = sector_metric(("CH_R",  0), zone_met)
    ch_tl = sector_metric(("CH_TL", 0), zone_met)
    ch_tr = sector_metric(("CH_TR", 0), zone_met)
    ch_bl = sector_metric(("CH_BL", 0), zone_met)
    ch_br = sector_metric(("CH_BR", 0), zone_met)
    sz    = {i: sector_metric(("SZ",   i), zone_met) for i in range(1, 10)}
    wa_tl = sector_metric(("WA_TL", 0), zone_met)
    wa_t  = [sector_metric(("WA_T", i), zone_met) for i in range(5)]
    wa_tr = sector_metric(("WA_TR", 0), zone_met)
    wa_l  = [sector_metric(("WA_L", i), zone_met) for i in range(5)]
    wa_r  = [sector_metric(("WA_R", i), zone_met) for i in range(5)]
    wa_bl = sector_metric(("WA_BL", 0), zone_met)
    wa_b  = [sector_metric(("WA_B", i), zone_met) for i in range(5)]
    wa_br = sector_metric(("WA_BR", 0), zone_met)

    _SZ = 68; _CH = 54; _WA = 48
    CHASE_BG = "#dbeafe"; WASTE_BG = "#f3f4f6"
    n_unit = "揮棒" if zone_met in ("揮空率%", "接觸率%") else "球"
    _sty = "border:1px solid #ccc;padding:0;text-align:center;vertical-align:middle;"

    def td_sz(v, n, bg):
        if v is None:
            return (f'<td style="{_sty}width:{_SZ}px;height:{_SZ}px;'
                    f'background:#e8e8e8;font-size:14px;color:#aaa">-</td>')
        cnt = f"<br><span style='font-size:10px;color:#555'>{n}{n_unit}</span>"
        return (f'<td style="{_sty}width:{_SZ}px;height:{_SZ}px;background:{bg};'
                f'font-size:14px;font-weight:600">{fmt_v(v)}{cnt}</td>')

    def td_ch(v, n, bg, w, h):
        if v is None:
            return (f'<td style="{_sty}width:{w}px;height:{h}px;background:{bg};'
                    f'font-size:12px;color:#aaa">-</td>')
        cnt = f"<br><span style='font-size:9px;color:#444'>{n}{n_unit}</span>"
        return (f'<td style="{_sty}width:{w}px;height:{h}px;background:{bg};font-size:12px">'
                f'{fmt_v(v)}{cnt}</td>')

    def td_wa(v, n, w, h):
        if v is None:
            return (f'<td style="{_sty}width:{w}px;height:{h}px;'
                    f'background:{WASTE_BG};font-size:11px;color:#ccc">-</td>')
        cnt = f"<br><span style='font-size:9px;color:#777'>{n}{n_unit}</span>"
        return (f'<td style="{_sty}width:{w}px;height:{h}px;'
                f'background:{WASTE_BG};font-size:11px;color:#444">{fmt_v(v)}{cnt}</td>')

    html = '<table style="border-collapse:collapse;margin:4px auto">'
    # 第 1 行：WA_TL | WA_T[0..4] | WA_TR（各格獨立資料）
    html += "<tr>"
    html += td_wa(*wa_tl,    _WA, _WA)
    html += td_wa(*wa_t[0],  _CH, _WA)
    html += td_wa(*wa_t[1],  _SZ, _WA)
    html += td_wa(*wa_t[2],  _SZ, _WA)
    html += td_wa(*wa_t[3],  _SZ, _WA)
    html += td_wa(*wa_t[4],  _CH, _WA)
    html += td_wa(*wa_tr,    _WA, _WA)
    html += "</tr>"
    # 第 2 行：WA_L[0] | CH_TL | CH_T×3 | CH_TR | WA_R[0]
    html += "<tr>"
    html += td_wa(*wa_l[0], _WA, _CH)
    html += td_ch(*ch_tl, CHASE_BG, _CH, _CH)
    for i in range(3): html += td_ch(*ch_t[i], CHASE_BG, _SZ, _CH)
    html += td_ch(*ch_tr, CHASE_BG, _CH, _CH)
    html += td_wa(*wa_r[0], _WA, _CH)
    html += "</tr>"
    # 第 3–5 行：WA_L[1-3] | CH_L | 好球帶 1-9 | CH_R | WA_R[1-3]
    ch_l_v, ch_l_n = ch_l
    ch_r_v, ch_r_n = ch_r
    for row_idx, zones in enumerate([[1,2,3],[4,5,6],[7,8,9]]):
        html += "<tr>"
        html += td_wa(*wa_l[row_idx + 1], _WA, _SZ)
        html += td_ch(ch_l_v, ch_l_n, CHASE_BG, _CH, _SZ)
        for z in zones:
            v, n = sz[z]; html += td_sz(v, n, cell_color(v, zone_met))
        html += td_ch(ch_r_v, ch_r_n, CHASE_BG, _CH, _SZ)
        html += td_wa(*wa_r[row_idx + 1], _WA, _SZ)
        html += "</tr>"
    # 第 6 行：WA_L[4] | CH_BL | CH_B×3 | CH_BR | WA_R[4]
    html += "<tr>"
    html += td_wa(*wa_l[4], _WA, _CH)
    html += td_ch(*ch_bl, CHASE_BG, _CH, _CH)
    for i in range(3): html += td_ch(*ch_b[i], CHASE_BG, _SZ, _CH)
    html += td_ch(*ch_br, CHASE_BG, _CH, _CH)
    html += td_wa(*wa_r[4], _WA, _CH)
    html += "</tr>"
    # 第 7 行：WA_BL | WA_B[0..4] | WA_BR（各格獨立資料）
    html += "<tr>"
    html += td_wa(*wa_bl,    _WA, _WA)
    html += td_wa(*wa_b[0],  _CH, _WA)
    html += td_wa(*wa_b[1],  _SZ, _WA)
    html += td_wa(*wa_b[2],  _SZ, _WA)
    html += td_wa(*wa_b[3],  _SZ, _WA)
    html += td_wa(*wa_b[4],  _CH, _WA)
    html += td_wa(*wa_br,    _WA, _WA)
    html += "</tr></table>"
    _, _gc, _ = st.columns([1, 2, 1])
    with _gc:
        st.markdown(html, unsafe_allow_html=True)
        st.caption(f"灰=無效區　藍=追打區　紅深=高值 ｜ {zone_met}，小字=分母（{n_unit}）｜ 沒投球的格顯示「-」")


# ═══════════════════════════════════════════════════════════
# TAB 3：情境弱點
# ═══════════════════════════════════════════════════════════
if _sec == "⚡ 情境弱點":
    st.subheader("情境弱點分析")

    # PA 層級（取每打席第一球代表打席情境）
    pa = (sub.sort_values("pitch_seq")
           .groupby(["pitcher","game_date","batter_uid","pa_order"], as_index=False)
           .first())
    pa["bases_lbl"] = pd.to_numeric(pa["bases"], errors="coerce").fillna(0).astype(int).map(
                          lambda x: BASES_LABEL.get(x,"?"))
    pa["end_outs_i"] = pd.to_numeric(pa["end_outs"], errors="coerce").fillna(1).astype(int)
    pa["LI_f"]       = pd.to_numeric(pa["LI"],   errors="coerce")
    pa["RE24_f"]     = pd.to_numeric(pa["RE24"], errors="coerce")
    pa["score_diff"] = pd.to_numeric(pa["home_score"], errors="coerce") - \
                       pd.to_numeric(pa["away_score"], errors="coerce")

    st.info(
        "**資料說明**：打擊率 = 安打數 ÷ 打數（打數不含保送/觸身球/犧牲打飛）　"
        "三振率 = SO ÷ 總打席　保送率 = BB ÷ 總打席"
    )

    # ── A. 壘包 × 出局數熱圖 ─────────────────────────
    cA, cB = st.columns(2)
    with cA:
        st.markdown("##### A. 壘包 × 出局數（打擊率熱圖）")
        # 打擊率 = 安打 ÷ 打數；打數 = 打席 - (保送/觸身球/犧牲打飛)
        _NON_AB = {"HBP", "IBB", "uBB", "SF", "SH"}
        piv = pa.groupby(["bases_lbl","end_outs_i"]).agg(
            安打=("is_hit_actual","sum"),
            打席=("is_hit_actual","count"),
            非AB=("pa_result_type", lambda x: x.isin(_NON_AB).sum()),
        ).reset_index()
        piv["打數"]   = (piv["打席"] - piv["非AB"]).clip(lower=0)
        piv["打擊率"] = (piv["安打"] / piv["打數"].replace(0, float("nan"))).round(3).fillna(0)
        piv_t = piv.pivot(index="bases_lbl", columns="end_outs_i", values="打擊率").fillna(0)
        fig_h = px.imshow(piv_t, color_continuous_scale="RdYlGn_r",
                          text_auto=True, aspect="auto",
                          labels=dict(x="出局數(局末)", y="壘包", color="打擊率"))
        fig_h.update_layout(height=340, margin=dict(l=0,r=0,t=10,b=30))
        st.plotly_chart(fig_h, use_container_width=True)

    with cB:
        st.markdown("##### B. 壘包 × 出局數（三振率熱圖）")
        piv2 = pa.groupby(["bases_lbl","end_outs_i"]).agg(
            三振=("is_so","sum"), 打席=("is_so","count")).reset_index()
        piv2["三振率"] = (piv2["三振"]/piv2["打席"]).round(3)
        piv2_t = piv2.pivot(index="bases_lbl", columns="end_outs_i", values="三振率").fillna(0)
        fig_h2 = px.imshow(piv2_t, color_continuous_scale="Blues",
                           text_auto=True, aspect="auto",
                           labels=dict(x="出局數(局末)", y="壘包", color="三振率"))
        fig_h2.update_layout(height=340, margin=dict(l=0,r=0,t=10,b=30))
        st.plotly_chart(fig_h2, use_container_width=True)

    # ── C. 分差情境 × 投球習慣 ───────────────────────
    st.markdown("---")
    st.markdown("##### C. 分差情境下投球習慣")
    st.caption(
        "分差 = home_score − away_score（正=主隊領先）｜"
        "大幅落後 ≤−4｜小幅落後 −3~−1｜平手 0｜小幅領先 1~3｜大幅領先 ≥4"
    )

    bins = [-99,-4,-1,0,3,99]
    lbls = ["大幅落後(≤-4)","小幅落後(-3~-1)","平手(0)","小幅領先(1~3)","大幅領先(≥4)"]

    # 把分差加到 pitch-level data
    sub_sc = sub.copy()
    sub_sc["score_diff"] = sub_sc["home_score"] - sub_sc["away_score"]
    sub_sc["情境"] = pd.cut(sub_sc["score_diff"], bins=bins, labels=lbls)

    # PA 層級分差
    pa["情境"] = pd.cut(pa["score_diff"], bins=bins, labels=lbls)

    # 情境下球種下拉
    all_pts = sub["pitch_type"].value_counts()
    active_pt_list = [p for p in all_pts.index if all_pts[p] >= 20]
    pt_sit = st.selectbox("球種（過濾情境分析）",
                          ["全部球種"] + [f"{PITCH_NAMES.get(p,p)} ({p})" for p in active_pt_list],
                          key="sit_pt")

    sub_sit = sub_sc.copy()
    if pt_sit != "全部球種":
        pc = pt_sit.split("(")[-1].rstrip(")")
        sub_sit = sub_sit[sub_sit["pitch_type"] == pc]

    # 各情境：使用率、揮空%、接觸% 統計表
    sit_rows = []
    for sit, g in sub_sit.groupby("情境", observed=True):
        sw = g["is_swing"].sum()
        sit_rows.append({
            "情境": sit, "投球#": len(g),
            "使用%": round(len(g)/len(sub_sit)*100,1) if len(sub_sit) else None,
            "揮空%": round(g["is_whiff"].sum()/sw*100,1) if sw else None,
            "接觸%": round(g["is_contact"].sum()/sw*100,1) if sw else None,
            "揮棒%": round(sw/len(g)*100,1),
        })
    st.dataframe(pd.DataFrame(sit_rows), hide_index=True, use_container_width=True)

    # 各情境打席結果（跟著球種下拉動）
    st.markdown("**各情境打席結果**")
    NOT_AB_RESULTS = {"uBB","IBB","HBP","BB","SF","SH","SH_FC","SH_E"}
    if pt_sit != "全部球種":
        pc_sit = pt_sit.split("(")[-1].rstrip(")")
        pa_keys = sub[sub["pitch_type"] == pc_sit][
            ["game_date","batter_uid","pa_order"]].drop_duplicates()
        pa_filt = pa.merge(pa_keys, on=["game_date","batter_uid","pa_order"], how="inner")
    else:
        pa_filt = pa
    pa_sit_rows = []
    for sit, g in pa_filt.groupby("情境", observed=True):
        n = len(g)
        h = int(g["is_hit_actual"].sum())
        so = int(g["is_so"].sum())
        bb = int(g["is_bb"].sum())
        ab = n - int(g["pa_result_type"].isin(NOT_AB_RESULTS).sum())
        pa_sit_rows.append({
            "情境": sit, "打席": n, "打數": ab,
            "安打": h,
            "打擊率": round(h/ab,3) if ab > 0 else None,
            "三振": so, "保送": bb,
            "三振率": round(so/n,3) if n else None,
            "保送率": round(bb/n,3) if n else None,
        })
    st.dataframe(pd.DataFrame(pa_sit_rows), hide_index=True, use_container_width=True)

    # 各情境球種比例群組長條圖
    st.markdown("##### 各分差情境下球種使用比例")
    mix_sit = sub_sc.groupby(["情境","pitch_type"], observed=True).size().reset_index(name="n")
    mix_sit["pct"] = mix_sit.groupby("情境")["n"].transform(lambda x: x/x.sum()*100)
    mix_sit["球種"] = mix_sit["pitch_type"].map(lambda x: PITCH_NAMES.get(x,x))
    # 只保留有使用的球種
    used_pts = mix_sit.groupby("pitch_type")["n"].sum()
    mix_sit = mix_sit[mix_sit["pitch_type"].isin(used_pts[used_pts >= 5].index)]

    fig_sit = px.bar(mix_sit, x="情境", y="pct", color="球種", barmode="group",
                     labels={"pct":"使用率%","情境":""}, height=350)
    fig_sit.update_layout(margin=dict(l=0,r=0,t=10,b=70),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(fig_sit, use_container_width=True)

    # ── D. 壘包 × 出局數 × 球種使用比例（下拉式選單）──────────
    st.markdown("---")
    st.markdown("##### D. 壘包 × 出局數 × 球種使用比例")
    st.caption("選定情境後，查看該投手在此壘包 / 出局數組合下的配球策略")

    sub_bs = sub.copy()
    sub_bs["壘包"] = (pd.to_numeric(sub_bs["bases"], errors="coerce")
                      .fillna(0).astype(int)
                      .map(lambda x: BASES_LABEL.get(x, "?")))
    sub_bs["出局"] = (pd.to_numeric(sub_bs["end_outs"], errors="coerce")
                      .fillna(0).astype(int)
                      .map({0: "0 出", 1: "1 出", 2: "2 出"}))
    sub_bs = sub_bs[sub_bs["出局"].notna()]

    # 建立下拉選單選項（只顯示有足夠資料的情境）
    _out_ord   = ["0 出", "1 出", "2 出"]
    _bases_ord = [BASES_LABEL[i] for i in range(8)
                  if BASES_LABEL.get(i, "?") in sub_bs["壘包"].unique()]
    _grp_counts = (sub_bs.groupby(["壘包", "出局"])
                   .size().reset_index(name="n"))
    opts_d = []
    for _outs in _out_ord:
        for _bases in _bases_ord:
            _row = _grp_counts[
                (_grp_counts["壘包"] == _bases) & (_grp_counts["出局"] == _outs)
            ]
            if not _row.empty and _row["n"].iloc[0] >= 5:
                opts_d.append(f"{_bases}｜{_outs}")

    if opts_d:
        sel_d = st.selectbox("選擇情境", opts_d, key="sit_d_sel")
        sel_bases_d, sel_outs_d = sel_d.split("｜", 1)

        filt_d = sub_bs[
            (sub_bs["壘包"] == sel_bases_d) & (sub_bs["出局"] == sel_outs_d)
        ]
        mix_d_sel = filt_d.groupby("pitch_type").size().reset_index(name="球數")
        total_d   = mix_d_sel["球數"].sum()
        mix_d_sel["使用率%"] = (mix_d_sel["球數"] / total_d * 100).round(1)
        mix_d_sel["球種"]    = mix_d_sel["pitch_type"].map(
            lambda x: PITCH_NAMES.get(x, x))
        mix_d_sel = mix_d_sel.sort_values("使用率%", ascending=False)

        col_d1, col_d2 = st.columns([3, 2])
        with col_d1:
            fig_d = px.bar(
                mix_d_sel, x="球種", y="使用率%", color="球種",
                text="使用率%",
                labels={"使用率%": "使用率%", "球種": ""},
                height=360,
            )
            fig_d.update_traces(
                texttemplate="%{text}%", textposition="outside",
                showlegend=False,
            )
            fig_d.update_layout(
                margin=dict(l=0, r=0, t=30, b=40),
                showlegend=False,
                yaxis_range=[0, mix_d_sel["使用率%"].max() * 1.35],
                title=dict(text=f"{sel_d}　共 {total_d} 球", x=0.5, font_size=13),
            )
            st.plotly_chart(fig_d, use_container_width=True)

        with col_d2:
            st.markdown(f"**{sel_d}　共 {total_d} 球**")
            tbl_d = (mix_d_sel[["球種", "使用率%", "球數"]]
                     .reset_index(drop=True))
            st.dataframe(tbl_d, use_container_width=True, hide_index=True)
    else:
        st.info("此投手的壘包 / 出局數資料不足，無法分析。")


# ═══════════════════════════════════════════════════════════
# TAB 4：首球策略
# ═══════════════════════════════════════════════════════════
if _sec == "1️⃣ 首球策略":
    st.subheader("首球（0-0 count）策略")
    st.caption(
        "揮棒率 = 揮棒次數 ÷ 首球投球數 × 100（揮棒含揮空/界外/打入場）　"
        "揮空率 = 揮空次數 ÷ 揮棒次數 × 100　"
        "安打率 = 打席結果為安打 ÷ 總打席數（首球安打：第一顆球被打入場且為安打）"
    )

    fp = sub[sub["is_first_pitch"] == True].copy()
    if fp.empty:
        st.info("無首球資料"); st.stop()

    sw_fp = fp["is_swing"].sum()
    # 首球安打 = 首球打入場 且 該打席最終是安打
    fp_hits    = fp["is_hit_actual"].sum()
    fp_in_play = fp["is_contact"].sum()
    fp_out     = (fp["is_contact"] & ~fp["is_hit_actual"]).sum()

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("首球好球率",   f"{fp['is_strike'].mean()*100:.1f}%")
    c2.metric("首球揮棒率",   f"{fp['is_swing'].mean()*100:.1f}%", help="揮棒÷首球投球數")
    c3.metric("首球揮空率",   f"{fp['is_whiff'].sum()/sw_fp*100:.1f}%" if sw_fp else "-",
              help="揮空÷揮棒次數")
    c4.metric("首球安打率",   f"{fp_hits/len(fp)*100:.1f}%", help="打席最終安打÷總打席")
    c5.metric("首球打入場出局率", f"{fp_out/len(fp)*100:.1f}%", help="打入場但出局÷總打席")

    # 各球種首球表現
    fp_rows = []
    for pt, g in fp[fp["pitch_type"] != "EP"].groupby("pitch_type"):
        sw = g["is_swing"].sum()
        fp_rows.append({
            "球種": PITCH_NAMES.get(pt,pt), "_pt": pt,
            "使用%": round(len(g)/len(fp)*100,1),
            "好球%": round(g["is_strike"].mean()*100,1),
            "揮棒%": round(sw/len(g)*100,1) if len(g) else None,
            "揮空%": round(g["is_whiff"].sum()/sw*100,1) if sw else None,
            "安打率": round(g["is_hit_actual"].mean()*100,1),
            "出局率": round((g["is_contact"] & ~g["is_hit_actual"]).mean()*100,1),
        })
    fp_df = pd.DataFrame(fp_rows).sort_values("安打率", ascending=False)
    st.dataframe(fp_df.drop(columns=["_pt"]), hide_index=True, use_container_width=True)

    # 球種選擇 → 落點散點 + 熱圖
    st.markdown("---")
    st.markdown("##### 首球落點圖")

    pt_fp_opts = ["全部球種"] + [f"{PITCH_NAMES.get(r['_pt'],r['_pt'])} ({r['_pt']})"
                                  for _,r in fp_df.iterrows() if r["_pt"] != "EP"]
    colA, colB, colC = st.columns([2,2,3])
    with colA:
        pt_fp_sel = st.selectbox("球種", pt_fp_opts, key="fp_pt")
    with colB:
        fp_res_sel = st.selectbox("顯示情境", list(RESULT_MAP_FP.keys()), key="fp_res")
    with colC:
        fp_slider = st.slider("◄ 散點　　熱圖 ►", 0, 100, 20, key="fp_sl")

    fp_plot = fp.copy()
    if pt_fp_sel != "全部球種":
        fp_code = pt_fp_sel.split("(")[-1].rstrip(")")
        fp_plot = fp_plot[fp_plot["pitch_type"] == fp_code]

    fp_filter_fn = RESULT_MAP_FP[fp_res_sel]
    if fp_filter_fn is not None:
        fp_plot = fp_plot[fp_filter_fn(fp_plot)]
    fp_plot = fp_plot.dropna(subset=["coord_x","coord_y"])

    fig_fp = go.Figure()

    # 顏色對照表（全部/單一情境共用）
    _FP_CAT_STYLE = {
        "安打":               ("#d0021b", 9),
        "揮空":               ("#7c3aed", 6),
        "界外球":             ("#16a34a", 5),
        "滾地球出局":         ("#0ea5e9", 6),
        "高飛球出局":         ("#f97316", 6),
        "其他":               ("#94a3b8", 4),
        "其他（好球/壞球/觸身球）": ("#94a3b8", 4),
    }

    if fp_slider < 65:
        op = max(0.15, 1 - fp_slider/65)
        if fp_filter_fn is None:
            # 全部球：依情境分 6 色（與下拉選單分類一致）
            _traj = fp_plot["trajectory"] if "trajectory" in fp_plot.columns else ""
            _fp_c = fp_plot.copy()
            _fp_c["_cat"] = np.select(
                [
                    _fp_c["is_hit_actual"],
                    _fp_c["is_whiff"],
                    _fp_c["result_code"].isin({"F", "FT", "FOUL_BUNT"}),
                    _fp_c["is_go"] | (_fp_c.get("trajectory", "") == "G"),
                    _fp_c["is_fo"] | (_fp_c.get("trajectory", "") == "F"),
                ],
                ["安打", "揮空", "界外球", "滾地球出局", "高飛球出局"],
                default="其他",
            )
            for nm, (col, sz) in _FP_CAT_STYLE.items():
                grp = _fp_c[_fp_c["_cat"] == nm]
                if not grp.empty:
                    fig_fp.add_scatter(x=grp["coord_x"], y=grp["coord_y"],
                                       mode="markers", name=nm,
                                       marker=dict(color=col, size=sz, opacity=op+0.1))
        else:
            # 特定情境：使用對應顏色（若有），否則藍色
            col, sz = _FP_CAT_STYLE.get(fp_res_sel, ("#6baed6", 6))
            if not fp_plot.empty:
                fig_fp.add_scatter(x=fp_plot["coord_x"], y=fp_plot["coord_y"],
                                   mode="markers", name=fp_res_sel,
                                   marker=dict(color=col, size=sz, opacity=op+0.1))

    if fp_slider > 35 and not fp_plot.empty:
        op2 = min(1.0, (fp_slider-35)/65)
        fig_fp.add_trace(go.Histogram2dContour(
            x=fp_plot["coord_x"], y=fp_plot["coord_y"],
            colorscale="Reds", showscale=False,
            opacity=op2, contours=dict(showlines=False), ncontours=12))

    # 無效區背景 + 邊框（±105）
    fig_fp.add_shape(type="rect", x0=-105, x1=105, y0=-105, y1=105,
                     line=dict(color="#888888", width=1.2, dash="dash"),
                     fillcolor="rgba(180,180,180,0.15)", layer="below")
    # 追打區背景 + 邊框（±80）
    fig_fp.add_shape(type="rect", x0=-80, x1=80, y0=-80, y1=80,
                     line=dict(color="#cc8000", width=1.5, dash="dot"),
                     fillcolor="rgba(255,180,0,0.12)", layer="below")
    # 好球帶背景 + 邊框（±50）
    fig_fp.add_shape(type="rect", x0=SZ_X1, x1=SZ_X2, y0=SZ_Y1, y1=SZ_Y2,
                     line=dict(color="black", width=2),
                     fillcolor="rgba(100,149,237,0.10)", layer="below")
    for xv in [SZ_X1+X3, SZ_X1+2*X3]:
        fig_fp.add_vline(x=xv, line=dict(color="gray", width=1, dash="dot"))
    for yv in [SZ_Y1+Y3, SZ_Y1+2*Y3]:
        fig_fp.add_hline(y=yv, line=dict(color="gray", width=1, dash="dot"))
    # 九宮格編號
    for _zi in range(9):
        _zr, _zc = divmod(_zi, 3)
        fig_fp.add_annotation(x=SZ_X1+_zc*X3+X3/2, y=SZ_Y2-_zr*Y3-Y3/2,
                               text=str(_zi+1), showarrow=False,
                               font=dict(color="rgba(0,0,0,0.22)", size=16))
    fig_fp.add_annotation(x=86, y=105, text="無效區", showarrow=False, yanchor="bottom",
                          font=dict(size=10, color="#777777"))
    fig_fp.add_annotation(x=66, y=80, text="追打區", showarrow=False, yanchor="bottom",
                          font=dict(size=10, color="#cc8000"))

    fig_fp.update_layout(
        xaxis=dict(range=[-120, 120], zeroline=False, showgrid=False, showticklabels=False),
        yaxis=dict(range=[-120, 120], zeroline=False, showgrid=False, showticklabels=False,
                   scaleanchor="x", scaleratio=1),
        height=480, margin=dict(l=5, r=5, t=10, b=5),
        legend=dict(orientation="h", y=-0.05, font_size=11),
        plot_bgcolor="rgba(248,248,248,1)",
    )
    st.plotly_chart(fig_fp, use_container_width=True)


# ═══════════════════════════════════════════════════════════
# TAB 5：球數配球
# ═══════════════════════════════════════════════════════════
if _sec == "🔢 球數配球":
    # ── 輔助函式 ───────────────────────────────────────────
    def _pt_font(pct):
        """pct(0-100) → (font_size, font_weight, color)"""
        sz = max(9, min(18, int(9 + pct * 0.2)))
        if pct >= 40: return sz, "700", "#0f172a"
        if pct >= 25: return sz, "600", "#1e293b"
        if pct >= 15: return sz, "500", "#334155"
        return sz, "400", "#64748b"

    # ── A. 球數配球矩陣 ─────────────────────────────────────
    st.subheader("球數配球矩陣")
    st.caption(
        "🔵 藍色＝打者有利球數（2-0 / 3-0 / 3-1）　"
        "🔴 紅色＝投手有利球數（0-2 / 1-2 / 2-2）　"
        "字體越大 = 使用率越高，僅顯示使用率 ≥ 1% 的球種（至多顯示前 5 種）"
    )

    matrix_data5 = {}
    for b, s in ALL_COUNTS:
        g = sub_c[(sub_c["balls_before"]==b) & (sub_c["strikes_before"]==s)]
        if len(g) == 0:
            matrix_data5[(b,s)] = None; continue
        vc = g["pitch_type"].value_counts()
        pts = [
            {"nm": PITCH_NAMES.get(vc.index[i], vc.index[i]),
             "pct": vc.iloc[i]/len(g)*100}
            for i in range(len(vc)) if vc.iloc[i]/len(g)*100 >= 1
        ]
        matrix_data5[(b,s)] = {"n": len(g), "pts": pts}

    cell_w5, cell_h5 = 148, 130
    html_m5  = '<table style="border-collapse:collapse;margin:0 auto">'
    html_m5 += ('<tr><th style="padding:6px 12px;font-size:12px;color:#888;text-align:right">好球數→</th>'
                + ''.join(
                    f'<th style="padding:6px 18px;font-size:14px;font-weight:700;color:#333">好球 {s}</th>'
                    for s in range(3))
                + '</tr>')
    for b in range(4):
        html_m5 += (f'<tr><td style="padding:6px 12px;font-size:14px;font-weight:700;'
                    f'color:#333;text-align:right">壞球 {b}</td>')
        for s in range(3):
            d  = matrix_data5.get((b,s))
            ct = count_type(b, s)
            bg = COUNT_BG[ct]; bd = COUNT_BD[ct]
            sty5 = (f"width:{cell_w5}px;height:{cell_h5}px;border:2px solid {bd};"
                    f"background:{bg};text-align:center;vertical-align:middle;padding:4px 6px")
            if d is None:
                html_m5 += f'<td style="{sty5}"><span style="color:#bbb;font-size:20px">—</span></td>'
            else:
                inner = f'<div style="font-size:9px;color:#94a3b8;margin-bottom:3px">#{d["n"]}球</div>'
                for p in d["pts"][:5]:
                    fsz, fwt, fcol = _pt_font(p["pct"])
                    inner += (f'<div style="font-size:{fsz}px;font-weight:{fwt};'
                              f'color:{fcol};line-height:1.35">'
                              f'{p["nm"]} {p["pct"]:.0f}%</div>')
                html_m5 += f'<td style="{sty5}">{inner}</td>'
        html_m5 += '</tr>'
    html_m5 += '</table>'

    _, _m5c, _ = st.columns([1, 5, 1])
    with _m5c:
        st.markdown(html_m5, unsafe_allow_html=True)

    # ── B. 選定球數球種分解 ─────────────────────────────────
    st.markdown("---")
    st.subheader("選定球數的球種分解")

    count_opts_t5  = [f"B{b}-S{s}" for b,s in ALL_COUNTS]
    label_to_bs_t5 = {f"B{b}-S{s}": (b,s) for b,s in ALL_COUNTS}

    col_cnt5, _ = st.columns([2, 4])
    with col_cnt5:
        cnt_sel5 = st.selectbox("選擇球數（B=壞球 S=好球）", count_opts_t5,
                                index=0, key="cnt_sel_t5")
    b5, s5 = label_to_bs_t5[cnt_sel5]
    g5 = sub_c[(sub_c["balls_before"]==b5) & (sub_c["strikes_before"]==s5)]

    if g5.empty:
        st.info(f"「{cnt_sel5}」球數下無投球資料")
    else:
        pt_rows5 = []
        for pt, g in g5.groupby("pitch_type"):
            sw = g["is_swing"].sum()
            pt_rows5.append({
                "球種": PITCH_NAMES.get(pt, pt), "_pt": pt,
                "投球數": len(g),
                "使用%": round(len(g)/len(g5)*100, 1),
                "揮棒%": round(sw/len(g)*100, 1),
                "揮空%": round(g["is_whiff"].sum()/sw*100, 1) if sw else 0.0,
                "接觸%": round(g["is_contact"].sum()/sw*100, 1) if sw else 0.0,
                "均速(km/h)": round(g["velocity"].mean(), 1),
            })
        pt_df5 = pd.DataFrame(pt_rows5).sort_values("使用%", ascending=False)
        max_pct5 = pt_df5["使用%"].max()

        c_bar5, c_tbl5 = st.columns([3, 2])
        with c_bar5:
            fig_bar5 = px.bar(
                pt_df5, x="球種", y="使用%", text="使用%",
                color="揮空%", color_continuous_scale="Reds", range_color=[0, 50],
                title=f"{cnt_sel5}　球種使用率（色深=揮空率）",
                height=320,
            )
            fig_bar5.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
            fig_bar5.update_layout(
                margin=dict(l=0, r=0, t=36, b=40),
                coloraxis_showscale=False,
                xaxis_title="", yaxis_title="使用率%",
                yaxis_range=[0, max_pct5 * 1.3],
            )
            st.plotly_chart(fig_bar5, use_container_width=True)
        with c_tbl5:
            st.dataframe(
                pt_df5.drop(columns=["_pt"]),
                hide_index=True, use_container_width=True, height=300
            )

        # ── C. 落點熱點圖（各球數 × 球種獨立選擇）──────────────
        st.markdown("---")
        st.markdown("##### 各球數組合落點熱點圖")
        st.caption("球數組合與上方分解區可獨立選擇，方便對比不同球數下同一球種的進壘位置")

        col_cnt_p5, col_fpt5, col_fsl5 = st.columns([2, 2, 3])
        with col_cnt_p5:
            cnt5_plot = st.selectbox(
                "球數組合", count_opts_t5,
                index=count_opts_t5.index(cnt_sel5),
                key="c5_cnt"
            )
        b5p, s5p = label_to_bs_t5[cnt5_plot]
        g5_plot = sub_c[(sub_c["balls_before"]==b5p) & (sub_c["strikes_before"]==s5p)]

        pt5_vc   = g5_plot["pitch_type"].value_counts() if not g5_plot.empty else pd.Series(dtype=int)
        pt5_opts = ["全部球種"] + [f"{PITCH_NAMES.get(pt, pt)} ({pt})" for pt in pt5_vc.index]
        with col_fpt5:
            pt5_sel = st.selectbox("球種篩選", pt5_opts, key="c5_pt")
        with col_fsl5:
            c5_slider = st.slider("◄ 散點　　熱圖 ►", 0, 100, 20, key="c5_sl")

        plot5 = g5_plot.copy()
        if pt5_sel != "全部球種":
            pt5_code = pt5_sel.split("(")[-1].rstrip(")")
            plot5 = plot5[plot5["pitch_type"] == pt5_code]
        plot5 = plot5.dropna(subset=["coord_x","coord_y"])

        fig5 = go.Figure()
        if c5_slider < 65 and not plot5.empty:
            op5 = max(0.15, 1 - c5_slider/65)
            _iz5 = (plot5["coord_x"].between(SZ_X1, SZ_X2) &
                    plot5["coord_y"].between(SZ_Y1, SZ_Y2))
            _in5, _out5 = plot5[_iz5], plot5[~_iz5]
            if not _in5.empty:
                fig5.add_scatter(x=_in5["coord_x"], y=_in5["coord_y"],
                                 mode="markers", name="好球帶內",
                                 marker=dict(color="steelblue", size=6, opacity=op5))
            if not _out5.empty:
                fig5.add_scatter(x=_out5["coord_x"], y=_out5["coord_y"],
                                 mode="markers", name="追打/無效區",
                                 marker=dict(color="#f97316", size=6, opacity=op5))
        if c5_slider > 35 and not plot5.empty:
            op5h = min(1.0, (c5_slider-35)/65)
            fig5.add_trace(go.Histogram2dContour(
                x=plot5["coord_x"], y=plot5["coord_y"],
                colorscale="Reds", showscale=False, opacity=op5h,
                contours=dict(showlines=False), ncontours=12))
        fig5.add_shape(type="rect", x0=-105, x1=105, y0=-105, y1=105,
                       line=dict(color="#888888", width=1.2, dash="dash"),
                       fillcolor="rgba(180,180,180,0.15)", layer="below")
        fig5.add_shape(type="rect", x0=-80, x1=80, y0=-80, y1=80,
                       line=dict(color="#cc8000", width=1.5, dash="dot"),
                       fillcolor="rgba(255,180,0,0.12)", layer="below")
        fig5.add_shape(type="rect", x0=SZ_X1, x1=SZ_X2, y0=SZ_Y1, y1=SZ_Y2,
                       line=dict(color="black", width=2),
                       fillcolor="rgba(100,149,237,0.10)", layer="below")
        for xv in [SZ_X1+X3, SZ_X1+2*X3]:
            fig5.add_vline(x=xv, line=dict(color="gray", width=1, dash="dot"))
        for yv in [SZ_Y1+Y3, SZ_Y1+2*Y3]:
            fig5.add_hline(y=yv, line=dict(color="gray", width=1, dash="dot"))
        for _zi in range(9):
            _zr, _zc = divmod(_zi, 3)
            fig5.add_annotation(x=SZ_X1+_zc*X3+X3/2, y=SZ_Y2-_zr*Y3-Y3/2,
                                 text=str(_zi+1), showarrow=False,
                                 font=dict(color="rgba(0,0,0,0.22)", size=16))
        fig5.add_annotation(x=86, y=105, text="無效區", showarrow=False, yanchor="bottom",
                             font=dict(size=10, color="#777777"))
        fig5.add_annotation(x=66, y=80, text="追打區", showarrow=False, yanchor="bottom",
                             font=dict(size=10, color="#cc8000"))
        fig5.update_layout(
            xaxis=dict(range=[-120,120], zeroline=False, showgrid=False, showticklabels=False),
            yaxis=dict(range=[-120,120], zeroline=False, showgrid=False, showticklabels=False,
                       scaleanchor="x", scaleratio=1),
            height=480, margin=dict(l=5,r=5,t=10,b=5),
            legend=dict(orientation="h", y=-0.05, font_size=11),
            plot_bgcolor="rgba(248,248,248,1)",
        )
        st.plotly_chart(fig5, use_container_width=True)


# ═══════════════════════════════════════════════════════════
# TAB 6：打擊建議
# ═══════════════════════════════════════════════════════════
if _sec == "💡 打擊建議":
    st.subheader("📋 打擊建議書")

    # ── 投手武器庫 ─────────────────────────────────────────
    arsenal = []
    for pt, g in sub.groupby("pitch_type"):
        sw = g["is_swing"].sum()
        if len(g) < 20: continue
        arsenal.append({
            "pt": pt, "name": PITCH_NAMES.get(pt, pt),
            "n": len(g), "pct": len(g)/len(sub)*100,
            "whiff_r":   g["is_whiff"].sum()/sw if sw else 0,
            "contact_r": g["is_contact"].sum()/sw if sw else 0,
            "hit_r":     g["is_hit_actual"].mean(),
            "avg_velo":  g["velocity"].mean(),
        })
    if not arsenal:
        st.info("樣本不足"); st.stop()
    adf         = pd.DataFrame(arsenal)
    main_pt      = adf.sort_values("pct",     ascending=False).iloc[0]
    weakest_pt   = adf.sort_values("whiff_r"               ).iloc[0]
    strongest_pt = adf.sort_values("whiff_r", ascending=False).iloc[0]
    best_hit_pt  = adf.sort_values("hit_r",   ascending=False).iloc[0]

    # ── 速球趨勢 ───────────────────────────────────────────
    ff_sub  = sub[sub["pitch_type"] == "FF"]
    yr_velo = ff_sub.groupby("year")["velocity"].mean()
    if len(yr_velo) >= 2:
        delta       = yr_velo.iloc[-1] - yr_velo.iloc[0]
        velo_trend  = ("⬇️ 球速下滑" if delta < -1 else
                       ("⬆️ 球速上升" if delta > 1 else "➡️ 球速持平"))
        velo_detail = f"{abs(delta):.1f} km/h（{int(yr_velo.index[0])}→{int(yr_velo.index[-1])}）"
    else:
        delta = 0; velo_trend = "年度資料不足"; velo_detail = ""

    # ── 左右打差異 ─────────────────────────────────────────
    r_sub = sub[sub["b_hand"] == "R"]; l_sub = sub[sub["b_hand"] == "L"]
    def _wr(g):
        sw = g["is_swing"].sum(); return g["is_whiff"].sum()/sw if sw else None
    rw, lw = _wr(r_sub), _wr(l_sub)
    if rw and lw:
        harder  = "右打較難打" if rw > lw else "左打較難打"
        lr_note = f"對右打揮空率 {rw*100:.1f}%，對左打 {lw*100:.1f}%（{harder}）"
    else:
        lr_note = "左右打樣本不足"

    # ── 首球 ───────────────────────────────────────────────
    fp_all      = sub[sub["is_first_pitch"] == True]
    fp_strike_r = fp_all["is_strike"].mean() if not fp_all.empty else 0
    fp_vc       = fp_all["pitch_type"].value_counts(normalize=True) if not fp_all.empty else pd.Series(dtype=float)
    fp_main     = fp_vc.index[0] if not fp_vc.empty else main_pt["pt"]
    fp_main_pct = fp_vc.iloc[0]*100 if not fp_vc.empty else 0
    fp_hit_r    = fp_all.groupby("pitch_type")["is_hit_actual"].mean() if not fp_all.empty else pd.Series(dtype=float)
    worst_fp    = fp_hit_r.idxmax() if not fp_hit_r.empty else None

    # ── 球數可預測性（全 12 球數）─────────────────────────
    cp_rows = []
    for b, s in ALL_COUNTS:
        g_bs = sub_c[(sub_c["balls_before"]==b) & (sub_c["strikes_before"]==s)]
        if len(g_bs) < 10: continue
        vc_bs  = g_bs["pitch_type"].value_counts()
        tp_pt  = vc_bs.index[0]
        tp_pct = vc_bs.iloc[0]/len(g_bs)*100
        ct     = count_type(b, s)
        pred   = ("⭐⭐⭐ 極可預測" if tp_pct >= 75 else
                  "⭐⭐ 高可預測"  if tp_pct >= 60 else
                  "⭐ 可預測"     if tp_pct >= 45 else "— 需觀察")
        action = ("主動備戰，不猶豫" if tp_pct >= 65 else
                  "積極選球出棒"     if tp_pct >= 50 else "觀察配球")
        cp_rows.append({
            "球數": f"B{b}-S{s}",
            "情境": "打者有利" if ct=="hitter" else ("投手有利" if ct=="pitcher" else "中性"),
            "最常投": PITCH_NAMES.get(tp_pt, tp_pt),
            "使用率": f"{tp_pct:.0f}%",
            "可預測性": pred,
            "建議動作": action,
            "_top_pct": tp_pct, "_ct": ct,
        })
    cp_df6 = pd.DataFrame(cp_rows)

    # ── 攻擊落點（主球路在好球帶各格的接觸率）──────────────
    mp_code = main_pt["pt"]
    mp_zd   = sub[sub["pitch_type"]==mp_code].dropna(subset=["coord_x","coord_y"]).copy()
    mp_zd["_sec"] = mp_zd.apply(lambda r: sector(r["coord_x"], r["coord_y"]), axis=1)
    mp_total = len(mp_zd)
    POS_NAME = ["左上","中上","右上","左中","正中","右中","左下","中下","右下"]
    zone_rows6 = []
    for zi in range(1, 10):
        g_z  = mp_zd[mp_zd["_sec"] == ("SZ", zi)]
        if len(g_z) < 5: continue
        sw_z = g_z["is_swing"].sum()
        zone_rows6.append({
            "格號": f"{zi}號", "位置": POS_NAME[zi-1],
            "投球數": len(g_z),
            "投球%": round(len(g_z)/mp_total*100, 1) if mp_total else 0,
            "接觸率%": round(g_z["is_contact"].sum()/sw_z*100, 1) if sw_z else 0,
            "安打率%": round(g_z["is_hit_actual"].mean()*100, 1),
            "揮空率%": round(g_z["is_whiff"].sum()/sw_z*100, 1) if sw_z else 0,
        })
    zone_df6 = (pd.DataFrame(zone_rows6).sort_values("接觸率%", ascending=False)
                if zone_rows6 else pd.DataFrame())

    # ── 高壓情境 ───────────────────────────────────────────
    pa_hi = sub[pd.to_numeric(sub["LI"], errors="coerce") > 1.5]
    if len(pa_hi) > 20:
        hi_top = pa_hi["pitch_type"].value_counts(normalize=True).head(1)
        hi_pt  = hi_top.index[0] if not hi_top.empty else None
        hi_pct = hi_top.iloc[0]*100 if not hi_top.empty else 0
        pressure_note = f"**{PITCH_NAMES.get(hi_pt,'')}** 使用率升至 {hi_pct:.0f}%，加強識別此球路"
    else:
        hi_pt = None; pressure_note = "高壓情境樣本較少，趨勢待確認"

    # ══════════════════ DISPLAY ════════════════════════════
    yrs = ', '.join(str(y) for y in sorted(sub['year'].unique()))
    st.markdown(f"## {pitcher}（{yrs}年）打擊建議書")

    # 一、投手概覽
    st.markdown("### 一、投手概覽")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("主武器", main_pt['name'],
              f"使用率 {main_pt['pct']:.0f}%｜均速 {main_pt['avg_velo']:.1f} km/h")
    c2.metric("封殺球", strongest_pt['name'],
              f"揮空率 {strongest_pt['whiff_r']*100:.0f}%，最難揮中")
    c3.metric("最易打到", best_hit_pt['name'],
              f"安打率 {best_hit_pt['hit_r']*100:.0f}%")
    c4.metric("速球趨勢", velo_trend, velo_detail if velo_detail else None)
    st.markdown(f"**左右打差異**：{lr_note}")

    # 二、球數可預測性對照表
    st.markdown("---")
    st.markdown("### 二、球數可預測性對照表")
    st.caption("🔵 藍=打者有利　🔴 紅=投手有利　⭐⭐⭐=主球路使用率≥75%，幾乎可預測球路")
    if not cp_df6.empty:
        show_cp = cp_df6.drop(columns=["_top_pct","_ct"])
        st.dataframe(show_cp, hide_index=True, use_container_width=True, height=460)
        top3_b   = cp_df6.nlargest(3, "_top_pct")
        top3_str = "、".join(
            f"{r['球數']}（{r['最常投']} {r['使用率']}）"
            for _, r in top3_b.iterrows()
        )
        st.success(f"**最可預測球數**：{top3_str} → 這些球數鎖定速球，不考慮其他球路")

    # 三、攻擊落點
    st.markdown("---")
    st.markdown(f"### 三、{main_pt['name']} 落點攻擊建議（好球帶九宮格）")
    st.caption("依接觸率排序，投球數較多且接觸率高的格子＝打者的甜蜜點")
    if not zone_df6.empty:
        col_z1, col_z2 = st.columns([3, 2])
        with col_z1:
            st.dataframe(zone_df6, hide_index=True, use_container_width=True, height=300)
        with col_z2:
            top_ctc   = zone_df6.iloc[0]
            top_hit6  = zone_df6.sort_values("安打率%", ascending=False).iloc[0]
            top_whiff6 = zone_df6.sort_values("揮空率%", ascending=False).iloc[0]
            st.success(
                f"**最佳接觸格**：{top_ctc['位置']}（{top_ctc['格號']}）  \n"
                f"接觸率 {top_ctc['接觸率%']}%　安打率 {top_ctc['安打率%']}%")
            st.info(
                f"**安打率最高格**：{top_hit6['位置']}（{top_hit6['格號']}）  \n"
                f"安打率 {top_hit6['安打率%']}%　投球率 {top_hit6['投球%']}%")
            st.warning(
                f"**揮空陷阱格**：{top_whiff6['位置']}（{top_whiff6['格號']}）  \n"
                f"揮空率 {top_whiff6['揮空率%']}%，謹慎出棒")

    # 四、首球策略
    st.markdown("---")
    st.markdown("### 四、首球策略")
    fp_advice = "進帶率高，看到目標球路直接出棒" if fp_strike_r > 0.55 else "首球不穩，可等待，消耗球數"
    st.markdown(f"""
- **首球主球路**：{PITCH_NAMES.get(fp_main, fp_main)} 佔首球 **{fp_main_pct:.0f}%**
- **首球好球率**：{fp_strike_r*100:.1f}%　→　{fp_advice}
- **首球安打率最高球種**：{PITCH_NAMES.get(worst_fp, worst_fp or '—')}（安打率 {fp_hit_r.get(worst_fp, 0)*100:.1f}%）
""")

    # 五、情境壓力
    st.markdown("---")
    st.markdown("### 五、情境壓力下的配球")
    st.markdown(f"""
- **高壓情境（LI > 1.5）**：{pressure_note}
- **有人在壘**：投手傾向回歸主武器 **{main_pt['name']}**，打者更應積極備戰
- **分差細節**：見「情境弱點」頁
""")

    # 六、執行策略三欄表
    st.markdown("---")
    st.markdown("### 六、打擊執行策略一覽表")
    hitter_c = (cp_df6[cp_df6["_ct"]=="hitter"]
                .sort_values("_top_pct", ascending=False)
                .head(3)) if not cp_df6.empty else pd.DataFrame()
    hcnt_str = "、".join(hitter_c["球數"].tolist()) if not hitter_c.empty else "打者有利球數"
    hpct_str = "、".join(f"{p:.0f}%" for p in hitter_c["_top_pct"].tolist()) if not hitter_c.empty else ""
    top_zone_str = (f"{zone_df6.iloc[0]['位置']}（{zone_df6.iloc[0]['格號']}）"
                    if not zone_df6.empty else "好球帶中路")
    velo_row = ("下滑的速球，可提前備戰、更早出棒" if delta < -1 else
                "球速穩定，依標準計時備戰")

    st.markdown(f"""
<table style="border-collapse:collapse;width:100%;font-size:14px;margin-top:8px">
<thead>
<tr style="background:#1e293b;color:white">
  <th style="padding:12px 16px;text-align:center;width:33%">等什麼</th>
  <th style="padding:12px 16px;text-align:center;width:33%">什麼時候出棒</th>
  <th style="padding:12px 16px;text-align:center;width:33%">主動放棄</th>
</tr>
</thead>
<tbody>
<tr style="background:#f0fdf4">
  <td style="padding:10px 16px;text-align:center">
    <strong style="font-size:16px">{main_pt['name']}</strong><br>
    <span style="color:#555;font-size:12px">主武器，使用率 {main_pt['pct']:.0f}%</span>
  </td>
  <td style="padding:10px 16px;text-align:center">
    <strong style="font-size:16px">{hcnt_str}</strong><br>
    <span style="color:#555;font-size:12px">速球率 {hpct_str}，高度可預測</span>
  </td>
  <td style="padding:10px 16px;text-align:center">
    <strong style="font-size:16px">{strongest_pt['name']}（低外角）</strong><br>
    <span style="color:#555;font-size:12px">揮空率 {strongest_pt['whiff_r']*100:.0f}%，最難打中</span>
  </td>
</tr>
<tr style="background:#eff6ff">
  <td style="padding:10px 16px;text-align:center">
    <strong>落點鎖定：{top_zone_str}</strong><br>
    <span style="color:#555;font-size:12px">接觸率最高的進壘格</span>
  </td>
  <td style="padding:10px 16px;text-align:center">
    <strong>首球 {PITCH_NAMES.get(fp_main, fp_main)} 進好球帶</strong><br>
    <span style="color:#555;font-size:12px">不猶豫，直接出棒</span>
  </td>
  <td style="padding:10px 16px;text-align:center">
    <strong>追打區外所有球</strong><br>
    <span style="color:#555;font-size:12px">好球帶外一律放掉，製造選球機會</span>
  </td>
</tr>
<tr style="background:#fefce8">
  <td style="padding:10px 16px;text-align:center">
    <strong>{velo_row}</strong><br>
    <span style="color:#555;font-size:12px">{velo_detail}</span>
  </td>
  <td style="padding:10px 16px;text-align:center">
    <strong>有壘包情境</strong><br>
    <span style="color:#555;font-size:12px">投手更依賴 {main_pt['name']}，積極備戰</span>
  </td>
  <td style="padding:10px 16px;text-align:center">
    <strong>投手有利球數硬追</strong><br>
    <span style="color:#555;font-size:12px">0-2 / 1-2 放大好球帶，耐心選球</span>
  </td>
</tr>
</tbody>
</table>
""", unsafe_allow_html=True)

    # 七、最終打擊策略總結
    st.markdown("---")
    st.markdown("### ✅ 打擊策略總結")
    first_action = "首球不猶豫出棒" if fp_strike_r > 0.55 else "首球等球、消耗球數"
    best_cnt_top = cp_df6.nlargest(1, "_top_pct").iloc[0] if not cp_df6.empty else None
    best_cnt_pct = f"{best_cnt_top['_top_pct']:.0f}%" if best_cnt_top is not None else ""

    st.markdown(f"""
> ### 「等 {main_pt['name']}，{first_action}，{hcnt_str} 全力出棒，{strongest_pt['name']} 低外角一律放掉。」

---

**教練簡報 5 點：**

1. **球路識別訓練**：針對 **{main_pt['name']}** vs **{strongest_pt['name']}** 出手點差異做影片分析，讓打者在出手瞬間就能判斷球種
2. **球數紀律**：{hcnt_str} 速球率高達 {hpct_str}，站好打速球不等第二球路，這是全場最划算的出棒時機
3. **落點選擇**：{main_pt['name']} 接觸率最高格在 **{top_zone_str}**，鎖定這個位置出棒；揮空陷阱格（{top_whiff6['位置'] if not zone_df6.empty else '—'}）謹慎出棒
4. **首球機會**：{PITCH_NAMES.get(fp_main, fp_main)} 佔首球 {fp_main_pct:.0f}%，進好球帶直接打，不看第二球
5. **規避陷阱**：{strongest_pt['name']} 揮空率 {strongest_pt['whiff_r']*100:.0f}%，任何球數都不主動追打，尤其低外角
    """)
