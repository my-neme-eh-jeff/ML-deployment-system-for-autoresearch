"""Tests for the preprocessing stage."""

import json

import pandas as pd


def test_preprocess_creates_splits(sample_processed_data):
    assert (sample_processed_data / "train.csv").exists()
    assert (sample_processed_data / "test.csv").exists()
    assert (sample_processed_data / "stats.json").exists()


def test_preprocess_drops_columns(sample_processed_data):
    train = pd.read_csv(sample_processed_data / "train.csv")
    assert "customerID" not in train.columns


def test_preprocess_encodes_target(sample_processed_data):
    train = pd.read_csv(sample_processed_data / "train.csv")
    assert set(train["Churn"].unique()).issubset({0, 1})


def test_preprocess_coerces_blank_numeric_to_nan(sample_processed_data):
    """Raw `TotalCharges` had a blank string; coerce-to-numeric should turn
    that into NaN. Imputation is now done by the SimpleImputer inside the
    sklearn pipeline (train.py:build_pipeline) so it fits on TRAIN ONLY —
    test-set values can't leak into the median. preprocess.py used to
    `.fillna(median())` on the full DataFrame before split, which is the
    canonical data-leakage pattern.
    """
    train = pd.read_csv(sample_processed_data / "train.csv")
    # Column should be float (coerce succeeded); blanks become NaN, not strings.
    assert train["TotalCharges"].dtype.kind == "f"


def test_preprocess_stats_json(sample_processed_data):
    stats = json.loads((sample_processed_data / "stats.json").read_text())
    assert "total_rows" in stats
    assert "positive_rate" in stats
    assert 0 <= stats["positive_rate"] <= 1
    assert stats["target_column"] == "Churn"
