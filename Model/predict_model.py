import argparse
import pickle
import sys
from pathlib import Path
from typing import List, Tuple

from model_utils import GaussianNaiveBayes


def load_model(model_path: Path):
    with model_path.open("rb") as handle:
        return pickle.load(handle)


def resolve_feature_columns(header: List[str]) -> List[str]:
    preferred = ["EI_smooth10", "FAA_smooth10", "BPM_smooth10"]
    fallback = ["EI_smooth10", "FAA_smooth", "10BPM_smooth10"]
    for candidate in preferred:
        if candidate in header:
            continue
        if candidate in fallback:
            continue
        return []

    columns = []
    for candidate in ["EI_smooth10", "FAA_smooth10", "BPM_smooth10"]:
        if candidate in header:
            columns.append(candidate)
        elif "FAA_smooth" in header and candidate == "FAA_smooth10":
            columns.append("FAA_smooth")
        elif "10BPM_smooth10" in header and candidate == "BPM_smooth10":
            columns.append("10BPM_smooth10")
        else:
            raise ValueError(f"Missing required feature column for {candidate}; available columns: {header}")
    return columns


def load_samples(csv_path: Path) -> Tuple[List[List[float]], List[str]]:
    with csv_path.open("r", encoding="utf-8") as handle:
        lines = [line.strip() for line in handle if line.strip()]

    if not lines:
        raise ValueError("CSV file is empty")

    header = lines[0].split(",")
    feature_columns = resolve_feature_columns(header)

    samples = []
    row_ids = []
    for line in lines[1:]:
        values = line.split(",")
        if len(values) != len(header):
            continue
        row = dict(zip(header, values))
        sample = []
        for column_name in feature_columns:
            raw_value = row.get(column_name, "")
            try:
                sample.append(float(raw_value))
            except ValueError:
                sample.append(0.0)
        if sample:
            samples.append(sample)
            row_ids.append(line)

    if not samples:
        raise ValueError("No valid rows found in the CSV file")

    return samples, row_ids


def predict(csv_path: Path, model_path: Path) -> float:
    model = load_model(model_path)
    samples, _ = load_samples(csv_path)
    probas = model.predict_proba(samples)
    interesting_probabilities = [proba.get(1, 0.0) for proba in probas]
    avg_prob = sum(interesting_probabilities) / len(interesting_probabilities) if interesting_probabilities else 0.0
    return avg_prob


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict probability of interesting for a CSV file")
    parser.add_argument("csv_path", help="Path to the CSV file to predict")
    parser.add_argument("--model", default=str(Path(__file__).resolve().parent / "trained_model.joblib"))
    args = parser.parse_args()

    csv_path = Path(args.csv_path).resolve()
    model_path = Path(args.model).resolve()

    if not csv_path.exists():
        print(f"CSV file not found: {csv_path}", file=sys.stderr)
        return 1

    if not model_path.exists():
        print(f"Model file not found: {model_path}", file=sys.stderr)
        return 1

    prob = predict(csv_path, model_path)
    print(f"{prob:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
