"""Asserts the 'any binary CSV plugs in' pitch by running the full
preprocess → train → evaluate code path over two real schemas (Telco-Churn
and IEEE-CIS Fraud). The `dataset_case` fixture in conftest.py is
parametrized — every test below runs twice.

These tests complement the legacy Telco-specific tests in test_preprocess.py /
test_train.py / test_evaluate.py. Those assert column-name-level invariants
that only make sense for Telco; the tests here assert structural invariants
that must hold for any binary classification CSV the user plugs in.
"""

import json
import os
import pickle

import pandas as pd

from src.evaluate import evaluate
from src.train import train


def test_preprocess_writes_splits_and_stats(dataset_case):
    proc = dataset_case["processed_dir"]
    assert (proc / "train.csv").exists()
    assert (proc / "test.csv").exists()
    assert (proc / "stats.json").exists()
    stats = json.loads((proc / "stats.json").read_text())
    assert stats["target_column"] == dataset_case["target_col"]
    assert 0 <= stats["positive_rate"] <= 1


def test_preprocess_drops_configured_columns(dataset_case):
    train_df = pd.read_csv(dataset_case["processed_dir"] / "train.csv")
    for col in dataset_case["dropped_cols"]:
        assert col not in train_df.columns, (
            f"{dataset_case['name']}: {col} should have been dropped per "
            f"params.dataset.drop_columns"
        )


def test_preprocess_target_is_binary_ints(dataset_case):
    train_df = pd.read_csv(dataset_case["processed_dir"] / "train.csv")
    target = train_df[dataset_case["target_col"]]
    assert set(target.unique()).issubset({0, 1}), (
        f"{dataset_case['name']}: target must be encoded to {{0,1}} "
        f"(got {sorted(target.unique())})"
    )


def test_train_to_evaluate_round_trip(tmp_path, dataset_case):
    """preprocess output → train → evaluate produces metrics with all the
    keys downstream (autoresearch loop, dashboards) reads. Same code path,
    different schema."""
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path}/mlflow.db"

    model_path = str(tmp_path / "classifier.pkl")
    train(
        train_path=str(dataset_case["processed_dir"] / "train.csv"),
        model_path=model_path,
        params_path=str(dataset_case["params_path"]),
    )
    assert (tmp_path / "classifier.pkl").exists()
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    assert hasattr(model, "predict")

    metrics_path = str(tmp_path / "metrics.json")
    evaluate(
        test_path=str(dataset_case["processed_dir"] / "test.csv"),
        model_path=model_path,
        metrics_path=metrics_path,
        run_id_path=str(tmp_path / "run_id.txt"),
        params_path=str(dataset_case["params_path"]),
    )

    metrics = json.loads((tmp_path / "metrics.json").read_text())
    for k in ("accuracy", "auc_roc", "f1", "precision", "recall", "pr_auc"):
        assert k in metrics, f"{dataset_case['name']}: missing metric {k}"
        assert 0 <= metrics[k] <= 1

    del os.environ["MLFLOW_TRACKING_URI"]
