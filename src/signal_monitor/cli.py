#!/usr/bin/env python3
"""
MUSE 2 EEG 互動式控制台（終端機選單）。

把整個流程整合到一個介面：掃描裝置、即時監控、錄製、一鍵流程（監控+錄製→FFT→EI）、
單獨做 FFT / EI，以及「查看數據」（列出錄製檔、訊號摘要、EI 結果、FFT 主頻）與清除資料。

擷取/監控類功能會以子程序呼叫既有模組（python -m signal_monitor.hardware.monitor_raw /
…record_csv / …overall_process …），這樣即時畫面能正常顯示；查看數據則直接讀
Data/、EI/、FFT/ 內的 CSV 算給你看。

用法:
    python -m signal_monitor
"""
import os
import re
import subprocess
import sys

import numpy as np

# 重用既有模組的路徑與函式
from signal_monitor.data_utils.record_csv import CSV_DIR, next_csv_path  # noqa: F401  (next_csv_path 供未來擴充)
from signal_monitor.analysis.fft_energy import BASE_DIR, CHANNELS, load_eeg

PY = sys.executable                       # 目前的 venv python
EI_DIR = os.path.join(BASE_DIR, "EI")
FFT_DIR = os.path.join(BASE_DIR, "FFT")
FAA_DIR = os.path.join(BASE_DIR, "FAA")

# ANSI
BOLD = "\033[1m"; DIM = "\033[2m"; RESET = "\033[0m"
CYAN = "\033[36m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"

# 目前選定的裝置（供 monitor/record/main 重用；未設定則各腳本自行掃描）
state = {"address": None, "name": None}


# ---------- 小工具 ----------
def clear():
    # 2J 清畫面、3J 清捲動歷史(scrollback)、H 游標回左上 -> 只保留當下這一頁
    print("\033[2J\033[3J\033[H", end="")


def pause():
    try:
        input(f"\n{DIM}按 Enter 返回選單...{RESET}")
    except (EOFError, KeyboardInterrupt):
        pass


def ask(prompt, default=None):
    try:
        s = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return s if s else default


def run_module(module, *args):
    """以子程序執行套件內模組（python -m ...），繼承終端機（即時 UI 正常）。"""
    cmd = [PY, "-m", module, *[a for a in args if a is not None]]
    print(f"{DIM}$ {' '.join(cmd)}{RESET}\n")
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}已中斷，返回選單。{RESET}")


def device_args():
    if state["address"]:
        return ["--address", state["address"]]
    return []


def list_recordings():
    """回傳 Data/ 內的 <編號>.csv 檔名，依編號排序。"""
    if not os.path.isdir(CSV_DIR):
        return []
    files = [f for f in os.listdir(CSV_DIR) if re.match(r"^\d+\.csv$", f)]
    return sorted(files, key=lambda x: int(x[:-4]))


def count_rows(path):
    with open(path) as f:
        return sum(1 for _ in f) - 1  # 扣掉表頭


def choose_recording(prompt_default_last=True):
    """列出錄製檔讓使用者選；Enter 預設選最後（最新）一個。回傳完整路徑或 None。"""
    files = list_recordings()
    if not files:
        print(f"{YELLOW}Data/ 內沒有任何錄製檔（<編號>.csv）。先錄一段吧。{RESET}")
        return None
    print(f"{BOLD}Data/ 內的錄製檔：{RESET}")
    for i, f in enumerate(files):
        n = count_rows(os.path.join(CSV_DIR, f))
        print(f"  [{i}] {f:<10} 約 {n/256:6.1f} 秒 （{n} 樣本）")
    default = files[-1] if prompt_default_last else None
    sel = ask(f"選擇編號（Enter = 最新 {default}）：", default="__last__")
    if sel == "__last__":
        return os.path.join(CSV_DIR, files[-1])
    if sel is None:
        return None
    if sel.isdigit() and int(sel) < len(files):
        return os.path.join(CSV_DIR, files[int(sel)])
    print(f"{RED}無效的選擇。{RESET}")
    return None


# ---------- 各功能 ----------
def do_scan():
    from muselsl import list_muses
    print(f"{CYAN}掃描 MUSE 裝置中（請確認頭帶已開機、LED 閃爍）...{RESET}")
    muses = list_muses(backend="bleak")
    if not muses:
        print(f"{RED}找不到任何 MUSE 裝置。{RESET}")
        return
    print(f"\n找到 {len(muses)} 台：")
    for i, m in enumerate(muses):
        print(f"  [{i}] {m['name']:<18} {m['address']}")
    sel = ask("要把哪一台設為「目前裝置」？輸入編號（Enter 跳過）：")
    if sel and sel.isdigit() and int(sel) < len(muses):
        m = muses[int(sel)]
        state["address"], state["name"] = m["address"], m["name"]
        print(f"{GREEN}已設定目前裝置：{m['name']} [{m['address']}]{RESET}")


def do_monitor():
    run_module("signal_monitor.hardware.monitor_raw", *device_args())


def do_record():
    secs = ask("要錄幾秒？（Enter = 一直錄到 Ctrl+C）：", default="0")
    run_module("signal_monitor.data_utils.record_csv", *device_args(), "--seconds", secs)


def do_pipeline():
    secs = ask("一鍵流程要錄幾秒？（建議 ≥10；Enter = 錄到 Ctrl+C）：", default="0")
    run_module("signal_monitor.overall_process", *device_args(), "--seconds", secs)


def do_fft():
    path = choose_recording()
    if path:
        run_module("signal_monitor.analysis.fft_energy", path)


def do_ei():
    path = choose_recording()
    if path:
        run_module("signal_monitor.analysis.engagement", path)
        run_module("signal_monitor.analysis.faa", path)


def do_clean():
    # clean_csv 內含 y/N 確認，直接交給它
    run_module("signal_monitor.data_utils.clean_csv")


# ---------- 查看數據 ----------
def view_recording_stats():
    path = choose_recording()
    if not path:
        return
    try:
        data = load_eeg(path)
    except SystemExit as e:
        print(f"{RED}{e}{RESET}")
        return
    n = len(data)
    print(f"\n{BOLD}{os.path.basename(path)}{RESET}：{n} 樣本，約 {n/256:.1f} 秒")
    print(f"{DIM}通道      平均      RMS(交流)     最小      最大{RESET}")
    for c, ch in enumerate(CHANNELS):
        col = data[:, c]
        rms = float(np.sqrt(np.mean((col - col.mean()) ** 2)))
        print(f"  {ch:<5} {col.mean():9.1f} {rms:10.1f} {col.min():9.1f} {col.max():9.1f}")
    print(f"{DIM}（單位 µV。RMS 太大通常代表未配戴或電極接觸不良）{RESET}")


def view_ei_result():
    files = [f for f in os.listdir(EI_DIR) if re.match(r"^\d+\.csv$", f)] if os.path.isdir(EI_DIR) else []
    if not files:
        print(f"{YELLOW}EI/ 內沒有結果。先對某個錄製檔跑「算 EI」。{RESET}")
        return
    files.sort(key=lambda x: int(x[:-4]))
    print(f"{BOLD}EI/ 內的結果：{RESET} " + ", ".join(files))
    stem = ask(f"看哪個編號？（Enter = 最新 {files[-1][:-4]}）：", default=files[-1][:-4])
    path = os.path.join(EI_DIR, f"{stem}.csv")
    if not os.path.exists(path):
        print(f"{RED}找不到 {path}{RESET}")
        return
    import csv as _csv
    secs, eis, smooths = [], [], []
    with open(path) as f:
        r = _csv.reader(f)
        next(r)
        for row in r:
            if not row:
                continue
            secs.append(int(row[0]))
            eis.append(float(row[1]) if row[1] else float("nan"))
            smooths.append(float(row[2]) if len(row) > 2 and row[2] else float("nan"))
    print(f"\n{BOLD}{'秒':>3}  {'EI(每秒)':>10}  {'穩定EI(10秒平均)':>16}{RESET}")
    for s, e, sm in zip(secs, eis, smooths):
        e_str = "nan" if np.isnan(e) else f"{e:.4f}"
        sm_str = f"{sm:.4f}" if not np.isnan(sm) else f"{DIM}—{RESET}"
        print(f"{s:>3}  {e_str:>10}  {sm_str:>16}")
    valid = [v for v in smooths if not np.isnan(v)]
    if valid:
        print(f"\n{GREEN}穩定分數 {len(valid)} 個，平均 = {np.mean(valid):.4f}，"
              f"最新 = {valid[-1]:.4f}{RESET}")
    else:
        print(f"{YELLOW}此段未滿 10 秒，沒有穩定分數。{RESET}")


def view_faa_result():
    files = [f for f in os.listdir(FAA_DIR) if re.match(r"^\d+\.csv$", f)] if os.path.isdir(FAA_DIR) else []
    if not files:
        print(f"{YELLOW}FAA/ 內沒有結果。先對某個錄製檔跑「算 FAA」（選單 [6]）。{RESET}")
        return
    files.sort(key=lambda x: int(x[:-4]))
    print(f"{BOLD}FAA/ 內的結果：{RESET} " + ", ".join(files))
    stem = ask(f"看哪個編號？（Enter = 最新 {files[-1][:-4]}）：", default=files[-1][:-4])
    path = os.path.join(FAA_DIR, f"{stem}.csv")
    if not os.path.exists(path):
        print(f"{RED}找不到 {path}{RESET}")
        return
    import csv as _csv
    secs, faas, smooths = [], [], []
    with open(path) as f:
        r = _csv.reader(f)
        next(r)
        for row in r:
            if not row:
                continue
            secs.append(int(row[0]))
            faas.append(float(row[1]) if row[1] else float("nan"))
            smooths.append(float(row[2]) if len(row) > 2 and row[2] else float("nan"))
    print(f"\n{BOLD}{'秒':>3}  {'FAA(每秒)':>10}  {'穩定FAA(10秒平均)':>16}{RESET}")
    for s, e, sm in zip(secs, faas, smooths):
        e_str = "nan" if np.isnan(e) else f"{e:+.4f}"
        sm_str = f"{sm:+.4f}" if not np.isnan(sm) else f"{DIM}—{RESET}"
        print(f"{s:>3}  {e_str:>10}  {sm_str:>16}")
    valid = [v for v in smooths if not np.isnan(v)]
    if valid:
        avg = np.mean(valid)
        tone = "偏正向/趨近" if avg > 0 else "偏負向/退縮"
        print(f"\n{GREEN}穩定分數 {len(valid)} 個，平均 = {avg:+.4f}（{tone}），"
              f"最新 = {valid[-1]:+.4f}{RESET}")
    else:
        print(f"{YELLOW}此段未滿 10 秒，沒有穩定分數。{RESET}")


def view_fft_peaks():
    stem = None
    # 以 TP9 資料夾判斷有哪些編號
    tp9 = os.path.join(FFT_DIR, "TP9")
    files = [f for f in os.listdir(tp9) if re.match(r"^\d+\.csv$", f)] if os.path.isdir(tp9) else []
    if not files:
        print(f"{YELLOW}FFT/ 內沒有結果。先對某個錄製檔跑「做 FFT」。{RESET}")
        return
    files.sort(key=lambda x: int(x[:-4]))
    print(f"{BOLD}FFT/ 內的結果編號：{RESET} " + ", ".join(f[:-4] for f in files))
    stem = ask(f"看哪個編號？（Enter = 最新 {files[-1][:-4]}）：", default=files[-1][:-4])
    print(f"\n{BOLD}各通道整段平均下的主頻與頻帶能量（µV²）{RESET}")
    print(f"{DIM}通道     主頻    θ(4-8)   α(8-12)  β(13-30){RESET}")
    for ch in CHANNELS:
        path = os.path.join(FFT_DIR, ch, f"{stem}.csv")
        if not os.path.exists(path):
            print(f"  {ch:<5} {RED}(缺檔){RESET}")
            continue
        arr = np.genfromtxt(path, delimiter=",", skip_header=1)
        if arr.ndim == 1:
            arr = arr[None, :]
        # 欄 0 = second；欄 1..128 = 1..128 Hz
        mean_e = arr[:, 1:].mean(axis=0)          # 長度 128，索引 i -> (i+1) Hz
        peak_hz = int(np.argmax(mean_e)) + 1
        theta = mean_e[3:7].sum()                  # 4..7 Hz
        alpha = mean_e[7:12].sum()                 # 8..12 Hz
        beta = mean_e[12:30].sum()                 # 13..30 Hz
        print(f"  {ch:<5} {peak_hz:4d}Hz {theta:9.0f} {alpha:9.0f} {beta:9.0f}")
    print(f"{DIM}（主頻若在 60 Hz 附近多為市電干擾/接觸不良；正常 EEG 多集中在低頻）{RESET}")


def cat_file(path):
    """如 cat：清屏後把整個檔案原始內容逐行印出。"""
    clear()
    rel = os.path.relpath(path, BASE_DIR)
    try:
        with open(path) as f:
            content = f.read()
    except OSError as e:
        print(f"{RED}無法讀取 {rel}：{e}{RESET}")
        return
    n_lines = content.count("\n")
    print(f"{BOLD}{CYAN}== 原始數據（cat）：{rel} =={RESET}")
    print(f"{DIM}共 {n_lines} 行（含表頭）；以下為檔案原始內容{RESET}")
    print(f"{DIM}{'-' * 44}{RESET}")
    sys.stdout.write(content)
    if not content.endswith("\n"):
        sys.stdout.write("\n")
    print(f"{DIM}{'-' * 44}{RESET}")


def pick_csv_in_dir(d, label):
    """列出目錄 d 內的 *.csv 讓使用者選一個，回傳完整路徑或 None。"""
    if not os.path.isdir(d):
        print(f"{YELLOW}{label} 資料夾不存在（還沒產生資料）。{RESET}")
        return None
    files = sorted(f for f in os.listdir(d) if f.endswith(".csv"))
    if not files:
        print(f"{YELLOW}{label} 內還沒有任何 .csv。{RESET}")
        return None
    print(f"\n{BOLD}{label} 內的檔案：{RESET}")
    for i, f in enumerate(files):
        print(f"  [{i}] {f}")
    sel = ask("選擇編號：")
    if sel and sel.isdigit() and int(sel) < len(files):
        return os.path.join(d, files[int(sel)])
    print(f"{RED}無效的選擇。{RESET}")
    return None


def do_cat():
    """查看原始數據：選 EI / FFT / FAA 裡的 csv，如 cat 直接印出內容。"""
    print(f"{BOLD}{CYAN}== 查看原始數據 =={RESET}\n")
    print("  [1] EI 專注度結果（EI/）")
    print("  [2] FFT 頻譜能量（FFT/<通道>/）")
    print("  [3] FAA 前額 alpha 不對稱（FAA/）")
    print("  [0] 返回")
    c = ask("\n請選擇：")

    if c == "1":
        path = pick_csv_in_dir(os.path.join(BASE_DIR, "EI"), "EI")
        if path:
            cat_file(path)
    elif c == "3":
        path = pick_csv_in_dir(os.path.join(BASE_DIR, "FAA"), "FAA")
        if path:
            cat_file(path)
    elif c == "2":
        fft_dir = os.path.join(BASE_DIR, "FFT")
        chans = [ch for ch in CHANNELS
                 if os.path.isdir(os.path.join(fft_dir, ch))
                 and any(f.endswith(".csv") for f in os.listdir(os.path.join(fft_dir, ch)))]
        if not chans:
            print(f"{YELLOW}FFT/ 內還沒有任何資料。先做一次 FFT（選單 [5]）。{RESET}")
            return
        print(f"\n{BOLD}選擇通道：{RESET}")
        for i, ch in enumerate(chans):
            print(f"  [{i}] {ch}")
        sel = ask("通道編號：")
        if not (sel and sel.isdigit() and int(sel) < len(chans)):
            print(f"{RED}無效的選擇。{RESET}")
            return
        ch = chans[int(sel)]
        path = pick_csv_in_dir(os.path.join(fft_dir, ch), f"FFT/{ch}")
        if path:
            cat_file(path)


def do_view_data():
    while True:
        clear()
        print(f"{BOLD}{CYAN}== 查看數據 =={RESET}\n")
        recs = list_recordings()
        print(f"目前 Data/ 有 {len(recs)} 個錄製檔"
              + ("： " + ", ".join(r[:-4] for r in recs) if recs else "（無）") + "\n")
        print("  [1] 錄製檔訊號摘要（每通道 平均/RMS/最小/最大）")
        print("  [2] 查看 EI 專注度結果")
        print("  [3] 查看 FFT 主頻與頻帶能量")
        print("  [4] 查看 FAA 前額 alpha 不對稱")
        print("  [0] 返回主選單")
        c = ask("\n請選擇：")
        if c == "1":
            clear(); view_recording_stats(); pause()
        elif c == "2":
            clear(); view_ei_result(); pause()
        elif c == "3":
            clear(); view_fft_peaks(); pause()
        elif c == "4":
            clear(); view_faa_result(); pause()
        elif c in ("0", None, "q"):
            return
        else:
            print(f"{RED}無效選擇。{RESET}"); pause()


# ---------- 主選單 ----------
MENU = f"""{BOLD}{CYAN}============================================
        MUSE 2 EEG 控制台
============================================{RESET}
 目前裝置：{{device}}

 {BOLD}擷取 / 監控{RESET}
   [1] 掃描並選擇 MUSE 裝置
   [2] 即時監控原始 EEG
   [3] 錄製資料到 Data/
   [4] 一鍵流程：監控+錄製 → FFT → EI → FAA  {DIM}(★推薦){RESET}

 {BOLD}分析{RESET}
   [5] 對錄製檔做每秒 FFT（輸出 FFT/）
   [6] 對錄製檔算 EI + FAA（輸出 EI/、FAA/）

 {BOLD}查看 / 管理{RESET}
   [7] 查看數據（訊號摘要 / EI / FFT / FAA）
   [8] 刪除專案內所有 CSV
   [9] 查看原始數據
   [0] 離開
{DIM}--------------------------------------------{RESET}"""


def main():
    actions = {
        "1": do_scan, "2": do_monitor, "3": do_record, "4": do_pipeline,
        "5": do_fft, "6": do_ei, "7": do_view_data, "8": do_clean, "9": do_cat,
    }
    while True:
        clear()
        dev = (f"{GREEN}{state['name']} [{state['address']}]{RESET}"
               if state["address"] else f"{DIM}未設定（執行時自動掃描）{RESET}")
        print(MENU.format(device=dev))
        try:
            choice = input("請選擇功能編號：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再見！")
            return
        if choice in ("0", "q", "quit", "exit"):
            print("再見！")
            return
        action = actions.get(choice)
        if not action:
            print(f"{RED}無效選擇：{choice}{RESET}"); pause(); continue
        clear()             # 每個功能都獨佔一頁，不把主選單留在上面
        try:
            action()
        except KeyboardInterrupt:
            print(f"\n{YELLOW}已中斷，返回選單。{RESET}")
        if choice != "7":   # 查看數據子選單自己有暫停
            pause()


if __name__ == "__main__":
    main()
