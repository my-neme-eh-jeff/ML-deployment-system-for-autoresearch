"""Tests for the evaluation stage."""

import json
import os

import yaml

from src.evaluate import evaluate
from src.train import train

_MINIMAL_PARAMS = {
    "train": {
        "model_type": "RandomForestClassifier",
        "n_estimators": 10,
        "random_state": 42,
        "max_depth": None,
        "min_samples_split": 2,
        "min_samples_leaf": 1,
        "max_features": "sqrt",
        "class_weight": None,
        "bootstrap": True,
        "learning_rate": 0.1,
        "subsample": 1.0,
        "use_log_transform": False,
        "add_charges_per_month": False,
    }
}


def test_evaluate_writes_metrics(sample_processed_data, tmp_path):
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"
    model_path = str(tmp_path / "model.pkl")
    metrics_path = str(tmp_path / "metrics.json")
    params_path = tmp_path / "params.yaml"
    params_path.write_text(yaml.dump(_MINIMAL_PARAMS))

    run_id_path = str(tmp_path / "run_id.txt")
    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        params_path=str(params_path),
    )
    evaluate(
        test_path=str(sample_processed_data / "test.csv"),
        model_path=model_path,
        metrics_path=metrics_path,
        run_id_path=run_id_path,
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
    run_id_path = str(tmp_path / "run_id.txt")
    params_path = tmp_path / "params.yaml"
    params_path.write_text(yaml.dump(_MINIMAL_PARAMS))

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        params_path=str(params_path),
    )
    evaluate(
        test_path=str(sample_processed_data / "test.csv"),
        model_path=model_path,
        metrics_path=str(tmp_path / "metrics.json"),
        run_id_path=run_id_path,
    )

    client = mlflow.MlflowClient(f"sqlite:///{tmp_path}/mlflow.db")
    champion = client.get_model_version_by_alias("churn-model", "champion")
    assert int(champion.version) == 1

    del os.environ["MLFLOW_TRACKING_URI"]
