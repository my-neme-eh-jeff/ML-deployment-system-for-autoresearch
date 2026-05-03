"""KFP pipeline that mirrors the DVC stages (preprocess → train → evaluate).

Each component shells out to the matching src/*.py so DVC and KFP run the same
code. Compile with `uv run python pipelines/pipeline.py`; add `--run` to submit
one run to a KFP host (`--host http://...`).
"""

import argparse

from kfp import compiler, dsl

# Image name still `churn-kfp` for now — CI publishes that path. The rename to
# `pipeline-kfp` will happen atomically in a follow-up PR that also updates the
# Docker push target in .github/workflows/ci.yaml.
BASE_IMAGE = "ghcr.io/my-neme-eh-jeff/churn-kfp:latest"


@dsl.component(base_image=BASE_IMAGE)
def preprocess(
    params_yaml: str,
    raw_data_gcs_path: str,
    train_csv: dsl.Output[dsl.Dataset],
    test_csv: dsl.Output[dsl.Dataset],
    stats: dsl.Output[dsl.Artifact],
):
    import shutil
    import subprocess
    from pathlib import Path

    workdir = Path("/app")
    (workdir / "configs").mkdir(parents=True, exist_ok=True)
    (workdir / "configs" / "params.yaml").write_text(params_yaml)

    import pandas as pd
    import yaml

    cfg = yaml.safe_load(params_yaml)
    csv_path = cfg["dataset"]["csv_path"]
    (workdir / "data").mkdir(parents=True, exist_ok=True)
    # Pull the raw dataset from GCS and re-materialize at the path the
    # params.yaml `dataset.csv_path` points to.
    if raw_data_gcs_path.endswith(".parquet"):
        raw = pd.read_parquet(raw_data_gcs_path)
        raw.to_parquet(workdir / csv_path, index=False)
    else:
        raw = pd.read_csv(raw_data_gcs_path)
        raw.to_csv(workdir / csv_path, index=False)

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

    shutil.copy(workdir / "models" / "classifier.pkl", model_artifact.path)
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
    """Returns the MLflow run_id so callers can fetch metrics by id."""
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
    shutil.copy(model_artifact.path, workdir / "models" / "classifier.pkl")
    shutil.copy(run_id_artifact.path, workdir / "models" / "run_id.txt")

    env = {**os.environ, "MLFLOW_TRACKING_URI": mlflow_tracking_uri}
    subprocess.run(["python", "src/evaluate.py"], cwd=str(workdir), check=True, env=env)

    shutil.copy(workdir / "metrics.json", metrics.path)

    run_id = (workdir / "models" / "run_id.txt").read_text().strip()
    print(f"MLflow run_id: {run_id}")
    print(f"Metrics: {Path(metrics.path).read_text()}")
    return run_id


@dsl.pipeline(name="classifier-training-pipeline")
def classifier_pipeline(
    params_yaml: str,
    raw_data_gcs_path: str = "gs://customer-churn-dvc-remote/raw/ieee_cis.parquet",
    mlflow_tracking_uri: str = "http://mlflow.mlflow.svc.cluster.local:5000",
):
    """Full preprocess → train → evaluate DAG. params_yaml is the entire file content."""
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

    output_path = "pipelines/pipeline.yaml"
    compiler.Compiler().compile(classifier_pipeline, output_path)

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
            run_name="classifier-training-run",
        )
        print(f"Run submitted: {run.run_id}")
