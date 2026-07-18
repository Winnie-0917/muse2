# MUSE 2 原始 EEG 擷取（muselsl + BLE）

在電腦上透過 **Python + muselsl**，直接以 **BLE** 連線 MUSE 2 頭帶，
接收原始腦電（EEG）數據並即時監控。

- EEG 通道（5）：`TP9, AF7, AF8, TP10`
- 取樣率：256 Hz，單位：微伏（µV）
- BLE 後端：`bleak`（Linux 原生 BlueZ / D-Bus，免額外驅動、免藍牙 dongle 專屬程式）
---

## 一、環境安裝

已建立虛擬環境 `venv/` 並安裝：`muselsl 2.5.1`、`bleak 3.0.2`、`pylsl 1.10.5`。

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

---

## 二、使用步驟

**先讓頭帶進入待連線狀態**：開機、確認 LED 閃爍、且**未被手機 App 佔用**

### 1 掃描裝置，取得 address

```bash
./venv/bin/python list_devices.py
```

會列出類似 `Muse-35CA  address = 00:55:DA:B6:35:CA`。

### 2 即時監控原始 EEG（主要功能）

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


### 3 錄製成 CSV（離線分析用）

```bash
./venv/bin/python record_csv.py --address 00:55:DA:B6:35:CA --seconds 60
```
---

## 三、離線分析：每秒 FFT 能量（1..128 Hz）

把錄好的 CSV 做**每秒一次**的傅立葉變換，算出每 1 秒內
**TP9 / AF7 / AF8 / TP10** 四個通道各自在 1 Hz、2 Hz … 128 Hz 上包含多少能量。

```bash
./venv/bin/python fft_energy.py               # 分析 csv/ 內編號最大的檔
./venv/bin/python fft_energy.py csv/1.csv      # 指定輸入檔
./venv/bin/python fft_energy.py csv/1.csv --fs 256 --out fft
```

原理：MUSE 2 取樣率 256 Hz，**1 秒 = 256 個樣本**；對 256 點做 FFT，頻率解析度剛好
**1 Hz**，涵蓋 0–128 Hz（128 Hz = 奈奎斯特頻率）。能量以單瓣功率頻譜（µV²）表示，
各頻率能量加總 = 該秒訊號能量（Parseval 定理）；FFT 前先減掉視窗平均以去除直流/漂移。



> 小提醒：若某通道能量集中在 **60 Hz**，通常是電源干擾（台灣市電 60 Hz），
> 代表該電極接觸不良；正常 EEG 能量多集中在低頻（delta/theta/alpha 等）。

---


其他 LSL 消費端（如 MNE、OpenViBE、自寫 pylsl 程式）也能同時接同一條串流。

---


## 檔案說明

| 檔案 | 用途 |
|------|------|
| `list_devices.py` | 掃描附近 MUSE 裝置、取得 BLE address |
| `monitor_raw.py`  | **直接 BLE 連線 + 即時監控原始 EEG**（主程式）|
| `record_csv.py`   | 直接 BLE 連線、把原始 EEG 錄成 CSV |
| `fft_energy.py`   | 對錄好的 CSV 做每秒 FFT，算 1..128 Hz 各頻率能量 |
| `requirements.txt`| 相依套件 |
| `venv/`           | Python 虛擬環境 |
