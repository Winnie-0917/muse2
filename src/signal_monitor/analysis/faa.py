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
import sys

import numpy as np

# 沿用 FFT 腳本（每秒 FFT、讀檔、找最新檔）與 EI 腳本的頻帶定義
from signal_monitor.analysis.fft_energy import (
    CSV_DIR,
    latest_csv,
    load_eeg,
    compute_band_energies,
    DEFAULT_WINDOW,
)
from signal_monitor.analysis.engagement import (  # 與 EI 共用頻帶定義與平滑邏輯
    band_energy,
    smooth_series,
)
from signal_monitor.analysis.blink import blink_second_mask


def compute_faa_series(data, fs=256, reject_blinks=True, window=DEFAULT_WINDOW,
                       energies=None, blink_mask=None):
    """由原始 EEG 算出每秒 FAA 的陣列。

    FAA = ln(alpha_AF8) - ln(alpha_AF7)；alpha 能量須 > 0 才能取對數，否則該秒為 NaN。
    reject_blinks=True 時，被眨眼污染的秒也記為 NaN。眨眼的能量主要落在低頻，
    對 alpha 的影響小於對 theta，但眨眼是雙側同步、左右未必等量，
    仍會擾動 AF8 與 AF7 的比值，所以一併排除。

    抽出來讓 cli / overall_process 可以直接在記憶體裡取用，不必先落檔到 FAA/。
    """
    n_sec = len(data) // fs
    if energies is None:
        energies = compute_band_energies(data, fs, window)
    alpha_AF7 = band_energy(energies["AF7"], "alpha")
    alpha_AF8 = band_energy(energies["AF8"], "alpha")

    faa = np.full(n_sec, np.nan)
    valid = (alpha_AF7 > 0) & (alpha_AF8 > 0)
    faa[valid] = np.log(alpha_AF8[valid]) - np.log(alpha_AF7[valid])
    if reject_blinks:
        if blink_mask is None:
            blink_mask = blink_second_mask(data, fs=fs)
        faa[blink_mask] = np.nan
    return faa


def main():
    ap = argparse.ArgumentParser(description="FAA（前額 alpha 不對稱）每秒計算 + 10 秒滑動平均")
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

    # 步驟 1+2：AF7 / AF8 每秒 FFT → alpha 能量 → 每秒 FAA
    faa = compute_faa_series(data, fs, reject_blinks=not args.keep_blinks,
                             window=args.fft_window)

    # 步驟 3：長度 win 的滑動視窗平均（deque maxlen 會自動踢掉最舊的一秒）
    n_valid = int(np.count_nonzero(~np.isnan(faa)))
    if not args.keep_blinks:
        n_drop = n_sec - n_valid
        print(f"眨眼排除：{n_drop}/{n_sec} 秒（{n_drop / n_sec:.1%}）被標記為眨眼污染而略過")
    print(f"輸入：{in_path}")
    print(f"取樣率 {fs} Hz → 每秒 {fs} 樣本；可分析 {n_sec} 秒")
    print(f"公式：FAA = ln(α_AF8) − ln(α_AF7)　α 8–12Hz　滑動視窗 = {win} 秒\n")
    print(f"{'秒':>3}  {'FAA(每秒)':>10}  {'穩定FAA(近%d秒平均)' % win:>18}")
    print("-" * 40)

    rows = []              # 輸出 CSV 用
    stable_scores = []     # 收集所有穩定分數
    for second, raw, smooth in smooth_series(faa, win):
        if smooth is None:
            smooth_str = f"{'（收集中）':>16}"
        else:
            stable_scores.append(smooth)
            smooth_str = f"{smooth:18.4f}"
        raw_str = "nan" if np.isnan(raw) else f"{raw:+.4f}"
        print(f"{second:>3}  {raw_str:>10}  {smooth_str}")
        rows.append([second, "" if np.isnan(raw) else f"{raw:.6f}",
                     "" if smooth is None else f"{smooth:.6f}"])

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
    # 預設不落檔：FAA 已經併進 Features/<編號>.csv，不需要另一份中繼檔。
    # 只有明確指定 --out 時才寫出來。
    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["second", "FAA", f"FAA_smooth{win}"])
            w.writerows(rows)
        print(f"\n已存檔：{args.out}")


if __name__ == "__main__":
    main()
