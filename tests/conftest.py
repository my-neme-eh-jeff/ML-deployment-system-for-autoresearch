"""Shared fixtures for tests."""

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
        "add_charges_per_month": False,
    },
    "evaluate": {"primary_metric": "auc_roc", "auto_promote": True},
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
