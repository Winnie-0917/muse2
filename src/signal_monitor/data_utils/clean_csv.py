#!/usr/bin/env python3
"""
刪除專案內的 CSV 檔，但**保留 Model/ 的訓練資料**。

預設會刪除
----------
  - Data/*.csv       （原始 EEG 錄製）
  - Features/*.csv   （EI / FAA / 眨眼 / BPM 合併輸出）
  - 其他散落在專案內的 *.csv

預設**不會**碰
--------------
  - Model/**/*.csv   boring / interesting 的訓練資料。這是人工標註整理過的，
                     不是分析產物，重跑任何步驟都生不回來，所以預設保護。
                     要一起刪：--include-model（或 --all）

安全機制
--------
- 只在「本程式所在的專案資料夾」內找，並**排除 venv/、.git/、__pycache__**
  等目錄，避免誤刪 Python 套件或版控內部的 .csv。
- 只刪副檔名為 .csv 的檔；**不動 .gitkeep，也不刪資料夾**（空資料夾結構保留）。
- 預設會先列出清單並要求輸入 y 確認；刪除不可復原。
- 刪掉 Data/ 的原始錄製後，Features 就再也算不回來了（只能重錄）。
  想留著原始錄製請加 --keep-recordings，那樣刪完會自動重建一次 Features/。

用法
----
    python -m signal_monitor.data_utils.clean_csv                   # 列出並詢問確認後刪除
    python -m signal_monitor.data_utils.clean_csv --dry-run         # 只預覽、不刪除
    python -m signal_monitor.data_utils.clean_csv -y                # 不詢問，直接刪除
    python -m signal_monitor.data_utils.clean_csv --keep-recordings # 保留 Data/ 的原始錄製
    python -m signal_monitor.data_utils.clean_csv --all             # 連訓練資料也刪
"""
import argparse
import os
import re
import shutil
import sys

from signal_monitor.analysis.features import build_features_csv
from signal_monitor.paths import PROJECT_ROOT

BASE_DIR = PROJECT_ROOT
# 這些目錄不進入搜尋（避免誤刪套件/版控內部的 .csv）
EXCLUDE_DIRS = {"venv", ".venv", "env", ".git", "__pycache__", ".idea", ".vscode"}

RECORDINGS_DIR = "Data"    # 原始 EEG 錄製
# 預設保護：訓練資料是人工標註整理過的，不是分析產物，重跑任何步驟都生不回來。
MODEL_DIR = "Model"        # boring / interesting 訓練資料


def find_csv_files(base, keep_recordings=False, include_model=False):
    """回傳 base 底下可刪除的 .csv 絕對路徑。

    預設刪掉除了 Model/（訓練資料）以外的所有 .csv，包含 Data/ 的原始錄製。
    keep_recordings=True 時額外保住 Data/；include_model=True 時連訓練資料也刪。
    """
    protected = set()
    if keep_recordings:
        protected.add(os.path.join(base, RECORDINGS_DIR))
    if not include_model:
        protected.add(os.path.join(base, MODEL_DIR))

    found = []
    for root, dirs, files in os.walk(base):
        # 就地修改 dirs 讓 os.walk 不要進入被排除的資料夾
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        if any(root == p or root.startswith(p + os.sep) for p in protected):
            continue
        for name in files:
            if name.lower().endswith(".csv"):
                found.append(os.path.join(root, name))
    return sorted(found)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def regenerate_features_output(base_dir=None):
    """刪除並重建 Features/ 輸出（EI / FAA / 眨眼 / BPM），用最新的錄製檔生成。

    EI 與 FAA 不再各自輸出到 EI/ 與 FAA/ —— 兩者都直接併進 Features/<編號>.csv，
    所以這裡只重建 Features/。回傳重建出來的檔案路徑清單。
    """
    base_dir = base_dir or PROJECT_ROOT
    data_dir = os.path.join(base_dir, "Data")
    if not os.path.isdir(data_dir):
        return []

    files = [f for f in os.listdir(data_dir) if re.match(r"^\d+\.csv$", f)]
    if not files:
        return []

    latest_file = sorted(files, key=lambda x: int(x[:-4]))[-1]
    latest_path = os.path.join(data_dir, latest_file)
    stem = re.sub(r"\.csv$", "", latest_file)

    out_dir = os.path.join(base_dir, "Features")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, ".gitkeep"), "a").close()

    features_path = os.path.join(out_dir, f"{stem}.csv")
    try:
        build_features_csv(latest_path, features_path)
    except ValueError as exc:
        print(f"重建 Features 失敗：{exc}", file=sys.stderr)
        return []
    return [features_path]


def main():
    ap = argparse.ArgumentParser(description="刪除本專案內所有 CSV 檔（保留資料夾與 .gitkeep）")
    ap.add_argument("-y", "--yes", action="store_true", help="不詢問，直接刪除")
    ap.add_argument("-n", "--dry-run", action="store_true", help="只預覽要刪的檔，不實際刪除")
    ap.add_argument("--keep-recordings", action="store_true",
                    help=f"保留 {RECORDINGS_DIR}/ 的原始錄製不刪（Features 才有辦法重建）")
    ap.add_argument("--include-model", action="store_true",
                    help=f"連 {MODEL_DIR}/ 的訓練資料一起刪（預設保護）")
    ap.add_argument("-a", "--all", action="store_true",
                    help="連訓練資料也刪，等同 --include-model")
    args = ap.parse_args()

    keep_recordings = args.keep_recordings
    include_model = args.include_model or args.all
    files = find_csv_files(BASE_DIR, keep_recordings, include_model)
    kept = []
    if keep_recordings:
        kept.append(f"{RECORDINGS_DIR}/（原始錄製）")
    if not include_model:
        kept.append(f"{MODEL_DIR}/（訓練資料）")
    if kept:
        print(f"保護中，不會刪除：{'、'.join(kept)}")
        if not include_model:
            print("（要連訓練資料一起刪請加 --include-model）")
        print()

    if not files:
        print("沒有可刪除的 .csv 檔。")
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
        if not keep_recordings:
            print(f"\n警告：這會刪掉 {RECORDINGS_DIR}/ 的原始 EEG 錄製。"
                  f"Features 是從它重算出來的，刪掉之後只能重錄；"
                  f"要保留請加 --keep-recordings。")
        if include_model:
            print(f"警告：這會刪掉 {MODEL_DIR}/ 的訓練資料（人工標註整理過的）。")
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

    # 只有加了 --keep-recordings 時 Data/ 才會留著，也才重建得出 Features。
    regenerated = regenerate_features_output(BASE_DIR)
    if regenerated:
        print(f"已用最新的錄製檔重建：{', '.join(os.path.relpath(p, BASE_DIR) for p in regenerated)}")
    elif keep_recordings:
        print(f"{RECORDINGS_DIR}/ 內沒有可用的錄製檔，Features 未重建。")


if __name__ == "__main__":
    main()
