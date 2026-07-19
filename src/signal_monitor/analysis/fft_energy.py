#!/usr/bin/env python3
"""
對 record_csv.py 錄下的 EEG（TP9, AF7, AF8, TP10）做「每秒一次」的傅立葉變換，
算出每 1 秒內 1 Hz、2 Hz、3 Hz ... 直到 128 Hz 各自包含多少能量。

原理
----
MUSE 2 取樣率 = 256 Hz，所以「1 秒」= 256 個樣本。
對 256 點做 FFT，頻率解析度剛好是 256/256 = 1 Hz，
輸出的頻率點就是 0, 1, 2, ... , 128 Hz（128 Hz = 奈奎斯特頻率）。

能量（單瓣功率頻譜，單位 µV²）定義為：
    E[k] = c * |X[k]|² / N          （N = 256）
其中 c = 2（1 ≤ k ≤ 127），c = 1（k = 0 或 k = 128）。
如此每個頻率點的能量加總會等於該秒訊號的能量（Parseval 定理），
數值即為「該 1 Hz 頻帶在這 1 秒內貢獻的變異量（µV²）」。
計算前會先減掉每個視窗的平均值（去除直流/漂移），所以只看 1 Hz 起。

輸出
----
每個通道各有一個子資料夾（TP9/ AF7/ AF8/ TP10/），檔名 = 輸入檔的編號。
例如輸入 Data/1.csv 會產生：
    FFT/TP9/1.csv, FFT/AF7/1.csv, FFT/AF8/1.csv, FFT/TP10/1.csv
每個檔的一列 = 一秒；欄位為 second, 1HZ, 2HZ, ... , 128HZ（各 Hz 的能量 µV²）。
每個通道資料夾內會保留一個 .gitkeep 佔位檔（實際 .csv 由 .gitignore 忽略、不上傳）。

用法
----
    python -m signal_monitor.analysis.fft_energy                  # 分析 Data/ 內最新（編號最大）的檔
    python -m signal_monitor.analysis.fft_energy Data/1.csv       # 指定輸入檔
    python -m signal_monitor.analysis.fft_energy Data/1.csv --fs 256 --out FFT
"""
import argparse
import csv
import os
import re
import sys

import numpy as np

from signal_monitor.paths import PROJECT_ROOT

BASE_DIR = PROJECT_ROOT
CSV_DIR = os.path.join(BASE_DIR, "Data")
CHANNELS = ["TP9", "AF7", "AF8", "TP10"]


def latest_csv(csv_dir):
    """回傳 Data/ 內編號最大的 <n>.csv；找不到就結束程式。"""
    used = [
        (int(m.group(1)), f)
        for f in os.listdir(csv_dir)
        if (m := re.match(r"^(\d+)\.csv$", f))
    ]
    if not used:
        sys.exit(f"找不到任何 <編號>.csv 於 {csv_dir}")
    return os.path.join(csv_dir, max(used)[1])


def load_eeg(path):
    """讀入 CSV，回傳 shape=(樣本數, 4) 的 float 陣列，欄序為 TP9, AF7, AF8, TP10。"""
    rows = []
    with open(path, newline="") as f:
        r = csv.reader(f)
        header = next(r)
        # 依表頭找出四個通道所在欄位（容忍欄位順序不同）
        idx = [header.index(ch) for ch in CHANNELS]
        for row in r:
            if not row:
                continue
            rows.append([float(row[i]) for i in idx])
    if not rows:
        sys.exit(f"{path} 沒有資料列。")
    return np.asarray(rows, dtype=float)


def per_second_energy(signal, fs):
    """
    把單一通道的訊號切成連續、不重疊的 1 秒（fs 樣本）視窗，
    對每個視窗做 FFT，回傳 shape=(秒數, fs//2 + 1) 的能量矩陣。
    欄 k 對應頻率 k Hz（k = 0 .. fs/2）。不足一秒的尾段會被捨棄。
    """
    n_win = len(signal) // fs
    energies = np.empty((n_win, fs // 2 + 1))
    for w in range(n_win):
        seg = signal[w * fs:(w + 1) * fs].astype(float)
        seg = seg - seg.mean()                 # 去除直流/漂移
        X = np.fft.rfft(seg)                    # 長度 fs//2 + 1
        power = (np.abs(X) ** 2) / fs           # |X|²/N
        power[1:-1] *= 2                         # 單瓣：中間頻率乘 2
        energies[w] = power
    return energies


def main():
    ap = argparse.ArgumentParser(description="對 MUSE 2 EEG 做每秒 FFT 能量分析（1..128 Hz）")
    ap.add_argument("input", nargs="?", help="輸入 CSV（省略則用 Data/ 內編號最大的檔）")
    ap.add_argument("--fs", type=int, default=256, help="取樣率 Hz（MUSE 2 = 256）")
    ap.add_argument("--out", default="FFT", help="輸出資料夾（預設 FFT/）")
    args = ap.parse_args()

    in_path = args.input or latest_csv(CSV_DIR)
    fs = args.fs
    out_dir = args.out if os.path.isabs(args.out) else os.path.join(BASE_DIR, args.out)
    os.makedirs(out_dir, exist_ok=True)

    data = load_eeg(in_path)
    n_win = len(data) // fs
    if n_win == 0:
        sys.exit(f"資料不足 1 秒（需要 {fs} 個樣本，只有 {len(data)} 個）。")

    freqs = np.arange(fs // 2 + 1)              # 0, 1, 2, ... , 128 Hz
    stem = re.sub(r"\.csv$", "", os.path.basename(in_path))
    print(f"輸入：{in_path}")
    print(f"取樣率 {fs} Hz → 每秒 {fs} 樣本；可分析 {n_win} 秒；頻率 0..{fs // 2} Hz（每 1 Hz 一格）\n")

    for c, ch in enumerate(CHANNELS):
        energies = per_second_energy(data[:, c], fs)   # (秒數, 129)
        ch_dir = os.path.join(out_dir, ch)             # FFT/<通道>/
        os.makedirs(ch_dir, exist_ok=True)
        open(os.path.join(ch_dir, ".gitkeep"), "a").close()   # 保留資料夾結構
        out_path = os.path.join(ch_dir, f"{stem}.csv")
        with open(out_path, "w", newline="") as f:
            writer = csv.writer(f)
            # 只輸出 1..128 Hz（跳過 0 Hz 直流），欄名為 1HZ, 2HZ, ... , 128HZ
            writer.writerow(["second"] + [f"{hz}HZ" for hz in freqs[1:]])
            for sec in range(n_win):
                writer.writerow(
                    [sec] + [f"{v:.4f}" for v in energies[sec, 1:]]
                )
        # 摘要：整段平均下，能量最大的頻率（1..128 Hz）
        mean_energy = energies[:, 1:].mean(axis=0)
        peak_hz = int(freqs[1:][mean_energy.argmax()])
        total = mean_energy.sum()
        print(f"  {ch:>4}: 已存 {out_path}  主頻≈{peak_hz} Hz  平均每秒總能量≈{total:.1f} µV²")

    print("\n完成。每個檔一列 = 一秒，欄位 1..128 為各 Hz 的能量（µV²）。")


if __name__ == "__main__":
    main()
