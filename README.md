# MUSE 2 原始 EEG 擷取（muselsl + BLE）

在電腦上透過 **Python + muselsl**，直接以 **BLE** 連線 MUSE 2 頭帶，
接收原始腦電（EEG）數據並即時監控。

- EEG 通道（5）：`TP9, AF7, AF8, TP10, Right AUX`
- 取樣率：256 Hz，單位：微伏（µV）
- BLE 後端：`bleak`（Linux 原生 BlueZ / D-Bus，免額外驅動、免藍牙 dongle 專屬程式）

> 已在本機實測連線成功：裝置 `Muse-35CA` (`00:55:DA:B6:35:CA`)。
> 註：MUSE 2 的 `Right AUX` 沒有外接電極，數值恆為 0，屬正常現象。

---

## 一、環境安裝（已完成）

已建立虛擬環境 `venv/` 並安裝：`muselsl 2.5.1`、`bleak 3.0.2`、`pylsl 1.10.5`。
若要在別台機器重建：

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

> 因系統為 externally-managed（PEP 668），務必用虛擬環境，不要直接 `pip install`。

---

## 二、使用步驟

**先讓頭帶進入待連線狀態**：開機、確認 LED 閃爍、且**未被手機 App 佔用**
（同一時間只能有一個裝置連 MUSE）。

### 1) 掃描裝置，取得 address

```bash
./venv/bin/python list_devices.py
```

會列出類似 `Muse-35CA  address = 00:55:DA:B6:35:CA`。

### 2) 即時監控原始 EEG（主要功能）

```bash
# 自動掃描並連第一台
./venv/bin/python monitor_raw.py

# 或指定 address（較快、較穩）
./venv/bin/python monitor_raw.py --address 00:55:DA:B6:35:CA
```

終端機會即時顯示每個通道的**波形（sparkline）**、最新值、RMS、峰對峰值、
訊號品質，以及實際取樣率。按 `Ctrl+C` 結束。

畫面範例：

```
MUSE 2 原始 EEG 即時監控
裝置 Muse-35CA  [00:55:DA:B6:35:CA]   取樣 256Hz  單位 µV
● 串流中   實際取樣率  203.0 Hz   封包 171   樣本 2052   已執行  10.1s

通道   波形（各自正規化，左舊右新）              最新      RMS       峰峰     品質
TP9   ▃▅▄▄▆▁▄▆▆▅▄▇▂▆▅▅█▃▆▆▃▁▁▃▅▅▄▄▂▂▅▃…      -12.3     18.4     95.0  良好
AF7   ▅▅▄▂▅▅▆▃▃▅▅▅█▄▆▅▆▆▅▄▆▅▃▄▇█▄▁▄▇▂▂▄…      +12.5     19.8    110.1  良好
...
```

預設只顯示 4 個真正的 EEG 通道（TP9/AF7/AF8/TP10）。`Right AUX` 因為沒有外接電極、
數值恆為 0，預設會**隱藏**；若你有自行接外部電極到 AUX，加 `--aux` 即可顯示。

常用參數：`--aux`（顯示 AUX）、`--fps 20`（刷新率）、`--window 3`（統計/波形視窗秒數）、`--retries 5`。

### 3) 錄製成 CSV（離線分析用）

```bash
./venv/bin/python record_csv.py --address 00:55:DA:B6:35:CA --seconds 60 --out eeg.csv
```

輸出每列為一個時間樣本：`timestamp, TP9, AF7, AF8, TP10, AUX`（µV）。

---

## 三、進階：LSL 串流 + 圖形化波形

muselsl 內建 LSL（Lab Streaming Layer）架構，可搭配內建的 GUI 波形檢視器。
需要**兩個終端機**：

```bash
# 終端機 A：BLE -> LSL 串流
./venv/bin/muselsl stream --address 00:55:DA:B6:35:CA

# 終端機 B：從 LSL 讀取並畫圖（需要 matplotlib）
./venv/bin/pip install matplotlib
./venv/bin/muselsl view
```

其他 LSL 消費端（如 MNE、OpenViBE、自寫 pylsl 程式）也能同時接同一條串流。

---

## 四、判讀與排錯

**訊號品質**（本專案依 RMS 粗估）：
- 戴上頭帶、額頭/耳後電極接觸良好時，RMS 約 **10–45 µV** → 顯示「良好」。
- 未配戴 / 電極懸空時，數值會飄到數百 µV（正常，代表沒有貼合皮膚）。
- 想改善接觸：把額頭電極壓貼、稍微沾濕耳後 TP9/TP10 電極、保持不動、避免說話眨眼。

**連不上 / 找不到裝置**：
- 確認頭帶 LED 在**閃爍**（待連線），而不是恆亮（已被別的裝置連走）。
- 關掉手機的 Muse App、或其他正在連的程式。
- `bluetoothctl show` 確認藍牙 `Powered: yes`。
- address 會偶爾改變，連不上時重跑 `list_devices.py`。

**實際取樣率低於 256 Hz**：BLE 封包偶有遺失，屬正常；靠近電腦、減少 2.4GHz 干擾
（Wi-Fi、其他藍牙）可改善。此行為與 muselsl 內建 `stream` 相同。

---

## 檔案說明

| 檔案 | 用途 |
|------|------|
| `list_devices.py` | 掃描附近 MUSE 裝置、取得 BLE address |
| `monitor_raw.py`  | **直接 BLE 連線 + 即時監控原始 EEG**（主程式）|
| `record_csv.py`   | 直接 BLE 連線、把原始 EEG 錄成 CSV |
| `requirements.txt`| 相依套件 |
| `venv/`           | Python 虛擬環境 |
