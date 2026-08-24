"""訓練「有趣機率」模型。

    輸入：Model/boring/*.csv (y=0) 與 Model/interesting/*.csv (y=1)
    特徵：EI_smooth10, FAA_smooth10, BPM_smooth10（每秒一列）
    前處理：Z-score 標準化（mu/sigma 只由訓練資料估）
    模型：Logistic Regression -> 直接輸出 P(interesting)

用法：
    python Model/train_model.py                 # 全域 Z-score（預設）
    python Model/train_model.py --per-subject   # 改用每位受測者自己的 mu/sigma
    python Model/train_model.py --features EI FAA
"""

import argparse
import json
import math
from pathlib import Path
from typing import Dict, List, Sequence

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression

from model_utils import (
    DEFAULT_FEATURES,
    Session,
    ZScoreScaler,
    load_sessions,
    per_subject_zscore,
    roc_auc,
)

YELLOW = "\033[33m"
RESET = "\033[0m"

DATA_DIR = Path(__file__).resolve().parent
MODEL_PATH = DATA_DIR / "trained_model.joblib"


def json_safe(value):
    """把 NaN / Inf 換成 None，讓輸出是合法 JSON。"""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def stack(sessions: Sequence[Session]):
    X = np.vstack([s.X for s in sessions])
    y = np.concatenate([np.full(len(s.X), s.label) for s in sessions])
    return X, y


def scale(sessions: Sequence[Session], scalers: Dict[int, ZScoreScaler] | None,
          global_scaler: ZScoreScaler | None) -> np.ndarray:
    """per-subject 模式用各受測者自己的 scaler，否則用全域 scaler。"""
    if scalers is not None:
        return np.vstack([scalers[s.subject].transform(s.X) for s in sessions])
    assert global_scaler is not None
    return global_scaler.transform(np.vstack([s.X for s in sessions]))


def make_classifier(C: float) -> LogisticRegression:
    return LogisticRegression(C=C, max_iter=2000, solver="lbfgs")


def print_data_summary(sessions: List[Session], features: Sequence[str]) -> None:
    print("=" * 74)
    print("訓練資料")
    print("=" * 74)
    print(f"{'檔案':<26}{'受測者':<8}{'標籤':<14}{'秒數':<8}" + "".join(f"{f+'平均':<12}" for f in features))
    for s in sorted(sessions, key=lambda s: (s.subject, s.label)):
        means = "".join(f"{v:<12.3f}" for v in s.X.mean(axis=0))
        print(f"{s.name:<26}S{s.subject:<7}{s.label_name:<14}{len(s.X):<8}{means}")
    X, y = stack(sessions)
    print(f"\n合計 {len(X)} 列 / {len(sessions)} 段錄製 / "
          f"{len({s.subject for s in sessions})} 位受測者   "
          f"（boring {int((y==0).sum())} 列, interesting {int((y==1).sum())} 列）")


def temporal_holdout(sessions: List[Session], features: Sequence[str],
                     per_subject: bool, C: float,
                     train_frac: float = 0.7, gap_s: int = 10) -> Dict[str, float]:
    """受測者只有一位時的退路：用「時間」切，前段訓練、後段測試。

    這**測不到跨受測者的泛化能力**，只能看出模型在同一個人身上、
    面對沒看過的時間段時是否還站得住。

    train 與 test 之間留 gap_s 秒不用：EI_smooth10 / FAA_smooth10 是 10 秒滑動
    平均，切點附近的前後兩列共用同一批原始樣本，不留間隔就是直接洩漏。
    """
    print("\n" + "=" * 74)
    print(f"時間切分驗證（每段前 {train_frac:.0%} 訓練 / 後 {1-train_frac:.0%} 測試，"
          f"中間空 {gap_s} 秒）")
    print("=" * 74)
    print(f"{YELLOW}注意：只有 1 位受測者，無法做 Leave-One-Subject-Out。{RESET}")
    print(f"{YELLOW}      以下數字測的是「同一人、沒看過的時間段」，"
          f"不代表換一個人也有同樣表現。{RESET}")

    train_parts, test_parts = [], []
    for session in sessions:
        n = len(session.X)
        cut = int(n * train_frac)
        head = session.X[:cut]
        tail = session.X[cut + gap_s:]
        if len(head) == 0 or len(tail) == 0:
            raise ValueError(f"{session.name} 太短，無法做時間切分（{n} 秒）")
        train_parts.append((session, head))
        test_parts.append((session, tail))
        print(f"  {session.name:<24}{session.label_name:<13}"
              f"訓練 1-{cut} 秒（{len(head)} 列）　測試 {cut+gap_s+1}-{n} 秒（{len(tail)} 列）")

    Xtr = np.vstack([x for _, x in train_parts])
    ytr = np.concatenate([np.full(len(x), sess.label) for sess, x in train_parts])
    Xte = np.vstack([x for _, x in test_parts])
    yte = np.concatenate([np.full(len(x), sess.label) for sess, x in test_parts])

    if len(set(ytr)) < 2:
        raise ValueError("訓練段只有單一類別，無法訓練分類器")

    scaler = ZScoreScaler().fit(Xtr)
    clf = make_classifier(C).fit(scaler.transform(Xtr), ytr)
    proba = clf.predict_proba(scaler.transform(Xte))[:, list(clf.classes_).index(1)]

    acc = float(((proba >= 0.5).astype(int) == yte).mean())
    auc = roc_auc(yte, proba)
    print(f"\n  測試段每秒準確率 {acc:.1%}    AUC {auc:.3f}")

    offset = 0
    for session, tail in test_parts:
        p_seg = proba[offset:offset + len(tail)]
        offset += len(tail)
        verdict = "OK " if (p_seg.mean() >= 0.5) == (session.label == 1) else "錯 "
        print(f"    {verdict}{session.name:<24}實際={session.label_name:<12}"
              f"測試段平均 P(interesting)={p_seg.mean():.3f}")

    return {"method": "temporal_holdout",
            "mean_accuracy": acc, "mean_auc": float(auc),
            "pooled_accuracy": acc, "pooled_auc": float(auc),
            "fold_accuracy": [acc]}


def leave_one_subject_out(sessions: List[Session], features: Sequence[str],
                          per_subject: bool, C: float) -> Dict[str, float]:
    """以受測者為單位做交叉驗證 —— 相鄰秒高度相關，隨機切 row 會嚴重高估表現。"""
    subjects = sorted({s.subject for s in sessions})
    print("\n" + "=" * 74)
    print(f"Leave-One-Subject-Out 交叉驗證（{len(subjects)} 折）")
    print("=" * 74)

    fold_acc, fold_auc, all_y, all_p = [], [], [], []
    for held_out in subjects:
        train = [s for s in sessions if s.subject != held_out]
        test = [s for s in sessions if s.subject == held_out]

        if per_subject:
            # 每位受測者用自己 boring+interesting 合併後的 mu/sigma。
            # 測試者的 mu/sigma 只用到特徵值、沒用到標籤，等同實際場景中
            # 「先錄 PDF 與 Learn8 兩段、合併當基準線」的做法。
            scalers = per_subject_zscore(sessions)
            Xtr, Xte, gs = scale(train, scalers, None), scale(test, scalers, None), None
        else:
            gs = ZScoreScaler().fit(np.vstack([s.X for s in train]))
            Xtr, Xte = scale(train, None, gs), scale(test, None, gs)

        _, ytr = stack(train)
        _, yte = stack(test)

        clf = make_classifier(C).fit(Xtr, ytr)
        proba = clf.predict_proba(Xte)[:, list(clf.classes_).index(1)]

        acc = float(((proba >= 0.5).astype(int) == yte).mean())
        auc = roc_auc(yte, proba)
        fold_acc.append(acc)
        fold_auc.append(auc)
        all_y.append(yte)
        all_p.append(proba)

        print(f"\n  折 {held_out}：留下 S{held_out}，用 "
              f"{', '.join('S'+str(x) for x in subjects if x != held_out)} 訓練")
        print(f"    每秒準確率 {acc:6.1%}    AUC {auc:5.3f}")
        offset = 0
        for s in test:
            p = proba[offset:offset + len(s.X)]
            offset += len(s.X)
            verdict = "OK " if (p.mean() >= 0.5) == (s.label == 1) else "錯 "
            print(f"    {verdict}{s.name:<24}實際={s.label_name:<12}"
                  f"整段平均 P(interesting)={p.mean():.3f}")

    y_all, p_all = np.concatenate(all_y), np.concatenate(all_p)
    pooled_acc = float(((p_all >= 0.5).astype(int) == y_all).mean())
    pooled_auc = roc_auc(y_all, p_all)
    print(f"\n  各折平均：準確率 {np.mean(fold_acc):.1%} (±{np.std(fold_acc):.1%})   "
          f"AUC {np.nanmean(fold_auc):.3f}")
    print(f"  彙總全部：準確率 {pooled_acc:.1%}   AUC {pooled_auc:.3f}")
    return {"method": "leave_one_subject_out",
            "mean_accuracy": float(np.mean(fold_acc)),
            "mean_auc": float(np.nanmean(fold_auc)),
            "pooled_accuracy": pooled_acc,
            "pooled_auc": float(pooled_auc),
            "fold_accuracy": [float(a) for a in fold_acc]}


def main() -> int:
    parser = argparse.ArgumentParser(description="訓練有趣機率模型（Z-score + Logistic Regression）")
    parser.add_argument("--features", nargs="+", default=DEFAULT_FEATURES,
                        choices=DEFAULT_FEATURES, help="要使用的特徵（預設三個全用）")
    parser.add_argument("--per-subject", action="store_true",
                        help="改用每位受測者自己的 mu/sigma 做 Z-score（抵銷個體差異）")
    parser.add_argument("--C", type=float, default=1.0, help="Logistic Regression 正則化強度倒數")
    parser.add_argument("--out", default=str(MODEL_PATH), help="模型輸出路徑")
    args = parser.parse_args()

    sessions = load_sessions(DATA_DIR, args.features)
    print_data_summary(sessions, args.features)
    print(f"\nZ-score 模式：{'每位受測者各自標準化' if args.per_subject else '全域（單一 mu/sigma）'}")

    n_subjects = len({s.subject for s in sessions})
    CV_NAMES = {"leave_one_subject_out": "Leave-One-Subject-Out",
                "temporal_holdout": "時間切分驗證"}
    try:
        if n_subjects >= 2:
            cv = leave_one_subject_out(sessions, args.features, args.per_subject, args.C)
        else:
            cv = temporal_holdout(sessions, args.features, args.per_subject, args.C)
    except ValueError as exc:
        # 資料不足以驗證（太短、只有單一類別…）：說清楚原因就好，不要吐 traceback。
        print(f"\n{YELLOW}無法進行驗證：{exc}{RESET}")
        print(f"{YELLOW}仍會用現有資料訓練模型，但沒有任何表現數字可參考。{RESET}")
        cv = {}

    # ---- 用全部資料訓練最終模型 ----
    print("\n" + "=" * 74)
    print("最終模型（使用全部資料重新訓練）")
    print("=" * 74)

    if args.per_subject:
        scalers = per_subject_zscore(sessions)
        X = scale(sessions, scalers, None)
        # 預測新受測者時沒有他的 mu/sigma，退而求其次存各受測者的平均值當預設基準線
        fallback = ZScoreScaler()
        fallback.mean_ = np.mean([sc.mean_ for sc in scalers.values()], axis=0)
        fallback.std_ = np.mean([sc.std_ for sc in scalers.values()], axis=0)
        scaler = fallback
    else:
        scaler = ZScoreScaler().fit(np.vstack([s.X for s in sessions]))
        X = scaler.transform(np.vstack([s.X for s in sessions]))

    _, y = stack(sessions)
    clf = make_classifier(args.C).fit(X, y)

    train_proba = clf.predict_proba(X)[:, list(clf.classes_).index(1)]
    train_acc = float(((train_proba >= 0.5).astype(int) == y).mean())

    if cv:
        cv_name = CV_NAMES.get(cv.get("method"), cv.get("method", "驗證"))
        print(f"  訓練集每秒準確率 {train_acc:.1%}（僅供參考，會樂觀；請看上面的{cv_name}數字）")
    else:
        print(f"  訓練集每秒準確率 {train_acc:.1%}"
              f"{YELLOW}（模型是在這些資料上訓練的，這個數字必然樂觀，不能當作表現）{RESET}")
    print("\n  Z-score 參數（套用到新資料時要沿用同一組）：")
    for f, m, s in zip(args.features, scaler.mean_, scaler.std_):
        print(f"    {f:<6} mu = {m:8.4f}   sigma = {s:7.4f}")
    print("\n  係數（已在 z 空間，可直接互相比較大小）：")
    for f, w in zip(args.features, clf.coef_[0]):
        arrow = "值越大 -> 越有趣" if w > 0 else "值越大 -> 越無聊"
        print(f"    {f:<6} w = {w:+8.4f}   {arrow}")
    print(f"    {'截距':<6} b = {clf.intercept_[0]:+8.4f}")

    bundle = {
        "model_type": "zscore+logistic_regression",
        "features": list(args.features),
        "scaler": scaler,
        "classifier": clf,
        "per_subject_zscore": args.per_subject,
        "positive_label": 1,
        "positive_class_index": int(list(clf.classes_).index(1)),
        "cv": cv,
        "n_subjects": len({s.subject for s in sessions}),
        "n_rows": int(len(X)),
    }
    out_path = Path(args.out).resolve()
    joblib.dump(bundle, out_path)
    print(f"\n模型已儲存：{out_path}")
    print("預測用法：python Model/predict_model.py <csv>")
    # json.dumps 遇到 float("nan") 會吐出裸的 NaN —— 那不是合法 JSON（RFC 8259），
    # jq 會靜默轉成 null、瀏覽器的 JSON.parse 則直接拒絕。統一先換成 None。
    print("\n" + json.dumps({"cv": json_safe(cv)}, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
