"""Generic CSV preprocessor — schema is read from configs/params.yaml."""

import json
from pathlib import Path

import pandas as pd
import yaml
from sklearn.model_selection import train_test_split


def load_params(params_path: str = "configs/params.yaml") -> dict:
    with open(params_path) as f:
        return yaml.safe_load(f)


def preprocess(
    output_dir: str = "data/processed",
    params_path: str = "configs/params.yaml",
    input_path: str | None = None,
    test_size: float | None = None,
    seed: int | None = None,
):
    params = load_params(params_path)
    dataset = params["dataset"]
    pre = params["preprocess"]

    csv_path = input_path or dataset["csv_path"]
    test_size = test_size if test_size is not None else pre["test_size"]
    seed = seed if seed is not None else pre["random_state"]

    df = (
        pd.read_parquet(csv_path)
        if csv_path.endswith(".parquet")
        else pd.read_csv(csv_path)
    )

    target_col = dataset["target_column"]
    numeric = list(dataset.get("numeric_features", []))
    categorical = list(dataset.get("categorical_features", []))
    drop = list(dataset.get("drop_columns", []))

    # Drop unwanted columns first
    for col in drop:
        if col in df.columns:
            df = df.drop(columns=[col])

    # Coerce numeric columns to float and median-fill any NaNs that result —
    # handles "blank" strings and similar quirks generically (e.g. TotalCharges
    # in the Telco churn dataset has whitespace strings for some new customers).
    for col in numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].fillna(df[col].median())

    target_mapping = dataset.get("target_mapping")
    if target_mapping:
        df[target_col] = df[target_col].map(target_mapping)

    if target_col not in df.columns:
        raise ValueError(
            f"Required target column {target_col!r} missing from {csv_path}"
        )
    # Capture the full available catalog before we filter, so stats.json can
    # carry the full column list to downstream consumers.
    available_columns = [c for c in df.columns if c != target_col]
    missing = [c for c in numeric + categorical if c not in df.columns]
    if missing:
        print(
            f"WARNING: dropping {len(missing)} missing column(s) from schema: "
            f"{missing[:10]}{'...' if len(missing) > 10 else ''}"
        )
    numeric = [c for c in numeric if c in df.columns]
    categorical = [c for c in categorical if c in df.columns]
    keep = [target_col, *numeric, *categorical]
    df = df[keep]

    # Stratify only when the target has at least two classes with multiple rows.
    stratify = df[target_col] if df[target_col].nunique() > 1 else None
    train_df, test_df = train_test_split(
        df, test_size=test_size, random_state=seed, stratify=stratify
    )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    train_df.to_csv(out / "train.csv", index=False)
    test_df.to_csv(out / "test.csv", index=False)

    stats = {
        "total_rows": len(df),
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "positive_rate": float(df[target_col].mean()),
        "target_column": target_col,
        "numeric_features": numeric,
        "categorical_features": categorical,
        "all_columns": available_columns,
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=2))
    print(
        f"Train: {len(train_df)}, Test: {len(test_df)}, "
        f"positive rate: {stats['positive_rate']:.2%}, "
        f"features: {len(numeric)} numeric + {len(categorical)} categorical"
    )


if __name__ == "__main__":
    preprocess()
