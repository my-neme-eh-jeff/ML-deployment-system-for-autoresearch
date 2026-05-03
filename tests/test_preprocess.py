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


def test_preprocess_handles_blank_numeric(sample_processed_data):
    """Raw `TotalCharges` had a blank string; coerce-to-numeric + median-fill should clean it."""
    train = pd.read_csv(sample_processed_data / "train.csv")
    test = pd.read_csv(sample_processed_data / "test.csv")
    combined = pd.concat([train, test])
    assert combined["TotalCharges"].notna().all()


def test_preprocess_stats_json(sample_processed_data):
    stats = json.loads((sample_processed_data / "stats.json").read_text())
    assert "total_rows" in stats
    assert "positive_rate" in stats
    assert 0 <= stats["positive_rate"] <= 1
    assert stats["target_column"] == "Churn"
