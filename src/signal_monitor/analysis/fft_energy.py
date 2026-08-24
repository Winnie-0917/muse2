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
計算前會先減掉每個視窗的平均值（去除直流/漂移），所以只看 1 Hz 起，
接著套用 Tukey(0.25) 視窗抑制頻譜洩漏（--window rect 可還原成不加窗的舊行為）。

輸出
----
每個通道各有一個子資料夾（TP9/ AF7/ AF8/ TP10/），檔名 = 輸入檔的編號。
例如輸入 Data/1.csv 會產生：
    <out>/TP9/1.csv, <out>/AF7/1.csv, <out>/AF8/1.csv, <out>/TP10/1.csv（僅在給了 --out 時）
每個檔的一列 = 一秒；欄位為 second, 1HZ, 2HZ, ... , 128HZ（各 Hz 的能量 µV²）。
每個通道資料夾內會保留一個 .gitkeep 佔位檔（實際 .csv 由 .gitignore 忽略、不上傳）。

用法
----
    python -m signal_monitor.analysis.fft_energy                  # 分析 Data/ 內最新（編號最大）的檔
    python -m signal_monitor.analysis.fft_energy Data/1.csv       # 指定輸入檔
    python -m signal_monitor.analysis.fft_energy Data/1.csv --out FFT   # 額外存成 CSV
"""
import argparse
import csv
import os
import re
import sys

import numpy as np
from scipy.signal import windows as signal_windows

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


# 視窗函數預設值。Tukey alpha=0.25：只把視窗兩端各 12.5% 做餘弦收斂，
# 中間 75% 維持原樣。抑制洩漏的效果與 Hann 相同，但保留的有效樣本多得多。
DEFAULT_WINDOW = "tukey0.25"


def analysis_window(fs, kind=DEFAULT_WINDOW):
    """回傳長度 fs 的視窗函數，已正規化為 RMS = 1（保持功率尺度）。

    為什麼要加窗
    ------------
    直接對 256 點做 FFT 等同套用「矩形窗」：視窗兩端訊號被硬切斷，
    產生頻譜洩漏（spectral leakage）—— 低頻的大能量會滲進高頻帶。
    合成訊號實測（2.37 Hz 振幅 200µV 的慢波 + 20.4 Hz 振幅 10µV）：

        矩形窗       beta 被高估 4.57 倍
        tukey 0.1    1.29 倍
        tukey 0.25   1.02 倍
        hann         1.02 倍

    眨眼與漂移正是這種「低頻大振幅」，所以不加窗時 beta（EI 的分子）會被灌水。

    為什麼不用 Hann
    ---------------
    加窗會壓低視窗邊緣樣本的權重，等於減少有效樣本數，讓每秒的頻帶能量
    估計變吵。有效樣本比 (Σw²)²/(N·Σw⁴)：

        矩形窗 1.000    tukey 0.25 → 0.867    hann → 0.512

    Hann 只用到約一半的有效樣本。實測三段錄製的「段間變異/段內雜訊」F 比值，
    tukey 0.25 = 99.4 明顯優於 hann = 63.2 與矩形窗 = 78.5。
    所以預設取 tukey alpha=0.25：洩漏抑制與 Hann 同級，雜訊代價小得多。

    正規化
    ------
    除以 sqrt(mean(w²))，讓加窗後的平均功率尺度與原訊號一致。
    Parseval 關係對「加窗後的訊號」仍精確成立：各頻率能量加總 = Σ(x[n]·w[n])²。
    """
    if kind in (None, "rect", "none"):
        return None
    if kind == "hann":
        w = np.hanning(fs)
    elif kind.startswith("tukey"):
        alpha = float(kind[5:]) if len(kind) > 5 else 0.25
        if not 0.0 <= alpha <= 1.0:
            raise ValueError(f"tukey alpha 必須在 0..1：{alpha}")
        w = signal_windows.tukey(fs, alpha)
    else:
        raise ValueError(f"不支援的視窗類型：{kind}（可用 tukey<alpha> / hann / rect）")
    return w / np.sqrt(np.mean(w ** 2))


def per_second_energy(signal, fs, window=DEFAULT_WINDOW):
    """
    把單一通道的訊號切成連續、不重疊的 1 秒（fs 樣本）視窗，
    對每個視窗做 FFT，回傳 shape=(秒數, fs//2 + 1) 的能量矩陣。
    欄 k 對應頻率 k Hz（k = 0 .. fs/2）。不足一秒的尾段會被捨棄。

    window 預設 "tukey0.25"，先加窗再做 FFT 以抑制頻譜洩漏；
    也可給 "hann" 或 "rect"（不加窗，舊行為，保留給需要比對的場合）。
    """
    n_win = len(signal) // fs
    energies = np.empty((n_win, fs // 2 + 1))
    win = analysis_window(fs, window)
    for w in range(n_win):
        seg = signal[w * fs:(w + 1) * fs].astype(float)
        seg = seg - seg.mean()                 # 去除直流/漂移
        if win is not None:
            seg = seg * win                     # 加窗，抑制頻譜洩漏
        X = np.fft.rfft(seg)                    # 長度 fs//2 + 1
        power = (np.abs(X) ** 2) / fs           # |X|²/N
        power[1:-1] *= 2                         # 單瓣：中間頻率乘 2
        energies[w] = power
    return energies


def main():
    ap = argparse.ArgumentParser(description="對 MUSE 2 EEG 做每秒 FFT 能量分析（1..128 Hz）")
    ap.add_argument("input", nargs="?", help="輸入 CSV（省略則用 Data/ 內編號最大的檔）")
    ap.add_argument("--fs", type=int, default=256, help="取樣率 Hz（MUSE 2 = 256）")
    ap.add_argument("--out", default=None,
                    help="把每秒 FFT 結果存成 CSV 的資料夾（省略則只顯示、不存檔）")
    ap.add_argument("--window", default=DEFAULT_WINDOW,
                    help=f"FFT 前的視窗函數：tukey<alpha> / hann / rect（預設 {DEFAULT_WINDOW}）")
    args = ap.parse_args()

    in_path = args.input or latest_csv(CSV_DIR)
    fs = args.fs
    out_dir = None
    if args.out:
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

    print(f"{'通道':>4}  {'主頻':>8}  {'θ(4-8)':>10}  {'α(8-12)':>10}  {'β(13-30)':>10}  {'每秒總能量':>12}")
    print("-" * 66)
    for c, ch in enumerate(CHANNELS):
        energies = per_second_energy(data[:, c], fs, window=args.window)   # (秒數, 129)

        if out_dir is not None:
            ch_dir = os.path.join(out_dir, ch)             # <out>/<通道>/
            os.makedirs(ch_dir, exist_ok=True)
            open(os.path.join(ch_dir, ".gitkeep"), "a").close()   # 保留資料夾結構
            out_path = os.path.join(ch_dir, f"{stem}.csv")
            with open(out_path, "w", newline="") as f:
                writer = csv.writer(f)
                # 只輸出 1..128 Hz（跳過 0 Hz 直流），欄名為 1HZ, 2HZ, ... , 128HZ
                writer.writerow(["second"] + [f"{hz}HZ" for hz in freqs[1:]])
                for sec in range(n_win):
                    writer.writerow([sec] + [f"{v:.4f}" for v in energies[sec, 1:]])

        # 摘要：整段平均下的主頻與三個頻帶能量
        mean_energy = energies[:, 1:].mean(axis=0)     # 索引 i -> (i+1) Hz
        peak_hz = int(freqs[1:][mean_energy.argmax()])
        theta = mean_energy[3:7].sum()                  # 4..7 Hz
        alpha = mean_energy[7:12].sum()                 # 8..12 Hz
        beta = mean_energy[12:30].sum()                 # 13..30 Hz
        print(f"{ch:>4}  {peak_hz:>6} Hz  {theta:>10.0f}  {alpha:>10.0f}  {beta:>10.0f}  "
              f"{mean_energy.sum():>12.1f}")

    print(f"\n{'（主頻若在 60 Hz 附近多為市電干擾/接觸不良；正常 EEG 多集中在低頻）'}")
    if out_dir is not None:
        print(f"每秒 FFT 明細已存到 {out_dir}/<通道>/{stem}.csv")


if __name__ == "__main__":
    main()
