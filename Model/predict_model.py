"""用訓練好的模型算一段錄製的「有趣機率」。

    python Model/predict_model.py Features/1.csv
    python Model/predict_model.py Features/pdf.csv Features/learn8.csv
    python Model/predict_model.py Features/pdf.csv Features/learn8.csv --baseline auto
    python Model/predict_model.py Features/1.csv --per-second

--baseline auto 會把列出的所有 CSV 合併起來當這位受測者的基準線，用它算 Z-score
的 mu/sigma（取代模型內建的全域 mu/sigma）。同一人做 PDF 與 Learn8 兩段時建議加上，
可抵銷個體差異；只有單一檔案時不要用（會把平均壓成 0，訊號就沒了）。
"""

import argparse
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

import joblib
import numpy as np

from model_utils import Session, ZScoreScaler, load_session

MODEL_PATH = Path(__file__).resolve().parent / "trained_model.joblib"


def load_bundle(path: Path) -> dict:
    bundle = joblib.load(path)
    if not isinstance(bundle, dict) or "classifier" not in bundle:
        raise ValueError(
            f"{path} 不是本版訓練腳本產生的模型；請先重新執行 python Model/train_model.py"
        )
    return bundle


def probabilities(bundle: dict, X: np.ndarray, scaler: ZScoreScaler) -> np.ndarray:
    """回傳每一列的 P(interesting)。"""
    clf = bundle["classifier"]
    return clf.predict_proba(scaler.transform(X))[:, bundle["positive_class_index"]]


def build_scaler(bundle: dict, sessions: Sequence[Session],
                 use_baseline: bool) -> Tuple[ZScoreScaler, bool]:
    """回傳 (scaler, 是否真的用了輸入資料當基準線)。"""
    if not use_baseline:
        return bundle["scaler"], False
    if len(sessions) < 2:
        print("警告：--baseline auto 需要至少兩段錄製才有意義，改用模型內建的全域基準線。",
              file=sys.stderr)
        return bundle["scaler"], False
    return ZScoreScaler().fit(np.vstack([s.X for s in sessions])), True


def display_names(sessions: Sequence[Session]) -> List[str]:
    """產生不重複的顯示名稱。

    boring/2.csv 與 interesting/2.csv 同名是常態（同號 = 同一位受測者），
    只印檔名會變成「2.csv 比 2.csv 有趣」，所以撞名時補上所在資料夾。
    """
    names = [s.path.name for s in sessions]
    return [f"{s.path.parent.name}/{s.path.name}" if names.count(s.path.name) > 1
            else s.path.name for s in sessions]


def verdict(prob: float) -> str:
    if prob >= 0.65:
        return "有趣"
    if prob >= 0.55:
        return "偏有趣"
    if prob > 0.45:
        return "難以判定"
    if prob > 0.35:
        return "偏無聊"
    return "無聊"


def main() -> int:
    parser = argparse.ArgumentParser(description="預測一段錄製屬於「有趣」的機率")
    parser.add_argument("csv_path", nargs="+", help="要預測的 CSV（可給多個）")
    parser.add_argument("--model", default=str(MODEL_PATH), help="模型檔路徑")
    parser.add_argument("--baseline", default=None, metavar="auto",
                        help="給 auto：用列出的所有 CSV 合併當基準線重算 Z-score")
    parser.add_argument("--per-second", action="store_true", help="逐秒印出機率")
    args = parser.parse_args()

    model_path = Path(args.model).resolve()
    if not model_path.exists():
        print(f"找不到模型檔：{model_path}\n請先執行：python Model/train_model.py", file=sys.stderr)
        return 1

    bundle = load_bundle(model_path)
    features = bundle["features"]

    sessions: List[Session] = []
    for raw in args.csv_path:
        path = Path(raw).resolve()
        if not path.exists():
            print(f"找不到 CSV：{path}", file=sys.stderr)
            return 1
        # 預測資料沒有標籤，label / subject 只是佔位用
        sessions.append(load_session(path, label=-1, subject=-1, features=features))

    use_baseline = (args.baseline or "").lower() == "auto"
    scaler, use_baseline = build_scaler(bundle, sessions, use_baseline)

    print(f"模型：{bundle['model_type']}   特徵：{', '.join(features)}")
    print(f"Z-score 基準線：{'本次輸入的所有檔案合併' if use_baseline else '模型內建（訓練資料）'}")
    cv = bundle.get("cv", {})
    if cv:
        names = {"leave_one_subject_out": "Leave-One-Subject-Out",
                 "temporal_holdout": "時間切分（同一人，測不到跨受測者泛化）"}
        method = names.get(cv.get("method"), "驗證")
        print(f"參考表現：{method} 每秒準確率 {cv['mean_accuracy']:.1%}、"
              f"AUC {cv['mean_auc']:.3f}（{bundle.get('n_subjects', '?')} 位受測者）")
    print("-" * 68)
    print(f"{'檔案':<32}{'秒數':<8}{'P(有趣)':<12}{'判定'}")

    labels = display_names(sessions)
    results = []
    for session, label in zip(sessions, labels):
        proba = probabilities(bundle, session.X, scaler)
        mean_prob = float(proba.mean())
        results.append((label, proba, mean_prob))
        print(f"{label:<32}{len(session.X):<8}{mean_prob:<12.4f}{verdict(mean_prob)}")

    if len(results) == 2:
        (name_a, _, pa), (name_b, _, pb) = results
        higher, lower = (name_a, name_b) if pa >= pb else (name_b, name_a)
        print("-" * 68)
        print(f"比較：{higher} 比 {lower} 有趣 {abs(pa - pb):.4f}（機率差）")

    if args.per_second:
        for session, (label, proba, _) in zip(sessions, results):
            print(f"\n逐秒結果 — {label}")
            print("second,P_interesting")
            for sec, p in zip(session.seconds, proba):
                print(f"{int(sec)},{p:.4f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
