#!/usr/bin/env python3
"""
把 MUSE 2 的原始 EEG 直接（BLE）錄成 CSV 檔。

每一列 = 一個時間樣本：timestamp, TP9, AF7, AF8, TP10（單位 µV）。
不含 Right AUX（MUSE 2 無外接 AUX 電極，數值恆為 0）。取樣率 256 Hz。

檔案一律存到 Data/ 資料夾，並以流水號命名：1.csv, 2.csv, 3.csv ...

用法:
    python -m signal_monitor.data_utils.record_csv                          # 自動掃描，錄到 Data/<下一個編號>.csv
    python -m signal_monitor.data_utils.record_csv --address 00:55:DA:B0:XX:XX --seconds 60

按 Ctrl+C 可提前結束並存檔。
"""
import argparse
import csv
import os
import re
import sys
import time

from signal_monitor.paths import PROJECT_ROOT

# 預設把錄製檔存到專案下的 Data/ 資料夾
CSV_DIR = os.path.join(PROJECT_ROOT, "Data")

from muselsl import list_muses, backends
from muselsl.muse import Muse

# 只錄 4 個真實 EEG 通道，不含 AUX（AUX 是 data 的索引 4）
CHANNELS = ["TP9", "AF7", "AF8", "TP10"]


class Recorder:
    def __init__(self, writer):
        self.writer = writer
        self.count = 0

    def on_eeg(self, data, timestamps):
        # data: (5, 12)；逐一時間樣本寫入，只取前 4 個通道（跳過 AUX）
        for i in range(data.shape[1]):
            self.writer.writerow(
                [f"{timestamps[i]:.6f}"]
                + [f"{data[ch, i]:.3f}" for ch in range(len(CHANNELS))]
            )
            self.count += 1


def next_csv_path(csv_dir):
    """在 Data/ 找出下一個未使用的流水號檔名：1.csv, 2.csv, 3.csv ..."""
    os.makedirs(csv_dir, exist_ok=True)
    used = [
        int(m.group(1))
        for f in os.listdir(csv_dir)
        if (m := re.match(r"^(\d+)\.csv$", f))
    ]
    n = (max(used) + 1) if used else 1
    return os.path.join(csv_dir, f"{n}.csv")


def resolve_address(args):
    if args.address:
        return args.address, (args.name or "Muse")
    print("掃描 MUSE 裝置中 ...")
    muses = list_muses(backend="bleak")
    if not muses:
        sys.exit("找不到 MUSE 裝置。")
    print(f"使用裝置：{muses[0]['name']}  [{muses[0]['address']}]")
    return muses[0]["address"], muses[0]["name"]


def main():
    ap = argparse.ArgumentParser(description="錄製 MUSE 2 原始 EEG 到 CSV")
    ap.add_argument("--address", help="MUSE 的 BLE address（省略則自動掃描）")
    ap.add_argument("--name", help="裝置名稱（可選）")
    ap.add_argument("--seconds", type=float, default=0, help="錄製秒數（0=不限，直到 Ctrl+C）")
    ap.add_argument("--retries", type=int, default=3, help="連線重試次數")
    args = ap.parse_args()

    address, name = resolve_address(args)
    # 一律存到 Data/，用流水號命名（1.csv, 2.csv, ...）
    out_path = next_csv_path(CSV_DIR)

    f = open(out_path, "w", newline="")
    writer = csv.writer(f)
    writer.writerow(["timestamp"] + CHANNELS)
    rec = Recorder(writer)

    muse = Muse(address=address, name=name, callback_eeg=rec.on_eeg, backend="bleak")
    print(f"連線中 {name} [{address}] ...")
    if not muse.connect(retries=args.retries):
        f.close()
        sys.exit("連線失敗。")
    muse.start()

    limit = args.seconds
    print(f"開始錄製 -> {out_path}" + (f"（{limit:.0f} 秒）" if limit else "（Ctrl+C 結束）"))
    t0 = time.time()
    try:
        while True:
            backends.sleep(0.5)
            elapsed = time.time() - t0
            print(f"\r已錄 {elapsed:6.1f}s  樣本 {rec.count}", end="", flush=True)
            if limit and elapsed >= limit:
                break
    except KeyboardInterrupt:
        pass
    finally:
        try:
            muse.stop()
            muse.disconnect()
        except Exception:
            pass
        f.close()
        print(f"\n已存檔：{out_path}（共 {rec.count} 個樣本）")


if __name__ == "__main__":
    main()
