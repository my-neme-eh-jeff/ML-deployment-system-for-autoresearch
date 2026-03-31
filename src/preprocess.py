"""Preprocess raw churn data into train/test splits."""

import json
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

TARGET = "Churn"


def preprocess(
    input_path: str = "data/churn_data.csv",
    output_dir: str = "data/processed",
    test_size: float = 0.2,
    seed: int = 42,
):
    df = pd.read_csv(input_path)

    # Clean TotalCharges (has some blank strings)
    df["TotalCharges"] = pd.to_numeric(df["TotalCharges"], errors="coerce")
    df["TotalCharges"] = df["TotalCharges"].fillna(df["TotalCharges"].median())

    # Encode target: Yes=1, No=0
    df[TARGET] = df[TARGET].map({"Yes": 1, "No": 0})

    # Drop customer ID
    df = df.drop(columns=["customerID"])

    # Split
    train_df, test_df = train_test_split(
        df, test_size=test_size, random_state=seed, stratify=df[TARGET]
    )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    train_df.to_csv(out / "train.csv", index=False)
    test_df.to_csv(out / "test.csv", index=False)

    stats = {
        "total_rows": len(df),
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "churn_rate": float(df[TARGET].mean()),
        "features": [c for c in df.columns if c != TARGET],
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=2))
    print(
        f"Train: {len(train_df)}, Test: {len(test_df)}, Churn rate: {stats['churn_rate']:.2%}"
    )


if __name__ == "__main__":
    preprocess()
