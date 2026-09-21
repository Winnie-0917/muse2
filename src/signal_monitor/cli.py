#!/usr/bin/env python3
"""
MUSE 2 EEG 互動式控制台（終端機選單）。

把整個流程整合到一個介面：掃描裝置、即時監控、一鍵流程（監控+錄製→FFT→EI）、
實驗歸檔、模型預測（比較 PDF 與 Learn8）、單獨做 FFT（只顯示）/ EI+FAA+眨眼
（只輸出 Features/），以及「查看數據」（列出錄製檔、訊號摘要、EI / FAA 結果、
FFT 主頻）與清除資料。

擷取/監控類功能會以子程序呼叫既有模組（python -m signal_monitor.hardware.monitor_raw /
…overall_process …），這樣即時畫面能正常顯示；模型預測則呼叫 Model/predict_model.py；
查看數據直接讀 Data/ 與 Features/ 內的 CSV 算給你看。

用法:
    python -m signal_monitor
"""
import os
import re
import shutil
import subprocess
import traceback
import unicodedata
import sys

import numpy as np

# 重用既有模組的路徑與函式
from signal_monitor.data_utils.record_csv import (  # noqa: F401  (next_csv_path 供未來擴充)
    CSV_DIR, next_csv_path, ORIGINAL_SUFFIX, ORIGINAL_RE,
)
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


def disp_width(text):
    """終端機顯示寬度：全形字（CJK）佔兩欄。

    f"{s:<18}" 是按「字數」補的，中文欄位會短一截而對不齊，所以自己算。
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text, width):
    """靠左補空白到指定顯示寬度；ANSI 色碼請在外面再包，別混進來算寬度。"""
    return text + " " * max(0, width - disp_width(text))


def ask(prompt, default=None):
    try:
        s = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return s if s else default


def run_cmd(cmd, label):
    """跑一個子程序並繼承終端機（即時 UI 正常）；label 只用在錯誤訊息上。"""
    print(f"{DIM}$ {' '.join(cmd)}{RESET}\n")
    try:
        result = subprocess.run(cmd)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}已中斷，返回選單。{RESET}")
        return None
    if result.returncode != 0:
        print(f"\n{YELLOW}{label} 以非零狀態結束（{result.returncode}），"
              f"後續步驟的結果可能不完整。{RESET}")
    return result.returncode


def run_module(module, *args):
    """以子程序執行套件內模組（python -m ...）。"""
    return run_cmd([PY, "-m", module, *[a for a in args if a is not None]], module)


def run_script(path, *args):
    """以子程序執行獨立腳本。

    Model/ 下的訓練與預測腳本不在 signal_monitor 套件裡，不能用 python -m 跑；
    直接給路徑則 Python 會把腳本所在的 Model/ 放進 sys.path，
    predict_model.py 的 `import model_utils` 才找得到。
    """
    return run_cmd([PY, path, *[a for a in args if a is not None]],
                   os.path.basename(path))


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


def name_order(name):
    """排序鍵：流水號在前（依數字大小），其餘檔名在後（依字母）。

    Data/ 與 Features/ 都可能同時有 <編號>.csv 與 <實驗檔名>_Original.csv，
    純數字排序會對後者丟 ValueError，所以兩種檔名共用這個鍵。
    """
    stem = name[:-4]
    return (0, int(stem), "") if stem.isdigit() else (1, 0, stem.lower())


def list_recordings():
    """回傳 Data/ 內的錄製檔，流水號在前、實驗保存的 _Original 在後。

    Data/ 有兩種錄製檔：<編號>.csv 是 [3] 一鍵流程產生的流水號，
    <實驗檔名>_Original.csv 則是 [4] 實驗把原始 EEG 改名保存的那份。
    兩種都是可以重算的原始 EEG，所以 [6] / [7] 都要看得到——
    實驗跑完 Data/ 只剩 _Original 那份，漏掉就等於找不到原始資料了。
    """
    if not os.path.isdir(CSV_DIR):
        return []
    files = [f for f in os.listdir(CSV_DIR)
             if re.match(r"^\d+\.csv$", f) or ORIGINAL_RE.search(f)]
    return sorted(files, key=name_order)


def count_rows(path):
    with open(path) as f:
        return sum(1 for _ in f) - 1  # 扣掉表頭


def choose_recording(prompt_default_last=True):
    """列出錄製檔讓使用者選；Enter 預設選最後（最新）一個。回傳完整路徑或 None。"""
    files = list_recordings()
    if not files:
        print(f"{YELLOW}Data/ 內沒有任何錄製檔（<編號>.csv 或 <實驗檔名>_Original.csv）。"
              f"先錄一段吧。{RESET}")
        return None
    print(f"{BOLD}Data/ 內的錄製檔：{RESET}")
    for i, f in enumerate(files):
        n = count_rows(os.path.join(CSV_DIR, f))
        print(f"  [{i}] {f:<32} 約 {n/256:6.1f} 秒 （{n} 樣本）")
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
# 原始錄製會被「改名保存」成 Data/<實驗檔名>_Original.csv：搬移而非複製，
# Data/ 不再留下流水號那份，同一段錄製不會在 Data/ 裡存兩次。
EXPERIMENTS = {
    "1": ("無聊實驗",    os.path.join(BASE_DIR, "Model", "boring"),      "boring_S"),
    "2": ("有趣實驗",    os.path.join(BASE_DIR, "Model", "interesting"), "interesting_S"),
    "3": ("PDF 實驗",    os.path.join(BASE_DIR, "Model", "PDF_Experiment"),    "PDF_S"),
    "4": ("Learn8 實驗", os.path.join(BASE_DIR, "Model", "Learn8_Experiment"), "Learn8_S"),
}

# ---------- 模型預測 ----------
MODEL_DIR = os.path.join(BASE_DIR, "Model")
MODEL_FILE = os.path.join(MODEL_DIR, "trained_model.joblib")
PREDICT_SCRIPT = os.path.join(MODEL_DIR, "predict_model.py")
TRAIN_SCRIPT = os.path.join(MODEL_DIR, "train_model.py")

# 選單 [5] 要拿來對比的兩種情境。沿用 EXPERIMENTS 裡 PDF / Learn8 那兩項的資料夾，
# 改動實驗歸檔位置時兩邊會一起跟著走。
COMPARE_SETS = (("PDF 閱讀", EXPERIMENTS["3"][1]), ("Learn8 學習", EXPERIMENTS["4"][1]))


def subject_index(stem):
    """檔名 -> 受測者編號；規則與 next_subject_index() / model_utils 一致。

    先認結尾的 _S<編號>，認不出來才退回「檔名裡第一個數字」——順序不能反過來，
    Learn8_S1 用「第一個數字」會抓到 Learn8 的 8。
    """
    m = re.search(r"_S(\d+)$", stem, re.IGNORECASE) or re.search(r"(\d+)", stem)
    return int(m.group(1)) if m else None


def collect_compare_subjects():
    """掃 PDF / Learn8 兩個資料夾，回傳 {受測者編號: {情境名: 檔案路徑}}。"""
    found = {}
    for label, folder in COMPARE_SETS:
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".csv"):
                continue
            index = subject_index(name[:-4])
            if index is not None:
                found.setdefault(index, {})[label] = os.path.join(folder, name)
    return found


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



def original_dest_path(archive_stem):
    """回傳 Data/<實驗檔名>_Original.csv 的可用路徑。

    正常情況下 archive_stem（例如 boring_S1）是 Model/ 裡的新編號，Data/ 不會有同名檔。
    但若 Model/ 的歸檔被刪掉、編號重新從 S1 算起而撞名——這時往後找 _2、_3，
    寧可多留一個檔，也不要覆蓋掉上一次實驗的原始資料。

    檔名不是純數字，next_csv_path() 只認純數字，所以這份檔不會佔用流水號；
    list_recordings() 則另外認得它，[6] / [7] 依然選得到這段原始資料。
    """
    base = os.path.join(CSV_DIR, f"{archive_stem}{ORIGINAL_SUFFIX}")
    path = f"{base}.csv"
    n = 2
    while os.path.exists(path):
        path = f"{base}_{n}.csv"
        n += 1
    return path


def do_experiment():
    """選實驗類型 -> 跑一鍵流程 -> 把 Features 與原始錄製一起歸檔。

    一鍵流程本身不變（錄製 -> FFT -> Features），這裡多兩步：
      1. Features/<編號>.csv 複製一份到實驗資料夾，改成帶受測者編號的檔名。
      2. Data/<編號>.csv（原始 EEG）改名搬移成 Data/<實驗檔名>_Original.csv。

    第 2 步是搬移不是複製：Data/ 不留流水號那份，同一段原始 EEG 不會存兩次。
    Features/<編號>.csv 仍照舊保留，實驗分類選錯時刪掉歸檔那份重做即可。
    """
    print(f"{BOLD}{CYAN}== 實驗 =={RESET}\n")
    print("要做哪一種實驗？（Features 自動歸檔，原始資料自動存成 _Original.csv）\n")
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

    # 這次新產生的錄製一定是流水號檔（overall_process 錄的），先濾掉 _Original，
    # 否則上一次實驗搬過來的檔案會混進差集，算錯這次的編號。
    new_files = sorted((f for f in set(list_recordings()) - before if f[:-4].isdigit()),
                       key=lambda x: int(x[:-4]))
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
    archive_stem = f"{prefix}{index}"
    dest = os.path.join(folder, f"{archive_stem}.csv")
    shutil.copy2(features_path, dest)

    # 原始 EEG 改名保存：看到 Model/boring/boring_S1.csv，就知道對應的原始資料是
    # Data/boring_S1_Original.csv，不必回頭查錄製編號。用搬移，Data/ 不留流水號那份。
    # 選單 [8] 清 CSV 時，*_Original.csv 與 Model/ 的歸檔一樣預設受保護。
    original_dest = original_dest_path(archive_stem)
    try:
        shutil.move(os.path.join(CSV_DIR, f"{stem}.csv"), original_dest)
    except OSError as exc:
        # 搬移失敗不影響已歸檔好的 Features，提醒原檔還在原地就好。
        print(f"\n{YELLOW}原始資料改名失敗：{exc}{RESET}")
        print(f"{DIM}Features 已歸檔，原始錄製仍在 Data/{stem}.csv。{RESET}")
        original_dest = None

    rel_dest = os.path.relpath(dest, BASE_DIR).replace(os.sep, "/")
    print(f"\n{GREEN}已歸檔至 {rel_dest}（S{index}）{RESET}")
    if original_dest:
        rel_original = os.path.relpath(original_dest, BASE_DIR).replace(os.sep, "/")
        print(f"{GREEN}原始資料已保存至 {rel_original}{RESET}")
        print(f"{DIM}（Data/{stem}.csv 已改名搬走，不再保留流水號那份）{RESET}")
    print(f"{DIM}中繼結果：Features/{stem}.csv（保留）{RESET}")


def do_predict():
    """選一位受測者，用訓練好的模型比較他的 PDF 與 Learn8 兩段錄製。

    每位受測者各跑一次 predict_model.py，而不是把所有人一次丟進去：
    --baseline auto 會把列出的檔案合併起來估 mu/sigma，混進別人的資料
    就不再是「這個人自己的基準線」，個體差異也就抵銷不掉了。
    """
    print(f"{BOLD}{CYAN}== 模型預測：PDF 閱讀 vs Learn8 學習 =={RESET}\n")

    if not os.path.exists(MODEL_FILE):
        print(f"{RED}找不到模型檔：Model/trained_model.joblib{RESET}")
        if (ask(f"{DIM}要現在用 Model/boring 與 Model/interesting 訓練一個嗎？(y/N)："
                f"{RESET}", default="n") or "n").lower() not in ("y", "yes"):
            print(f"{YELLOW}已取消。可自行執行：python Model/train_model.py{RESET}")
            return
        print()
        run_script(TRAIN_SCRIPT)
        if not os.path.exists(MODEL_FILE):
            print(f"\n{RED}訓練沒有產生模型檔，無法預測。{RESET}")
            return
        print()

    subjects = collect_compare_subjects()
    if not subjects:
        print(f"{YELLOW}Model/PDF_Experiment/ 與 Model/Learn8_Experiment/ 都沒有資料。{RESET}")
        print(f"{DIM}先用選單 [4] 實驗各錄一段 PDF 與 Learn8。{RESET}")
        return

    labels = [label for label, _folder in COMPARE_SETS]
    print(f"{DIM}用 Model/trained_model.joblib 算每秒的 P(有趣)，再取整段平均。"
          f"每位受測者以自己的兩段錄製當基準線，抵銷個體差異。{RESET}\n")
    col = 20
    print("  " + pad("受測者", 9) + "".join(pad(label, col) for label in labels))
    ready = []
    for index in sorted(subjects):
        have = subjects[index]
        cells = []
        for label in labels:
            path = have.get(label)
            cells.append(pad(os.path.basename(path), col) if path
                         else f"{YELLOW}{pad('（缺）', col)}{RESET}")
        complete = len(have) == len(labels)
        if complete:
            ready.append(index)
        mark = " " if complete else f"{YELLOW}!{RESET}"
        print(f" {mark}" + pad(f"S{index}", 9) + "".join(cells))

    if not ready:
        print(f"\n{YELLOW}沒有任何受測者同時有 PDF 與 Learn8 兩段，無法比較。{RESET}")
        return
    if len(ready) < len(subjects):
        print(f"\n{DIM}標 ! 的受測者少了一邊，不列入比較。{RESET}")

    sel = ask("\n請輸入受測者編號（a = 全部逐一比較，Enter 取消）：")
    if not sel:
        print(f"{YELLOW}已取消。{RESET}")
        return
    if sel.lower() in ("a", "all"):
        chosen = ready
    else:
        digits = sel.lstrip("Ss")
        if not digits.isdigit() or int(digits) not in subjects:
            print(f"{RED}無效選擇：{sel}{RESET}")
            return
        if int(digits) not in ready:
            missing = [label for label in labels if label not in subjects[int(digits)]]
            print(f"{YELLOW}S{digits} 少了 {' 與 '.join(missing)}，無法比較。{RESET}")
            return
        chosen = [int(digits)]

    for index in chosen:
        print(f"\n{BOLD}{CYAN}---- 受測者 S{index} ----{RESET}")
        paths = [subjects[index][label] for label in labels]
        run_script(PREDICT_SCRIPT, *paths, "--baseline", "auto")


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
    files = [f for f in os.listdir(FEATURES_DIR) if f.endswith(".csv")] \
        if os.path.isdir(FEATURES_DIR) else []
    if not files:
        print(f"{YELLOW}Features/ 內沒有結果。先跑選單 [6] 對某個錄製檔算 EI + FAA。{RESET}")
        return
    # [6] 對 _Original 檔算出來的結果檔名也不是純數字，一併列出
    files.sort(key=name_order)
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
   [3] 一鍵流程：監控+錄製 → FFT → Features  {DIM}(★推薦){RESET}
   [4] 實驗：選實驗類型 → 一鍵流程 → Features + 原始資料自動歸檔

 {BOLD}分析{RESET}
   [5] 模型預測：選受測者比較 PDF 與 Learn8
   [6] 對錄製檔算 EI + FAA + 眨眼（只輸出 Features/）

 {BOLD}查看 / 管理{RESET}
   [7] 查看數據（訊號摘要 / EI / FAA / FFT）
   [8] 刪除 CSV（Data/、Features/；保留實驗歸檔與 _Original 原始資料）
   [9] 查看 Features 原始內容
   [0] 離開
{DIM}--------------------------------------------{RESET}"""


def main():
    actions = {
        "1": do_scan, "2": do_monitor, "3": do_pipeline, "4": do_experiment,
        "5": do_predict, "6": do_ei, "7": do_view_data, "8": do_clean, "9": do_cat,
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
