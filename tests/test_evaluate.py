"""Tests for the evaluation stage."""

import json
import os

from src.evaluate import evaluate
from src.train import train


def test_evaluate_writes_metrics(sample_processed_data, tmp_path):
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"
    model_path = str(tmp_path / "model.pkl")
    metrics_path = str(tmp_path / "metrics.json")

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        n_estimators=10,
    )
    evaluate(
        test_path=str(sample_processed_data / "test.csv"),
        model_path=model_path,
        metrics_path=metrics_path,
    )

    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert "accuracy" in metrics
    assert "auc_roc" in metrics
    assert "f1" in metrics
    assert all(0 <= v <= 1 for v in metrics.values())

    del os.environ["MLFLOW_TRACKING_URI"]


def test_evaluate_promotes_first_model_to_champion(sample_processed_data, tmp_path):
    """First model ever should auto-promote to champion."""
    import mlflow

    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"
    model_path = str(tmp_path / "model.pkl")

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        n_estimators=10,
    )
    evaluate(
        test_path=str(sample_processed_data / "test.csv"),
        model_path=model_path,
        metrics_path=str(tmp_path / "metrics.json"),
    )

    client = mlflow.MlflowClient(f"sqlite:///{tmp_path}/mlflow.db")
    champion = client.get_model_version_by_alias("churn-model", "champion")
    assert int(champion.version) == 1

    del os.environ["MLFLOW_TRACKING_URI"]
