---
title: CPBL Network Diagnostic
emoji: 🔬
colorFrom: gray
colorTo: red
sdk: streamlit
sdk_version: 1.38.0
app_file: app.py
pinned: false
---

# CPBL 官網連線診斷（測試用）

獨立診斷工具，與正式的 `cpbl-dashboard` Space 完全分開，不會互相影響。

用來判斷從 HuggingFace Space 的雲端執行環境存取 CPBL 官網時，
封鎖發生在哪一層：

1. **網路層**：TCP 三向交握就被拒絕/逾時 → 依來源 IP 封鎖，程式碼無法解決
2. **TLS 層**：TCP 通但 TLS handshake 失敗 → 可能是 TLS 指紋（JA3/JA4）封鎖
3. **應用層**：完整 HTTP request 收到 403/429 或 Challenge/CAPTCHA 頁面 → WAF bot detection

同時對照 rebas.tw（已知可用）確認網路環境本身沒問題，問題是否為 CPBL 官網特定的封鎖。
