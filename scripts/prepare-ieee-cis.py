"""Prepare the IEEE-CIS Fraud Detection raw CSV for the autoresearch pipeline.

Reads `data/ieee_cis_raw.csv` (the Kaggle `train_transaction.csv` renamed),
stratified-samples to a target row count (default 200K), drops columns with
>nan_threshold% NaN, writes Parquet at `data/ieee_cis.parquet`.

Run once locally after the raw CSV is in place; afterwards `dvc add` the
parquet and `dvc push` to put it on the GCS remote.
"""

import argparse
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

PROJECT_ROOT = Path(__file__).parent.parent
RAW_PATH = PROJECT_ROOT / "data" / "ieee_cis_raw.csv"
OUT_PATH = PROJECT_ROOT / "data" / "ieee_cis.parquet"


def main(target_rows: int, nan_threshold: float):
    print(f"Reading {RAW_PATH} ...")
    df = pd.read_csv(RAW_PATH)
    print(f"  raw shape: {df.shape}, fraud rate: {df['isFraud'].mean():.4f}")

    drop_cols = [c for c in df.columns if df[c].isna().mean() > nan_threshold]
    print(
        f"Dropping {len(drop_cols)} columns with >{nan_threshold * 100:.0f}% NaN "
        f"(e.g. {drop_cols[:5]}{'...' if len(drop_cols) > 5 else ''})"
    )
    df = df.drop(columns=drop_cols)

    if len(df) > target_rows:
        df, _ = train_test_split(
            df,
            train_size=target_rows,
            stratify=df["isFraud"],
            random_state=42,
        )
        df = df.reset_index(drop=True)
    print(f"  sampled shape: {df.shape}, fraud rate: {df['isFraud'].mean():.4f}")

    print(f"Writing {OUT_PATH} ...")
    df.to_parquet(OUT_PATH, index=False)
    size_mb = OUT_PATH.stat().st_size / (1024 * 1024)
    print(f"  done. {size_mb:.1f} MB on disk")

    print("\nNext: `uv run dvc add data/ieee_cis.parquet && uv run dvc push`")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--nan-threshold", type=float, default=0.9)
    args = parser.parse_args()
    main(args.rows, args.nan_threshold)
