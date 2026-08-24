#!/usr/bin/env python3

# 掃描附近的 MUSE 裝置（透過 BLE / bleak）。
import argparse
from signal_monitor.hardware.ble import NO_DEVICE_HINT, scan_muses


def main():
    ap = argparse.ArgumentParser(description="掃描 MUSE 裝置")
    ap.add_argument("--timeout", type=int, default=10, help="掃描秒數（預設 10）")
    ap.parse_args()

    # backend='bleak' -> 使用 Linux 原生 BlueZ / D-Bus，不需要額外驅動
    muses, error = scan_muses()

    if error:
        print(f"\n掃描失敗：{error}")
        return

    if not muses:
        print("\n" + NO_DEVICE_HINT)
        return

    print(f"\n找到 {len(muses)} 台 MUSE 裝置：\n")
    for i, m in enumerate(muses):
        print(f"  [{i}] {m['name']:<20} address = {m['address']}")
    print("\n把上面的 address 傳給即時監控：")
    print(f"  python -m signal_monitor.hardware.monitor_raw --address {muses[0]['address']}")


if __name__ == "__main__":
    main()
