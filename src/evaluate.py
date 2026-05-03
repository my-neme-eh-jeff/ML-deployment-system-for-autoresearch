"""Evaluate the trained classifier, log to MLflow, and run champion/challenger."""

import json
import pickle
from pathlib import Path

import mlflow
import pandas as pd
import yaml
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

try:
    from src.features import apply_feature_engineering
except ImportError:
    from features import apply_feature_engineering

MODEL_NAME = "classifier"
EXPERIMENT_NAME = "training"
PRIMARY_METRIC = "auc_roc"


def get_champion_metric() -> float | None:
    client = mlflow.MlflowClient()
    try:
        v = client.get_model_version_by_alias(MODEL_NAME, "champion")
        return client.get_run(v.run_id).data.metrics.get(PRIMARY_METRIC)
    except (mlflow.exceptions.MlflowException, Exception):
        return None


def evaluate(
    test_path: str = "data/processed/test.csv",
    model_path: str = "models/classifier.pkl",
    metrics_path: str = "metrics.json",
    run_id_path: str = "models/run_id.txt",
    params_path: str = "configs/params.yaml",
    auto_promote: bool = True,
):
    with open(params_path) as f:
        cfg = yaml.safe_load(f)
    dataset = cfg["dataset"]
    train_params = cfg["train"]
    target = dataset["target_column"]

    df = pd.read_csv(test_path)
    X = df.drop(columns=[target])
    y = df[target]
    X = apply_feature_engineering(X, train_params)

    with open(model_path, "rb") as f:
        model = pickle.load(f)

    y_pred = model.predict(X)
    y_proba = model.predict_proba(X)[:, 1]

    metrics = {
        "accuracy": round(accuracy_score(y, y_pred), 4),
        "auc_roc": round(roc_auc_score(y, y_proba), 4),
        "f1": round(f1_score(y, y_pred, zero_division=0), 4),
        "precision": round(precision_score(y, y_pred, zero_division=0), 4),
        "recall": round(recall_score(y, y_pred, zero_division=0), 4),
    }
    Path(metrics_path).write_text(json.dumps(metrics, indent=2))
    for k, v in metrics.items():
        print(f"{k}: {v}")

    mlflow.set_experiment(EXPERIMENT_NAME)
    rid_file = Path(run_id_path)
    if not rid_file.exists():
        print(f"run_id file not found at {run_id_path} — skipping MLflow logging.")
        return

    run_id = rid_file.read_text().strip()
    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics(metrics)

    client = mlflow.MlflowClient()
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    if not versions:
        print("No registered model versions found.")
        return

    new_version = next((v for v in versions if v.run_id == run_id), None)
    if new_version is None:
        print(f"No model version found for run_id {run_id}.")
        return

    champion_metric = get_champion_metric()
    if champion_metric is None:
        client.set_registered_model_alias(MODEL_NAME, "champion", new_version.version)
        print(f"No existing champion. v{new_version.version} promoted to champion.")
    elif metrics[PRIMARY_METRIC] > champion_metric:
        if auto_promote:
            client.set_registered_model_alias(
                MODEL_NAME, "challenger", new_version.version
            )
            client.set_registered_model_alias(
                MODEL_NAME, "champion", new_version.version
            )
            print(
                f"v{new_version.version} ({metrics[PRIMARY_METRIC]}) beats champion "
                f"({champion_metric}) — promoted."
            )
        else:
            client.set_registered_model_alias(
                MODEL_NAME, "challenger", new_version.version
            )
            print(f"v{new_version.version} tagged as challenger (auto_promote=False).")
    else:
        client.set_registered_model_alias(MODEL_NAME, "challenger", new_version.version)
        print(
            f"v{new_version.version} ({metrics[PRIMARY_METRIC]}) does not beat champion "
            f"({champion_metric}) — challenger only."
        )


if __name__ == "__main__":
    evaluate()
