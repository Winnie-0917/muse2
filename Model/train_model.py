import pickle
from pathlib import Path
from typing import List, Tuple

from model_utils import GaussianNaiveBayes


MODEL_PATH = Path(__file__).resolve().parent / "trained_model.joblib"
DATA_DIR = Path(__file__).resolve().parent
FEATURE_CANDIDATES = {
    "EI_smooth10": ["EI_smooth10"],
    "FAA_smooth10": ["FAA_smooth10", "FAA_smooth"],
    "BPM_smooth10": ["BPM_smooth10", "10BPM_smooth10"],
}


def resolve_feature_columns(header: List[str]) -> List[str]:
    resolved = []
    for feature_name, candidates in FEATURE_CANDIDATES.items():
        for candidate in candidates:
            if candidate in header:
                resolved.append(candidate)
                break
        else:
            raise ValueError(f"Missing required column for {feature_name}; available columns: {header}")
    return resolved


def load_training_rows(data_dir: Path) -> Tuple[List[List[float]], List[int]]:
    features: List[List[float]] = []
    labels: List[int] = []

    labels_by_folder = {
        "boring": 0,
        "interesting": 1,
    }

    csv_files: List[Path] = []
    for folder_name, label in labels_by_folder.items():
        folder_path = data_dir / folder_name
        if not folder_path.exists():
            raise FileNotFoundError(f"Training data folder not found: {folder_path}")
        csv_files.extend(sorted(folder_path.glob("*.csv")))

    if not csv_files:
        raise FileNotFoundError("No CSV training files found")

    for csv_path in csv_files:
        with csv_path.open("r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
        if not lines:
            continue

        header = lines[0].split(",")
        feature_columns = resolve_feature_columns(header)

        for line in lines[1:]:
            values = line.split(",")
            if len(values) != len(header):
                continue
            row = {}
            for key, value in zip(header, values):
                row[key] = value.strip()

            sample = []
            for column_name in feature_columns:
                raw_value = row.get(column_name, "")
                try:
                    sample.append(float(raw_value))
                except ValueError:
                    sample.append(0.0)

            if not sample:
                continue

            features.append(sample)
            labels.append(labels_by_folder[csv_path.parent.name])

    if not features:
        raise ValueError("No valid training rows collected")

    return features, labels


def main() -> None:
    model = GaussianNaiveBayes()
    features, labels = load_training_rows(DATA_DIR)
    model.fit(features, labels)

    predictions = model.predict(features)
    correct = sum(int(pred == label) for pred, label in zip(predictions, labels))
    accuracy = correct / len(labels) if labels else 0.0

    with MODEL_PATH.open("wb") as handle:
        pickle.dump(model, handle)

    print(f"Training completed with {len(features)} samples")
    print(f"Accuracy on training data: {accuracy:.2%}")
    print(f"Model saved to {MODEL_PATH}")


if __name__ == "__main__":
    main()
