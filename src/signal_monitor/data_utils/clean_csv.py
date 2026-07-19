#!/usr/bin/env python3
"""
刪除本專案內所有 CSV 檔（錄製的原始資料、FFT 輸出、EI 輸出、FAA 輸出）。

會刪除的位置：專案根目錄底下所有 *.csv
  - Data/*.csv        （record_csv.py / Overall_process.py 錄下的原始 EEG）
  - EI/*.csv         （engagement.py 的 EI 輸出）
  - FAA/*.csv        （faa.py 的 FAA 輸出）
  - FFT/<通道>/*.csv （fft_energy.py 的每秒 FFT 輸出）
  - 其他散落在專案內的 *.csv

安全機制
--------
- 只在「本程式所在的專案資料夾」內找，並**排除 venv/、.git/、__pycache__**
  等目錄，避免誤刪 Python 套件或版控內部的 .csv。
- 只刪副檔名為 .csv 的檔；**不動 .gitkeep，也不刪資料夾**（空資料夾結構保留）。
- 預設會先列出清單並要求輸入 y 確認；刪除不可復原。

用法
----
    python -m signal_monitor.data_utils.clean_csv            # 列出並詢問確認後刪除
    python -m signal_monitor.data_utils.clean_csv --dry-run  # 只預覽、不刪除
    python -m signal_monitor.data_utils.clean_csv -y         # 不詢問，直接刪除（給腳本用）
"""
import argparse
import os
import sys

from signal_monitor.paths import PROJECT_ROOT

BASE_DIR = PROJECT_ROOT
# 這些目錄不進入搜尋（避免誤刪套件/版控內部的 .csv）
EXCLUDE_DIRS = {"venv", ".venv", "env", ".git", "__pycache__", ".idea", ".vscode"}


def find_csv_files(base):
    """回傳 base 底下所有 .csv 的絕對路徑（已排除 EXCLUDE_DIRS）。"""
    found = []
    for root, dirs, files in os.walk(base):
        # 就地修改 dirs 讓 os.walk 不要進入被排除的資料夾
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in files:
            if name.lower().endswith(".csv"):
                found.append(os.path.join(root, name))
    return sorted(found)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def main():
    ap = argparse.ArgumentParser(description="刪除本專案內所有 CSV 檔（保留資料夾與 .gitkeep）")
    ap.add_argument("-y", "--yes", action="store_true", help="不詢問，直接刪除")
    ap.add_argument("-n", "--dry-run", action="store_true", help="只預覽要刪的檔，不實際刪除")
    args = ap.parse_args()

    files = find_csv_files(BASE_DIR)
    if not files:
        print("專案內沒有任何 .csv 檔，無需刪除。")
        return

    total = sum(os.path.getsize(f) for f in files)
    print(f"在專案內找到 {len(files)} 個 .csv 檔（共 {human_size(total)}）：\n")
    for f in files:
        rel = os.path.relpath(f, BASE_DIR)
        print(f"  {rel:<40} {human_size(os.path.getsize(f)):>10}")

    if args.dry_run:
        print("\n[--dry-run] 只是預覽，未刪除任何檔案。")
        return

    if not args.yes:
        try:
            ans = input(f"\n確定要刪除以上 {len(files)} 個檔案嗎？此動作無法復原。(y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""  # 沒有互動輸入（例如管線）或被中斷 -> 視為取消
        if ans not in ("y", "yes"):
            print("\n已取消，未刪除任何檔案。（要免詢問刪除請加 -y）")
            return

    deleted, failed = 0, 0
    for f in files:
        try:
            os.remove(f)
            deleted += 1
        except OSError as e:
            failed += 1
            print(f"  刪除失敗：{os.path.relpath(f, BASE_DIR)} -> {e}", file=sys.stderr)

    print(f"\n完成：已刪除 {deleted} 個檔案" + (f"，{failed} 個失敗。" if failed else "。"))
    print("（資料夾與 .gitkeep 保留，結構不變。）")


if __name__ == "__main__":
    main()
