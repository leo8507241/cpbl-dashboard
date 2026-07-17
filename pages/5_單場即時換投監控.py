import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import fatigue_common as fc

st.set_page_config(page_title="單場即時換投監控", page_icon="🔥", layout="wide")
st.title("🔥 單場即時換投監控")
fc.data_source_caption("此頁面是「這場比賽此刻」的即時分數，跟其他頁面的賽季規劃疲勞分數是不同單位、不同用途。")
st.caption(
    "只計算先發（中繼/後援隨時可能中局才上場，「第幾局該長怎樣」的基準線邏輯對他們不成立，"
    "中繼/後援的疲勞請看賽季規劃疲勞分數頁面）。"
)
st.caption(
    "分數跟該投手「本季前10場出賽的整場最終累計數據」比較，不受局數影響；"
    "背景色帶是這一局「分數 -> 歷年真實換投機率」的連續曲線(顏色深淺=機率高低，不是切死的3段門檻)。"
)
st.caption(
    "⚠️ 每局標題旁的「鑑別力係數」是換投分數跟真實換投決策的相關係數：係數越低，代表分數在那一局"
    "對「教練會不會換投」的解釋力越弱，換投決策更可能是被局數本身、比分、用球數等分數以外的因素主導。"
    "例如第7局係數只有0.125，分數最低那組換投機率都已經65.6%——色帶偏紅不是因為分數高，"
    "是那一局教練本來就傾向換投手，色帶請當參考，不是精準門檻。"
)

checkpoints = fc.load_intra_game_checkpoints()
thresholds = fc.load_inning_thresholds()
removal_curve = fc.load_inning_removal_curve()
score_quartiles = fc.load_inning_score_quartiles()
pt_detail = fc.load_pitch_type_detail()

with st.sidebar:
    years = sorted(checkpoints["year"].unique())
    season = st.selectbox("球季", years, index=len(years) - 1)
    season_df = checkpoints[checkpoints["year"] == season]
    pitchers = sorted(season_df["pitcher"].unique())
    pitcher = st.selectbox("投手", pitchers)
    pitcher_df = season_df[season_df["pitcher"] == pitcher]
    game_dates = sorted(pitcher_df["game_date"].unique(), reverse=True)
    game_date = st.selectbox("出賽日期", game_dates)

game_df = pitcher_df[pitcher_df["game_date"] == game_date].sort_values("inning")

if game_df.empty:
    st.info("這場沒有資料。")
    st.stop()

game_rank = int(game_df["baseline_game_rank"].iloc[0]) if "baseline_game_rank" in game_df.columns else None
if game_rank is not None:
    if game_rank <= fc.BASELINE_GAMES:
        st.warning(
            f"⚠️ 基準形成中：這是{pitcher}本季第{game_rank}場先發(基準線用前{fc.BASELINE_GAMES}場)，"
            f"基準值會包含這場自己、或是比這場更晚才發生的比賽，不是嚴格意義下的「事前」基準，"
            f"分數的參考價值請打折看待。要等第{fc.BASELINE_GAMES + 1}場以後，基準才是完全獨立於這場的過去資料。"
        )
    else:
        st.success(f"✅ 基準已固定：這是{pitcher}本季第{game_rank}場先發，基準值只用完全發生在這場之前的資料，比較可信。")


GOOD, WARN, CRIT = (0x0c, 0xa3, 0x0c), (0xfa, 0xb2, 0x19), (0xd0, 0x3b, 0x3b)


def color_for_prob(prob: float) -> str:
    """機率0->GOOD，0.3->WARN，0.6->CRIT，中間線性內插，畫成連續漸層而非3段死區間。
    轉換點對齊zone_for_prob()的30%/60%門檻(不是寫死0.5)，不然色帶在文字標籤已經寫
    「一定要換」(60%)的地方，顏色還停留在偏黃橘、看起來不夠紅，跟文字警示程度對不起來。"""
    if prob <= 0.3:
        c0, c1, t = GOOD, WARN, prob / 0.3
    elif prob <= 0.6:
        c0, c1, t = WARN, CRIT, (prob - 0.3) / 0.3
    else:
        c0, c1, t = CRIT, CRIT, 0.0
    r = round(c0[0] + (c1[0] - c0[0]) * t)
    g = round(c0[1] + (c1[1] - c0[1]) * t)
    b = round(c0[2] + (c1[2] - c0[2]) * t)
    return f"rgb({r},{g},{b})"


NO_DATA_GRAY = "rgb(150,150,150)"

fig = go.Figure()
innings_shown = game_df["inning"].tolist()
for inn in innings_shown:
    curve = removal_curve[removal_curve["inning"] == inn].sort_values("score")
    if curve.empty:
        # 樣本數太少(<100場)沒有校準過，畫灰色「無資料」色帶，不能留白讓人誤以為漏算
        fig.add_shape(type="rect", x0=inn - 0.5, x1=inn + 0.5, y0=0, y1=100,
                      fillcolor=NO_DATA_GRAY, opacity=0.15, line_width=0, layer="below")
        continue
    scores = curve["score"].tolist()
    probs = curve["removal_prob"].tolist()
    for i in range(len(scores) - 1):
        fig.add_shape(type="rect", x0=inn - 0.5, x1=inn + 0.5, y0=scores[i], y1=scores[i + 1],
                      fillcolor=color_for_prob((probs[i] + probs[i + 1]) / 2), opacity=0.28,
                      line_width=0, layer="below")

game_df = game_df.copy()
game_df["removal_prob"] = game_df.apply(
    lambda r: fc.removal_prob_for(removal_curve, r["inning"], r["score_with_overlap"]), axis=1)
game_df["zone"] = game_df["removal_prob"].map(fc.zone_for_prob)
game_df["sample_weight"] = game_df["n_pa"].map(fc.sample_weight_multiplier)

game_df["removal_prob_pct"] = (game_df["removal_prob"] * 100).round(1)
fig.add_trace(go.Scatter(
    x=game_df["inning"], y=game_df["score_with_overlap"], mode="lines+markers",
    name="即時換投分數", line=dict(color="#2a78d6", width=3),
    marker=dict(size=9),
    customdata=game_df[["zone", "velocity", "rpm", "n_pa", "removal_prob_pct"]],
    hovertemplate=("第%{x}局　分數 %{y:.1f}<br>狀態：%{customdata[0]}(這局同分數歷史換投機率約%{customdata[4]}%)<br>"
                    "球速(混合球種,僅供參考) %{customdata[1]:.1f}　轉速 %{customdata[2]:.0f}<br>"
                    "累計面對打席數 %{customdata[3]}<extra></extra>"),
))

for inn in innings_shown:
    row = thresholds[thresholds["inning"] == inn]
    if row.empty:
        fig.add_annotation(x=inn, y=103, text="樣本不足", showarrow=False,
                            font=dict(size=10, color="#888"), yanchor="bottom")
        continue
    coef = row["鑑別力係數"].iloc[0]
    tag = "⚠️" if coef < 0.15 else ""
    fig.add_annotation(x=inn, y=103, text=f"{tag}係數{coef:.2f}", showarrow=False,
                        font=dict(size=10, color="#888"), yanchor="bottom")

fig.update_layout(
    title=f"{pitcher}　{game_date}", xaxis_title="局數", yaxis_title="即時換投分數",
    yaxis=dict(range=[0, 112]), xaxis=dict(dtick=1), height=440,
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
)
st.plotly_chart(fig, use_container_width=True)

col1, col2, col3, col4 = st.columns(4)
col1.markdown(f"🟩 **不用換**（該分數歷史換投機率 <30%）")
col2.markdown(f"🟨 **可換可不換**（30%~60%）")
col3.markdown(f"🟥 **一定要換**（>60%）")
col4.markdown(f"⬜ **樣本不足**（<100場，不校準）")
st.caption(
    "每局上方灰字「係數」是鑑別力係數，⚠️標示係數<0.15的局數——分數在那裡對換投決策的解釋力弱，色帶漸層僅供參考。"
    "第9局以後(先發完投滿9局的場次很少，n=36)樣本太少沒有校準，色帶固定灰色、標「樣本不足」，不是漏算。"
)

st.subheader("每局分數粗略分層對照表（把上面的連續曲線切成Q1~Q4四層看）")
st.caption("Q1=該局分數最低的1/4場次，Q4=最高的1/4場次；「機率」是那一層真實換投機率，"
           "「鑑別力係數」是換投分數跟真實換投決策的相關係數(同上圖係數)。想知道切法：Q1~Q4是按分數由低到高，"
           "四等分場次數(不是四等分分數值)切出來的，所以範圍寬窄不一——分數越集中的區間，範圍會越窄。"
           "表格只列到樣本數>=100場的局數，第9局以後樣本太少(n<100)不列入。")

pivot = score_quartiles.pivot(index="inning", columns="quartile", values=["score_low", "score_high", "removal_rate"])
rows = []
for inn in sorted(score_quartiles["inning"].unique()):
    coef_row = thresholds[thresholds["inning"] == inn]
    coef = coef_row["鑑別力係數"].iloc[0] if not coef_row.empty else None
    row = {"局數": inn, "鑑別力係數": coef}
    for q in ["Q1", "Q2", "Q3", "Q4"]:
        lo = pivot.loc[inn, ("score_low", q)]
        hi = pivot.loc[inn, ("score_high", q)]
        rate = pivot.loc[inn, ("removal_rate", q)]
        row[f"{q}分數範圍"] = f"{lo:.1f}-{hi:.1f}"
        row[f"{q}換投機率"] = f"{rate * 100:.1f}%"
    rows.append(row)
quartile_display = pd.DataFrame(rows)
st.dataframe(quartile_display, use_container_width=True, hide_index=True)

st.subheader("完整計算明細（現在值／基準值／正常波動範圍／偏離值，每個子指標全部攤開）")
st.caption("這場出賽每一局的完整算式明細，8個子指標各自的現在值、基準值、MAD、偏離值都列出來，反推換投分數怎麼加權出來的。")

BASELINE_METRICS = {
    "csw_all": "CSW%", "ops_against": "OPS-against", "iso_against": "ISO-against",
    "fip_overlap": "FIP組成", "fb_traj_pct": "飛球比例", "deep_fly_pct": "深遠飛球比例",
}
WEIGHTS = fc.WEIGHTS_WITH_OVERLAP

detail = pd.DataFrame({
    "局數": game_df["inning"], "累計球數": game_df["n_pitch"], "累計打席數": game_df["n_pa"],
    "打席數權重乘數": game_df["sample_weight"].round(2),
})
# 球速/轉速：現在值跟基準值也是用下面「逐球種明細」同一組使用率權重加權平均回來的同一個數字，
# 不是另外算的，兩張表是同一套計算、只是這裡看合成結果、下面看拆解過程。
detail["球速-現在值(使用率加權)"] = game_df["velocity_weighted"].round(2)
detail["球速-基準值(使用率加權)"] = game_df["velocity_weighted_baseline"].round(2)
detail[f"球速-偏離值 (權重{WEIGHTS['velocity']})"] = game_df["velocity_deviation"].round(3)
detail["轉速-現在值(使用率加權)"] = game_df["rpm_weighted"].round(1)
detail["轉速-基準值(使用率加權)"] = game_df["rpm_weighted_baseline"].round(1)
detail[f"轉速-偏離值 (權重{WEIGHTS['rpm']})"] = game_df["rpm_deviation"].round(3)
for m, label in BASELINE_METRICS.items():
    w = WEIGHTS.get(m, 0)
    detail[f"{label}-現在值"] = game_df[m].round(3)
    detail[f"{label}-基準值"] = game_df[f"{m}_baseline"].round(3)
    detail[f"{label}-MAD"] = game_df[f"{m}_spread"].round(3)
    detail[f"{label}-偏離值 (權重{w})"] = game_df[f"{m}_deviation"].round(3)
detail["換投分數"] = game_df["score_with_overlap"].round(1)
detail["狀態"] = game_df["zone"]

st.dataframe(detail, use_container_width=True, hide_index=True)
st.caption(
    f"「基準值」是這位投手本季前10場出賽、每一場最終累計數據的中位數，不分局數，同一整場只有一組數字。"
    f"「現在值」是這場比賽累計到這一局為止的數字。「MAD」是這位投手基準期間的正常波動範圍。"
    f"「偏離值」=(現在值跟基準值的差距)÷MAD，方向已經統一成「正值=比平常差」，clip在0以上(比平常好不倒扣分)。"
    f"換投分數 = 每個偏離值 × 對應權重，加總後除以有效權重總和，再乘以100/3、裁切在0-100。\n\n"
    f"**打席數權重乘數**：OPS-against/ISO-against/FIP組成/飛球比例/深遠飛球比例這幾個「靠打席數算比率」的指標，"
    f"樣本數不足時容易被單一事件雜訊放大（例如第1局只有3-4個打席，一次保送就讓比率暴衝）。"
    f"公式是 `min(累計打席數 / {fc.SAMPLE_RAMP_FULL_AT}, 1.0)`——打席數0時權重乘數0（完全不計入這幾個指標），"
    f"累積到{fc.SAMPLE_RAMP_FULL_AT}個打席（大約投完前兩局）權重乘數封頂1.0（全額計入），中間線性內插。"
    f"實際套用時，上面各指標的權重會再乘上這個打席數權重乘數。"
    f"球速/轉速/CSW%因為每一球都有數據不受影響，維持固定權重。\n\n"
    f"**球速/轉速的「現在值」「基準值」是「使用率加權」後的合成數字**：不是全部球種混在一起算，"
    f"是下面「逐球種明細」表裡每個球種各自的現在值/基準值，先各自算完偏離、再用這位投手球季正常的球種使用率"
    f"當權重加權平均回同一個數字，這裡看到的是合成結果，下面那張表是完整拆解，同一套算法、同一組數字，不是兩件事。"
)

st.subheader("球速/轉速逐球種明細（上面那兩欄「使用率加權」數字，就是從這張表算出來的）")
st.caption(
    "球速/轉速不是全部球種混在一起算中位數（會被『這局丟比較多變化球』這種戰術選擇干擾），"
    "是每個球種各自算現在值/基準值/偏離，再用這位投手『球季正常這個球種的使用率』當權重加權平均，"
    "合成上面那張表看不到、但實際拿去計分的 velocity_deviation / rpm_deviation。"
)
st.caption(
    "**MAD**是這位投手該球季前10場出賽、這個球種「各場velocity/rpm」相對於基準值(中位數)的絕對離差中位數——"
    "反映這個球種本來波動就大還是穩定，這欄資訊沒辦法從「現在值/基準值」反推回來，只能直接秀出來。"
    "**偏離值**=(基準值-現在值)/MAD，clip在0以上(比平常快不倒扣分)，這是每個球種先各自算好、"
    "上面那張總表的「球速-偏離值」才是拿這裡每一列再用「球季正常使用率」加權平均出來的。"
)
pt_game = pt_detail[(pt_detail["pitcher_uid"] == game_df["pitcher_uid"].iloc[0]) & (pt_detail["game_date"] == game_date)]
if pt_game.empty:
    st.info("這場沒有逐球種明細。")
else:
    pt_game = pt_game.copy()
    pt_game["velocity_dev_type"] = (
        (pt_game["velocity_type_baseline"] - pt_game["velocity_type"]) / pt_game["velocity_type_spread"].replace(0, float("nan"))
    ).clip(lower=0).fillna(0)
    pt_game["rpm_dev_type"] = (
        (pt_game["rpm_type_baseline"] - pt_game["rpm_type"]) / pt_game["rpm_type_spread"].replace(0, float("nan"))
    ).clip(lower=0).fillna(0)
    pt_show = pt_game[["inning", "pitch_type", "usage_share", "usage_rate",
                        "velocity_type", "velocity_type_baseline", "velocity_type_spread", "velocity_dev_type",
                        "rpm_type", "rpm_type_baseline", "rpm_type_spread", "rpm_dev_type"]].rename(columns={
        "inning": "局數", "pitch_type": "球種", "usage_share": "這場目前使用率",
        "usage_rate": "球季正常使用率(權重)", "velocity_type": "球速-現在值",
        "velocity_type_baseline": "球速-基準值", "velocity_type_spread": "球速-MAD",
        "velocity_dev_type": "球速-偏離值(該球種)",
        "rpm_type": "轉速-現在值", "rpm_type_baseline": "轉速-基準值",
        "rpm_type_spread": "轉速-MAD", "rpm_dev_type": "轉速-偏離值(該球種)",
    }).sort_values(["局數", "球種"])
    pt_show["這場目前使用率"] = (pt_show["這場目前使用率"] * 100).round(1)
    pt_show["球季正常使用率(權重)"] = (pt_show["球季正常使用率(權重)"] * 100).round(1)
    pt_show["球速-MAD"] = pt_show["球速-MAD"].round(3)
    pt_show["球速-偏離值(該球種)"] = pt_show["球速-偏離值(該球種)"].round(3)
    pt_show["轉速-MAD"] = pt_show["轉速-MAD"].round(1)
    pt_show["轉速-偏離值(該球種)"] = pt_show["轉速-偏離值(該球種)"].round(3)
    st.dataframe(pt_show, use_container_width=True, hide_index=True)
