#!/usr/bin/env python3
"""
FAA（Frontal Alpha Asymmetry，前額 alpha 不對稱）分析。

每 1 秒算一個 FAA，再用「長度 10 的滑動視窗」取平均（做法與 engagement.py 完全相同），
輸出以過去 10 秒為基準的平滑 FAA。

公式：

    FAA = ln(α_AF8) − ln(α_AF7)

  - α_AF8：右前額 AF8 的 alpha 能量
  - α_AF7：左前額 AF7 的 alpha 能量
  註：alpha 能量與皮質活躍度「相反」（alpha 越強代表越不活躍）。
  FAA > 0（右側 alpha 較多 → 左額較活躍）常對應「趨近/正向情緒」；
  FAA < 0 常對應「退縮/負向情緒」。（僅供參考，個體差異大）

流程（三步驟，與 EI 相同）
--------------------------
步驟 1：沿用 fft_energy.py 的「每秒 FFT」，算出 AF7 / AF8 每 1 秒的 alpha 能量（µV²）。
步驟 2：每 1 秒獨立套一次上面的 FAA 公式 → 得到 FAA_1, FAA_2, ..., FAA_n。
步驟 3：用一個長度 10 的佇列（deque, maxlen=10）做滑動平均：
        - 收滿第 1~10 秒才輸出第 1 個穩定分數 = mean(FAA_1..FAA_10)
        - 第 11 秒進來時自動踢掉最舊的第 1 秒 → 輸出 mean(FAA_2..FAA_11)
        - 依此類推，每過 1 秒給一個「過去 10 秒平滑後」的 FAA。

用法
----
    python -m signal_monitor.analysis.faa                        # 分析 Data/ 內編號最大的檔
    python -m signal_monitor.analysis.faa Data/1.csv             # 指定輸入檔
    python -m signal_monitor.analysis.faa Data/1.csv --window 10 --fs 256
"""
import argparse
import csv
import os
import re
import sys
from collections import deque

import numpy as np

# 沿用 FFT 腳本（每秒 FFT、讀檔、找最新檔）與 EI 腳本的頻帶定義
from signal_monitor.analysis.fft_energy import (
    BASE_DIR,
    CSV_DIR,
    CHANNELS,
    latest_csv,
    load_eeg,
    per_second_energy,
)
from signal_monitor.analysis.engagement import band_energy  # alpha 頻帶能量（與 EI 用同一套 BANDS）


def main():
    ap = argparse.ArgumentParser(description="FAA（前額 alpha 不對稱）每秒計算 + 10 秒滑動平均")
    ap.add_argument("input", nargs="?", help="輸入 CSV（省略則用 Data/ 內編號最大的檔）")
    ap.add_argument("--fs", type=int, default=256, help="取樣率 Hz（MUSE 2 = 256）")
    ap.add_argument("--window", type=int, default=10, help="滑動視窗秒數（預設 10）")
    ap.add_argument("--out", help="輸出 CSV 路徑（預設 FAA/<輸入編號>.csv）")
    args = ap.parse_args()

    in_path = args.input or latest_csv(CSV_DIR)
    fs = args.fs
    win = args.window

    data = load_eeg(in_path)          # (樣本數, 4) 欄序 TP9, AF7, AF8, TP10
    n_sec = len(data) // fs
    if n_sec == 0:
        sys.exit(f"資料不足 1 秒（需要 {fs} 個樣本，只有 {len(data)} 個）。")

    # 步驟 1：AF7 / AF8 每秒 FFT → alpha 頻帶能量
    energies = {ch: per_second_energy(data[:, i], fs) for i, ch in enumerate(CHANNELS)}
    alpha_AF7 = band_energy(energies["AF7"], "alpha")
    alpha_AF8 = band_energy(energies["AF8"], "alpha")

    # 步驟 2：每秒獨立套一次 FAA = ln(α_AF8) − ln(α_AF7)
    #         alpha 能量須 > 0 才能取對數；否則該秒記為 NaN。
    faa = np.full(n_sec, np.nan)
    valid = (alpha_AF7 > 0) & (alpha_AF8 > 0)
    faa[valid] = np.log(alpha_AF8[valid]) - np.log(alpha_AF7[valid])

    # 步驟 3：長度 win 的滑動視窗平均（deque maxlen 會自動踢掉最舊的一秒）
    print(f"輸入：{in_path}")
    print(f"取樣率 {fs} Hz → 每秒 {fs} 樣本；可分析 {n_sec} 秒")
    print(f"公式：FAA = ln(α_AF8) − ln(α_AF7)　α 8–12Hz　滑動視窗 = {win} 秒\n")
    print(f"{'秒':>3}  {'FAA(每秒)':>10}  {'穩定FAA(近%d秒平均)' % win:>18}")
    print("-" * 40)

    q = deque(maxlen=win)
    rows = []              # 輸出 CSV 用
    stable_scores = []     # 收集所有穩定分數
    for s in range(n_sec):
        q.append(faa[s])
        raw = faa[s]
        if len(q) == win and not np.all(np.isnan(q)):
            smooth = float(np.nanmean(q))
            stable_scores.append(smooth)
            smooth_str = f"{smooth:18.4f}"
        else:
            smooth = ""
            smooth_str = f"{'（收集中）':>16}"
        raw_str = "nan" if np.isnan(raw) else f"{raw:+.4f}"
        print(f"{s + 1:>3}  {raw_str:>10}  {smooth_str}")
        rows.append([s + 1, "" if np.isnan(raw) else f"{raw:.6f}",
                     "" if smooth == "" else f"{smooth:.6f}"])

    # 總結
    print("-" * 40)
    if stable_scores:
        print(f"\n共輸出 {len(stable_scores)} 個穩定分數。")
        print(f"第 1 個穩定分數（第 {win} 秒，涵蓋 1~{win} 秒）= {stable_scores[0]:+.4f}")
        print(f"最後一個穩定分數（第 {n_sec} 秒）= {stable_scores[-1]:+.4f}")
        print(f"整段平滑 FAA 平均 = {np.mean(stable_scores):+.4f}")
    else:
        print(f"\n資料只有 {n_sec} 秒，不足 {win} 秒，尚無法輸出穩定分數（每秒 FAA 見上表）。")
        if not np.all(np.isnan(faa)):
            print(f"（參考）每秒 FAA 平均 = {np.nanmean(faa):+.4f}")

    # 存檔到 FAA/<編號>.csv
    out_dir = os.path.join(BASE_DIR, "FAA")
    if args.out:
        out_path = args.out
    else:
        os.makedirs(out_dir, exist_ok=True)
        open(os.path.join(out_dir, ".gitkeep"), "a").close()
        stem = re.sub(r"\.csv$", "", os.path.basename(in_path))
        out_path = os.path.join(out_dir, f"{stem}.csv")
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["second", "FAA", f"FAA_smooth{win}"])
        w.writerows(rows)
    print(f"\n已存檔：{out_path}")


if __name__ == "__main__":
    main()
