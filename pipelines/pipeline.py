"""KFP pipeline that mirrors the DVC stages (preprocess → train → evaluate).

Each component shells out to the matching src/*.py so DVC and KFP run the same
code. Compile with `uv run python pipelines/pipeline.py`; add `--run` to submit
one run to a KFP host (`--host http://...`).
"""

import argparse

from kfp import compiler, dsl

BASE_IMAGE = "ghcr.io/my-neme-eh-jeff/pipeline-kfp:latest"


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

    import gcsfs
    import yaml

    cfg = yaml.safe_load(params_yaml)
    csv_path = cfg["dataset"]["csv_path"]
    (workdir / csv_path).parent.mkdir(parents=True, exist_ok=True)
    # Byte-level copy from GCS to local — avoids loading the full dataframe
    # into memory (a pd.read + pd.to_parquet round-trip on the 200K-row IEEE-CIS
    # parquet OOM'd this pod at the 1 GiB limit, with no traceback).
    gcsfs.GCSFileSystem().get(raw_data_gcs_path, str(workdir / csv_path))

    subprocess.run(["python", "-m", "src.preprocess"], cwd=str(workdir), check=True)

    shutil.copy(workdir / "data" / "processed" / "train.csv", train_csv.path)
    shutil.copy(workdir / "data" / "processed" / "test.csv", test_csv.path)
    shutil.copy(workdir / "data" / "processed" / "stats.json", stats.path)


@dsl.component(base_image=BASE_IMAGE)
def train(
    params_yaml: str,
    train_csv: dsl.Input[dsl.Dataset],
    mlflow_tracking_uri: str,
    kfp_run_id: str,
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

    # `kfp_run_id` is the resolved value of dsl.PIPELINE_JOB_ID_PLACEHOLDER
    # that the pipeline DAG passes in — KFP substitutes the placeholder at
    # runtime before the component starts. Previously we tried set_env_variable
    # with the placeholder constant; KFP v2 doesn't substitute env-var values
    # the same way (see kubeflow/pipelines#10155), so the loop's tag-based
    # MLflow lookup always missed and silently fell back to "latest run".
    env = {
        **os.environ,
        "MLFLOW_TRACKING_URI": mlflow_tracking_uri,
        "KFP_RUN_ID": kfp_run_id,
    }
    subprocess.run(["python", "-m", "src.train"], cwd=str(workdir), check=True, env=env)

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
    subprocess.run(
        ["python", "-m", "src.evaluate"], cwd=str(workdir), check=True, env=env
    )

    shutil.copy(workdir / "metrics.json", metrics.path)

    run_id = (workdir / "models" / "run_id.txt").read_text().strip()
    print(f"MLflow run_id: {run_id}")
    print(f"Metrics: {Path(metrics.path).read_text()}")
    return run_id


@dsl.pipeline(name="classifier-training-pipeline")
def classifier_pipeline(
    params_yaml: str,
    kfp_run_id: str,
    raw_data_gcs_path: str = "gs://customer-churn-dvc-remote/raw/ieee_cis.parquet",
    mlflow_tracking_uri: str = "http://mlflow.mlflow.svc.cluster.local:5000",
):
    """Full preprocess → train → evaluate DAG. params_yaml is the entire file content."""
    preprocess_task = preprocess(
        params_yaml=params_yaml,
        raw_data_gcs_path=raw_data_gcs_path,
    )
    # Memory budgets sized for the 200K × 339 IEEE-CIS subsample. Pandas DF
    # ~800 MB, peaks higher during sklearn fit. 1 GiB OOM'd silently at the OS
    # level (exit 137) on the previous run.
    preprocess_task.set_cpu_request("300m").set_memory_request("1Gi")
    preprocess_task.set_cpu_limit("1").set_memory_limit("3Gi")

    # `kfp_run_id` here is whatever string the submitter passes in via
    # `arguments={..., "kfp_run_id": "<uuid>"}`. KFP v2 does NOT substitute
    # dsl.PIPELINE_JOB_ID_PLACEHOLDER when it's used as a component param
    # value (only command/arg slots get substituted) — the literal
    # `{{$.pipeline_job_uuid}}` string showed up in MLflow tags during the
    # smoke test. So the autoresearch loop generates a UUID client-side
    # and passes it as a submission argument; train.py uses it as the
    # MLflow run tag the controller queries by.
    train_task = train(
        params_yaml=params_yaml,
        train_csv=preprocess_task.outputs["train_csv"],
        mlflow_tracking_uri=mlflow_tracking_uri,
        kfp_run_id=kfp_run_id,
    )
    train_task.set_cpu_request("500m").set_memory_request("1Gi")
    train_task.set_cpu_limit("2").set_memory_limit("4Gi")

    evaluate_task = evaluate(
        params_yaml=params_yaml,
        test_csv=preprocess_task.outputs["test_csv"],
        model_artifact=train_task.outputs["model_artifact"],
        run_id_artifact=train_task.outputs["run_id_artifact"],
        mlflow_tracking_uri=mlflow_tracking_uri,
    )
    evaluate_task.set_cpu_request("300m").set_memory_request("1Gi")
    evaluate_task.set_cpu_limit("1").set_memory_limit("3Gi")


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
