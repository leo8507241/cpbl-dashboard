"""
近似線性權重，數值跟 樂天林立分析/report_lib.py 完全一樣。
⚠️ 不是官方逐年 wOBA 權重（rebas.tw 沒有可爬的權重 API），只適合「同一位打者跟自己近況比」的
滾動趨勢計算，不能拿來跟球季官方 wOBA/wRC+ 做精確的跨球員比較。
"""
W_BB, W_HBP, W_1B, W_2B, W_3B, W_HR = 0.69, 0.72, 0.88, 1.24, 1.57, 2.00
WOBA_SCALE = 1.15


def rolling_woba_ish(bb, hbp, one_b, two_b, three_b, hr, ab, sf, ibb=0):
    numerator = W_BB * bb + W_HBP * hbp + W_1B * one_b + W_2B * two_b + W_3B * three_b + W_HR * hr
    denominator = ab + bb - ibb + sf + hbp
    if denominator <= 0:
        return None
    return numerator / denominator
