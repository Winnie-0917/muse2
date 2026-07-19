# MUSE 2 原始 EEG 擷取與專注度分析（muselsl + BLE）

在電腦上透過 **Python + muselsl**，直接以 **BLE** 連線 MUSE 2 頭帶，接收原始腦電（EEG）
數據、即時監控，並可離線做每秒 FFT 頻譜與 NASA 專注度指數（EI）分析。

- **有效 EEG 通道（4）**：`TP9, AF7, AF8, TP10`
  （硬體上還有第 5 個 `Right AUX`，但沒有外接電極、數值恆為 0，預設不顯示也不錄製）
- **取樣率**：256 Hz，單位：微伏（µV）
- **BLE 後端**：`bleak`（Linux 原生 BlueZ / D-Bus，免額外驅動、免藍牙 dongle 專屬程式）

---

## 資料夾結構

| 資料夾 | 內容 | 產生者 |
|--------|------|--------|
| `Data/` | 原始 EEG 錄製檔 `1.csv, 2.csv …`（欄：`timestamp, TP9, AF7, AF8, TP10`）| `record_csv.py` / `Overall_process.py` |
| `FFT/`  | 每秒 FFT 能量，依通道分子資料夾 `FFT/<通道>/<編號>.csv` | `fft_energy.py` |
| `EI/`   | 每秒 EI + 10 秒滑動平均 `EI/<編號>.csv` | `engagement.py` |

> 這三個資料夾內的 `*.csv` 都被 `.gitignore` 忽略、不上傳；只保留空資料夾（`.gitkeep`）。
> 檔案一律用流水號命名（`1.csv, 2.csv …`，自動取下一個未使用編號）。

---

## 一、環境安裝

已建立虛擬環境 `venv/` 並安裝：`muselsl 2.5.1`、`bleak 3.0.2`、`pylsl 1.10.5`。
若要在別台機器重建：

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

> 系統為 externally-managed（PEP 668），務必用虛擬環境，不要直接 `pip install`。

---

## 二、使用步驟

**先讓頭帶進入待連線狀態**：開機、確認 LED 閃爍、且**未被手機 App 佔用**
（同一時間只能有一個裝置連 MUSE）。

### 互動式控制台（`main.py`）★最推薦

不想記指令的話，直接開這個選單式介面，所有功能一鍵操作：

```bash
./venv/bin/python main.py
```

選單功能：

```
擷取 / 監控
  [1] 掃描並選擇 MUSE 裝置
  [2] 即時監控原始 EEG
  [3] 錄製資料到 Data/
  [4] 一鍵流程：監控+錄製 → FFT → EI   (★推薦)
分析
  [5] 對錄製檔做每秒 FFT（輸出 FFT/）
  [6] 對錄製檔算專注度 EI（輸出 EI/）
查看 / 管理
  [7] 查看數據（訊號摘要 / EI 結果 / FFT 主頻與頻帶能量）
  [8] 刪除專案內所有 CSV
  [9] 查看原始數據（選 EI 或 FFT 的 csv，如 cat 直接印出完整內容）
  [0] 離開
```

底層仍是呼叫下面各支腳本，兩種用法可混用。

### 一鍵流程（`Overall_process.py`）

單一指令跑完整流程，不進選單：

```bash
./venv/bin/python Overall_process.py --address 00:55:DA:B6:35:CA --seconds 60
```

一條龍：**即時監控 + 錄製到 `Data/` → 停止後自動跑 FFT（`FFT/`）→ 再跑 EI（`EI/`）**。
`--seconds` 到時自動停，或隨時按 `Ctrl+C`。想看「穩定 EI」請至少錄 10 秒以上。
其他：`--no-analyze`（只監控+錄製）、`--aux`（畫面顯示 AUX）、省略 `--address` 則自動掃描。

### 分開手動操作

#### 1) 掃描裝置，取得 address

```bash
./venv/bin/python list_devices.py
```

會列出類似 `Muse-35CA  address = 00:55:DA:B6:35:CA`。

#### 2) 即時監控原始 EEG

```bash
./venv/bin/python monitor_raw.py                        # 自動掃描並連第一台
./venv/bin/python monitor_raw.py --address 00:55:DA:B6:35:CA   # 指定 address（較快）
```

終端機即時顯示每個通道的**波形（sparkline）**、最新值、RMS、峰對峰值、訊號品質，
以及實際取樣率。按 `Ctrl+C` 結束。畫面範例：

```
MUSE 2 原始 EEG 即時監控
裝置 Muse-35CA  [00:55:DA:B6:35:CA]   取樣 256Hz  單位 µV
● 串流中   實際取樣率  203.0 Hz   封包 171   樣本 2052   已執行  10.1s

通道   波形（各自正規化，左舊右新）              最新      RMS       峰峰     品質
TP9   ▃▅▄▄▆▁▄▆▆▅▄▇▂▆▅▅█▃▆▆▃▁▁▃▅▅▄▄▂▂▅▃…      -12.3     18.4     95.0  良好
AF7   ▅▅▄▂▅▅▆▃▃▅▅▅█▄▆▅▆▆▅▄▆▅▃▄▇█▄▁▄▇▂▂▄…      +12.5     19.8    110.1  良好
...
```

#### 3) 錄製成 CSV

```bash
./venv/bin/python record_csv.py --address 00:55:DA:B6:35:CA --seconds 60
```

存成 `Data/<下一個編號>.csv`，每列為一個時間樣本：`timestamp, TP9, AF7, AF8, TP10`（µV）。

---

## 三、離線分析：每秒離散傅立葉變換（1..128 Hz）

$$X[k] = \sum_{n=0}^{N-1} x[n] \cdot e^{-i \frac{2\pi kn}{N}}$$

把錄好的 CSV 做**每秒一次**的傅立葉變換，算出每 1 秒內
**TP9 / AF7 / AF8 / TP10** 四個通道各自在 1 Hz、2 Hz … 128 Hz 上包含多少能量。

```bash
./venv/bin/python fft_energy.py               # 分析 Data/ 內編號最大的檔
./venv/bin/python fft_energy.py Data/1.csv     # 指定輸入檔
./venv/bin/python fft_energy.py Data/1.csv --fs 256 --out FFT
```

原理：MUSE 2 取樣率 256 Hz，**1 秒 = 256 個樣本**；對 256 點做 FFT，頻率解析度剛好
**1 Hz**，涵蓋 0–128 Hz（128 Hz = 奈奎斯特頻率）。能量以單瓣功率頻譜（µV²）表示，
各頻率能量加總 = 該秒訊號能量（Parseval 定理）；FFT 前先減掉視窗平均以去除直流/漂移。

---

## 四、專注度分析（NASA Engagement Index）

- ( $\theta$, 4–8 Hz )：通常出現在淺睡、極度放鬆或疲勞狀態。
- ( $\alpha$, 8–12 Hz )：閉眼、放鬆但清醒時最明顯。
- ( $\beta$, 13–30 Hz )：處於專注、思考、警覺或緊張狀態時會顯著增加。

$$
EI = \frac{\beta_{AF7} + \beta_{AF8}}{\alpha_{TP9} + \alpha_{TP10} + \theta_{AF7} + \theta_{AF8}}
$$

分子（β）在專注時上升、分母（α+θ）在放鬆時上升，所以 **EI 越大代表越投入專注**。

### 每秒 EI + 10 秒滑動平均（`engagement.py`）

```bash
./venv/bin/python engagement.py                 # 分析 Data/ 內編號最大的檔
./venv/bin/python engagement.py Data/1.csv       # 指定輸入檔
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
（欄位 `second, EI, EI_smooth10`）。

> 頻帶邊界：θ 4–8、α 8–12、β 13–30 Hz，以 1 Hz 整數格計算；重疊的 8 Hz 併入 α、
> 13 Hz 併入 β，避免重複計算。

---

## 五、判讀與排錯

**訊號品質**（依 RMS 粗估）：戴上頭帶、電極接觸良好時 RMS 約 **10–45 µV**（顯示「良好」）；
未配戴 / 電極懸空時會飄到數百 µV（正常，代表沒貼合皮膚）。改善接觸：額頭電極壓貼、
稍微沾濕耳後 TP9/TP10 電極、保持不動、避免說話眨眼。

**連不上 / 找不到裝置**：確認頭帶 LED 在**閃爍**（未被別的裝置連走）、關掉手機 Muse App、
`bluetoothctl show` 確認藍牙開啟；address 偶爾會變，連不上就重跑 `list_devices.py`。

**實際取樣率低於 256 Hz**：BLE 封包偶有遺失，屬正常；靠近電腦、減少 2.4GHz 干擾可改善。

---

## 檔案說明

| 檔案 | 用途 |
|------|------|
| `main.py`            | **互動式控制台**：選單操作全部功能 + 查看數據（最推薦入口）|
| `Overall_process.py` | 一鍵：即時監控+錄製 → FFT → EI（單一指令跑完整流程）|
| `list_devices.py`    | 掃描附近 MUSE 裝置、取得 BLE address |
| `monitor_raw.py`     | 直接 BLE 連線 + 即時監控原始 EEG |
| `record_csv.py`      | 直接 BLE 連線、把原始 EEG 錄成 `Data/<編號>.csv` |
| `fft_energy.py`      | 對錄好的 CSV 做每秒 FFT，算 1..128 Hz 各頻率能量 |
| `engagement.py`      | 每秒算 NASA 專注度指數（EI）+ 10 秒滑動平均 |
| `clean_csv.py`       | 刪除專案內所有 .csv（Data、FFT、EI），保留資料夾與 .gitkeep（`--dry-run` 可預覽）|
| `requirements.txt`   | 相依套件 |
| `venv/`              | Python 虛擬環境 |
