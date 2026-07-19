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

### 互動式控制台（`main.py`）★最推薦

不想記指令的話，直接開這個選單式介面，所有功能一鍵操作：

```bash
./venv/bin/python main.py
```

選單包含：掃描/選擇裝置、即時監控、錄製、一鍵流程（監控+錄製→FFT→EI）、
單獨做 FFT / EI，以及**查看數據**（列出錄製檔、訊號摘要、EI 專注度結果、FFT 主頻與頻帶能量）、
**查看原始數據**（如 `cat` 直接印出錄製檔的完整內容）、刪除所有 CSV。
底層仍是呼叫下面各支腳本，兩種用法可混用。

### 0 一鍵完成全部（`Overall_process.py`）★推薦

```bash
./venv/bin/python Overall_process.py --address 00:55:DA:B6:35:CA --seconds 60
```

一條龍：**即時監控 + 錄製到 `Data/` → 停止後自動跑 FFT（`FFT/`）→ 再跑 EI（`EI/`）**。
`--seconds` 到時自動停，或隨時按 `Ctrl+C`。想看「穩定 EI」請至少錄 10 秒以上。
其他：`--no-analyze`（只監控+錄製）、`--aux`（畫面顯示 AUX）、省略 `--address` 則自動掃描。

若想分開手動操作，見下面步驟 1~3 與各分析章節。

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

## 三、離線分析：每秒離散傅立葉變換（1..128 Hz）

$$X[k] = \sum_{n=0}^{N-1} x[n] \cdot e^{-i \frac{2\pi kn}{N}}$$

把錄好的 CSV 做**每秒一次**的傅立葉變換，算出每 1 秒內
**TP9 / AF7 / AF8 / TP10** 四個通道各自在 1 Hz、2 Hz … 128 Hz 上包含多少能量。

```bash
./venv/bin/python fft_energy.py               # 分析 Data/ 內編號最大的檔
./venv/bin/python fft_energy.py Data/1.csv      # 指定輸入檔
./venv/bin/python fft_energy.py Data/1.csv --fs 256 --out FFT
```

原理：MUSE 2 取樣率 256 Hz，**1 秒 = 256 個樣本**；對 256 點做 FFT，頻率解析度剛好
**1 Hz**，涵蓋 0–128 Hz（128 Hz = 奈奎斯特頻率）。能量以單瓣功率頻譜（µV²）表示，
各頻率能量加總 = 該秒訊號能量（Parseval 定理）；FFT 前先減掉視窗平均以去除直流/漂移。

---
## 專注度分析

( $\theta$, 4-8 Hz )： 通常出現在淺睡、極度放鬆或疲勞狀態。

( $\alpha$, 8-12 Hz )： 閉眼、放鬆但清醒時最明顯。

( $\beta$, 13-30 Hz )： 處於專注、思考、警覺或緊張狀態時會顯著增加。

$$
EI = \frac{\beta_{AF7} + \beta_{AF8}}{\alpha_{TP9} + \alpha_{TP10} + \theta_{AF7} + \theta_{AF8}}
$$

分子（β）在專注時上升、分母（α+θ）在放鬆時上升，所以 **EI 越大代表越投入專注**。

### 每秒 EI + 10 秒滑動平均（engagement.py）

```bash
./venv/bin/python engagement.py                 # 分析 Data/ 內編號最大的檔
./venv/bin/python engagement.py Data/1.csv        # 指定輸入檔
./venv/bin/python engagement.py Data/1.csv --window 10
```

做法（三步驟）：

1. **每秒 FFT**：沿用 `fft_energy.py`，把每 1 秒（256 樣本）做 FFT，算出每個通道在
   θ / α / β 頻帶的能量（µV²）。
2. **每秒一個 EI**：每 1 秒獨立套一次上面的公式 → 得到 $EI_1, EI_2, \dots$。
3. **長度 10 的佇列滑動平均**：用 `deque(maxlen=10)`，收滿第 1~10 秒才輸出第 1 個穩定分數
   `mean(EI_1..EI_10)`；第 11 秒進來自動踢掉最舊的第 1 秒 → 輸出 `mean(EI_2..EI_11)`；
   依此類推。每過 1 秒給一個「以過去 10 秒為基準」的平滑專注度，方便對比使用者當下的操作行為。

輸出：終端機印出每秒 EI 與穩定分數，並存到 `EI/<編號>.csv`
（欄位 `second, EI, EI_smooth10`；此檔已被 `.gitignore` 忽略、不上傳）。

> 頻帶邊界：θ 4–8、α 8–12、β 13–30 Hz，以 1 Hz 整數格計算；重疊的 8 Hz 併入 α、13 Hz 併入 β，避免重複計算。


## 檔案說明

| 檔案 | 用途 |
|------|------|
| `main.py`          | **互動式控制台**：選單操作全部功能 + 查看數據（最推薦入口）|
| `Overall_process.py`         | 一鍵：即時監控+錄製 → FFT → EI（單一指令跑完整流程）|
| `list_devices.py` | 掃描附近 MUSE 裝置、取得 BLE address |
| `monitor_raw.py`  | **直接 BLE 連線 + 即時監控原始 EEG**（主程式）|
| `record_csv.py`   | 直接 BLE 連線、把原始 EEG 錄成 CSV |
| `fft_energy.py`   | 對錄好的 CSV 做每秒 FFT，算 1..128 Hz 各頻率能量 |
| `engagement.py`   | 每秒算 NASA 專注度指數（EI）+ 10 秒滑動平均，輸出平滑專注度 |
| `clean_csv.py`    | 刪除專案內所有 .csv（Data、FFT、EI），保留資料夾與 .gitkeep（`--dry-run` 可預覽）|
| `requirements.txt`| 相依套件 |
| `venv/`           | Python 虛擬環境 |
