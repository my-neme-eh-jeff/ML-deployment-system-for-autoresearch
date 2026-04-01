"""Evaluate the trained churn model, log to MLflow, and optionally promote."""

import json
import pickle
from pathlib import Path

import mlflow
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

TARGET = "Churn"
MODEL_NAME = "churn-model"
PRIMARY_METRIC = "auc_roc"


def get_champion_metric() -> float | None:
    """Get the primary metric of the current champion model."""
    client = mlflow.MlflowClient()
    try:
        champion_version = client.get_model_version_by_alias(MODEL_NAME, "champion")
        champion_run = client.get_run(champion_version.run_id)
        return champion_run.data.metrics.get(PRIMARY_METRIC)
    except (mlflow.exceptions.MlflowException, Exception):
        return None


def evaluate(
    test_path: str = "data/processed/test.csv",
    model_path: str = "models/churn_model.pkl",
    metrics_path: str = "metrics.json",
    auto_promote: bool = True,
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

    # Write metrics file for DVC
    Path(metrics_path).write_text(json.dumps(metrics, indent=2))

    # Log metrics to the most recent MLflow run
    mlflow.set_experiment("churn-prediction")
    last_run = mlflow.search_runs(
        experiment_names=["churn-prediction"],
        order_by=["start_time DESC"],
        max_results=1,
    )

    if last_run.empty:
        print("No MLflow run found — skipping MLflow logging.")
        return

    run_id = last_run.iloc[0]["run_id"]

    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics(metrics)

    for k, v in metrics.items():
        print(f"{k}: {v}")

    # Champion/challenger promotion
    client = mlflow.MlflowClient()
    latest_versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    if not latest_versions:
        print("No registered model versions found.")
        return

    new_version = max(latest_versions, key=lambda v: int(v.version))
    champion_metric = get_champion_metric()

    if champion_metric is None:
        # No champion exists yet — this is the first model, auto-promote
        client.set_registered_model_alias(MODEL_NAME, "champion", new_version.version)
        print(
            f"No existing champion. Version {new_version.version} promoted to champion."
        )
    elif metrics[PRIMARY_METRIC] > champion_metric:
        if auto_promote:
            client.set_registered_model_alias(
                MODEL_NAME, "challenger", new_version.version
            )
            client.set_registered_model_alias(
                MODEL_NAME, "champion", new_version.version
            )
            print(
                f"New model ({metrics[PRIMARY_METRIC]}) beats champion ({champion_metric}). "
                f"Version {new_version.version} promoted to champion."
            )
        else:
            client.set_registered_model_alias(
                MODEL_NAME, "challenger", new_version.version
            )
            print(
                f"New model ({metrics[PRIMARY_METRIC]}) beats champion ({champion_metric}). "
                f"Version {new_version.version} tagged as challenger. "
                f"Run 'python src/promote.py' to promote manually."
            )
    else:
        client.set_registered_model_alias(MODEL_NAME, "challenger", new_version.version)
        print(
            f"New model ({metrics[PRIMARY_METRIC]}) does not beat champion ({champion_metric}). "
            f"Version {new_version.version} tagged as challenger only."
        )


if __name__ == "__main__":
    evaluate()
