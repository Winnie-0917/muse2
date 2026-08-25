#!/usr/bin/env python3
"""
AF7 眨眼偵測：每秒眨眼數 + 滑動窗口 BPM（每分鐘眨眼率）。

演算法
------
1. **帶通濾波 0.5-5 Hz**（Butterworth 2 階，filtfilt 零相位）。
   眨眼是慢波，能量集中在這個頻段；濾掉直流漂移與 alpha/肌電等高頻成分。
   濾波對「整段錄製」一次做完，不是逐秒做 —— 逐秒濾會產生邊緣假訊號。
2. **自適應門檻** thr = max(k * sigma_robust, floor)，其中

       sigma_robust = 1.4826 * MAD(filtered)

   MAD（中位數絕對離差）對離群值穩健：少數幾個大眨眼不會像標準差那樣把門檻撐高。
   每個人、每次配戴的電極接觸阻抗都不同，訊號振幅可以差好幾倍，所以門檻必須
   跟著訊號自己的尺度走，不能寫死一個 µV 值。
3. **雙極性偵測**：眨眼在額電極是單方向大偏轉，但極性取決於參考電極接法，
   兩個方向都找，再依 refractory 合併（同一次眨眼的正負兩瓣只算一次）。
4. **不應期**（refractory）：相鄰眨眼至少間隔 refractory 秒（預設 0.3 秒）。
5. **波寬驗證**：只保留半高寬落在 60-500 ms 的峰。真眨眼約 100-400 ms；
   太窄的是尖波雜訊，太寬的是體動或漂移。
6. 每秒眨眼數 = 該秒內波峰數；BPM = sum(近 window 秒) * (60 / window)。

原本的寫法是「逐秒取 |x - mean|，超過固定 150 µV 就算一次」。實測三段錄製的
AF7 偏離平均最大只到 122-134 µV，**沒有任何一點跨得過 150 µV**，所以整段輸出
幾乎恆為 0 —— 這是 BPM 一直不準的主因。
"""
import argparse
import csv
import sys
from collections import deque

import numpy as np
from scipy.signal import butter, filtfilt, find_peaks, peak_widths

from signal_monitor.analysis.fft_energy import CSV_DIR, CHANNELS, latest_csv, load_eeg

# 預設參數
BAND_LOW_HZ = 0.5      # 帶通下限
BAND_HIGH_HZ = 5.0     # 帶通上限
MAD_K = 3.0            # 門檻 = k * 穩健標準差
THRESHOLD_FLOOR_UV = 8.0   # 門檻下限，避免訊號極安靜時把雜訊當眨眼
REFRACTORY_S = 0.3     # 不應期（秒）
WIDTH_MIN_MS = 60.0    # 半高寬下限
WIDTH_MAX_MS = 500.0   # 半高寬上限


def robust_sigma(x):
    """1.4826 * MAD —— 常態分布下與標準差一致，但對離群值穩健。"""
    return 1.4826 * float(np.median(np.abs(x - np.median(x))))


def bandpass(x, fs, low=BAND_LOW_HZ, high=BAND_HIGH_HZ):
    """0.5-5 Hz 零相位帶通。資料太短時退回「減去平均」不濾波。"""
    nyq = fs / 2.0
    high = min(high, nyq * 0.99)
    b, a = butter(2, [low / nyq, high / nyq], btype="band")
    # filtfilt 需要的樣本數下限
    if len(x) <= 3 * max(len(a), len(b)):
        return x - np.mean(x)
    return filtfilt(b, a, x)


def detect_blink_peaks(signal, fs=256, k=MAD_K, threshold_uv=None,
                       refractory_s=REFRACTORY_S,
                       width_min_ms=WIDTH_MIN_MS, width_max_ms=WIDTH_MAX_MS,
                       band=(BAND_LOW_HZ, BAND_HIGH_HZ),
                       threshold_floor=THRESHOLD_FLOOR_UV):
    """在整段訊號上找眨眼波峰。

    回傳 (peak_indices, info_dict)。threshold_uv 有給就用固定門檻，否則用自適應門檻。
    """
    filtered = bandpass(np.asarray(signal, dtype=float), fs, band[0], band[1])
    sigma = robust_sigma(filtered)

    if threshold_uv is not None:
        threshold = float(threshold_uv)
    else:
        threshold = max(k * sigma, threshold_floor)

    distance = max(1, int(round(refractory_s * fs)))

    # 兩個極性各自找峰，再合併
    candidates = []
    for sign in (1.0, -1.0):
        peaks, props = find_peaks(sign * filtered, height=threshold, distance=distance)
        if len(peaks) == 0:
            continue
        widths_ms = peak_widths(sign * filtered, peaks, rel_height=0.5)[0] / fs * 1000.0
        for idx, height, width in zip(peaks, props["peak_heights"], widths_ms):
            if width_min_ms <= width <= width_max_ms:
                candidates.append((int(idx), float(height), float(width)))

    # 依振幅由大到小貪婪保留，確保任兩個保留的峰間隔 >= 不應期
    # （同一次眨眼的正瓣與負瓣會落在不應期內，只會留下較大的那個）
    kept = []
    for idx, height, width in sorted(candidates, key=lambda t: -t[1]):
        if all(abs(idx - other) >= distance for other in kept):
            kept.append(idx)

    info = {
        "threshold_uv": threshold,
        "robust_sigma_uv": sigma,
        "adaptive": threshold_uv is None,
        "n_candidates": len(candidates),
        "n_blinks": len(kept),
    }
    return np.array(sorted(kept), dtype=int), info


def blink_second_mask(data, fs=256, channel="AF7", guard_s=0.25, peaks=None, **detect_kwargs):
    """標記每一秒是否被眨眼污染，回傳長度 n_sec 的布林陣列。

    眨眼會在低頻製造巨大能量，直接灌進 theta（4-8 Hz）。theta 位於
    EI = beta/(alpha+theta) 的分母，所以含眨眼的秒會讓 EI 被嚴重壓低 ——
    實測 theta 中位數被放大約 5 倍、EI 被壓低約 43%。這些秒必須排除，
    否則 EI 測到的有一大半是「這秒有沒有眨眼」而非專注度。

    guard_s：波峰前後各留的緩衝秒數。一次眨眼持續 100-400 ms，
    落在秒邊界附近時會同時污染前後兩秒，所以用時間區間去標記，
    而不是只標記波峰所在的那一秒。
    """
    if "+" in channel:
        names = [c.strip() for c in channel.split("+")]
        signal = np.mean([data[:, CHANNELS.index(c)] for c in names], axis=0)
    else:
        signal = data[:, CHANNELS.index(channel)]

    n_sec = len(data) // fs
    mask = np.zeros(n_sec, dtype=bool)
    if n_sec == 0:
        return mask

    if peaks is None:
        peaks, _ = detect_blink_peaks(signal, fs=fs, **detect_kwargs)
    guard = int(round(guard_s * fs))
    for idx in peaks:
        first = max(0, (idx - guard) // fs)
        last = min(n_sec - 1, (idx + guard) // fs)
        mask[first:last + 1] = True
    return mask


def count_spikes(segment, threshold_uv, min_distance):
    """（保留舊介面）單一片段的突波數，使用固定門檻。

    新的偵測流程請用 detect_blink_peaks —— 逐片段處理會在片段邊界切斷眨眼，
    而且無法做帶通濾波。
    """
    centered = np.abs(np.asarray(segment, dtype=float) - np.mean(segment))
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


def build_blink_rows(data, fs=256, window=10, threshold_uv=None, k=MAD_K,
                     refractory_s=REFRACTORY_S, channel="AF7", return_info=False,
                     peaks=None, info=None):
    """回傳 [(second, blinks, bpm_or_empty), ...]。

    channel 可給單一通道名，或 "AF7+AF8" 取兩通道平均
    （眨眼是雙側同步，平均可提升訊噪比）。
    """
    if "+" in channel:
        names = [c.strip() for c in channel.split("+")]
        signal = np.mean([data[:, CHANNELS.index(c)] for c in names], axis=0)
    else:
        signal = data[:, CHANNELS.index(channel)]

    n_sec = len(data) // fs
    if peaks is None:
        peaks, info = detect_blink_peaks(
            signal, fs=fs, k=k, threshold_uv=threshold_uv, refractory_s=refractory_s
        )
    elif info is None:
        info = {}

    # 把每個波峰歸到它所在的那一秒
    per_second = np.zeros(n_sec, dtype=int)
    for idx in peaks:
        second = idx // fs
        if 0 <= second < n_sec:
            per_second[second] += 1

    q = deque(maxlen=window)
    rows = []
    for s in range(n_sec):
        q.append(int(per_second[s]))
        bpm = round(sum(q) * (60 / window)) if len(q) == window else ""
        rows.append((s + 1, int(per_second[s]), bpm))

    info["n_seconds"] = n_sec
    info["rate_per_min"] = (len(peaks) / n_sec * 60) if n_sec else 0.0
    return (rows, info) if return_info else rows


def build_blink_lookup(input_csv_path, fs=256, window=10, threshold_uv=None,
                       k=MAD_K, refractory_s=REFRACTORY_S, channel="AF7"):
    """回傳 ({second_str: [blinks_str, bpm_str]}, bpm_col_name)。"""
    data = load_eeg(input_csv_path)
    rows = build_blink_rows(
        data, fs=fs, window=window, threshold_uv=threshold_uv,
        k=k, refractory_s=refractory_s, channel=channel,
    )
    lookup = {
        str(second): [str(blinks), "" if bpm == "" else str(bpm)]
        for second, blinks, bpm in rows
    }
    return lookup, f"BPM_smooth{window}"


def main():
    ap = argparse.ArgumentParser(description="AF7 眨眼偵測（每秒眨眼數 + 滑動窗口 BPM）")
    ap.add_argument("input", nargs="?", help="輸入 CSV（省略則用 Data/ 內編號最大的檔）")
    ap.add_argument("--fs", type=int, default=256, help="取樣率 Hz（預設 256）")
    ap.add_argument("--window", type=int, default=10, help="滑動視窗秒數（預設 10）")
    ap.add_argument("--k", type=float, default=MAD_K,
                    help=f"自適應門檻倍數：thr = k * 穩健標準差（預設 {MAD_K}）")
    ap.add_argument("--threshold", type=float, default=None,
                    help="改用固定門檻（µV）。不給就用自適應門檻（建議）")
    ap.add_argument("--refractory", type=float, default=REFRACTORY_S,
                    help=f"不應期秒數（預設 {REFRACTORY_S}）")
    ap.add_argument("--channel", default="AF7",
                    help='偵測通道，可用 "AF7+AF8" 取平均（預設 AF7）')
    ap.add_argument("--quiet", action="store_true", help="只印摘要，不逐秒列出")
    ap.add_argument("--out", help="輸出 CSV 路徑（省略則只在終端顯示，不另存檔）")
    args = ap.parse_args()

    in_path = args.input or latest_csv(CSV_DIR)
    data = load_eeg(in_path)
    n_sec = len(data) // args.fs
    if n_sec == 0:
        sys.exit(f"資料不足 1 秒（需要 {args.fs} 個樣本，只有 {len(data)} 個）。")

    rows, info = build_blink_rows(
        data, fs=args.fs, window=args.window, threshold_uv=args.threshold,
        k=args.k, refractory_s=args.refractory, channel=args.channel,
        return_info=True,
    )

    mode = f"自適應 k={args.k}" if info["adaptive"] else "固定門檻"
    print(f"輸入：{in_path}")
    print(f"通道：{args.channel}   帶通 {BAND_LOW_HZ}-{BAND_HIGH_HZ} Hz   不應期 {args.refractory}s")
    print(f"門檻：{info['threshold_uv']:.1f} µV（{mode}，穩健標準差 {info['robust_sigma_uv']:.2f} µV）")
    print(f"結果：{n_sec} 秒內 {info['n_blinks']} 次眨眼 -> 平均 {info['rate_per_min']:.1f} 次/分")
    if not 5 <= info["rate_per_min"] <= 40:
        print("  注意：一般清醒狀態約 15-20 次/分，此值偏離正常範圍，"
              "請檢查電極接觸或用 --k 調整門檻", file=sys.stderr)

    if not args.quiet:
        print(f"\n{'秒':>3}  {'Blinks':>8}  {f'BPM(近{args.window}秒)':>14}")
        print("-" * 34)
        for second, blinks, bpm in rows:
            bpm_str = f"{bpm:14d}" if bpm != "" else f"{'（收集中）':>12}"
            print(f"{second:>3}  {blinks:>8d}  {bpm_str}")

    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["second", "blinks", f"BPM_smooth{args.window}"])
            for second, blinks, bpm in rows:
                w.writerow([second, blinks, bpm])
        print(f"\n已存檔：{args.out}")


if __name__ == "__main__":
    main()
