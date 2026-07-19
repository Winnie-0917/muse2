#!/usr/bin/env python3

# 掃描附近的 MUSE 裝置（透過 BLE / bleak）。
import argparse
from muselsl import list_muses


def main():
    ap = argparse.ArgumentParser(description="掃描 MUSE 裝置")
    ap.add_argument("--timeout", type=int, default=10, help="掃描秒數（預設 10）")
    ap.parse_args()

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
    print("\n把上面的 address 傳給即時監控：")
    print(f"  python -m signal_monitor.hardware.monitor_raw --address {muses[0]['address']}")


if __name__ == "__main__":
    main()
