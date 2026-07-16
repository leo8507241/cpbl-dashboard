#!/bin/bash
# 部署 3 個獨立 CPBL Dashboard 至 Hugging Face Spaces
# 用法：bash deploy_hf.sh [your-hf-username]
#
# 第一次使用前確認已登入：
#   python3 -c "from huggingface_hub import whoami; print(whoami()['name'])"

HF_USER="${1:-leo88888}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "📦 部署至 HF Spaces（帳號：${HF_USER}）"

python3 - <<PYEOF
from huggingface_hub import HfApi
from huggingface_hub.utils import RepositoryNotFoundError
import io, os

api = HfApi()
user = '${HF_USER}'
script_dir = '${SCRIPT_DIR}'

def upload(repo_id, files_map):
    """files_map: {remote_path: local_path_or_bytes}"""
    try:
        api.repo_info(repo_id=repo_id, repo_type='space')
    except RepositoryNotFoundError:
        print(f'  ⚠️  Space {repo_id} 不存在，跳過（可在 HF 建立後重新執行）')
        return

    for remote, local in files_map.items():
        if isinstance(local, str):
            if not os.path.exists(local):
                print(f'  ⚠️  找不到 {local}，跳過')
                continue
            api.upload_file(path_or_fileobj=local, path_in_repo=remote,
                            repo_id=repo_id, repo_type='space')
        else:
            api.upload_file(path_or_fileobj=io.BytesIO(local), path_in_repo=remote,
                            repo_id=repo_id, repo_type='space')
        print(f'  ✅ {remote}')

req = os.path.join(script_dir, 'requirements.txt')

# ── 1. 打者真實排名（完整 MPA Dashboard）──────────────────────
print(f'\n=== 1/3 cpbl-dashboard ===')

def fix_page2_imports(src_path):
    """page 2 在 HF 上找不到 每日追蹤/ 子目錄，改成從 Space root 引入。"""
    with open(src_path, encoding='utf-8') as f:
        code = f.read()
    code = code.replace(
        'sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "每日追蹤"))',
        'sys.path.insert(0, os.path.dirname(_HERE))',
    )
    return code.encode('utf-8')

pages_dir = os.path.join(script_dir, 'pages')
upload(f'{user}/cpbl-dashboard', {
    '被低估打者預測.py':                  os.path.join(script_dir, 'app.py'),
    'requirements.txt':                    req,
    'README.md':                           os.path.join(script_dir, 'README.md'),
    'archetypes.py':                       os.path.join(script_dir, '每日追蹤/archetypes.py'),
    'fatigue_common.py':                   os.path.join(script_dir, 'fatigue_common.py'),
    'woba_weights.py':                     os.path.join(script_dir, '每日追蹤/woba_weights.py'),
    'pitcher_pitches.csv':                 os.path.join(script_dir, 'pitcher_pitches.csv'),
    'pages/1_打者趨勢雷達.py':            os.path.join(pages_dir, '1_打者趨勢雷達.py'),
    'pages/2_投手剋星分析.py':            fix_page2_imports(os.path.join(pages_dir, '2_投手剋星分析.py')),
    'pages/3_林立效應分析.py':            os.path.join(pages_dir, '3_林立效應分析.py'),
    'pages/4_投手弱點分析.py':            os.path.join(pages_dir, '4_投手弱點分析.py'),
    'pages/5_單場即時換投監控.py':        os.path.join(pages_dir, '5_單場即時換投監控.py'),
})

# ── 2. 打者趨勢雷達 ──────────────────────────────────────────
print(f'\n=== 2/3 cpbl-batter-radar ===')
upload(f'{user}/cpbl-batter-radar', {
    'app.py':           os.path.join(script_dir, 'pages/1_打者趨勢雷達.py'),
    'requirements.txt': req,
})

# ── 3. 投手剋星分析（獨立 Space）──────────────────────────────
print(f'\n=== 3/3 cpbl-pitcher-matchup ===')
# 修正 import：archetypes.py 在同層（Space root），不在 每日追蹤/ 子目錄
# 保留 import os（load_pitches 需要）、移除 sys.path 多餘操作
with open(os.path.join(script_dir, 'pages/2_投手剋星分析.py'), encoding='utf-8') as f:
    code = f.read()
code = code.replace(
    '_HERE = os.path.dirname(os.path.abspath(__file__))\n'
    'sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "每日追蹤"))\n'
    'from archetypes import add_archetype, batter_season_stats_from_matchup, MIN_PA as ARCHETYPE_MIN_PA  # noqa: E402',
    '_HERE = os.path.dirname(os.path.abspath(__file__))\n'
    'sys.path.insert(0, os.path.dirname(_HERE))\n'
    'from archetypes import add_archetype, batter_season_stats_from_matchup, MIN_PA as ARCHETYPE_MIN_PA  # noqa: E402'
)
upload(f'{user}/cpbl-pitcher-matchup', {
    'app.py':              code.encode('utf-8'),
    'archetypes.py':       os.path.join(script_dir, '每日追蹤/archetypes.py'),
    'pitcher_pitches.csv': os.path.join(script_dir, 'pitcher_pitches.csv'),
    'requirements.txt':    req,
})

print(f'''
🚀 部署完成！
   打者真實排名  https://huggingface.co/spaces/{user}/cpbl-dashboard
   打者趨勢雷達  https://huggingface.co/spaces/{user}/cpbl-batter-radar
   投手剋星分析  https://huggingface.co/spaces/{user}/cpbl-pitcher-matchup
''')
PYEOF
