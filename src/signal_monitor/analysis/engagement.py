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
    python -m signal_monitor.analysis.engagement                 # 分析 Data/ 內編號最大的檔
    python -m signal_monitor.analysis.engagement Data/1.csv       # 指定輸入檔
    python -m signal_monitor.analysis.engagement Data/1.csv --window 10 --fs 256
"""
import argparse
import csv
import sys
from collections import deque

import numpy as np

# 沿用「之前的 FFT 腳本」的函式（每秒 FFT、讀檔、找最新檔）
from signal_monitor.analysis.fft_energy import (
    CSV_DIR,
    latest_csv,
    load_eeg,
    compute_band_energies,
    DEFAULT_WINDOW,
)
from signal_monitor.analysis.blink import blink_second_mask

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


def compute_ei_series(data, fs=256, reject_blinks=True, window=DEFAULT_WINDOW,
                      energies=None, blink_mask=None):
    """由原始 EEG 算出每秒 EI 的陣列。

    分母為 0 的秒記為 NaN（避免除以零）；reject_blinks=True 時，
    被眨眼污染的秒也記為 NaN —— 眨眼會把 theta（分母）放大約 5 倍，
    讓 EI 被壓低約 43%，不排除的話 EI 有一大半在測眨眼而非專注度。
    後續 smooth_series 用 nanmean，NaN 的秒會自動跳過。

    抽出來讓 cli / overall_process 可以直接在記憶體裡取用，
    不必先把 EI 落檔到 EI/ 再讀回來。
    """
    n_sec = len(data) // fs
    if energies is None:
        energies = compute_band_energies(data, fs, window)

    numerator = band_energy(energies["AF7"], "beta") + band_energy(energies["AF8"], "beta")
    denominator = (
        band_energy(energies["TP9"], "alpha") + band_energy(energies["TP10"], "alpha")
        + band_energy(energies["AF7"], "theta") + band_energy(energies["AF8"], "theta")
    )
    ei = np.divide(
        numerator, denominator,
        out=np.full(n_sec, np.nan), where=denominator > 0,
    )
    if reject_blinks:
        if blink_mask is None:
            blink_mask = blink_second_mask(data, fs=fs)
        ei[blink_mask] = np.nan
    return ei


def smooth_series(values, win, min_valid=3):
    """長度 win 的滑動視窗平均。

    回傳 [(second, raw, smooth_or_None), ...]，second 從 1 起算；
    視窗未收滿、或視窗內有效（非 NaN）的秒數少於 min_valid 時 smooth 為 None。
    排除眨眼後視窗內會出現 NaN，min_valid 確保平滑值不是由一兩秒硬撐出來的。
    EI 與 FAA 用的是同一套平滑邏輯，所以放在這裡共用。
    """
    # min_valid 不能大於視窗長度，否則條件永遠不成立、整欄都是空值。
    # （--window 1 或 2 搭配預設 min_valid=3 就會踩到）
    min_valid = max(1, min(min_valid, win))

    q = deque(maxlen=win)
    out = []
    for i, value in enumerate(values):
        q.append(value)
        n_valid = int(np.count_nonzero(~np.isnan(q)))
        ready = len(q) == win and n_valid >= min_valid
        out.append((i + 1, value, float(np.nanmean(q)) if ready else None))
    return out


def main():
    ap = argparse.ArgumentParser(description="NASA 專注度指數（EI）每秒計算 + 10 秒滑動平均")
    ap.add_argument("input", nargs="?", help="輸入 CSV（省略則用 Data/ 內編號最大的檔）")
    ap.add_argument("--fs", type=int, default=256, help="取樣率 Hz（MUSE 2 = 256）")
    ap.add_argument("--window", type=int, default=10, help="滑動視窗秒數（預設 10）")
    ap.add_argument("--out", help="把逐秒結果存成 CSV 的路徑（省略則只顯示、不存檔）")
    ap.add_argument("--keep-blinks", action="store_true",
                    help="不排除眨眼污染的秒（舊行為，不建議）")
    ap.add_argument("--fft-window", default=DEFAULT_WINDOW,
                    help=f"FFT 前的視窗函數：tukey<alpha> / hann / rect（預設 {DEFAULT_WINDOW}）")
    args = ap.parse_args()

    in_path = args.input or latest_csv(CSV_DIR)
    fs = args.fs
    win = args.window

    data = load_eeg(in_path)          # (樣本數, 4) 欄序 TP9, AF7, AF8, TP10
    n_sec = len(data) // fs
    if n_sec == 0:
        sys.exit(f"資料不足 1 秒（需要 {fs} 個樣本，只有 {len(data)} 個）。")

    # 步驟 1+2：每秒 FFT → 頻帶能量 → 每秒 EI
    ei = compute_ei_series(data, fs, reject_blinks=not args.keep_blinks,
                           window=args.fft_window)

    # 步驟 3：長度 win 的滑動視窗平均（deque maxlen 會自動踢掉最舊的一秒）
    n_valid = int(np.count_nonzero(~np.isnan(ei)))
    if not args.keep_blinks:
        n_drop = n_sec - n_valid
        print(f"眨眼排除：{n_drop}/{n_sec} 秒（{n_drop / n_sec:.1%}）被標記為眨眼污染而略過")
    print(f"輸入：{in_path}")
    print(f"取樣率 {fs} Hz → 每秒 {fs} 樣本；可分析 {n_sec} 秒")
    print("頻帶：θ 4–8Hz　α 8–12Hz　β 13–30Hz（1Hz 整數格，邊界 8→α、13→β）　"
          f"滑動視窗 = {win} 秒\n")
    print(f"{'秒':>3}  {'EI(每秒)':>10}  {'穩定EI(近%d秒平均)' % win:>18}")
    print("-" * 40)

    rows = []              # 輸出 CSV 用
    stable_scores = []     # 收集所有穩定分數
    for second, raw, smooth in smooth_series(ei, win):
        if smooth is None:
            smooth_str = f"{'（收集中）':>16}"
        else:
            stable_scores.append(smooth)
            smooth_str = f"{smooth:18.4f}"
        raw_str = "nan" if np.isnan(raw) else f"{raw:.4f}"
        print(f"{second:>3}  {raw_str:>10}  {smooth_str}")
        rows.append([second, "" if np.isnan(raw) else f"{raw:.6f}",
                     "" if smooth is None else f"{smooth:.6f}"])

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
    # 預設不落檔：EI 已經併進 Features/<編號>.csv，不需要另一份中繼檔。
    # 只有明確指定 --out 時才寫出來。
    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["second", "EI", f"EI_smooth{win}"])
            w.writerows(rows)
        print(f"\n已存檔：{args.out}")


if __name__ == "__main__":
    main()
