"""
修正 Supabase 中 台鋼雄鷹 2020-2022 球隊名稱錯誤
台鋼雄鷹 2023 年才加入 CPBL 一軍，
2020-2022 的記錄應為二軍時期，球隊名應為「台鋼隊」。
另外刪除「魔鷹」虛假球員紀錄。
"""

from supabase import create_client

SUPABASE_URL = 'https://vxgtgqlqukexpvnnvslf.supabase.co'
SUPABASE_KEY = 'sb_publishable_9DDMVwHFIMCdBxN12jaWkQ_gMeJkRuI'

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
TABLE = 'cpbl_batting_2020_2026'

# ── 步驟 1：確認受影響筆數 ──────────────────────────────────
print('=== 修正前確認 ===')
before = (sb.table(TABLE)
            .select('年度, 球員, 球隊', count='exact')
            .eq('球隊', '台鋼雄鷹')
            .lt('年度', 2023)
            .execute())
print(f'台鋼雄鷹 2020-2022 筆數：{before.count}')

ghost = (sb.table(TABLE)
           .select('年度, 球員', count='exact')
           .eq('球員', '魔鷹')
           .execute())
print(f'魔鷹虛假球員筆數：{ghost.count}')

# ── 步驟 2：刪除「魔鷹」虛假紀錄 ───────────────────────────
print('\n=== 刪除魔鷹紀錄 ===')
del_result = (sb.table(TABLE)
                .delete()
                .eq('球員', '魔鷹')
                .execute())
print(f'已刪除：{len(del_result.data)} 筆')

# ── 步驟 3：將 2020-2022 台鋼雄鷹 → 台鋼隊 ─────────────────
print('\n=== 更新 2020-2022 台鋼雄鷹 → 台鋼隊 ===')
for yr in [2020, 2021, 2022]:
    res = (sb.table(TABLE)
             .update({'球隊': '台鋼隊'})
             .eq('球隊', '台鋼雄鷹')
             .eq('年度', yr)
             .execute())
    print(f'  {yr} 年：更新 {len(res.data)} 筆')

# ── 步驟 4：確認結果 ─────────────────────────────────────────
print('\n=== 修正後確認 ===')
after = (sb.table(TABLE)
           .select('年度, 球員, 球隊', count='exact')
           .eq('球隊', '台鋼雄鷹')
           .lt('年度', 2023)
           .execute())
print(f'台鋼雄鷹 2020-2022 剩餘：{after.count}（應為 0）')

taigang = (sb.table(TABLE)
             .select('年度, 球員, 球隊', count='exact')
             .eq('球隊', '台鋼隊')
             .execute())
print(f'台鋼隊（二軍時期）總筆數：{taigang.count}')

print('\n✅ 完成！')
