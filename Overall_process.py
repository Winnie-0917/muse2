#!/usr/bin/env python3
"""
一鍵流程：即時監控 + 錄製 CSV → FFT → EI 專注度。

這支程式把專案的四個步驟串成一條龍（重用既有腳本，不重寫邏輯）：

  步驟 1（同時進行）：直接 BLE 連線 MUSE 2，
      - 終端機即時顯示原始 EEG（4 通道波形 / RMS / 訊號品質 / 取樣率）
      - 同一份資料逐一樣本寫入 Data/<編號>.csv
  步驟 2：停止後自動對該檔跑 fft_energy.py（每秒 FFT → FFT/<通道>/<編號>.csv）
  步驟 3：再跑 engagement.py（每秒 EI + 10 秒滑動平均 → EI/<編號>.csv）

停止錄製的方式：--seconds 到時自動停，或隨時按 Ctrl+C。

用法:
    ./venv/bin/python Overall_process.py                              # 自動掃描，錄到 Ctrl+C 為止
    ./venv/bin/python Overall_process.py --seconds 60                 # 錄 60 秒後自動分析
    ./venv/bin/python Overall_process.py --address 00:55:DA:B6:35:CA --seconds 60
    ./venv/bin/python Overall_process.py --aux                        # 即時畫面也顯示 AUX
    ./venv/bin/python Overall_process.py --no-analyze                 # 只監控+錄製，不做 FFT/EI

備註：EI 的「穩定分數」需要滿 10 秒才會輸出，想看平滑專注度請至少錄 10 秒以上。
"""
import argparse
import csv
import os
import subprocess
import sys
import time

from muselsl import list_muses, backends
from muselsl.muse import Muse

# 重用即時監控（畫面）與錄製（檔名/通道）的既有元件
from monitor_raw import (
    Monitor, render,
    CLEAR, HIDE_CURSOR, SHOW_CURSOR, RESET, BOLD, CYAN, DIM,
)
from record_csv import CSV_DIR, next_csv_path, CHANNELS as REC_CHANNELS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class LiveRecorder:
    """單一 BLE 回呼：同時餵給即時畫面（Monitor）並把樣本寫入 CSV。"""

    def __init__(self, mon, writer):
        self.mon = mon
        self.writer = writer
        self.count = 0

    def on_eeg(self, data, timestamps):
        # data 形狀 (5, 12)：先更新即時畫面緩衝，再寫入前 4 個通道（不含 AUX）
        self.mon.on_eeg(data, timestamps)
        n_ch = len(REC_CHANNELS)
        for i in range(data.shape[1]):
            self.writer.writerow(
                [f"{timestamps[i]:.6f}"]
                + [f"{data[ch, i]:.3f}" for ch in range(n_ch)]
            )
            self.count += 1


def resolve_address(args):
    if args.address:
        return args.address, (args.name or "Muse")
    print("掃描 MUSE 裝置中（請確認頭帶已開機、LED 閃爍）...")
    muses = list_muses(backend="bleak")
    if not muses:
        sys.exit("找不到 MUSE 裝置。先執行 ./venv/bin/python list_devices.py 排查。")
    print(f"使用裝置：{muses[0]['name']}  [{muses[0]['address']}]")
    return muses[0]["address"], muses[0]["name"]


def run_analysis(csv_path):
    """依序執行 FFT 與 EI 分析（沿用既有腳本，輸出到 FFT/ 與 EI/）。"""
    py = sys.executable  # 目前的 venv python
    # 先 flush 再叫子程序，確保標題印在子程序輸出「之前」（輸出被導向檔案時尤其重要）
    print(f"\n{BOLD}{CYAN}=== 步驟 2：每秒 FFT（fft_energy.py）==={RESET}", flush=True)
    subprocess.run([py, os.path.join(BASE_DIR, "fft_energy.py"), csv_path])
    print(f"\n{BOLD}{CYAN}=== 步驟 3：專注度指數 EI（engagement.py）==={RESET}", flush=True)
    subprocess.run([py, os.path.join(BASE_DIR, "engagement.py"), csv_path])


def main():
    ap = argparse.ArgumentParser(
        description="MUSE 2 一鍵：即時監控 + 錄製 CSV → FFT → EI 專注度"
    )
    ap.add_argument("--address", help="MUSE 的 BLE address（省略則自動掃描）")
    ap.add_argument("--name", help="裝置名稱（可選）")
    ap.add_argument("--seconds", type=float, default=0, help="錄製秒數（0=直到 Ctrl+C）")
    ap.add_argument("--fps", type=float, default=15.0, help="即時畫面刷新率（預設 15）")
    ap.add_argument("--window", type=float, default=2.0, help="即時統計/波形視窗秒數（預設 2）")
    ap.add_argument("--retries", type=int, default=3, help="連線重試次數（預設 3）")
    ap.add_argument("--aux", action="store_true", help="即時畫面顯示 AUX（預設隱藏）")
    ap.add_argument("--no-analyze", action="store_true", help="只監控+錄製，不做 FFT/EI")
    args = ap.parse_args()

    show_idx = list(range(5)) if args.aux else [0, 1, 2, 3]
    address, name = resolve_address(args)

    # 準備輸出 CSV：Data/<下一個未使用編號>.csv
    out_path = next_csv_path(CSV_DIR)
    f = open(out_path, "w", newline="")
    writer = csv.writer(f)
    writer.writerow(["timestamp"] + REC_CHANNELS)

    mon = Monitor(window_sec=args.window)
    rec = LiveRecorder(mon, writer)

    muse = Muse(address=address, name=name, callback_eeg=rec.on_eeg, backend="bleak")
    print(f"連線中 {name} [{address}] ...")
    if not muse.connect(retries=args.retries):
        f.close()
        os.remove(out_path)  # 連線失敗就不留空檔
        sys.exit("連線失敗。請確認頭帶未被手機 App 佔用，且在附近。")
    muse.start()

    limit = args.seconds
    hint = f"錄製 {limit:.0f} 秒" if limit else "錄製中，按 Ctrl+C 停止"
    print(f"開始 -> {out_path}（{hint}）")

    sys.stdout.write(CLEAR + HIDE_CURSOR)
    period = 1.0 / max(args.fps, 1.0)
    t0 = time.time()
    try:
        while True:
            # backends.sleep 會推動 asyncio 事件迴圈 -> 觸發 BLE 資料回呼
            backends.sleep(period)
            render(mon, name, address, show_idx)
            if limit and (time.time() - t0) >= limit:
                break
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET + "\n")
        try:
            muse.stop()
            muse.disconnect()
        except Exception:
            pass
        f.close()

    secs = rec.count / 256.0
    print(f"已停止串流。錄製檔：{out_path}"
          f"（共 {rec.count} 個樣本，約 {secs:.1f} 秒）")

    if args.no_analyze:
        print(f"{DIM}（--no-analyze：略過 FFT/EI 分析）{RESET}")
        return
    if rec.count < 256:
        print(f"{DIM}資料不足 1 秒，略過 FFT/EI 分析。{RESET}")
        return

    run_analysis(out_path)
    print(f"\n{BOLD}全部完成{RESET}：原始 {out_path} → FFT（FFT/）→ EI（EI/）")
    if secs < 10:
        print(f"{DIM}提醒：此段只有約 {secs:.0f} 秒，未滿 10 秒故無「穩定 EI」；"
              f"想看平滑專注度請錄 10 秒以上。{RESET}")


if __name__ == "__main__":
    main()
