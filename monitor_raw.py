#!/usr/bin/env python3
"""
即時監控 MUSE 2 的原始 EEG 數據（直接透過 BLE 連線，使用 muselsl 的 Muse 類別）。

會直接與頭帶建立 BLE 連線、訂閱 5 個 EEG 通道，並在終端機即時顯示：
  - 每個通道的即時波形（unicode sparkline）
  - 最新值、RMS（交流有效值）、峰對峰值（µV）
  - 依 RMS 判斷的接觸/訊號品質
  - 實際取樣率（Hz）與封包統計

MUSE 2 原始 EEG：5 通道 = TP9, AF7, AF8, TP10, Right AUX
                取樣率 256 Hz，單位為微伏（µV）。

用法:
    ./venv/bin/python monitor_raw.py                       # 自動掃描並連第一台
    ./venv/bin/python monitor_raw.py --address 00:55:DA:B0:XX:XX
    ./venv/bin/python monitor_raw.py --fps 20 --window 2   # 調整刷新率與統計視窗

按 Ctrl+C 結束。

技術說明：muselsl 的 bleak 後端是「單執行緒」的——它把 sleep 換成一個會推動
asyncio 事件迴圈的 pump。BLE 通知（也就是資料回呼）只有在主迴圈呼叫
backends.sleep() 時才會被處理。因此回呼與畫面繪製都在同一條主執行緒上輪流執行，
不需要鎖（lock）。這一點與 muselsl 內建的 stream() 迴圈寫法一致。
"""
import argparse
import sys
import time
from collections import deque

import numpy as np

from muselsl import list_muses, backends
from muselsl.muse import Muse

# MUSE 2（classic 協定）的 EEG 通道與取樣率
CHANNELS = ["TP9", "AF7", "AF8", "TP10", "AUX"]
FS = 256  # Hz

# ANSI 控制碼
HOME = "\033[H"          # 游標移到左上角
CLEAR = "\033[2J"        # 清整個畫面
CLEAR_EOL = "\033[K"     # 清到行尾
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"

# sparkline 使用的 8 級方塊
BLOCKS = "▁▂▃▄▅▆▇█"


class Monitor:
    """在 EEG 回呼中累積資料；繪製時讀取。單執行緒，無需鎖。"""

    def __init__(self, window_sec=2.0):
        self.win = int(FS * window_sec)
        self.buf = [deque(maxlen=self.win) for _ in CHANNELS]
        self.total_samples = 0
        self.packets = 0
        self.t0 = None
        self.last_data_t = None

    def on_eeg(self, data, timestamps):
        """muselsl 回呼：data 形狀 (5, 12)，12 個樣本；timestamps 形狀 (12,)。"""
        if self.t0 is None:
            self.t0 = time.time()
        n = data.shape[1]
        for ci in range(len(CHANNELS)):
            self.buf[ci].extend(data[ci, :])
        self.total_samples += n
        self.packets += 1
        self.last_data_t = time.time()


def sparkline(vals, width=52):
    """把最近的樣本畫成一條 unicode 波形（每個通道各自正規化）。"""
    if len(vals) == 0:
        return DIM + "·" * width + RESET
    a = np.asarray(vals, dtype=float)[-width:]
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-9:
        return BLOCKS[3] * len(a)  # 幾乎平線 -> 置中
    norm = (a - lo) / (hi - lo)
    idx = np.clip((norm * (len(BLOCKS) - 1)).round().astype(int), 0, len(BLOCKS) - 1)
    return "".join(BLOCKS[i] for i in idx)


def quality(rms):
    """依 RMS（µV）粗略判斷訊號品質。"""
    if rms < 1.0:
        return RED, "無訊號/飽和"
    if rms <= 45.0:
        return GREEN, "良好"
    if rms <= 120.0:
        return YELLOW, "偏動/一般"
    return RED, "雜訊/接觸不良"


def render(mon, name, address, show_idx):
    elapsed = (time.time() - mon.t0) if mon.t0 else 0.0
    # 前 1 秒樣本太少、估出的取樣率不穩，先顯示「量測中」
    rate = (mon.total_samples / elapsed) if elapsed > 1.0 else float("nan")

    lines = []
    lines.append(f"{BOLD}{CYAN}MUSE 2 原始 EEG 即時監控{RESET}")
    lines.append(
        f"{DIM}裝置 {name}  [{address}]   取樣 {FS}Hz  單位 µV{RESET}"
    )

    if mon.last_data_t is None:
        lines.append("")
        lines.append(f"{YELLOW}已連線，等待資料串流中 ...{RESET}")
        blank = "\n".join(f"{ln}{CLEAR_EOL}" for ln in lines)
        sys.stdout.write(HOME + blank + "\n" + CLEAR_EOL)
        sys.stdout.flush()
        return

    stall = time.time() - mon.last_data_t
    status = (
        f"{GREEN}● 串流中{RESET}"
        if stall < 2
        else f"{RED}● 資料停滯 {stall:.0f}s{RESET}"
    )
    rate_str = f"{BOLD}{rate:6.1f}{RESET} Hz" if rate == rate else f"{DIM}量測中{RESET}"
    lines.append(
        f"{status}   實際取樣率 {rate_str}   "
        f"封包 {mon.packets}   樣本 {mon.total_samples}   已執行 {elapsed:5.1f}s"
    )
    lines.append("")
    lines.append(
        f"{DIM}通道   波形（各自正規化，左舊右新）"
        f"{'':>14}最新      RMS       峰峰     品質{RESET}"
    )

    for ci in show_idx:
        ch = CHANNELS[ci]
        arr = np.asarray(mon.buf[ci], dtype=float)
        if arr.size:
            last = arr[-1]
            rms = float(np.sqrt(np.mean((arr - arr.mean()) ** 2)))
            pp = float(arr.max() - arr.min())
        else:
            last = rms = pp = 0.0
        col, q = quality(rms)
        spark = sparkline(mon.buf[ci])
        lines.append(
            f"{BOLD}{ch:<5}{RESET} {spark}  "
            f"{last:+8.1f} {rms:8.1f} {pp:8.1f}  {col}{q}{RESET}"
        )

    lines.append("")
    lines.append(f"{DIM}Ctrl+C 結束{RESET}")

    out = "\n".join(f"{ln}{CLEAR_EOL}" for ln in lines)
    sys.stdout.write(HOME + out + "\n" + CLEAR_EOL)
    sys.stdout.flush()


def resolve_address(args):
    if args.address:
        return args.address, (args.name or "Muse")
    print("掃描 MUSE 裝置中（請確認頭帶已開機、LED 閃爍）...")
    muses = list_muses(backend="bleak")
    if not muses:
        sys.exit("找不到 MUSE 裝置。先執行  ./venv/bin/python list_devices.py  排查。")
    m = muses[0]
    print(f"使用裝置：{m['name']}  [{m['address']}]")
    return m["address"], m["name"]


def main():
    ap = argparse.ArgumentParser(description="即時監控 MUSE 2 原始 EEG")
    ap.add_argument("--address", help="MUSE 的 BLE address（省略則自動掃描）")
    ap.add_argument("--name", help="裝置名稱（可選）")
    ap.add_argument("--fps", type=float, default=15.0, help="畫面刷新率（預設 15）")
    ap.add_argument("--window", type=float, default=2.0, help="統計/波形視窗秒數（預設 2）")
    ap.add_argument("--retries", type=int, default=3, help="連線重試次數（預設 3）")
    ap.add_argument(
        "--aux",
        action="store_true",
        help="顯示 Right AUX 通道（預設隱藏；MUSE 2 無外接 AUX 電極時恆為 0）",
    )
    args = ap.parse_args()

    # MUSE 2 的 Right AUX 沒有外接電極、恆為 0，預設不顯示；接了外部電極再用 --aux 打開
    show_idx = list(range(5)) if args.aux else [0, 1, 2, 3]

    address, name = resolve_address(args)
    mon = Monitor(window_sec=args.window)

    muse = Muse(
        address=address,
        name=name,
        callback_eeg=mon.on_eeg,
        backend="bleak",
    )

    print(f"連線中 {name} [{address}] ...")
    if not muse.connect(retries=args.retries):
        sys.exit("連線失敗。請確認頭帶未被手機 App 佔用，且在附近。")

    muse.start()  # 送出開始串流指令

    sys.stdout.write(CLEAR + HIDE_CURSOR)
    period = 1.0 / max(args.fps, 1.0)
    try:
        while True:
            # backends.sleep 是被 muselsl 換成 pump 的版本：
            # 在這段時間內推動 asyncio 事件迴圈 -> 觸發 BLE 資料回呼
            backends.sleep(period)
            render(mon, name, address, show_idx)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET + "\n")
        try:
            muse.stop()
            muse.disconnect()
        except Exception:
            pass
        print("已中斷連線。")


if __name__ == "__main__":
    main()
