"""
Kubeflow Pipelines version of the churn prediction pipeline.

Mirrors the DVC pipeline (preprocess → train → evaluate) but runs each stage
as a containerized step on Kubernetes — each @component becomes a pod, visible
in the KFP UI as a DAG node with logs and artifacts.

Each component is a thin wrapper that shells out to the existing src/*.py.
This means params.yaml is the single source of truth for hyperparameters /
feature engineering; KFP and DVC paths run the same code.

The autoresearch loop submits this pipeline once per iteration with the
proposed params.yaml content as an inline string argument.

Usage:
    # Compile to YAML (CI does this automatically)
    uv run python pipelines/churn_pipeline.py

    # Submit one run to a running KFP instance
    uv run python pipelines/churn_pipeline.py --run \\
        --host http://34.93.2.209
"""

import argparse

from kfp import compiler, dsl

BASE_IMAGE = "ghcr.io/my-neme-eh-jeff/churn-kfp:latest"


@dsl.component(base_image=BASE_IMAGE)
def preprocess(
    params_yaml: str,
    raw_data_gcs_path: str,
    train_csv: dsl.Output[dsl.Dataset],
    test_csv: dsl.Output[dsl.Dataset],
    stats: dsl.Output[dsl.Artifact],
):
    """Run src/preprocess.py with the proposed params.yaml. Outputs flow to KFP artifacts."""
    import shutil
    import subprocess
    from pathlib import Path

    workdir = Path("/app")
    (workdir / "configs").mkdir(parents=True, exist_ok=True)
    (workdir / "configs" / "params.yaml").write_text(params_yaml)

    # src/preprocess.py reads data/churn_data.csv. Pull it from GCS via gcsfs.
    import pandas as pd

    raw = pd.read_csv(raw_data_gcs_path)
    (workdir / "data").mkdir(parents=True, exist_ok=True)
    raw.to_csv(workdir / "data" / "churn_data.csv", index=False)

    subprocess.run(["python", "src/preprocess.py"], cwd=str(workdir), check=True)

    shutil.copy(workdir / "data" / "processed" / "train.csv", train_csv.path)
    shutil.copy(workdir / "data" / "processed" / "test.csv", test_csv.path)
    shutil.copy(workdir / "data" / "processed" / "stats.json", stats.path)


@dsl.component(base_image=BASE_IMAGE)
def train(
    params_yaml: str,
    train_csv: dsl.Input[dsl.Dataset],
    mlflow_tracking_uri: str,
    model_artifact: dsl.Output[dsl.Model],
    run_id_artifact: dsl.Output[dsl.Artifact],
):
    """Run src/train.py with the proposed params.yaml. Logs to MLflow + emits the run_id."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    workdir = Path("/app")
    (workdir / "configs").mkdir(parents=True, exist_ok=True)
    (workdir / "configs" / "params.yaml").write_text(params_yaml)

    (workdir / "data" / "processed").mkdir(parents=True, exist_ok=True)
    shutil.copy(train_csv.path, workdir / "data" / "processed" / "train.csv")

    env = {**os.environ, "MLFLOW_TRACKING_URI": mlflow_tracking_uri}
    subprocess.run(["python", "src/train.py"], cwd=str(workdir), check=True, env=env)

    shutil.copy(workdir / "models" / "churn_model.pkl", model_artifact.path)
    shutil.copy(workdir / "models" / "run_id.txt", run_id_artifact.path)


@dsl.component(base_image=BASE_IMAGE)
def evaluate(
    params_yaml: str,
    test_csv: dsl.Input[dsl.Dataset],
    model_artifact: dsl.Input[dsl.Model],
    run_id_artifact: dsl.Input[dsl.Artifact],
    mlflow_tracking_uri: str,
    metrics: dsl.Output[dsl.Artifact],
) -> str:
    """Run src/evaluate.py. Returns the MLflow run_id so callers can deref metrics."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    workdir = Path("/app")
    (workdir / "configs").mkdir(parents=True, exist_ok=True)
    (workdir / "configs" / "params.yaml").write_text(params_yaml)

    (workdir / "data" / "processed").mkdir(parents=True, exist_ok=True)
    (workdir / "models").mkdir(parents=True, exist_ok=True)
    shutil.copy(test_csv.path, workdir / "data" / "processed" / "test.csv")
    shutil.copy(model_artifact.path, workdir / "models" / "churn_model.pkl")
    shutil.copy(run_id_artifact.path, workdir / "models" / "run_id.txt")

    env = {**os.environ, "MLFLOW_TRACKING_URI": mlflow_tracking_uri}
    subprocess.run(["python", "src/evaluate.py"], cwd=str(workdir), check=True, env=env)

    shutil.copy(workdir / "metrics.json", metrics.path)

    # Return the MLflow run id so the autoresearch loop can fetch metrics by id
    # (instead of having to read metrics.json from a local file).
    run_id = (workdir / "models" / "run_id.txt").read_text().strip()
    print(f"MLflow run_id: {run_id}")
    print(f"Metrics: {Path(metrics.path).read_text()}")
    return run_id


@dsl.pipeline(name="churn-prediction-pipeline")
def churn_pipeline(
    params_yaml: str,
    raw_data_gcs_path: str = "gs://customer-churn-dvc-remote/raw/churn_data.csv",
    mlflow_tracking_uri: str = "http://mlflow.mlflow.svc.cluster.local:5000",
):
    """The full preprocess → train → evaluate DAG.

    `params_yaml` is the entire params.yaml content as a string. The autoresearch
    loop passes its mutated params here so each KFP run uses the proposed change.
    """
    preprocess_task = preprocess(
        params_yaml=params_yaml,
        raw_data_gcs_path=raw_data_gcs_path,
    )
    preprocess_task.set_cpu_request("200m").set_memory_request("512Mi")
    preprocess_task.set_cpu_limit("500m").set_memory_limit("1Gi")

    train_task = train(
        params_yaml=params_yaml,
        train_csv=preprocess_task.outputs["train_csv"],
        mlflow_tracking_uri=mlflow_tracking_uri,
    )
    train_task.set_cpu_request("300m").set_memory_request("512Mi")
    train_task.set_cpu_limit("1").set_memory_limit("2Gi")

    evaluate_task = evaluate(
        params_yaml=params_yaml,
        test_csv=preprocess_task.outputs["test_csv"],
        model_artifact=train_task.outputs["model_artifact"],
        run_id_artifact=train_task.outputs["run_id_artifact"],
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
        from pathlib import Path

        from kfp.client import Client

        client = Client(host=args.host)
        run = client.create_run_from_pipeline_package(
            output_path,
            arguments={
                "params_yaml": Path("configs/params.yaml").read_text(),
            },
            run_name="churn-prediction-run",
        )
        print(f"Run submitted: {run.run_id}")
