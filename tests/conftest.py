"""Shared fixtures for tests."""

import pandas as pd
import pytest


@pytest.fixture
def sample_raw_data(tmp_path):
    """Create a small raw dataset matching the Kaggle schema."""
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
def sample_processed_data(tmp_path, sample_raw_data):
    """Run preprocessing and return the output directory."""
    from src.preprocess import preprocess

    out_dir = str(tmp_path / "processed")
    preprocess(input_path=str(sample_raw_data), output_dir=out_dir)
    return tmp_path / "processed"
