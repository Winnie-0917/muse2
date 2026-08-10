#!/usr/bin/env python3
"""
AF7 眨眼偵測：每秒眨眼數 + 10 秒滑動窗口 BPM（每分鐘眨眼率）。

規則：
1. 每秒 256 點（預設 fs=256），取 AF7 該秒訊號 seg。
2. 預處理 y[n] = |seg[n] - mean(seg)|。
3. 振幅門檻：y[n] >= threshold（預設 150 µV）。
4. 最小距離：約束相鄰眨眼至少 min_distance 點（預設 128 = 0.5 秒）。
5. 每秒眨眼數 = 該秒中符合規則的突波數。
6. 10 秒滑動窗口 BPM：窗口滿時 BPM = sum(last10_blinks) * 6。
"""
import argparse
import csv
import sys
from collections import deque

import numpy as np

from signal_monitor.analysis.fft_energy import CSV_DIR, CHANNELS, latest_csv, load_eeg


def count_spikes(segment, threshold_uv, min_distance):
    """計算單一 1 秒片段中的突波數（已預處理後）。"""
    centered = np.abs(segment - np.mean(segment))
    candidates = np.where(centered >= threshold_uv)[0]
    if len(candidates) == 0:
        return 0

    count = 0
    last_idx = -min_distance
    for idx in candidates:
        if idx - last_idx >= min_distance:
            count += 1
            last_idx = idx
    return count


def build_blink_rows(data, fs=256, window=10, threshold_uv=150.0, min_distance=128):
    """回傳 [(second, blinks, bpm_or_empty), ...]。"""
    n_sec = len(data) // fs
    af7_idx = CHANNELS.index("AF7")
    af7 = data[:, af7_idx]

    q = deque(maxlen=window)
    rows = []
    for s in range(n_sec):
        seg = af7[s * fs:(s + 1) * fs]
        blinks = count_spikes(seg, threshold_uv=threshold_uv, min_distance=min_distance)
        q.append(blinks)
        bpm = int(sum(q) * (60 / window)) if len(q) == window else ""
        rows.append((s + 1, blinks, bpm))
    return rows


def build_blink_lookup(input_csv_path, fs=256, window=10, threshold_uv=150.0, min_distance=128):
    """回傳 ({second_str: [blinks_str, bpm_str]}, bpm_col_name)。"""
    data = load_eeg(input_csv_path)
    rows = build_blink_rows(
        data,
        fs=fs,
        window=window,
        threshold_uv=threshold_uv,
        min_distance=min_distance,
    )
    lookup = {
        str(second): [str(blinks), "" if bpm == "" else str(bpm)]
        for second, blinks, bpm in rows
    }
    return lookup, f"BPM_smooth{window}"


def main():
    ap = argparse.ArgumentParser(description="AF7 眨眼偵測（每秒眨眼數 + 10 秒滑動 BPM）")
    ap.add_argument("input", nargs="?", help="輸入 CSV（省略則用 Data/ 內編號最大的檔）")
    ap.add_argument("--fs", type=int, default=256, help="取樣率 Hz（預設 256）")
    ap.add_argument("--window", type=int, default=10, help="滑動視窗秒數（預設 10）")
    ap.add_argument("--threshold", type=float, default=150.0, help="突波門檻（µV，預設 150）")
    ap.add_argument("--min-distance", type=int, default=128, help="最小距離（點數，預設 128=0.5 秒）")
    ap.add_argument("--out", help="輸出 CSV 路徑（省略則只在終端顯示，不另存檔）")
    args = ap.parse_args()

    in_path = args.input or latest_csv(CSV_DIR)
    fs = args.fs
    win = args.window
    threshold_uv = args.threshold
    min_distance = args.min_distance

    data = load_eeg(in_path)
    n_sec = len(data) // fs
    if n_sec == 0:
        sys.exit(f"資料不足 1 秒（需要 {fs} 個樣本，只有 {len(data)} 個）。")

    print(f"輸入：{in_path}")
    print(f"AF7 門檻：{threshold_uv:.1f} µV，最小距離：{min_distance} 點，視窗：{win} 秒")
    print(f"{'秒':>3}  {'Blinks':>8}  {'BPM(近10秒)':>14}")
    print("-" * 34)

    rows = build_blink_rows(
        data,
        fs=fs,
        window=win,
        threshold_uv=threshold_uv,
        min_distance=min_distance,
    )
    for second, blinks, bpm in rows:
        bpm_str = f"{bpm:14d}" if bpm != "" else f"{'（收集中）':>12}"
        print(f"{second:>3}  {blinks:>8d}  {bpm_str}")

    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["second", "blinks", f"BPM_smooth{win}"])
            for second, blinks, bpm in rows:
                w.writerow([second, blinks, bpm])
        print(f"\n已存檔：{args.out}")


if __name__ == "__main__":
    main()
