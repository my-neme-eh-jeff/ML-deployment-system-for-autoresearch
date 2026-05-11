"""Evaluate the trained classifier, log to MLflow, and run champion/challenger."""

import json
import pickle
from pathlib import Path

import mlflow
import pandas as pd
import yaml
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

MODEL_NAME = "classifier"
EXPERIMENT_NAME = "training"
PRIMARY_METRIC = "auc_roc"


def get_champion_metric() -> float | None:
    """Return current champion's primary metric, or None if no champion exists.

    Distinguishes "no champion yet" (which is expected on first bootstrap)
    from any other MLflow error (network blip, auth failure, schema mismatch)
    that should propagate — otherwise a transient error returns None, which
    `evaluate()` treats as "no champion → unconditionally promote", letting a
    weak new version become champion just because the registry was briefly
    unreachable.
    """
    client = mlflow.MlflowClient()
    try:
        v = client.get_model_version_by_alias(MODEL_NAME, "champion")
    except mlflow.exceptions.MlflowException as e:
        # Both backends (SQLAlchemy local, REST in cluster) report the
        # "no alias yet" case with these substrings. Any other MlflowException
        # is a real error — re-raise.
        msg = str(e).lower()
        if (
            "not found" in msg
            or "does not exist" in msg
            or "resource_does_not_exist" in msg
        ):
            return None
        raise
    return client.get_run(v.run_id).data.metrics.get(PRIMARY_METRIC)


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
    target = dataset["target_column"]
    # Promotion threshold must match the autoresearch loop's `min_improvement`.
    # Without this, evaluate.py promotes on any positive delta but the loop only
    # opens a PR when delta ≥ min_improvement — diverging the registry from the
    # deployed pods (registry advances, no PR fires, ArgoCD never rolls).
    min_improvement = float(
        cfg.get("auto_experiment", {}).get("min_improvement", 0.001)
    )

    df = pd.read_csv(test_path)
    X = df.drop(columns=[target])
    y = df[target]
    # Feature engineering is baked into the saved sklearn pipeline (since
    # the move to a Pipeline-first design), so we feed raw X — the pipeline
    # auto-applies the same FE that train saw.

    with open(model_path, "rb") as f:
        model = pickle.load(f)

    y_pred = model.predict(X)
    y_proba = model.predict_proba(X)[:, 1]

    # AUC-ROC stays primary for now; PR-AUC (Average Precision) is logged as
    # the secondary metric — it's the industry standard for imbalanced binary
    # classification (fraud, medical screening). With our ~3.5% positive rate,
    # AUC-ROC can read high even when the model misses most positives; PR-AUC
    # is harder to fake. Loop optimizes PRIMARY_METRIC; reviewers see both.
    metrics = {
        "accuracy": round(accuracy_score(y, y_pred), 4),
        "auc_roc": round(roc_auc_score(y, y_proba), 4),
        "pr_auc": round(average_precision_score(y, y_proba), 4),
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
    elif metrics[PRIMARY_METRIC] >= champion_metric + min_improvement:
        if auto_promote:
            client.set_registered_model_alias(
                MODEL_NAME, "challenger", new_version.version
            )
            client.set_registered_model_alias(
                MODEL_NAME, "champion", new_version.version
            )
            print(
                f"v{new_version.version} ({metrics[PRIMARY_METRIC]}) beats champion "
                f"({champion_metric}) by ≥{min_improvement} — promoted."
            )
        else:
            client.set_registered_model_alias(
                MODEL_NAME, "challenger", new_version.version
            )
            print(f"v{new_version.version} tagged as challenger (auto_promote=False).")
    else:
        client.set_registered_model_alias(MODEL_NAME, "challenger", new_version.version)
        delta = metrics[PRIMARY_METRIC] - champion_metric
        print(
            f"v{new_version.version} ({metrics[PRIMARY_METRIC]}) vs champion "
            f"({champion_metric}) Δ={delta:+.4f} < {min_improvement} — challenger only."
        )


if __name__ == "__main__":
    evaluate()
