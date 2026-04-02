"""Tests for the training stage."""

import os
import pickle

import mlflow

from src.train import build_pipeline, train


def test_build_pipeline_has_correct_steps():
    pipeline = build_pipeline(n_estimators=10)
    assert "preprocessor" in pipeline.named_steps
    assert "classifier" in pipeline.named_steps
    assert pipeline.named_steps["classifier"].n_estimators == 10


def test_train_saves_model(sample_processed_data, tmp_path):
    model_path = str(tmp_path / "model.pkl")
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        n_estimators=10,
    )

    assert (tmp_path / "model.pkl").exists()
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    assert hasattr(model, "predict")

    del os.environ["MLFLOW_TRACKING_URI"]


def test_train_registers_model_in_mlflow(sample_processed_data, tmp_path):
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=str(tmp_path / "model.pkl"),
        n_estimators=10,
    )

    client = mlflow.MlflowClient(f"sqlite:///{tmp_path}/mlflow.db")
    versions = client.search_model_versions("name='churn-model'")
    assert len(versions) >= 1

    del os.environ["MLFLOW_TRACKING_URI"]
