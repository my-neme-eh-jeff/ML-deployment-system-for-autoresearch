"""Evaluate the trained churn model and save metrics."""

import json
import pickle
from pathlib import Path

import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

TARGET = "Churn"


def evaluate(
    test_path: str = "data/processed/test.csv",
    model_path: str = "models/churn_model.pkl",
    metrics_path: str = "metrics.json",
):
    df = pd.read_csv(test_path)
    X = df.drop(columns=[TARGET])
    y = df[TARGET]

    with open(model_path, "rb") as f:
        model = pickle.load(f)

    y_pred = model.predict(X)
    y_proba = model.predict_proba(X)[:, 1]

    metrics = {
        "accuracy": round(accuracy_score(y, y_pred), 4),
        "auc_roc": round(roc_auc_score(y, y_proba), 4),
        "f1": round(f1_score(y, y_pred), 4),
        "precision": round(precision_score(y, y_pred), 4),
        "recall": round(recall_score(y, y_pred), 4),
    }

    Path(metrics_path).write_text(json.dumps(metrics, indent=2))
    for k, v in metrics.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    evaluate()
