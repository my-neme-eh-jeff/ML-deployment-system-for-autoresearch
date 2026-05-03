"""Generic CSV preprocessor — schema is read from configs/params.yaml.

Extended to auto-discover available columns and expand numeric features
with whatever IEEE-CIS columns are actually present in the parquet file.
"""

import json
from pathlib import Path

import pandas as pd
import yaml
from sklearn.model_selection import train_test_split


def load_params(params_path: str = "configs/params.yaml") -> dict:
    with open(params_path) as f:
        return yaml.safe_load(f)


# IEEE-CIS candidate columns in rough order of expected importance.
# Only those actually present in the file will be used.
_IEEE_CANDIDATE_NUMERIC = [
    "TransactionAmt",
    "card1",
    "card2",
    "card3",
    "card5",
    "addr1",
    "addr2",
    "dist1",
    "dist2",
    "C1",
    "C2",
    "C3",
    "C4",
    "C5",
    "C6",
    "C7",
    "C8",
    "C9",
    "C10",
    "C11",
    "C12",
    "C13",
    "C14",
    "D1",
    "D2",
    "D3",
    "D4",
    "D5",
    "D6",
    "D7",
    "D8",
    "D9",
    "D10",
    "D11",
    "D12",
    "D13",
    "D14",
    "D15",
    "V1",
    "V2",
    "V3",
    "V4",
    "V5",
    "V6",
    "V7",
    "V8",
    "V9",
    "V10",
    "V11",
    "V12",
    "V13",
    "V14",
    "V15",
    "V16",
    "V17",
    "V18",
    "V19",
    "V20",
    "V21",
    "V22",
    "V23",
    "V24",
    "V25",
    "V26",
    "V27",
    "V28",
    "V29",
    "V30",
    "V31",
    "V32",
    "V33",
    "V34",
    "V35",
    "V36",
    "V37",
    "V38",
    "V39",
    "V40",
    "V41",
    "V42",
    "V43",
    "V44",
    "V45",
    "V46",
    "V47",
    "V48",
    "V49",
    "V50",
]

_IEEE_CANDIDATE_CATEGORICAL = [
    "ProductCD",
    "card4",
    "card6",
    "P_emaildomain",
    "R_emaildomain",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "M6",
    "M7",
    "M8",
    "M9",
]


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
    drop = list(dataset.get("drop_columns", []))

    # Drop unwanted columns first
    for col in drop:
        if col in df.columns:
            df = df.drop(columns=[col])

    # Auto-discover available columns from the candidate lists
    available_cols = set(df.columns)
    numeric = [c for c in _IEEE_CANDIDATE_NUMERIC if c in available_cols]
    categorical = [c for c in _IEEE_CANDIDATE_CATEGORICAL if c in available_cols]

    # Fall back to declared features if no candidates found (non-IEEE dataset)
    if not numeric:
        numeric = list(dataset.get("numeric_features", []))
    if not categorical:
        categorical = list(dataset.get("categorical_features", []))

    print(f"Auto-discovered {len(numeric)} numeric features: {numeric[:10]}...")
    print(f"Auto-discovered {len(categorical)} categorical features: {categorical}")

    # Coerce numeric columns to float and median-fill any NaNs
    for col in numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].fillna(df[col].median())

    target_mapping = dataset.get("target_mapping")
    if target_mapping:
        df[target_col] = df[target_col].map(target_mapping)

    # Keep only the columns the rest of the pipeline declares it cares about.
    keep = [target_col, *numeric, *categorical]
    missing = [c for c in keep if c not in df.columns]
    if missing:
        raise ValueError(
            f"Columns declared in params.dataset are missing from {csv_path}: {missing}"
        )
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
        "all_columns": [c for c in df.columns if c != target_col],
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=2))
    print(
        f"Train: {len(train_df)}, Test: {len(test_df)}, "
        f"positive rate: {stats['positive_rate']:.2%}, "
        f"features: {len(numeric)} numeric + {len(categorical)} categorical"
    )


if __name__ == "__main__":
    preprocess()
