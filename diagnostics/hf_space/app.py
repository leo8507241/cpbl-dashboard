import streamlit as st
from diagnostic_core import run_full_diagnostic, diagnose_verdict

st.set_page_config(page_title="CPBL 連線診斷（測試用）", layout="wide")
st.title("🔬 CPBL 官網連線診斷")
st.caption(
    "獨立測試用 Space，不影響正式的 CPBL Dashboard（leo88888/cpbl-dashboard）。\n\n"
    "目的：判斷從 HuggingFace Space 的雲端執行環境存取 CPBL 官網時，"
    "封鎖究竟發生在哪一層 —— 網路層（TCP 就被擋）、TLS 指紋層，還是應用層（WAF/Challenge 頁面）。"
)

if st.button("執行診斷", type="primary"):
    with st.spinner("診斷中（約 10–20 秒）..."):
        result = run_full_diagnostic()

    st.subheader("判讀結果")
    for line in diagnose_verdict(result):
        st.write(line)

    st.subheader("完整診斷資料")
    st.json(result)
else:
    st.info("點擊上方按鈕開始診斷。")
