"""
CPBL 官網連線診斷工具（獨立測試用，不影響任何正式 pipeline）。

目的：找出「從雲端執行環境（GitHub Actions / HuggingFace Space）存取
CPBL 官網」被擋下的實際發生層級 —— 網路層 IP、TLS 指紋、還是應用層 WAF。
如果是網路層/TLS 層，代表任何 header/UA/cookie 偽裝都無法解決。

用法：
  python diagnostic_core.py     # CLI / GitHub Actions 用，印出報告
  或 import run_full_diagnostic() 給 Streamlit 顯示
"""
import json
import socket
import ssl
import time

import requests

CPBL_HOST = "www.cpbl.com.tw"
CPBL_URL = "https://www.cpbl.com.tw/schedule"
REBAS_BASELINE_URL = (
    "https://www.rebas.tw/api/seasons/CPBL-2026-oB/leaders"
    "?type=pitcher&section=standard"
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.cpbl.com.tw/",
}

CHALLENGE_MARKERS = [
    "Just a moment", "cf-browser-verification", "Checking your browser",
    "__cf_chl_", "cf-chl", "Attention Required", "Access denied",
    "存取遭拒", "unusual traffic from your computer network",
]

WAF_HEADER_KEYS = {
    "server", "cf-ray", "cf-mitigated", "x-sucuri-id", "x-waf-event",
    "x-akamai-request-id",
}


def check_outbound_ip():
    """回報目前執行環境對外的 IP / 所屬組織（ASN），用來對照是否為雲端機房網段。"""
    t0 = time.time()
    try:
        r = requests.get("https://ipinfo.io/json", timeout=8)
        return {"ok": True, "elapsed": round(time.time() - t0, 2), "data": r.json()}
    except Exception as e:
        return {"ok": False, "elapsed": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"}


def check_tcp(host=CPBL_HOST, port=443, timeout=6):
    """第 1 層（網路層）：TCP 三向交握是否能建立。
    若這裡就逾時/被拒絕，代表對方在網路層就依來源 IP 擋掉，HTTP 內容完全不會被看到。"""
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "elapsed": round(time.time() - t0, 2)}
    except Exception as e:
        return {"ok": False, "elapsed": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"}


def check_tls(host=CPBL_HOST, port=443, timeout=6):
    """第 2 層（傳輸加密層）：TLS handshake 是否完成。
    有些 WAF 會在 handshake 階段就依 TLS 指紋（JA3/JA4）判斷客戶端是否為真實瀏覽器。"""
    t0 = time.time()
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls_sock:
                return {
                    "ok": True,
                    "elapsed": round(time.time() - t0, 2),
                    "cipher": tls_sock.cipher(),
                    "tls_version": tls_sock.version(),
                }
    except Exception as e:
        return {"ok": False, "elapsed": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"}


def check_http(url, headers=None, timeout=10):
    """第 3 層（應用層）：完整 HTTP request，檢查 status code、WAF 特徵 header、challenge 頁面關鍵字。"""
    t0 = time.time()
    try:
        r = requests.get(url, headers=headers or BROWSER_HEADERS, timeout=timeout)
        text = r.text
        waf_headers = {k: v for k, v in r.headers.items() if k.lower() in WAF_HEADER_KEYS}
        markers_hit = [m for m in CHALLENGE_MARKERS if m.lower() in text.lower()]
        return {
            "ok": True,
            "elapsed": round(time.time() - t0, 2),
            "status_code": r.status_code,
            "waf_headers": waf_headers,
            "challenge_markers_hit": markers_hit,
            "content_length": len(r.content),
            "body_snippet": text[:500],
        }
    except Exception as e:
        return {"ok": False, "elapsed": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"}


def run_full_diagnostic():
    return {
        "outbound_ip": check_outbound_ip(),
        "cpbl_tcp": check_tcp(),
        "cpbl_tls": check_tls(),
        "cpbl_http": check_http(CPBL_URL),
        "rebas_http_baseline": check_http(
            REBAS_BASELINE_URL, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]}
        ),
    }


def diagnose_verdict(result):
    """根據各層結果給出白話判讀。"""
    lines = []
    ip_info = result["outbound_ip"]
    if ip_info["ok"]:
        org = ip_info["data"].get("org", "未知")
        lines.append(f"執行環境對外 IP 組織：{org}")

    if not result["cpbl_tcp"]["ok"]:
        lines.append("❌ TCP 連線失敗 → 網路層封鎖，程式碼無法解決（對方連連線都不給建立）")
    elif not result["cpbl_tls"]["ok"]:
        lines.append("❌ TCP 通但 TLS handshake 失敗 → 可能是 TLS 指紋層級封鎖")
    elif not result["cpbl_http"]["ok"]:
        lines.append(f"❌ TLS 通但 HTTP 請求失敗：{result['cpbl_http']['error']}")
    else:
        http = result["cpbl_http"]
        if http["challenge_markers_hit"]:
            lines.append(f"🛑 收到 Challenge/CAPTCHA 頁面（關鍵字：{http['challenge_markers_hit']}）→ WAF bot detection")
        elif http["status_code"] in (403, 406, 429):
            lines.append(f"🛑 HTTP {http['status_code']} → 應用層封鎖或 rate limit")
        elif http["status_code"] == 200:
            lines.append(
                f"✅ HTTP 200，未偵測到明顯封鎖跡象（content_length={http['content_length']}），"
                "但仍需人工確認 body_snippet 是否真的含球員資料"
            )
        else:
            lines.append(f"⚠️ HTTP {http['status_code']}，需要人工檢查 body_snippet")

    baseline = result["rebas_http_baseline"]
    if baseline["ok"] and baseline["status_code"] == 200:
        lines.append("✅ 對照組 rebas.tw 正常回應 200（確認網路環境本身沒問題，問題是 CPBL 特定的）")
    else:
        lines.append(f"⚠️ 對照組 rebas.tw 異常：{baseline.get('error', baseline.get('status_code'))}")

    return lines


if __name__ == "__main__":
    result = run_full_diagnostic()
    print("=" * 60)
    print("CPBL 官網連線診斷報告")
    print("=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("-" * 60)
    print("判讀：")
    for line in diagnose_verdict(result):
        print(" -", line)
