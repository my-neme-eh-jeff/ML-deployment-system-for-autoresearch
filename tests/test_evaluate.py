"""Tests for the evaluation stage."""

import json
import os

from src.evaluate import evaluate
from src.train import train


def test_evaluate_writes_metrics(sample_processed_data, tmp_path, telco_params):
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"
    model_path = str(tmp_path / "classifier.pkl")
    metrics_path = str(tmp_path / "metrics.json")
    run_id_path = str(tmp_path / "run_id.txt")

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        params_path=str(telco_params),
    )
    evaluate(
        test_path=str(sample_processed_data / "test.csv"),
        model_path=model_path,
        metrics_path=metrics_path,
        run_id_path=run_id_path,
        params_path=str(telco_params),
    )

    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert "accuracy" in metrics
    assert "auc_roc" in metrics
    assert "f1" in metrics
    assert all(0 <= v <= 1 for v in metrics.values())

    del os.environ["MLFLOW_TRACKING_URI"]


def test_evaluate_promotes_first_model_to_champion(
    sample_processed_data, tmp_path, telco_params
):
    """First model ever registered should auto-promote to champion."""
    import mlflow

    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"
    model_path = str(tmp_path / "classifier.pkl")
    run_id_path = str(tmp_path / "run_id.txt")

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        params_path=str(telco_params),
    )
    evaluate(
        test_path=str(sample_processed_data / "test.csv"),
        model_path=model_path,
        metrics_path=str(tmp_path / "metrics.json"),
        run_id_path=run_id_path,
        params_path=str(telco_params),
    )

    client = mlflow.MlflowClient(f"sqlite:///{tmp_path}/mlflow.db")
    champion = client.get_model_version_by_alias("classifier", "champion")
    assert int(champion.version) == 1

    del os.environ["MLFLOW_TRACKING_URI"]
