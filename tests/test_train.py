"""Tests for the training stage."""

import os
import pickle

import mlflow
import yaml

from src.train import build_pipeline, train


def test_build_pipeline_has_correct_steps():
    params = {
        "model_type": "RandomForestClassifier",
        "n_estimators": 10,
        "random_state": 42,
    }
    pipeline = build_pipeline(params)
    assert "preprocessor" in pipeline.named_steps
    assert "classifier" in pipeline.named_steps
    assert pipeline.named_steps["classifier"].n_estimators == 10


def test_train_saves_model(sample_processed_data, tmp_path):
    model_path = str(tmp_path / "model.pkl")
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"

    # Write a minimal params.yaml for the test
    params = {
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
    params_path = tmp_path / "params.yaml"
    params_path.write_text(yaml.dump(params))

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        params_path=str(params_path),
    )

    assert (tmp_path / "model.pkl").exists()
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    assert hasattr(model, "predict")

    del os.environ["MLFLOW_TRACKING_URI"]


def test_train_registers_model_in_mlflow(sample_processed_data, tmp_path):
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"

    params = {
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
    params_path = tmp_path / "params.yaml"
    params_path.write_text(yaml.dump(params))

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=str(tmp_path / "model.pkl"),
        params_path=str(params_path),
    )

    client = mlflow.MlflowClient(f"sqlite:///{tmp_path}/mlflow.db")
    versions = client.search_model_versions("name='churn-model'")
    assert len(versions) >= 1

    del os.environ["MLFLOW_TRACKING_URI"]
