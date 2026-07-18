#!/usr/bin/env python3
"""
掃描附近的 MUSE 裝置（透過 BLE / bleak）。

用法:
    ./venv/bin/python list_devices.py
    ./venv/bin/python list_devices.py --timeout 15

找到後把顯示的 address（例如 00:55:DA:B0:xx:xx）複製到 monitor_raw.py / record_csv.py。
戴上頭帶並開機（LED 閃爍表示待連線）後再執行本程式。
"""
import argparse

from muselsl import list_muses


def main():
    ap = argparse.ArgumentParser(description="掃描 MUSE 裝置")
    ap.add_argument("--timeout", type=int, default=10, help="掃描秒數（預設 10）")
    args = ap.parse_args()

    # backend='bleak' -> 使用 Linux 原生 BlueZ / D-Bus，不需要額外驅動
    muses = list_muses(backend="bleak")

    if not muses:
        print("\n找不到任何 MUSE 裝置。請確認：")
        print("  1) 頭帶已開機，且 LED 正在閃爍（未與手機 App 連線）")
        print("  2) 頭帶距離電腦夠近")
        print("  3) 電腦藍牙已開啟（bluetoothctl show）")
        return

    print(f"\n找到 {len(muses)} 台 MUSE 裝置：\n")
    for i, m in enumerate(muses):
        print(f"  [{i}] {m['name']:<20} address = {m['address']}")
    print("\n把上面的 address 傳給 monitor_raw.py：")
    print(f"  ./venv/bin/python monitor_raw.py --address {muses[0]['address']}")


if __name__ == "__main__":
    main()
