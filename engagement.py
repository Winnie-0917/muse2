#!/usr/bin/env python3
"""
NASA 專注度 / 投入度指數（Engagement Index, EI）分析。

每 1 秒獨立算出一個 EI 分數，再用「長度 10 的滑動視窗」取平均，
輸出以過去 10 秒為基準的平滑專注度。

公式（依提供的圖，NASA EI）：

              β_AF7 + β_AF8
    EI = ---------------------------------
         α_TP9 + α_TP10 + θ_AF7 + θ_AF8

  - β（beta）：AF7、AF8 兩個前額通道的 beta 能量（專注/認知努力上升時增加）
  - α（alpha）：TP9、TP10 兩個耳後通道的 alpha 能量（放鬆/閒置時增加）
  - θ（theta）：AF7、AF8 的 theta 能量（放鬆/想睡時增加）
  分母大 → 放鬆；分子大 → 專注。EI 越高代表越投入。

流程
----
步驟 1：沿用 fft_energy.py 的「每秒 FFT」，把每 1 秒切成 256 個樣本做 FFT，
        算出每個通道在 θ / α / β 三個頻帶的能量（µV²）。
步驟 2：每 1 秒獨立套一次上面的 EI 公式 → 得到 EI_1, EI_2, ..., EI_n。
步驟 3：用一個長度 10 的佇列（deque, maxlen=10）做滑動平均：
        - 收滿第 1~10 秒才輸出第 1 個穩定分數 = mean(EI_1..EI_10)
        - 第 11 秒進來時自動踢掉最舊的第 1 秒 → 輸出 mean(EI_2..EI_11)
        - 依此類推，每過 1 秒給一個「過去 10 秒平滑後」的專注度。

用法
----
    ./venv/bin/python engagement.py                 # 分析 Data/ 內編號最大的檔
    ./venv/bin/python engagement.py Data/1.csv        # 指定輸入檔
    ./venv/bin/python engagement.py Data/1.csv --window 10 --fs 256
"""
import argparse
import csv
import os
import re
import sys
from collections import deque

import numpy as np

# 沿用「之前的 FFT 腳本」的函式（每秒 FFT、讀檔、找最新檔）
from fft_energy import (
    BASE_DIR,
    CSV_DIR,
    CHANNELS,
    latest_csv,
    load_eeg,
    per_second_energy,
)

# 頻帶定義：以 1 Hz 整數格（per_second_energy 的第 k 欄 = k Hz）為單位、含頭含尾。
# 對照 README 的 θ 4–8 / α 8–12 / β 13–30 Hz；重疊的邊界用標準規則歸類，
# 避免同一格被算兩次：8 Hz 併入 α、13 Hz 併入 β。
BANDS = {
    "theta": (4, 7),    # θ 4–8 Hz（8 歸給 α）→ 格 4,5,6,7
    "alpha": (8, 12),   # α 8–12 Hz          → 格 8,9,10,11,12
    "beta": (13, 30),   # β 13–30 Hz          → 格 13..30
}


def band_energy(energies, band):
    """energies: (秒數, 129)，欄 k = k Hz。回傳該頻帶每秒的能量和 (秒數,)。"""
    lo, hi = BANDS[band]
    return energies[:, lo:hi + 1].sum(axis=1)


def main():
    ap = argparse.ArgumentParser(description="NASA 專注度指數（EI）每秒計算 + 10 秒滑動平均")
    ap.add_argument("input", nargs="?", help="輸入 CSV（省略則用 Data/ 內編號最大的檔）")
    ap.add_argument("--fs", type=int, default=256, help="取樣率 Hz（MUSE 2 = 256）")
    ap.add_argument("--window", type=int, default=10, help="滑動視窗秒數（預設 10）")
    ap.add_argument("--out", help="輸出 CSV 路徑（預設 EI/<輸入編號>.csv）")
    args = ap.parse_args()

    in_path = args.input or latest_csv(CSV_DIR)
    fs = args.fs
    win = args.window

    data = load_eeg(in_path)          # (樣本數, 4) 欄序 TP9, AF7, AF8, TP10
    n_sec = len(data) // fs
    if n_sec == 0:
        sys.exit(f"資料不足 1 秒（需要 {fs} 個樣本，只有 {len(data)} 個）。")

    # 步驟 1：每個通道 → 每秒 FFT →（秒數, 129）能量矩陣
    energies = {ch: per_second_energy(data[:, i], fs) for i, ch in enumerate(CHANNELS)}

    # 各公式所需的「每秒頻帶能量」
    beta_AF7 = band_energy(energies["AF7"], "beta")
    beta_AF8 = band_energy(energies["AF8"], "beta")
    alpha_TP9 = band_energy(energies["TP9"], "alpha")
    alpha_TP10 = band_energy(energies["TP10"], "alpha")
    theta_AF7 = band_energy(energies["AF7"], "theta")
    theta_AF8 = band_energy(energies["AF8"], "theta")

    numerator = beta_AF7 + beta_AF8
    denominator = alpha_TP9 + alpha_TP10 + theta_AF7 + theta_AF8

    # 步驟 2：每秒獨立套一次 EI 公式（分母為 0 時記為 NaN，避免除以零）
    ei = np.divide(
        numerator, denominator,
        out=np.full(n_sec, np.nan), where=denominator > 0,
    )

    # 步驟 3：長度 win 的滑動視窗平均（deque maxlen 會自動踢掉最舊的一秒）
    print(f"輸入：{in_path}")
    print(f"取樣率 {fs} Hz → 每秒 {fs} 樣本；可分析 {n_sec} 秒")
    print("頻帶：θ 4–8Hz　α 8–12Hz　β 13–30Hz（1Hz 整數格，邊界 8→α、13→β）　"
          f"滑動視窗 = {win} 秒\n")
    print(f"{'秒':>3}  {'EI(每秒)':>10}  {'穩定EI(近%d秒平均)' % win:>18}")
    print("-" * 40)

    q = deque(maxlen=win)
    rows = []              # 輸出 CSV 用
    stable_scores = []     # 收集所有穩定分數
    for s in range(n_sec):
        q.append(ei[s])
        raw = ei[s]
        if len(q) == win and not np.all(np.isnan(q)):
            smooth = float(np.nanmean(q))
            stable_scores.append(smooth)
            smooth_str = f"{smooth:18.4f}"
        else:
            smooth = ""
            smooth_str = f"{'（收集中）':>16}"
        raw_str = "nan" if np.isnan(raw) else f"{raw:.4f}"
        print(f"{s + 1:>3}  {raw_str:>10}  {smooth_str}")
        rows.append([s + 1, "" if np.isnan(raw) else f"{raw:.6f}",
                     "" if smooth == "" else f"{smooth:.6f}"])

    # 總結
    print("-" * 40)
    if stable_scores:
        print(f"\n共輸出 {len(stable_scores)} 個穩定分數。")
        print(f"第 1 個穩定分數（第 {win} 秒，涵蓋 1~{win} 秒）= {stable_scores[0]:.4f}")
        print(f"最後一個穩定分數（第 {n_sec} 秒）= {stable_scores[-1]:.4f}")
        print(f"整段平滑專注度平均 = {np.mean(stable_scores):.4f}")
    else:
        print(f"\n資料只有 {n_sec} 秒，不足 {win} 秒，尚無法輸出穩定分數（每秒 EI 見上表）。")
        if not np.all(np.isnan(ei)):
            print(f"（參考）每秒 EI 平均 = {np.nanmean(ei):.4f}")

    # 存檔到 EI/<編號>.csv
    out_dir = os.path.join(BASE_DIR, "EI")
    if args.out:
        out_path = args.out
    else:
        os.makedirs(out_dir, exist_ok=True)
        open(os.path.join(out_dir, ".gitkeep"), "a").close()
        stem = re.sub(r"\.csv$", "", os.path.basename(in_path))
        out_path = os.path.join(out_dir, f"{stem}.csv")
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["second", "EI", f"EI_smooth{win}"])
        w.writerows(rows)
    print(f"\n已存檔：{out_path}")


if __name__ == "__main__":
    main()
