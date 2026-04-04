"""
Kubeflow Pipelines version of the churn prediction pipeline.

This mirrors the DVC pipeline (preprocess → train → evaluate) but runs each
stage as a containerized step on Kubernetes. Each @component becomes a pod.

Usage:
    # Compile to YAML (for uploading to KFP UI or submitting via CLI)
    uv run python pipelines/churn_pipeline.py

    # Submit directly to a running KFP instance
    uv run python pipelines/churn_pipeline.py --run
"""

import argparse

from kfp import compiler, dsl

BASE_IMAGE = "ghcr.io/my-neme-eh-jeff/churn-kfp:latest"
PACKAGES = ["pandas==2.3.3", "scikit-learn==1.8.0", "mlflow==3.10.1"]


@dsl.component(base_image=BASE_IMAGE)
def preprocess(
    raw_data_gcs_path: str,
    test_size: float,
    train_csv: dsl.Output[dsl.Dataset],
    test_csv: dsl.Output[dsl.Dataset],
    stats: dsl.Output[dsl.Artifact],
):
    """Clean raw data and split into train/test."""
    import json

    import pandas as pd
    from sklearn.model_selection import train_test_split

    TARGET = "Churn"

    df = pd.read_csv(raw_data_gcs_path)

    df["TotalCharges"] = pd.to_numeric(df["TotalCharges"], errors="coerce")
    df["TotalCharges"] = df["TotalCharges"].fillna(df["TotalCharges"].median())
    df[TARGET] = df[TARGET].map({"Yes": 1, "No": 0})
    df = df.drop(columns=["customerID"])

    train_df, test_df = train_test_split(
        df, test_size=test_size, random_state=42, stratify=df[TARGET]
    )

    train_df.to_csv(train_csv.path, index=False)
    test_df.to_csv(test_csv.path, index=False)

    stats_data = {
        "total_rows": len(df),
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "churn_rate": float(df[TARGET].mean()),
    }
    with open(stats.path, "w") as f:
        json.dump(stats_data, f, indent=2)


@dsl.component(base_image=BASE_IMAGE)
def train(
    train_csv: dsl.Input[dsl.Dataset],
    n_estimators: int,
    mlflow_tracking_uri: str,
    model_artifact: dsl.Output[dsl.Model],
):
    """Train a RandomForest model and register in MLflow."""
    import pickle

    import mlflow
    import pandas as pd
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    NUMERIC_FEATURES = ["SeniorCitizen", "tenure", "MonthlyCharges", "TotalCharges"]
    CATEGORICAL_FEATURES = [
        "gender",
        "Partner",
        "Dependents",
        "PhoneService",
        "MultipleLines",
        "InternetService",
        "OnlineSecurity",
        "OnlineBackup",
        "DeviceProtection",
        "TechSupport",
        "StreamingTV",
        "StreamingMovies",
        "Contract",
        "PaperlessBilling",
        "PaymentMethod",
    ]
    TARGET = "Churn"

    df = pd.read_csv(train_csv.path)
    X = df.drop(columns=[TARGET])
    y = df[TARGET]

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), NUMERIC_FEATURES),
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                CATEGORICAL_FEATURES,
            ),
        ]
    )
    pipeline = Pipeline(
        [
            ("preprocessor", preprocessor),
            (
                "classifier",
                RandomForestClassifier(n_estimators=n_estimators, random_state=42),
            ),
        ]
    )
    pipeline.fit(X, y)

    with open(model_artifact.path, "wb") as f:
        pickle.dump(pipeline, f)

    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow.set_experiment("churn-prediction-kfp")

    with mlflow.start_run(run_name="kfp-train"):
        mlflow.log_param("n_estimators", n_estimators)
        mlflow.log_param("n_train_samples", X.shape[0])
        mlflow.log_param("orchestrator", "kubeflow-pipelines")
        mlflow.sklearn.log_model(
            pipeline,
            artifact_path="model",
            registered_model_name="churn-model",
        )


@dsl.component(base_image=BASE_IMAGE)
def evaluate(
    test_csv: dsl.Input[dsl.Dataset],
    model_artifact: dsl.Input[dsl.Model],
    mlflow_tracking_uri: str,
    metrics: dsl.Output[dsl.Artifact],
):
    """Evaluate model and handle champion/challenger promotion."""
    import json
    import pickle

    import mlflow
    import pandas as pd
    from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

    TARGET = "Churn"
    MODEL_NAME = "churn-model"

    df = pd.read_csv(test_csv.path)
    X = df.drop(columns=[TARGET])
    y = df[TARGET]

    with open(model_artifact.path, "rb") as f:
        model = pickle.load(f)

    y_pred = model.predict(X)
    y_proba = model.predict_proba(X)[:, 1]

    results = {
        "accuracy": round(accuracy_score(y, y_pred), 4),
        "auc_roc": round(roc_auc_score(y, y_proba), 4),
        "f1": round(f1_score(y, y_pred), 4),
    }

    with open(metrics.path, "w") as f:
        json.dump(results, f, indent=2)

    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow.set_experiment("churn-prediction-kfp")

    last_run = mlflow.search_runs(
        experiment_names=["churn-prediction-kfp"],
        order_by=["start_time DESC"],
        max_results=1,
    )

    if not last_run.empty:
        with mlflow.start_run(run_id=last_run.iloc[0]["run_id"]):
            mlflow.log_metrics(results)

    # Champion/challenger promotion
    client = mlflow.MlflowClient(mlflow_tracking_uri)
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    if not versions:
        return

    new_version = max(versions, key=lambda v: int(v.version))

    try:
        champion = client.get_model_version_by_alias(MODEL_NAME, "champion")
        champion_run = client.get_run(champion.run_id)
        champion_auc = champion_run.data.metrics.get("auc_roc", 0)

        if results["auc_roc"] > champion_auc:
            client.set_registered_model_alias(
                MODEL_NAME, "champion", new_version.version
            )
            print(
                f"New champion: v{new_version.version} (AUC: {results['auc_roc']} > {champion_auc})"
            )
        else:
            client.set_registered_model_alias(
                MODEL_NAME, "challenger", new_version.version
            )
            print(
                f"Challenger only: v{new_version.version} (AUC: {results['auc_roc']} <= {champion_auc})"
            )
    except Exception:
        client.set_registered_model_alias(MODEL_NAME, "champion", new_version.version)
        print(f"First model — v{new_version.version} promoted to champion")


@dsl.pipeline(name="churn-prediction-pipeline")
def churn_pipeline(
    raw_data_gcs_path: str = "gs://customer-churn-dvc-remote/raw/churn_data.csv",
    test_size: float = 0.2,
    n_estimators: int = 100,
    mlflow_tracking_uri: str = "http://mlflow.mlflow.svc.cluster.local:5000",
):
    # Set explicit small resource requests so steps fit on the 2-node cluster.
    # GKE Autopilot defaults to 1 CPU + 4GB per pod if unset — too large for demo.
    preprocess_task = preprocess(
        raw_data_gcs_path=raw_data_gcs_path,
        test_size=test_size,
    )
    preprocess_task.set_cpu_request("200m").set_memory_request("512Mi")
    preprocess_task.set_cpu_limit("500m").set_memory_limit("1Gi")

    train_task = train(
        train_csv=preprocess_task.outputs["train_csv"],
        n_estimators=n_estimators,
        mlflow_tracking_uri=mlflow_tracking_uri,
    )
    train_task.set_cpu_request("300m").set_memory_request("512Mi")
    train_task.set_cpu_limit("1").set_memory_limit("2Gi")

    evaluate_task = evaluate(
        test_csv=preprocess_task.outputs["test_csv"],
        model_artifact=train_task.outputs["model_artifact"],
        mlflow_tracking_uri=mlflow_tracking_uri,
    )
    evaluate_task.set_cpu_request("200m").set_memory_request("512Mi")
    evaluate_task.set_cpu_limit("500m").set_memory_limit("1Gi")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run", action="store_true", help="Submit to a running KFP instance"
    )
    parser.add_argument("--host", default="http://localhost:8080", help="KFP host URL")
    args = parser.parse_args()

    output_path = "pipelines/churn_pipeline.yaml"
    compiler.Compiler().compile(churn_pipeline, output_path)

    print(f"Pipeline compiled to {output_path}")

    if args.run:
        from kfp.client import Client

        client = Client(host=args.host)
        run = client.create_run_from_pipeline_package(
            output_path,
            arguments={},
            run_name="churn-prediction-run",
        )
        print(f"Run submitted: {run.run_id}")
