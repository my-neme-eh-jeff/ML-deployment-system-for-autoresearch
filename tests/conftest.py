"""Shared fixtures for tests.

Exposes two dataset shapes for the same preprocess/train/evaluate code path:

* Telco-Churn (legacy): wide schema with mixed numeric + categorical columns,
  exercises target mapping, drop_columns, and `coerce_to_numeric`.
* IEEE-CIS Fraud (production): the schema currently in configs/params.yaml.
  Heavy class imbalance, mostly numeric features.

The schema-agnostic invariants (split files exist, target encoded to {0,1},
stats.json written, drop_columns absent from output) are asserted against
BOTH via @pytest.mark.parametrize("dataset", ["telco", "ieee_cis"]).
Telco-specific invariants stay in tests/test_preprocess.py.
"""

import numpy as np
import pandas as pd
import pytest
import yaml


# Full Telco schema as a `dataset:` block — used by every test that wants the
# whole schema rather than the bad-baseline single-feature setup that
# configs/params.yaml ships with.
TELCO_PARAMS = {
    "dataset": {
        "csv_path": "data/churn_data.csv",
        "target_column": "Churn",
        "target_mapping": {"Yes": 1, "No": 0},
        "drop_columns": ["customerID"],
        "numeric_features": [
            "SeniorCitizen",
            "tenure",
            "MonthlyCharges",
            "TotalCharges",
        ],
        "categorical_features": [
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
        ],
    },
    "preprocess": {"test_size": 0.2, "random_state": 42},
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
    },
}


@pytest.fixture
def telco_params(tmp_path):
    p = tmp_path / "params.yaml"
    p.write_text(yaml.dump(TELCO_PARAMS))
    return p


@pytest.fixture
def sample_raw_data(tmp_path):
    """Tiny Telco-shaped CSV — six rows is enough for the schema tests."""
    df = pd.DataFrame(
        {
            "customerID": ["C001", "C002", "C003", "C004", "C005", "C006"],
            "gender": ["Male", "Female", "Male", "Female", "Male", "Female"],
            "SeniorCitizen": [0, 1, 0, 0, 1, 0],
            "Partner": ["Yes", "No", "Yes", "No", "Yes", "No"],
            "Dependents": ["No", "No", "Yes", "No", "Yes", "No"],
            "tenure": [12, 1, 50, 3, 60, 8],
            "PhoneService": ["Yes", "Yes", "Yes", "No", "Yes", "Yes"],
            "MultipleLines": ["No", "Yes", "Yes", "No phone service", "Yes", "No"],
            "InternetService": [
                "DSL",
                "Fiber optic",
                "DSL",
                "No",
                "Fiber optic",
                "DSL",
            ],
            "OnlineSecurity": ["Yes", "No", "Yes", "No internet service", "Yes", "No"],
            "OnlineBackup": ["No", "Yes", "Yes", "No internet service", "No", "Yes"],
            "DeviceProtection": ["No", "No", "Yes", "No internet service", "Yes", "No"],
            "TechSupport": ["Yes", "No", "Yes", "No internet service", "Yes", "No"],
            "StreamingTV": ["No", "Yes", "No", "No internet service", "Yes", "No"],
            "StreamingMovies": ["No", "No", "Yes", "No internet service", "Yes", "No"],
            "Contract": [
                "One year",
                "Month-to-month",
                "Two year",
                "Month-to-month",
                "Two year",
                "Month-to-month",
            ],
            "PaperlessBilling": ["Yes", "Yes", "No", "Yes", "No", "Yes"],
            "PaymentMethod": [
                "Bank transfer",
                "Electronic check",
                "Credit card",
                "Mailed check",
                "Bank transfer",
                "Electronic check",
            ],
            "MonthlyCharges": [50.0, 85.5, 30.0, 20.0, 90.0, 45.0],
            "TotalCharges": ["600.0", "85.5", "1500.0", " ", "5400.0", "360.0"],
            "Churn": ["No", "Yes", "No", "Yes", "No", "Yes"],
        }
    )
    path = tmp_path / "raw.csv"
    df.to_csv(path, index=False)
    return path


@pytest.fixture
def sample_processed_data(tmp_path, sample_raw_data, telco_params):
    """Run preprocessing with the Telco schema and return the output directory."""
    from src.preprocess import preprocess

    out_dir = str(tmp_path / "processed")
    preprocess(
        input_path=str(sample_raw_data),
        output_dir=out_dir,
        params_path=str(telco_params),
    )
    return tmp_path / "processed"


# ── IEEE-CIS Fraud schema (matches configs/params.yaml) ──────────────────────

IEEE_CIS_PARAMS = {
    "dataset": {
        "csv_path": "data/ieee_cis.parquet",
        "target_column": "isFraud",
        "drop_columns": ["TransactionID", "TransactionDT"],
        "numeric_features": ["TransactionAmt"],
        "categorical_features": ["ProductCD"],
    },
    "preprocess": {"test_size": 0.2, "random_state": 42},
    "train": {
        "model_type": "DecisionTreeClassifier",
        "random_state": 42,
        "max_depth": None,
        "max_features": None,
    },
}


@pytest.fixture
def ieee_cis_params(tmp_path):
    p = tmp_path / "params.yaml"
    p.write_text(yaml.dump(IEEE_CIS_PARAMS))
    return p


@pytest.fixture
def sample_ieee_cis_raw_data(tmp_path):
    """Synthetic IEEE-CIS-shaped CSV. 80 rows, ~12% positive rate to roughly
    mimic the real dataset's class imbalance without dragging the full 590K
    Kaggle file into the test suite."""
    rng = np.random.default_rng(42)
    n = 80
    df = pd.DataFrame(
        {
            "TransactionID": np.arange(1, n + 1),
            "TransactionDT": rng.integers(86400, 86400 * 30, size=n),
            "TransactionAmt": np.round(rng.exponential(scale=50.0, size=n), 2),
            "ProductCD": rng.choice(["W", "C", "R", "H", "S"], size=n),
            "isFraud": rng.choice([0, 1], size=n, p=[0.88, 0.12]),
        }
    )
    path = tmp_path / "raw_ieee_cis.csv"
    df.to_csv(path, index=False)
    return path


@pytest.fixture
def sample_ieee_cis_processed_data(tmp_path, sample_ieee_cis_raw_data, ieee_cis_params):
    """Run preprocessing with the IEEE-CIS schema and return the output directory."""
    from src.preprocess import preprocess

    out_dir = str(tmp_path / "processed_ieee_cis")
    preprocess(
        input_path=str(sample_ieee_cis_raw_data),
        output_dir=out_dir,
        params_path=str(ieee_cis_params),
    )
    return tmp_path / "processed_ieee_cis"


# ── Parametrized fixture: same code, both schemas ───────────────────────────


# `@pytest.fixture(params=...)` runs every test that takes `dataset_case` once
# per case. Each parametrization yields a (raw_path, params_path,
# processed_dir, target_col, dropped_cols) bundle so test bodies can assert
# the schema-agnostic invariants without hardcoding column names.
@pytest.fixture(params=["telco", "ieee_cis"])
def dataset_case(request, tmp_path):
    if request.param == "telco":
        # Reconstruct the telco artefacts inside this fixture so the
        # parametrization owns its own tmp_path subtree (avoids step-on with
        # the standalone Telco-only fixtures elsewhere in the file).
        raw_request = request.getfixturevalue("sample_raw_data")
        params_request = request.getfixturevalue("telco_params")
        processed = request.getfixturevalue("sample_processed_data")
        return {
            "name": "telco",
            "raw_path": raw_request,
            "params_path": params_request,
            "processed_dir": processed,
            "target_col": "Churn",
            "dropped_cols": ["customerID"],
        }
    raw = request.getfixturevalue("sample_ieee_cis_raw_data")
    params = request.getfixturevalue("ieee_cis_params")
    processed = request.getfixturevalue("sample_ieee_cis_processed_data")
    return {
        "name": "ieee_cis",
        "raw_path": raw,
        "params_path": params,
        "processed_dir": processed,
        "target_col": "isFraud",
        "dropped_cols": ["TransactionID", "TransactionDT"],
    }
