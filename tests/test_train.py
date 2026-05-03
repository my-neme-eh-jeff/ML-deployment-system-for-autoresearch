"""Tests for the training stage."""

import os
import pickle

import mlflow

from src.train import build_pipeline, train
from tests.conftest import TELCO_PARAMS


def test_build_pipeline_has_correct_steps():
    pipeline = build_pipeline(TELCO_PARAMS["dataset"], TELCO_PARAMS["train"])
    assert "preprocessor" in pipeline.named_steps
    assert "classifier" in pipeline.named_steps
    assert pipeline.named_steps["classifier"].n_estimators == 10


def test_train_saves_model(sample_processed_data, tmp_path, telco_params):
    model_path = str(tmp_path / "classifier.pkl")
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        params_path=str(telco_params),
    )

    assert (tmp_path / "classifier.pkl").exists()
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    assert hasattr(model, "predict")

    del os.environ["MLFLOW_TRACKING_URI"]


def test_train_registers_model_in_mlflow(sample_processed_data, tmp_path, telco_params):
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=str(tmp_path / "classifier.pkl"),
        params_path=str(telco_params),
    )

    client = mlflow.MlflowClient(f"sqlite:///{tmp_path}/mlflow.db")
    versions = client.search_model_versions("name='classifier'")
    assert len(versions) >= 1

    del os.environ["MLFLOW_TRACKING_URI"]
