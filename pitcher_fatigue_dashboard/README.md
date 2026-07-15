# 投手疲勞監控儀表板

給教練團/防護員/總管在賽季規劃會議上使用的分析系統，**不是即時場中換投工具**。

## 技術棧

Streamlit + Plotly。選擇理由：專案裡另一個 CPBL 儀表板（`leo88888/cpbl-dashboard`）已經用同一套技術棧並部署在 Hugging Face Spaces，沿用同樣的框架與部署流程，不需要額外學習 Dash 的 callback 機制，團隊維護成本最低。

## 三層架構

```
data_cleaning.py        逐球層級的型別/髒值清理（velocity_clean / rpm_clean）
feature_engineering.py  投手 x 場次聚合 + 個人基準線 + 滾動趨勢 + 疲勞分數
pitcher_fatigue_dashboard/  純視覺化層，只呼叫上面兩層算好的結果，不在頁面程式碼裡算任何統計
```

`data_cleaning.py` 跟 `feature_engineering.py` 都放在專案根目錄（跟 `pitcher_pitches.csv` 同層），
是獨立、可被匯入測試的模組，不綁定在儀表板裡。

## 啟動方式

```bash
cd pitcher_fatigue_dashboard
streamlit run app.py
```

需要根目錄有 `pitcher_pitches.csv`（逐球明細，欄位需求見下方）。第一次執行時
`common.py` 會自動呼叫 `data_cleaning.clean_pitch_tracking()` 補上 `velocity_clean`/`rpm_clean`
兩欄（如果 CSV 裡還沒有的話）。

## 資料需求

`pitcher_pitches.csv` 需要放在本資料夾的上一層（跟 `data_cleaning.py`/`feature_engineering.py` 同層）。
必要欄位：

| 欄位 | 說明 |
|---|---|
| `pitcher`, `pitcher_uid` | 投手姓名/唯一ID |
| `year`, `game_date` | 年度、比賽日期 |
| `inning`, `pa_order`, `pitch_seq` | 局數/打席序/球序 |
| `velocity`, `rpm` | 原始球速/轉速（可能含髒值，`data_cleaning.py`會清理） |
| `pa_result_type` | 打席最終結果代碼（1B/2B/3B/HR/SO/GO/FO/uBB/IBB/HBP/SF/SH/GIDP/E/FC） |
| `away_score`, `home_score` | 用來推算失分率(RA) |

## 頁面說明

1. **球隊總覽**（`app.py`）：全隊投手依「最新一場疲勞分數」排行，自動標示分數≥30的高風險投手
2. **單一投手深度分析**（`pages/1_單一投手深度分析.py`）：球速/轉速/OPS-against/失分率的逐場趨勢圖，附滾動中位數線與個人基準線，以及綜合疲勞分數走勢
3. **賽季負荷總覽**（`pages/2_賽季負荷總覽.py`）：累積用球數排行、出賽間隔（休息天數）分布，標示出休息不足（0-1天）的投手

所有頁面都可以用側邊欄調整：球季、角色（先發/中繼）、個人基準線場數（預設10場）、滾動趨勢視窗（預設5場）。

## 疲勞分數計算方式

詳見 `feature_engineering.py` 的 docstring。重點：
- 以「投手 x 場次」為單位，不是逐球即時計算
- 先發/中繼判斷：該場首次登板局數==1 視為先發，否則中繼
- 個人基準線：該投手「本季」前N場（可調）的中位數，不跟其他投手比較
- 滾動趨勢：用中位數而非平均數，避免單場離群值污染趨勢
- 沒有官方自責分/非自責分判定，改用失分率(RA，不分自責/非自責) + OPS-against 兩個子指標
- 轉速資料涵蓋率不足（rebas.tw追蹤覆蓋率逐年不同）的視窗，會自動把轉速子指標權重歸零、其餘子指標按比例放大
- 五個子指標權重都在 `feature_engineering.DEFAULT_WEIGHTS`，可依球團回饋調整，不寫死在計算邏輯裡

## 已知限制

- `rpm` 缺值率約52%（逐年不均：2023年較低完整度、2026年較高），跨年度轉速比較需謹慎
- 失分率(RA)未區分自責分/非自責分，圖表上已明確標註「RA」而非「ERA」
- 部分投手本季出賽場次少於基準線場數（如新人/傷癒歸隊），基準線樣本數不足，分數可信度較低
