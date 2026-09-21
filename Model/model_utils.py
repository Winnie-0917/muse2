"""模型共用工具：資料載入、受測者分組、Z-score 標準化。

資料佈局約定
------------
Model/boring/*.csv       絕對無聊任務（標籤 y = 0）
Model/interesting/*.csv  絕對不無聊任務（標籤 y = 1）

檔名的數字就是「受測者編號」，兩個資料夾同號的檔案是同一個人：

    受測者 k  ->  boring/k.csv 與 interesting/k.csv

    S1 = (boring/1.csv, interesting/1.csv)
    S2 = (boring/2.csv, interesting/2.csv)
    S3 = (boring/3.csv, interesting/3.csv)

因為同一秒與相鄰秒高度相關，切分驗證集時必須以「受測者」為單位
（Leave-One-Subject-Out），否則隨機切 row 會嚴重高估準確率。
"""

import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

# 標籤資料夾 -> y
LABELS_BY_FOLDER = {"boring": 0, "interesting": 1}

# 特徵名 -> CSV 內可接受的欄位名（依序嘗試，取第一個存在的）
FEATURE_CANDIDATES: Dict[str, List[str]] = {
    "EI": ["EI_smooth10"],
    "FAA": ["FAA_smooth10", "FAA_smooth"],
    "BPM": ["BPM_smooth10", "10BPM_smooth10"],
}
# 平滑欄位名會跟著滑動視窗長度變（EI_smooth10 / EI_smooth30 / BPM_smooth30 ...）。
# 找不到上面列的固定名字時，退而接受任何 <特徵>_smooth* 欄位。
SMOOTH_SUFFIX = "_smooth"
DEFAULT_FEATURES = ["EI", "FAA", "BPM"]


@dataclass
class Session:
    """一次錄製（一個 CSV 檔）。"""

    path: Path
    label: int          # 0 = boring, 1 = interesting
    subject: int        # 受測者編號
    seconds: np.ndarray  # shape (n,)
    X: np.ndarray        # shape (n, n_features)

    @property
    def name(self) -> str:
        return f"{self.path.parent.name}/{self.path.name}"

    @property
    def label_name(self) -> str:
        return "interesting" if self.label == 1 else "boring"


def subject_from_filename(path: Path) -> int:
    """檔名數字即受測者編號：boring/2.csv 與 interesting/2.csv 都是 S2。

    先認新命名結尾的 _S<編號>，認不出來才退回「檔名裡第一個數字」，
    讓舊的 boring/1.csv 與新的 boring_S1.csv 都算 S1。

    順序不能反過來：Learn8_S1 用「第一個數字」會抓到 Learn8 的 8，
    而且 Learn8_S1 與 Learn8_S2 會一起被當成 S8 撞在一起。
    這條規則要跟 cli.py 的 next_subject_index() 保持一致。
    """
    match = re.search(r"_S(\d+)$", path.stem, re.IGNORECASE) or re.search(r"(\d+)", path.stem)
    if not match:
        raise ValueError(f"檔名不含受測者編號，無法分組：{path}")
    return int(match.group(1))


def resolve_feature_columns(header: Sequence[str], features: Sequence[str]) -> List[str]:
    """把特徵名對應到實際存在的 CSV 欄位名。"""
    resolved = []
    for feature in features:
        for candidate in FEATURE_CANDIDATES[feature]:
            if candidate in header:
                resolved.append(candidate)
                break
        else:
            # 視窗長度不是 10 時（--window 30 之類），欄名會是 EI_smooth30 / BPM_smooth30，
            # 對每個特徵都要能退而求其次，不能只對 BPM 生效。
            prefix = f"{feature}{SMOOTH_SUFFIX}"
            fallback = [c for c in header if c.startswith(prefix)]
            if fallback:
                resolved.append(sorted(fallback)[0])
                continue
            raise ValueError(
                f"找不到特徵 {feature} 對應的欄位（試過 {FEATURE_CANDIDATES[feature]}，"
                f"也找不到 {prefix}*）；CSV 現有欄位：{list(header)}"
            )
    return resolved


def load_session(path: Path, label: int, subject: int,
                 features: Sequence[str] = DEFAULT_FEATURES) -> Session:
    """讀一個 CSV。前 9 秒的 *_smooth10 欄是 NaN（10 秒滑動平均尚未成形），直接丟掉。"""
    frame = pd.read_csv(path)
    columns = resolve_feature_columns(frame.columns, features)

    # 秒數要在「丟掉不完整的列之前」決定，否則沒有 second 欄時
    # 編號會從 1 重新開始，與原始錄製的時間軸對不上。
    if "second" not in frame.columns:
        frame = frame.assign(second=np.arange(1, len(frame) + 1))

    keep = frame[columns].notna().all(axis=1)
    frame = frame.loc[keep]
    if frame.empty:
        raise ValueError(f"{path} 沒有任何完整的特徵列")

    seconds = frame["second"].to_numpy()
    return Session(
        path=path,
        label=label,
        subject=subject,
        seconds=seconds,
        X=frame[columns].to_numpy(dtype=float),
    )


def load_sessions(data_dir: Path,
                  features: Sequence[str] = DEFAULT_FEATURES) -> List[Session]:
    """載入 Model/boring 與 Model/interesting 底下所有 CSV。"""
    sessions: List[Session] = []
    for folder_name, label in LABELS_BY_FOLDER.items():
        folder = data_dir / folder_name
        if not folder.is_dir():
            raise FileNotFoundError(f"找不到訓練資料夾：{folder}")
        for csv_path in sorted(folder.glob("*.csv"), key=lambda p: subject_from_filename(p)):
            sessions.append(
                load_session(csv_path, label, subject_from_filename(csv_path), features)
            )
    if not sessions:
        raise FileNotFoundError(f"{data_dir} 底下沒有任何訓練用 CSV")
    check_pairing(sessions)
    return sessions


def check_pairing(sessions: Sequence[Session]) -> None:
    """檢查每位受測者都同時有 boring 與 interesting 兩段。

    配對是靠兩個資料夾裡「同號檔名」對上的。少一邊不會讓程式壞掉，但
    per-subject Z-score 的 mu/sigma 會只由單一情境估出來，該受測者的
    標準化基準線就偏了 —— 所以出聲警告。
    """
    by_subject: Dict[int, List[str]] = {}
    for session in sessions:
        by_subject.setdefault(session.subject, []).append(session.label_name)

    for subject in sorted(by_subject):
        found = by_subject[subject]
        missing = [name for name in ("boring", "interesting") if name not in found]
        if missing:
            print(f"警告：S{subject} 缺少 {'、'.join(missing)} 的錄製"
                  f"（只找到 {'、'.join(sorted(found))}）", file=sys.stderr)
        duplicated = {name for name in found if found.count(name) > 1}
        if duplicated:
            print(f"警告：S{subject} 有多筆 {'、'.join(sorted(duplicated))} 錄製，"
                  f"同一人同情境重複會讓該受測者在訓練中被加重權重", file=sys.stderr)


class ZScoreScaler:
    r"""Z-score 標準化：對每個特徵各自做

        z = (x - \mu) / \sigma

    \mu 與 \sigma 只能由「訓練資料」估出來，套用到驗證/預測資料時要沿用同一組，
    否則就是資料洩漏。\sigma = 0（常數特徵）時退化成 1，避免除以零。
    """

    def __init__(self) -> None:
        self.mean_: Optional[np.ndarray] = None
        self.std_: Optional[np.ndarray] = None

    def fit(self, X: np.ndarray) -> "ZScoreScaler":
        X = np.asarray(X, dtype=float)
        self.mean_ = X.mean(axis=0)
        std = X.std(axis=0)
        self.std_ = np.where(std < 1e-12, 1.0, std)
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.std_ is None:
            raise ValueError("ZScoreScaler 尚未 fit")
        return (np.asarray(X, dtype=float) - self.mean_) / self.std_

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        return self.fit(X).transform(X)


def per_subject_zscore(sessions: Sequence[Session]) -> Dict[int, ZScoreScaler]:
    """每位受測者用「自己 boring + interesting 兩段合起來」估一組 mu/sigma。

    這樣做的用意是抵銷受測者之間的個體差異（有人 EI 天生就高），
    但保留「同一人 boring 段 vs interesting 段」的差距 —— 也就是真正要學的訊號。
    注意不能每段各自標準化，那會把兩段的平均都壓成 0，訊號就沒了。
    """
    scalers: Dict[int, ZScoreScaler] = {}
    for subject in sorted({s.subject for s in sessions}):
        pooled = np.vstack([s.X for s in sessions if s.subject == subject])
        scalers[subject] = ZScoreScaler().fit(pooled)
    return scalers


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))


def roc_auc(y_true: np.ndarray, scores: np.ndarray) -> float:
    """AUC，用 Mann-Whitney U 的等價式算（含 tie 處理）。單一類別時回傳 nan。"""
    y_true = np.asarray(y_true)
    pos, neg = int((y_true == 1).sum()), int((y_true == 0).sum())
    if pos == 0 or neg == 0:
        return math.nan
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=float)
    sorted_scores = np.asarray(scores)[order]
    i = 0
    while i < len(sorted_scores):
        j = i
        while j + 1 < len(sorted_scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return (ranks[y_true == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg)
