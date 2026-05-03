"""Feature engineering shared by train and evaluate.

If `train.py` adds a column to X here, `evaluate.py` and any inference code must
apply the same function so the saved sklearn pipeline sees the columns it was
fit on. Centralizing it here is the only safe place.
"""

import pandas as pd


def apply_feature_engineering(X: pd.DataFrame, train_params: dict) -> pd.DataFrame:
    """Add engineered columns to X based on flags in `train` params.

    Returns a new DataFrame; does not mutate the input. The list of derived
    columns this function emits, given a params dict, is also returned by
    `derived_numeric_features(train_params)` so train can extend the
    ColumnTransformer's numeric column list accordingly.
    """
    X = X.copy()

    # Example flag: only fires when the dataset actually has the source columns.
    if (
        train_params.get("add_charges_per_month", False)
        and "TotalCharges" in X.columns
        and "tenure" in X.columns
    ):
        X["charges_per_month"] = X["TotalCharges"] / (X["tenure"] + 1)

    return X


def derived_numeric_features(train_params: dict) -> list[str]:
    """Names of columns that `apply_feature_engineering` may add to X.

    Used by train.py to extend the numeric features the ColumnTransformer
    handles, so the saved pipeline expects exactly the columns produced.
    """
    derived: list[str] = []
    if train_params.get("add_charges_per_month", False):
        derived.append("charges_per_month")
    return derived
