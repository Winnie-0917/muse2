# MUSE 2 原始 EEG 擷取（muselsl + BLE）

在電腦上透過 **Python + muselsl**，直接以 **BLE** 連線 MUSE 2 頭帶，
接收原始腦電（EEG）數據並即時監控。

- EEG 通道（4）：`TP9, AF7, AF8, TP10`
- 取樣率：256 Hz，單位：微伏（µV）
- BLE 後端：`bleak`（Linux 原生 BlueZ / D-Bus，免額外驅動、免藍牙 dongle 專屬程式）
---

## 一、環境安裝

建立虛擬環境 `venv/`，並以**可編輯模式**安裝本套件（相依套件 `muselsl`、`bleak`、`numpy` 都寫在 `pyproject.toml`，會自動一併安裝）：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

---

## 二、使用步驟

**先讓頭帶進入待連線狀態**：開機、確認 LED 閃爍、且**未被手機 App 佔用**

### 1 請用CLI操作（互動式控制台）

開啟選單式控制台，全部功能都在裡面：

```bash
python -m signal_monitor      # 或安裝後直接執行 signal-monitor
```

選單一覽：

```
擷取 / 監控
  [1] 掃描並選擇 MUSE 裝置
  [2] 即時監控原始 EEG
  [3] 錄製資料到 Data/
  [4] 一鍵流程：監控+錄製 → FFT → EI   (★推薦)
分析
  [5] 對錄製檔做每秒 FFT（只顯示摘要，不存檔）
  [6] 對錄製檔算 EI + FAA + 眨眼（只輸出 Features/）
查看 / 管理
  [7] 查看數據（訊號摘要 / EI / FAA / FFT 主頻與頻帶能量）
  [8] 刪除 CSV（Data/、Features/；保留 Model/ 訓練資料）
  [9] 查看原始數據（選 Features 或 FFT 的 csv，如 cat 直接印出）
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

## 六、眨眼每分鐘頻率（Blinks Per Minute, BPM）

眨眼會在額電極（AF7/AF8）造成一個持續約 100–400 ms 的大幅偏轉，是 EEG 裡最好認的偽訊之一。
一般清醒狀態約 **15–20 次/分**，專注閱讀時會下降，疲勞或分心時上升。

### 1. 帶通濾波（0.5–5 Hz）

眨眼是慢波，能量集中在 0.5–5 Hz。先用 2 階 Butterworth 零相位帶通濾掉直流漂移
與 alpha、肌電等高頻成分：

&nbsp;
$$\Large y[n] = \mathrm{BPF}_{0.5\text{–}5\,\mathrm{Hz}}\big(x[n]\big)$$
&nbsp;

濾波對**整段錄製一次做完**，不是逐秒做 —— 逐秒濾波會在每個 1 秒邊界產生假訊號，
也會把跨越邊界的眨眼切成兩半。

### 2. 自適應門檻（MAD）

每個人、每次配戴的電極接觸阻抗都不同，訊號振幅可以差好幾倍，所以門檻必須跟著訊號
自己的尺度走。用**中位數絕對離差**（MAD）估穩健標準差：

&nbsp;
$$\Large \sigma_{\text{robust}} = 1.4826 \times \mathrm{median}\big(\vert{}y[n] - \tilde{y}\vert{}\big)$$
&nbsp;
$$\Large \text{threshold} = \max\big(k \cdot \sigma_{\text{robust}},\ \text{floor}\big), \qquad k = 3.0$$
&nbsp;

MAD 對離群值穩健：少數幾個大眨眼不會像標準差那樣把門檻自己撐高。
`floor`（預設 8 µV）防止訊號極安靜（例如電極脫落）時把雜訊當成眨眼。

### 3. 最小距離限制（不應期）

為了防止「慢眨眼被誤判成 2 次」，加入冷卻距離 $D = 0.3 \times f_s \approx 77$ 個採樣點：

&nbsp;
$$\Large \forall n, m \in P \ (n \neq m), \quad \vert{}n - m\vert{} \ge D$$
&nbsp;

眨眼極性取決於參考電極接法，程式對正負兩個方向都找波峰，再依不應期合併 ——
同一次眨眼的正瓣與負瓣只會算一次。

### 4. 波寬驗證

只保留半高寬落在 **60–500 ms** 的波峰。太窄的是尖波雜訊，太寬的是體動或漂移。

### 5. 滑動窗口 BPM

&nbsp;
$$\Large \mathrm{BPM}_t = \Big(\sum_{i=t-w+1}^{t} \text{blinks}_i\Big) \times \frac{60}{w}, \qquad w = 10$$
&nbsp;

> **注意**：$w = 10$ 秒時 BPM 的解析度是 $60/10 = 6$，數值只會是 0, 6, 12, 18…
> 要平滑一點的曲線可以用 `--window 30`（解析度 2）或 `--window 60`（解析度 1），
> 但輸出欄位名會跟著變成 `BPM_smooth30` / `BPM_smooth60`。

### 用法

```bash
python -m signal_monitor.analysis.blink Temp/1.csv            # 逐秒列出
python -m signal_monitor.analysis.blink Temp/1.csv --quiet    # 只看摘要
python -m signal_monitor.analysis.blink Temp/1.csv --k 3.5    # 調門檻（越大越保守）
python -m signal_monitor.analysis.blink Temp/1.csv --channel AF7+AF8   # 雙通道平均
python -m signal_monitor.analysis.blink Temp/1.csv --out blink.csv
```

`--k` 是主要的調整旋鈕：抓太多就調大、抓太少就調小。
偵測結果若落在 5–40 次/分之外，程式會出聲提醒檢查電極接觸。
`--threshold <µV>` 可強制改用固定門檻（不建議，除非你確定訊號尺度）。

---

## 七、模型訓練

### 實驗設計

**「絕對無聊」任務（標籤 $y=0$）**：讓受測者盯著螢幕上一個緩慢移動的白點長達 5 分鐘。

**「絕對不無聊」任務（標籤 $y=1$）**：讓受測者玩一款極度需要反應速度的遊戲
（如網頁版的節奏遊戲或俄羅斯方塊）5 分鐘。

錄製結果放進 `Model/boring/`（$y=0$）與 `Model/interesting/`（$y=1$）。
**檔名數字就是受測者編號**，兩個資料夾裡同號的檔案是同一個人的兩段錄製：

$$\Large \text{受測者} k \;=\; \Big(\texttt{boring/}k\texttt{.csv},\; \texttt{interesting/}k\texttt{.csv}\Big)$$

也就是 S1 = (`boring/1.csv`, `interesting/1.csv`)、S2 = (`boring/2.csv`, `interesting/2.csv`)，依此類推。
載入時會檢查配對，某位受測者少了一邊會出聲警告。

### 特徵與 Z-score 標準化

每秒取三個特徵：`EI_smooth10`、`FAA_smooth10`、`BPM_smooth10`（前 9 秒滑動平均尚未成形，直接丟棄）。
三者量綱差很多（EI 約 0.4、BPM 可到 18），先做 **Z-score 標準化**把每個特徵各自壓到同一尺度：

&nbsp;
$$\Large z_j = \frac{x_j - \mu_j}{\sigma_j}$$
&nbsp;

$\mu_j, \sigma_j$ **只能由訓練資料估出來**，套用到驗證/預測資料時沿用同一組，否則就是資料洩漏。

### 分類器：Logistic Regression

標準化後丟進邏輯迴歸，直接輸出「有趣」的機率：

&nbsp;
$$\Large P(\text{interesting} \mid \mathbf{z}) = \sigma\!\left(b + \sum_j w_j z_j\right), \qquad \sigma(t) = \frac{1}{1 + e^{-t}}$$
&nbsp;

> 這裡不用高斯樸素貝氏，是因為高斯 NB 對每個特徵各自估 $\mu, \sigma$，
> 做 Z-score 這種 affine 變換後輸出機率**完全不變** —— 標準化等於白做。
> 邏輯迴歸則會受尺度影響（正則化與收斂），標準化才有意義。

### 訓練

```bash
python Model/train_model.py                # 全域 Z-score（預設）
python Model/train_model.py --per-subject  # 每位受測者用自己的 mu/sigma
python Model/train_model.py --features EI  # 只用單一特徵
```

驗證採 **Leave-One-Subject-Out**：留一位受測者當測試集，其餘訓練。
不能隨機切 row —— 相鄰秒高度相關，同一段錄製同時出現在訓練與測試會嚴重高估準確率。

### 預測

```bash
python Model/predict_model.py Features/1.csv                              # 單段
python Model/predict_model.py Features/pdf.csv Features/learn8.csv        # 兩段對比
python Model/predict_model.py Features/pdf.csv Features/learn8.csv --baseline auto
python Model/predict_model.py Features/1.csv --per-second                 # 逐秒機率
```

把使用者操作 PDF 與操作 Learn8 時的 `[EI, FAA, Blink]` 丟進模型，輸出每段的 P(有趣)。

`--baseline auto` 會把列出的所有 CSV 合併當這位受測者的基準線來算 $\mu, \sigma$，
抵銷個體差異（有人 EI 天生就高）。同一人做兩段時建議加上；
**單一檔案不要用** —— 那會把該段平均壓成 0，訊號就沒了。


## 八、專案結構與檔案說明

程式碼統一放在 `src/signal_monitor/` 套件內，輸出資料夾只剩 `Data/`（原始錄製）
與 `Features/`（分析結果），都在專案根目錄。

**FFT、EI、FAA 都不再落檔。** 三者都只是中間產物：FFT 是 EI/FAA 的輸入，
EI/FAA 則直接併進 Features。存起來又大又沒人讀，要看的時候即時重算就好
（256 點 FFT 很快）。選單 [6] 直接算完就併進
`Features/<編號>.csv`（欄位：`second, EI, EI_smooth10, FAA, FAA_smooth10, blinks, BPM_smooth10`），
那份檔案就是模型訓練與預測的輸入。

```
muse2/
├── pyproject.toml            # 專案設定與相依套件（取代 requirements.txt）
├── README.md
├── Data/                     # 原始 EEG 錄製（.csv 由 .gitignore 忽略）
├── Features/                 # EI / FAA / 眨眼 合併輸出，也是模型的輸入
├── Model/                    # 無聊/有趣 分類模型
│   ├── boring/k.csv          # 受測者 k 的絕對無聊任務錄製（y=0）
│   ├── interesting/k.csv     # 受測者 k 的絕對不無聊任務錄製（y=1）
│   ├── model_utils.py        # 資料載入、受測者分組、Z-score 標準化
│   ├── train_model.py        # 訓練 + Leave-One-Subject-Out 交叉驗證
│   ├── predict_model.py      # 輸出一段錄製的 P(有趣)
│   └── trained_model.joblib  # 訓練好的模型（scaler + 分類器）
└── src/signal_monitor/
    ├── __main__.py           # python -m signal_monitor → 開啟控制台
    ├── cli.py                # 互動式控制台（原 main.py）
    ├── overall_process.py    # 一鍵流程（原 Overall_process.py）
    ├── paths.py              # 統一計算專案根目錄
    ├── hardware/             # 設備操作
    │   ├── list_devices.py   # 掃描附近 MUSE 裝置、取得 BLE address
    │   └── monitor_raw.py    # 直接 BLE 連線 + 即時監控原始 EEG
    ├── data_utils/           # 資料處理
    │   ├── record_csv.py     # 直接 BLE 連線、把原始 EEG 錄成 CSV
    │   └── clean_csv.py      # 刪除 .csv，但保留 Model/ 訓練資料與 .gitkeep
    └── analysis/             # 演算法與分析
        ├── fft_energy.py     # 每秒 FFT（不落檔，結果供 EI/FAA 使用）
        ├── engagement.py     # 每秒 NASA 專注度指數 EI + 10 秒滑動平均
        └── faa.py            # 每秒前額 alpha 不對稱 FAA + 10 秒滑動平均
```

| 模組 | 用途 |
|------|------|
| `signal_monitor` | **互動式控制台**：選單操作全部功能 + 查看數據（最推薦入口）|
| `signal_monitor.overall_process` | 一鍵：即時監控+錄製 → FFT → EI → FAA（單一指令跑完整流程）|
| `signal_monitor.hardware.list_devices` | 掃描附近 MUSE 裝置、取得 BLE address |
| `signal_monitor.hardware.monitor_raw`  | 直接 BLE 連線 + 即時監控原始 EEG |
| `signal_monitor.data_utils.record_csv` | 直接 BLE 連線、把原始 EEG 錄成 CSV |
| `signal_monitor.analysis.fft_energy`   | 每秒 FFT，算 1..128 Hz 各頻率能量（只顯示摘要；加 `--out <dir>` 才存檔）|
| `signal_monitor.analysis.engagement`   | 每秒算 NASA 專注度指數（EI）+ 10 秒滑動平均 |
| `signal_monitor.analysis.faa`          | 每秒算前額 alpha 不對稱 FAA + 10 秒滑動平均 |
| `signal_monitor.data_utils.clean_csv`  | 刪除 .csv（Data、FFT、Features）；**預設保留 `Model/` 訓練資料**，保留資料夾與 .gitkeep |
| `Model/model_utils.py` | 資料載入、受測者分組、Z-score 標準化 |
| `Model/train_model.py` | 訓練有趣/無聊分類器 + Leave-One-Subject-Out 交叉驗證 |
| `Model/predict_model.py` | 對一段（或多段）錄製輸出 P(有趣) |
| `pyproject.toml` | 專案設定與相依套件 |
