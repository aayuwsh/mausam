"""Chronological validation helpers; no model is trained without supplied data."""
from dataclasses import dataclass
import numpy as np
from sklearn.metrics import brier_score_loss, mean_absolute_error, mean_squared_error, precision_recall_fscore_support
from sklearn.model_selection import TimeSeriesSplit


@dataclass(frozen=True)
class ValidationReport:
    split_count: int
    brier_score: float | None
    mae: float | None
    rmse: float | None
    precision: float | None
    recall: float | None
    f1: float | None
    calibration: list[dict[str, float]]


def chronological_splits(sample_count: int, splits: int = 5):
    """Return expanding chronological folds. Never shuffles time-series rows."""
    if sample_count < splits + 1:
        raise ValueError("Not enough time-ordered samples for the requested backtest splits.")
    return TimeSeriesSplit(n_splits=splits).split(np.arange(sample_count))


def score_predictions(observed_rain_mm, predicted_rain_mm, observed_event, event_probability, bins: int = 10) -> ValidationReport:
    actual = np.asarray(observed_rain_mm, dtype=float)
    predicted = np.asarray(predicted_rain_mm, dtype=float)
    event = np.asarray(observed_event, dtype=int)
    probability = np.asarray(event_probability, dtype=float)
    if not (len(actual) == len(predicted) == len(event) == len(probability)) or len(actual) == 0:
        raise ValueError("Observed and predicted arrays must have the same non-empty length.")
    if np.any((probability < 0) | (probability > 1)):
        raise ValueError("Event probabilities must be between zero and one.")
    precision, recall, f1, _ = precision_recall_fscore_support(event, probability >= 0.5, average="binary", zero_division=0)
    calibration = []
    for lower in np.linspace(0, 1, bins + 1)[:-1]:
        upper = lower + 1 / bins
        mask = (probability >= lower) & ((probability < upper) if upper < 1 else (probability <= upper))
        if mask.any():
            calibration.append({"mean_probability": float(probability[mask].mean()), "observed_frequency": float(event[mask].mean()), "count": int(mask.sum())})
    return ValidationReport(
        split_count=1,
        brier_score=float(brier_score_loss(event, probability)),
        mae=float(mean_absolute_error(actual, predicted)),
        rmse=float(np.sqrt(mean_squared_error(actual, predicted))),
        precision=float(precision), recall=float(recall), f1=float(f1), calibration=calibration,
    )
