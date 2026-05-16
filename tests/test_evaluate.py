"""Tests for the evaluation stage."""

import json
import os

from src.evaluate import evaluate
from src.train import train


def test_evaluate_writes_metrics(sample_processed_data, tmp_path, telco_params):
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"
    model_path = str(tmp_path / "classifier.pkl")
    metrics_path = str(tmp_path / "metrics.json")
    run_id_path = str(tmp_path / "run_id.txt")

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        params_path=str(telco_params),
    )
    evaluate(
        test_path=str(sample_processed_data / "test.csv"),
        model_path=model_path,
        metrics_path=metrics_path,
        run_id_path=run_id_path,
        params_path=str(telco_params),
    )

    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert "accuracy" in metrics
    assert "auc_roc" in metrics
    assert "f1" in metrics
    assert all(0 <= v <= 1 for v in metrics.values())

    del os.environ["MLFLOW_TRACKING_URI"]


def test_evaluate_promotes_first_model_to_champion(
    sample_processed_data, tmp_path, telco_params
):
    """First model ever registered should auto-promote to champion."""
    import mlflow

    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"
    model_path = str(tmp_path / "classifier.pkl")
    run_id_path = str(tmp_path / "run_id.txt")

    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=model_path,
        params_path=str(telco_params),
    )
    evaluate(
        test_path=str(sample_processed_data / "test.csv"),
        model_path=model_path,
        metrics_path=str(tmp_path / "metrics.json"),
        run_id_path=run_id_path,
        params_path=str(telco_params),
    )

    client = mlflow.MlflowClient(f"sqlite:///{tmp_path}/mlflow.db")
    champion = client.get_model_version_by_alias("classifier", "champion")
    assert int(champion.version) == 1

    del os.environ["MLFLOW_TRACKING_URI"]


def test_evaluate_respects_min_improvement_threshold(
    sample_processed_data, tmp_path, telco_params, monkeypatch
):
    """A sub-threshold delta against the existing champion must NOT promote.

    Regression for the bug CLAUDE.md captures: evaluate.py promotes when
    `metrics[PRIMARY_METRIC] >= champion + min_improvement`. If the delta is
    positive but below threshold, the new version should land as `challenger`,
    not `champion`. Without this test, a regression that lowers the threshold
    to 0 (or drops the comparison entirely) would silently promote every run.
    """
    import mlflow

    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"

    # Force a real, large min_improvement so the test asserts on threshold
    # behaviour rather than the model's actual learning curve.
    import yaml as _yaml

    cfg = _yaml.safe_load(telco_params.read_text())
    cfg["auto_experiment"] = {"min_improvement": 0.5}
    telco_params.write_text(_yaml.dump(cfg))

    # Round 1: trains and promotes the first model to champion (first-time
    # bootstrap; no `min_improvement` gate applies). train.py writes
    # run_id.txt next to model_path — use separate subdirs so v1 and v2
    # don't trample each other's run_id pointer.
    v1_dir = tmp_path / "v1"
    v1_dir.mkdir()
    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=str(v1_dir / "classifier.pkl"),
        params_path=str(telco_params),
    )
    evaluate(
        test_path=str(sample_processed_data / "test.csv"),
        model_path=str(v1_dir / "classifier.pkl"),
        metrics_path=str(v1_dir / "metrics.json"),
        run_id_path=str(v1_dir / "run_id.txt"),
        params_path=str(telco_params),
    )

    client = mlflow.MlflowClient(f"sqlite:///{tmp_path}/mlflow.db")
    champion_v1 = client.get_model_version_by_alias("classifier", "champion")
    assert int(champion_v1.version) == 1

    # Round 2: train and evaluate a second model with the SAME data — its
    # AUC will be within ±0.5 of v1's. min_improvement=0.5 makes any
    # plausible delta sub-threshold, so v2 must stay challenger.
    v2_dir = tmp_path / "v2"
    v2_dir.mkdir()
    train(
        train_path=str(sample_processed_data / "train.csv"),
        model_path=str(v2_dir / "classifier.pkl"),
        params_path=str(telco_params),
    )
    evaluate(
        test_path=str(sample_processed_data / "test.csv"),
        model_path=str(v2_dir / "classifier.pkl"),
        metrics_path=str(v2_dir / "metrics.json"),
        run_id_path=str(v2_dir / "run_id.txt"),
        params_path=str(telco_params),
    )

    # Champion must still be v1 (sub-threshold delta did not promote).
    champion_after = client.get_model_version_by_alias("classifier", "champion")
    assert int(champion_after.version) == 1, (
        f"min_improvement=0.5 should block promotion but champion advanced to "
        f"v{champion_after.version}"
    )
    # v2 must exist as challenger (so the autoresearch loop can see it).
    challenger = client.get_model_version_by_alias("classifier", "challenger")
    assert int(challenger.version) == 2

    del os.environ["MLFLOW_TRACKING_URI"]
