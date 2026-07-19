# MUSE 2 原始 EEG 擷取（muselsl + BLE）

在電腦上透過 **Python + muselsl**，直接以 **BLE** 連線 MUSE 2 頭帶，
接收原始腦電（EEG）數據並即時監控。

- EEG 通道（4）：`TP9, AF7, AF8, TP10`
- 取樣率：256 Hz，單位：微伏（µV）
- BLE 後端：`bleak`（Linux 原生 BlueZ / D-Bus，免額外驅動、免藍牙 dongle 專屬程式）
---

## 一、環境安裝

建立虛擬環境 `venv/` 並安裝：`muselsl 2.5.1`、`bleak 3.0.2`、`pylsl 1.10.5`。

```bash
python3 -m venv venv
source venv/bin/activate
./venv/bin/pip install -r requirements.txt
```

---

## 二、使用步驟

**先讓頭帶進入待連線狀態**：開機、確認 LED 閃爍、且**未被手機 App 佔用**

### 1 請用CLI操作（main.py）

開啟選單式控制台，全部功能都在裡面：

```bash
./venv/bin/python main.py
```

選單一覽：

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
  [9] 查看原始數據（選 EI 或 FFT 的 csv，如 cat 直接印出）
  [0] 離開
```


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

---

## 三、每秒離散傅立葉變換（1..128 Hz）
&nbsp;

$$\Large X[k] = \sum_{n=0}^{N-1} x[n] \cdot e^{-i \frac{2\pi kn}{N}}$$

&nbsp;

把錄好的 CSV 做**每秒一次**的傅立葉變換，算出每 1 秒內
**TP9 / AF7 / AF8 / TP10** 四個通道各自在 1 Hz、2 Hz … 128 Hz 上包含多少能量。

原理：MUSE 2 取樣率 256 Hz，**1 秒 = 256 個樣本**；對 256 點做 FFT，頻率解析度剛好
**1 Hz**，涵蓋 0–128 Hz（128 Hz = 奈奎斯特頻率）。能量以單瓣功率頻譜（µV²）表示，
各頻率能量加總 = 該秒訊號能量（Parseval 定理）；FFT 前先減掉視窗平均以去除直流/漂移。

---
## 四、專注度分析

θ  ( 4-8 Hz )：  通常出現在淺睡、極度放鬆或疲勞狀態。

α  ( 8-12 Hz )： 閉眼、放鬆但清醒時最明顯。

β  ( 13-30 Hz )： 處於專注、思考、警覺或緊張狀態時會顯著增加。

頻帶邊界：θ 4–8、α 8–12、β 13–30 Hz，以 1 Hz 整數格計算；重疊的 8 Hz 併入 α、13 Hz 併入 β，避免重複計算。

&nbsp;

$$\Large
EI = \frac{\beta_{AF7} + \beta_{AF8}}{\alpha_{TP9} + \alpha_{TP10} + \theta_{AF7} + \theta_{AF8}}
$$

&nbsp;

分子（β）在專注時上升、分母（α+θ）在放鬆時上升，所以 **EI 越大代表越投入專注**。

&nbsp;

### 每秒 EI + 10 秒滑動平均（engagement.py）


**每秒一個 EI**：每 1 秒獨立套一次上面的公式 → 得到 $EI_1, EI_2, \dots$。

**長度 10 的佇列滑動平均**：用每過 1 秒給一個「以過去 10 秒為基準」的平滑專注度，方便對比使用者當下的操作行為。

---
## 五、情緒與趨近動機（Frontal Alpha Asymmetry, FAA）

原理： 前額葉的不對稱性與情緒極度相關。左前額葉活躍代表「趨近、積極、感興趣」，右前額葉活躍代表「逃避、挫折、無聊」。

&nbsp;

$$\Large FAA = \ln(\alpha_{AF8}) - \ln(\alpha_{AF7})$$

&nbsp;

應用： 數值為正，代表使用者覺得有趣、有成就感；數值為負，代表使用者感到挫折或想放棄。

---

## 六、檔案說明

| 檔案 | 用途 |
|------|------|
| `main.py`            | **互動式控制台**：選單操作全部功能 + 查看數據（最推薦入口）|
| `Overall_process.py` | 一鍵：即時監控+錄製 → FFT → EI → FAA（單一指令跑完整流程）|
| `list_devices.py`    | 掃描附近 MUSE 裝置、取得 BLE address |
| `monitor_raw.py`     | 直接 BLE 連線 + 即時監控原始 EEG |
| `record_csv.py`      | 直接 BLE 連線、把原始 EEG 錄成 CSV |
| `fft_energy.py`      | 對錄好的 CSV 做每秒 FFT，算 1..128 Hz 各頻率能量 |
| `engagement.py`      | 每秒算 NASA 專注度指數（EI）+ 10 秒滑動平均 |
| `faa.py`             | 每秒算前額 alpha 不對稱 FAA + 10 秒滑動平均，輸出 FAA/ |
| `clean_csv.py`       | 刪除專案內所有 .csv（Data、FFT、EI、FAA），保留資料夾與 .gitkeep |
| `requirements.txt`| 相依套件 |
