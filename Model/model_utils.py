import math
from typing import Dict, List


class GaussianNaiveBayes:
    def __init__(self) -> None:
        self.class_names: List[int] = []
        self.class_stats: Dict[int, Dict[str, List[float]]] = {}
        self.feature_names: List[str] = []

    def fit(self, features: List[List[float]], labels: List[int]) -> None:
        if not features or not labels:
            raise ValueError("Training data is empty")
        if len(features) != len(labels):
            raise ValueError("Features and labels must have the same length")

        self.feature_names = [f"feature_{idx}" for idx in range(len(features[0]))]
        self.class_names = sorted(set(labels))

        total_samples = len(labels)
        self.class_stats = {}
        for label in self.class_names:
            class_features = [row for row, item in zip(features, labels) if item == label]
            if not class_features:
                continue
            means = [sum(row[idx] for row in class_features) / len(class_features) for idx in range(len(class_features[0]))]
            variances = []
            for idx in range(len(class_features[0])):
                mean = means[idx]
                variance = sum((row[idx] - mean) ** 2 for row in class_features) / len(class_features)
                variances.append(max(variance, 1e-9))
            self.class_stats[label] = {
                "mean": means,
                "variance": variances,
                "prior": len(class_features) / total_samples,
            }

    def _pdf(self, value: float, mean: float, variance: float) -> float:
        if variance <= 0:
            variance = 1e-9
        exponent = -((value - mean) ** 2) / (2 * variance)
        return (1 / math.sqrt(2 * math.pi * variance)) * math.exp(exponent)

    def predict_one(self, sample: List[float]) -> int:
        if not self.class_stats:
            raise ValueError("Model has not been trained")

        scores = {}
        for label, stats in self.class_stats.items():
            log_prob = math.log(stats["prior"])
            for idx, value in enumerate(sample):
                pdf = self._pdf(value, stats["mean"][idx], stats["variance"][idx])
                if pdf <= 0:
                    pdf = 1e-300
                log_prob += math.log(pdf)
            scores[label] = log_prob

        return max(scores, key=scores.get)

    def predict(self, features: List[List[float]]) -> List[int]:
        return [self.predict_one(sample) for sample in features]

    def predict_proba_one(self, sample: List[float]) -> Dict[int, float]:
        if not self.class_stats:
            raise ValueError("Model has not been trained")

        scores = {}
        for label, stats in self.class_stats.items():
            log_prob = math.log(stats["prior"])
            for idx, value in enumerate(sample):
                pdf = self._pdf(value, stats["mean"][idx], stats["variance"][idx])
                if pdf <= 0:
                    pdf = 1e-300
                log_prob += math.log(pdf)
            scores[label] = log_prob

        max_score = max(scores.values())
        exp_scores = {label: math.exp(score - max_score) for label, score in scores.items()}
        total = sum(exp_scores.values())
        return {label: prob / total for label, prob in exp_scores.items()}

    def predict_proba(self, features: List[List[float]]) -> List[Dict[int, float]]:
        return [self.predict_proba_one(sample) for sample in features]
