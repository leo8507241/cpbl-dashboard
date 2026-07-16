"""
樂天林立分析：每日自動更新腳本（給 LaunchAgent 呼叫）
1. 抓當天新完賽的比賽 2. 重新產生 notebook 3. 同步資料到 Hugging Face Space 4. 開瀏覽器顯示儀表板

用 python3 直接執行（而不是 bash），避免 macOS 對 /bin/bash 存取 Desktop 底下資料夾的
TCC 權限限制（跟另一個 daily_update.py 用同一種模式，親測可行）。
"""
import os
import shutil
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PY = "/opt/anaconda3/bin/python3"
JUPYTER = "/opt/anaconda3/bin/jupyter"
HF_REPO = "leo88888/rakuten-lin-li-effect"


def log(msg):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def main():
    os.chdir(BASE_DIR)
    log("===== 開始每日更新 =====")

    log("--- 1. 抓新比賽 ---")
    subprocess.run([PY, "scrape_games.py"], check=True)

    log("--- 2. 重新產生 notebook ---")
    subprocess.run([
        JUPYTER, "nbconvert", "--to", "notebook", "--execute", "--inplace",
        "--ExecutePreprocessor.kernel_name=python3",
        "樂天林立效應報告.ipynb",
    ], check=True)

    log("--- 3. 同步資料到 Hugging Face Space ---")
    shutil.copy("lin_li_games_cache.csv", "hf_space/lin_li_games_cache.csv")
    shutil.copy("last_update.json", "hf_space/last_update.json")

    from huggingface_hub import HfApi
    api = HfApi()

    # 推送至 rakuten-lin-li-effect（獨立 Space）
    api.upload_file(
        path_or_fileobj="hf_space/lin_li_games_cache.csv",
        path_in_repo="lin_li_games_cache.csv",
        repo_id=HF_REPO,
        repo_type="space",
    )
    api.upload_file(
        path_or_fileobj="hf_space/last_update.json",
        path_in_repo="last_update.json",
        repo_id=HF_REPO,
        repo_type="space",
    )

    # 推送至 cpbl-dashboard（主多頁儀表板）
    for remote_path in ["lin_li_games_cache.csv", "last_update.json", "stats.py"]:
        local_path = os.path.join("hf_space", remote_path)
        if os.path.exists(local_path):
            api.upload_file(
                path_or_fileobj=local_path,
                path_in_repo=remote_path,
                repo_id="leo88888/cpbl-dashboard",
                repo_type="space",
            )
    log("HF Space 資料同步完成（rakuten-lin-li-effect + cpbl-dashboard）")

    log("--- 4. 開啟瀏覽器顯示儀表板 ---")
    subprocess.run(["open", f"https://huggingface.co/spaces/{HF_REPO}"], check=False)

    log("===== 每日更新完成 =====")


if __name__ == "__main__":
    main()
