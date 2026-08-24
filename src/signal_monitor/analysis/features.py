#!/usr/bin/env python3
"""把一段原始錄製轉成 Features/<編號>.csv（EI / FAA / 眨眼 / BPM）。

放在 analysis/ 而不是 overall_process.py：這是純資料處理，不該為了用它
就把 muselsl / bleak 整個藍牙堆疊拉進來 —— clean_csv 這種純檔案工具
只是要重建 Features，卻因此在沒有藍牙的機器上 import 就失敗。
"""
import csv
import math
import os

import numpy as np

from signal_monitor.analysis.blink import build_blink_lookup
from signal_monitor.analysis.engagement import compute_ei_series, smooth_series
from signal_monitor.analysis.faa import compute_faa_series
from signal_monitor.analysis.fft_energy import DEFAULT_WINDOW, load_eeg


def build_features_csv(source_csv_path, out_path, fs=256, window=10,
                       reject_blinks=True, fft_window=DEFAULT_WINDOW):
    """直接由原始錄製檔算出 EI / FAA / 眨眼，只輸出 Features/<編號>.csv。

    EI / FAA / 眨眼全部在記憶體裡算完直接寫出去，不經過 EI/ 與 FAA/ 中繼檔。
    回傳寫出的秒數。

    reject_blinks：把被眨眼污染的秒的 EI / FAA 記為空值（預設開啟）。
    fft_window：FFT 前的視窗函數，預設 "tukey0.25"，也可 "hann" / "rect"（舊行為）。
    """
    data = load_eeg(source_csv_path)
    n_sec = len(data) // fs
    if n_sec == 0:
        raise ValueError(f"資料不足 1 秒（需要 {fs} 個樣本，只有 {len(data)} 個）")

    ei_series = compute_ei_series(data, fs, reject_blinks=reject_blinks, window=fft_window)
    if reject_blinks:
        n_drop = n_sec - int(np.count_nonzero(~np.isnan(ei_series)))
        print(f"  眨眼排除：{n_drop}/{n_sec} 秒（{n_drop / n_sec:.1%}）標記為眨眼污染，該秒 EI/FAA 留空")
    ei_rows = smooth_series(ei_series, window)
    faa_rows = smooth_series(
        compute_faa_series(data, fs, reject_blinks=reject_blinks, window=fft_window), window)
    blink_lookup, blink_smooth_name = build_blink_lookup(source_csv_path, fs=fs, window=window)

    def fmt(value):
        """NaN（該秒算不出來）與 None（視窗未收滿）都寫成空字串。"""
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return ""
        return f"{value:.6f}"

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "second", "EI", f"EI_smooth{window}",
            "FAA", f"FAA_smooth{window}",
            "blinks", blink_smooth_name,
        ])
        for (second, ei_raw, ei_smooth), (_, faa_raw, faa_smooth) in zip(ei_rows, faa_rows):
            blinks, bpm = blink_lookup.get(str(second), ["", ""])
            writer.writerow([
                second, fmt(ei_raw), fmt(ei_smooth),
                fmt(faa_raw), fmt(faa_smooth), blinks, bpm,
            ])
    return n_sec
