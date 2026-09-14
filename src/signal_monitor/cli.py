#!/usr/bin/env python3
"""
MUSE 2 EEG 互動式控制台（終端機選單）。

把整個流程整合到一個介面：掃描裝置、即時監控、錄製、一鍵流程（監控+錄製→FFT→EI）、
單獨做 FFT（只顯示）/ EI+FAA+眨眼（只輸出 Features/），以及「查看數據」（列出錄製檔、訊號摘要、
EI / FAA 結果、FFT 主頻）與清除資料。

擷取/監控類功能會以子程序呼叫既有模組（python -m signal_monitor.hardware.monitor_raw /
…record_csv / …overall_process …），這樣即時畫面能正常顯示；查看數據則直接讀
Data/ 與 Features/ 內的 CSV 算給你看。

用法:
    python -m signal_monitor
"""
import os
import re
import shutil
import subprocess
import traceback
import sys

import numpy as np

# 重用既有模組的路徑與函式
from signal_monitor.data_utils.record_csv import CSV_DIR, next_csv_path  # noqa: F401  (next_csv_path 供未來擴充)
from signal_monitor.analysis.fft_energy import (
    BASE_DIR, CHANNELS, load_eeg, per_second_energy,
)
from signal_monitor.analysis.features import build_features_csv

PY = sys.executable                       # 目前的 venv python
FEATURES_DIR = os.path.join(BASE_DIR, "Features")

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
        result = subprocess.run(cmd)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}已中斷，返回選單。{RESET}")
        return None
    if result.returncode != 0:
        print(f"\n{YELLOW}{module} 以非零狀態結束（{result.returncode}），"
              f"後續步驟的結果可能不完整。{RESET}")
    return result.returncode


def sort_features_csv_by_second(path):
    """保險重排：確保 Features CSV 以 second 數值遞增。"""
    import csv

    if not os.path.exists(path):
        return
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) <= 1:
        return

    header = rows[0]
    body = [r for r in rows[1:] if r]
    second_idx = header.index("second")

    def key_fn(row):
        value = row[second_idx]
        try:
            return (0, int(value))
        except ValueError:
            return (1, value)

    body.sort(key=key_fn)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)


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
    from signal_monitor.hardware.ble import NO_DEVICE_HINT, scan_muses
    print(f"{CYAN}掃描 MUSE 裝置中（請確認頭帶已開機、LED 閃爍）...{RESET}")
    muses, error = scan_muses()
    if error:
        print(f"{RED}掃描失敗：{error}{RESET}")
        return
    if not muses:
        print(f"{RED}{NO_DEVICE_HINT}{RESET}")
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
    """算 EI + FAA + 眨眼，只輸出 Features/<編號>.csv。

    EI 與 FAA 直接在記憶體裡算完就併進 Features，不再各自落檔到 EI/ 與 FAA/。
    """
    path = choose_recording()
    if not path:
        return

    stem = re.sub(r"\.csv$", "", os.path.basename(path))
    features_path = os.path.join(FEATURES_DIR, f"{stem}.csv")

    print(f"{CYAN}正在計算 EI / FAA / 眨眼…{RESET}")
    try:
        n_sec = build_features_csv(path, features_path)
    except ValueError as exc:
        print(f"{YELLOW}{exc}{RESET}")
        return

    sort_features_csv_by_second(features_path)
    print(f"{GREEN}已輸出 {n_sec} 秒的結果至 {features_path}{RESET}")


# ---------- 實驗 ----------
# 每種實驗的 Features 要歸檔到哪、檔名前綴是什麼。
# 原始錄製一律還是留在 Data/<編號>.csv，命名規則不變。
EXPERIMENTS = {
    "1": ("無聊實驗",    os.path.join(BASE_DIR, "Model", "boring"),      "boring_S"),
    "2": ("有趣實驗",    os.path.join(BASE_DIR, "Model", "interesting"), "interesting_S"),
    "3": ("PDF 實驗",    os.path.join(BASE_DIR, "Model", "PDF_Experiment"),    "PDF_S"),
    "4": ("Learn8 實驗", os.path.join(BASE_DIR, "Model", "Learn8_Experiment"), "Learn8_S"),
}


def next_subject_index(folder):
    """回傳這個實驗資料夾的下一個受測者編號。

    先認新命名結尾的 _S<編號>，認不出來才退回「檔名裡第一個數字」——
    也就是 Model/model_utils.py 的 subject_from_filename 規則，讓舊的
    boring/1.csv 與新的 boring_S1.csv 都算 S1，接續編號時不會撞在一起。

    順序不能反過來：Learn8_S1 用「第一個數字」會抓到 Learn8 的 8，
    整個資料夾的編號就會從 S9 開始跳號。
    """
    if not os.path.isdir(folder):
        return 1
    used = []
    for name in os.listdir(folder):
        if not name.endswith(".csv"):
            continue
        stem = os.path.splitext(name)[0]
        m = re.search(r"_S(\d+)$", stem, re.IGNORECASE) or re.search(r"(\d+)", stem)
        if m:
            used.append(int(m.group(1)))
    return (max(used) + 1) if used else 1

def do_experiment():
    """選實驗類型 -> 跑一鍵流程 -> 把 Features 歸檔到該實驗的資料夾。

    一鍵流程本身不變（錄製 -> FFT -> Features），這裡只是多一步：把算好的
    Features/<編號>.csv 另存一份到實驗資料夾，並改成帶受測者編號的檔名。
    用複製而非搬移，Features/ 仍保有一份，重跑分析或改用別的實驗分類都還有得救。
    """
    print(f"{BOLD}{CYAN}== 實驗 =={RESET}\n")
    print("要做哪一種實驗？（Features 會自動歸檔到對應資料夾）\n")
    for key in sorted(EXPERIMENTS):
        label, folder, prefix = EXPERIMENTS[key]
        rel = os.path.relpath(folder, BASE_DIR).replace(os.sep, "/")
        print(f"  [{key}] {label:<11} -> {rel}/{prefix}{next_subject_index(folder)}.csv")

    sel = ask("\n請選擇（Enter 取消）：")
    if sel not in EXPERIMENTS:
        print(f"{YELLOW}已取消，未進行任何實驗。{RESET}")
        return
    label, folder, prefix = EXPERIMENTS[sel]

    secs = ask(f"{label}要錄幾秒？（建議 ≥10；Enter = 錄到 Ctrl+C）：", default="0")

    # 先記下目前的錄製檔，跑完再比對差集，才知道這次新產生的是哪一個編號。
    before = set(list_recordings())
    print(f"\n{CYAN}開始「{label}」的一鍵流程…{RESET}")
    run_module("signal_monitor.overall_process", *device_args(), "--seconds", secs)

    new_files = sorted(set(list_recordings()) - before, key=lambda x: int(x[:-4]))
    if not new_files:
        print(f"\n{YELLOW}這次沒有產生新的錄製檔（可能連線失敗或中途取消），略過歸檔。{RESET}")
        return

    stem = new_files[-1][:-4]
    features_path = os.path.join(FEATURES_DIR, f"{stem}.csv")
    if not os.path.exists(features_path):
        print(f"\n{YELLOW}已錄到 Data/{stem}.csv，但沒有對應的 Features"
              f"（錄不到 1 秒或分析被中斷），略過歸檔。{RESET}")
        print(f"{DIM}可以先用選單 [6] 對 {stem}.csv 補算 Features，再重跑一次本選項歸檔。{RESET}")
        return

    os.makedirs(folder, exist_ok=True)
    index = next_subject_index(folder)
    dest = os.path.join(folder, f"{prefix}{index}.csv")
    shutil.copy2(features_path, dest)

    rel_dest = os.path.relpath(dest, BASE_DIR).replace(os.sep, "/")
    print(f"\n{GREEN}已歸檔至 {rel_dest}（S{index}）{RESET}")
    print(f"{DIM}原始錄製：Data/{stem}.csv　中繼結果：Features/{stem}.csv（皆保留）{RESET}")


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


def _read_features_column(path, metric):
    """從 Features CSV 讀出 (秒, 每秒值, 平滑值) 三串。欄位以表頭名稱定位。"""
    import csv as _csv
    with open(path) as f:
        rows = list(_csv.reader(f))
    if len(rows) < 2:
        return [], [], [], ""

    header = rows[0]
    raw_idx = header.index(metric)
    smooth_name = next(c for c in header if c.startswith(f"{metric}_smooth"))
    smooth_idx = header.index(smooth_name)

    def num(row, i):
        return float(row[i]) if i < len(row) and row[i] else float("nan")

    secs, raws, smooths = [], [], []
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        secs.append(int(row[0]))
        raws.append(num(row, raw_idx))
        smooths.append(num(row, smooth_idx))
    return secs, raws, smooths, smooth_name


def view_feature_metric(metric):
    """查看 Features/ 內某個指標（EI 或 FAA）的逐秒結果。

    EI 與 FAA 不再各自輸出到 EI/ 與 FAA/，兩者都併在 Features/<編號>.csv 裡，
    所以這裡統一從 Features/ 讀。
    """
    files = [f for f in os.listdir(FEATURES_DIR) if re.match(r"^\d+\.csv$", f)] \
        if os.path.isdir(FEATURES_DIR) else []
    if not files:
        print(f"{YELLOW}Features/ 內沒有結果。先跑選單 [6] 對某個錄製檔算 EI + FAA。{RESET}")
        return
    files.sort(key=lambda x: int(x[:-4]))
    print(f"{BOLD}Features/ 內的結果：{RESET} " + ", ".join(files))
    stem = ask(f"看哪個編號？（Enter = 最新 {files[-1][:-4]}）：", default=files[-1][:-4])
    path = os.path.join(FEATURES_DIR, f"{stem}.csv")
    if not os.path.exists(path):
        print(f"{RED}找不到 {path}{RESET}")
        return

    try:
        secs, raws, smooths, smooth_name = _read_features_column(path, metric)
    except (ValueError, StopIteration):
        print(f"{RED}{path} 內找不到 {metric} 欄位。{RESET}")
        return
    if not secs:
        print(f"{YELLOW}{path} 沒有資料列。{RESET}")
        return

    signed = metric == "FAA"
    fmt = "+.4f" if signed else ".4f"
    window = smooth_name.replace(f"{metric}_smooth", "")
    print(f"\n{BOLD}{'秒':>3}  {f'{metric}(每秒)':>10}  {f'穩定{metric}({window}秒平均)':>16}{RESET}")
    for sec, raw, smooth in zip(secs, raws, smooths):
        raw_str = "nan" if np.isnan(raw) else format(raw, fmt)
        smooth_str = format(smooth, fmt) if not np.isnan(smooth) else f"{DIM}—{RESET}"
        print(f"{sec:>3}  {raw_str:>10}  {smooth_str:>16}")

    valid = [v for v in smooths if not np.isnan(v)]
    if not valid:
        print(f"{YELLOW}此段未滿 {window} 秒，沒有穩定分數。{RESET}")
        return
    avg = np.mean(valid)
    extra = ""
    if signed:
        extra = "（偏正向/趨近）" if avg > 0 else "（偏負向/退縮）"
    print(f"\n{GREEN}穩定分數 {len(valid)} 個，平均 = {format(avg, fmt)}{extra}，"
          f"最新 = {format(valid[-1], fmt)}{RESET}")


def view_ei_result():
    view_feature_metric("EI")


def view_faa_result():
    view_feature_metric("FAA")


def view_fft_peaks():
    """從錄製檔即時算出各通道的主頻與頻帶能量。

    FFT 結果不再落檔到 FFT/ —— 它只是 EI / FAA 的中間產物，存起來又大又沒人讀。
    要看的時候直接從 Data/<編號>.csv 重算，256 點的 FFT 很快。
    """
    path = choose_recording()
    if not path:
        return

    data = load_eeg(path)
    fs = 256
    n_sec = len(data) // fs
    if n_sec == 0:
        print(f"{YELLOW}{os.path.basename(path)} 資料不足 1 秒。{RESET}")
        return

    print(f"\n{BOLD}{os.path.basename(path)}　各通道整段平均下的主頻與頻帶能量（µV²）{RESET}")
    print(f"{DIM}通道     主頻    θ(4-8)   α(8-12)  β(13-30){RESET}")
    for c, ch in enumerate(CHANNELS):
        mean_e = per_second_energy(data[:, c], fs)[:, 1:].mean(axis=0)  # 索引 i -> (i+1) Hz
        peak_hz = int(np.argmax(mean_e)) + 1
        print(f"  {ch:<5} {peak_hz:4d}Hz {mean_e[3:7].sum():9.0f} "
              f"{mean_e[7:12].sum():9.0f} {mean_e[12:30].sum():9.0f}")
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
    """查看原始數據：選 Features 裡的 csv，如 cat 直接印出內容。"""
    print(f"{BOLD}{CYAN}== 查看原始數據 =={RESET}\n")
    path = pick_csv_in_dir(FEATURES_DIR, "Features")
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
        print("  [3] 查看 FFT 主頻與頻帶能量（即時計算）")
        print("  [4] 查看 FAA 前額 alpha 不對稱")
        print("  [5] 每秒 FFT 明細（逐秒列出，不存檔）")
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
        elif c == "5":
            clear(); do_fft(); pause()
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
   [4] 一鍵流程：監控+錄製 → FFT → Features  {DIM}(★推薦){RESET}
   [5] 實驗：選實驗類型 → 一鍵流程 → Features 自動歸檔

 {BOLD}分析{RESET}
   [6] 對錄製檔算 EI + FAA + 眨眼（只輸出 Features/）

 {BOLD}查看 / 管理{RESET}
   [7] 查看數據（訊號摘要 / EI / FAA / FFT）
   [8] 刪除 CSV（Data/、Features/；保留實驗歸檔資料）
   [9] 查看 Features 原始內容
   [0] 離開
{DIM}--------------------------------------------{RESET}"""


def main():
    actions = {
        "1": do_scan, "2": do_monitor, "3": do_record, "4": do_pipeline,
        "5": do_experiment, "6": do_ei, "7": do_view_data, "8": do_clean, "9": do_cat,
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
        except SystemExit as exc:
            # 分析模組用 sys.exit("訊息") 回報資料問題（例如 CSV 沒有資料列）。
            # 那代表「這次操作失敗」，不該把整個控制台一起結束。
            if exc.code not in (0, None):
                print(f"\n{YELLOW}此操作結束：{exc.code}{RESET}")
        except Exception as exc:                  # noqa: BLE001 - 保護網：不讓使用者失去控制台
            print(f"\n{RED}執行時發生錯誤：{type(exc).__name__}: {exc}{RESET}")
            if os.environ.get("SIGNAL_MONITOR_DEBUG"):
                traceback.print_exc()
            else:
                print(f"{DIM}（設 SIGNAL_MONITOR_DEBUG=1 可顯示完整錯誤堆疊）{RESET}")
        if choice != "7":   # 查看數據子選單自己有暫停
            pause()


if __name__ == "__main__":
    main()
