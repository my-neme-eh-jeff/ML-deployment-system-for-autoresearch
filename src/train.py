"""Train a customer churn prediction model and log to MLflow."""

import pickle
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
import yaml
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    ExtraTreesClassifier,
    GradientBoostingClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

NUMERIC_FEATURES = [
    "SeniorCitizen",
    "tenure",
    "MonthlyCharges",
    "TotalCharges",
]

CATEGORICAL_FEATURES = [
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
]

TARGET = "Churn"
MODEL_NAME = "churn-model"

_CLASSIFIERS = {
    "RandomForestClassifier": RandomForestClassifier,
    "ExtraTreesClassifier": ExtraTreesClassifier,
    "GradientBoostingClassifier": GradientBoostingClassifier,
    "HistGradientBoostingClassifier": HistGradientBoostingClassifier,
}


def _build_classifier(params: dict):
    model_type = params.get("model_type", "RandomForestClassifier")
    if model_type not in _CLASSIFIERS:
        raise ValueError(
            f"Unknown model_type '{model_type}'. Choose from: {list(_CLASSIFIERS)}"
        )

    clf_cls = _CLASSIFIERS[model_type]
    random_state = params.get("random_state", 42)

    if model_type in ("RandomForestClassifier", "ExtraTreesClassifier"):
        kwargs = dict(
            n_estimators=params.get("n_estimators", 100),
            random_state=random_state,
            max_depth=params.get("max_depth") or None,
            min_samples_split=params.get("min_samples_split", 2),
            min_samples_leaf=params.get("min_samples_leaf", 1),
            max_features=params.get("max_features", "sqrt"),
            class_weight=params.get("class_weight") or None,
            bootstrap=params.get("bootstrap", True),
        )
    elif model_type == "GradientBoostingClassifier":
        kwargs = dict(
            n_estimators=params.get("n_estimators", 100),
            learning_rate=params.get("learning_rate", 0.1),
            max_depth=params.get("max_depth") or 3,
            subsample=params.get("subsample", 1.0),
            random_state=random_state,
        )
    elif model_type == "HistGradientBoostingClassifier":
        kwargs = dict(
            max_iter=params.get("n_estimators", 100),
            learning_rate=params.get("learning_rate", 0.1),
            max_depth=params.get("max_depth") or None,
            random_state=random_state,
        )

    return clf_cls(**kwargs)


def build_pipeline(params: dict) -> Pipeline:
    numeric_features = list(NUMERIC_FEATURES)

    use_log = params.get("use_log_transform", False)

    if use_log:
        numeric_transformer = Pipeline(
            [
                ("log", FunctionTransformer(np.log1p, validate=False)),
                ("scaler", StandardScaler()),
            ]
        )
    else:
        numeric_transformer = StandardScaler()

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", numeric_transformer, numeric_features),
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                CATEGORICAL_FEATURES,
            ),
        ]
    )

    return Pipeline(
        [
            ("preprocessor", preprocessor),
            ("classifier", _build_classifier(params)),
        ]
    )


def _apply_feature_engineering(X: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Apply optional feature engineering that adds new columns to X."""
    X = X.copy()

    if params.get("add_charges_per_month", False):
        X["charges_per_month"] = X["TotalCharges"] / (X["tenure"] + 1)

    return X


def train(
    train_path: str = "data/processed/train.csv",
    model_path: str = "models/churn_model.pkl",
    params_path: str = "configs/params.yaml",
):
    with open(params_path) as f:
        all_params = yaml.safe_load(f)
    params = all_params["train"]

    df = pd.read_csv(train_path)
    X = df.drop(columns=[TARGET])
    y = df[TARGET]

    X = _apply_feature_engineering(X, params)

    if (
        params.get("add_charges_per_month", False)
        and "charges_per_month" not in NUMERIC_FEATURES
    ):
        numeric_features_extended = NUMERIC_FEATURES + ["charges_per_month"]
        use_log = params.get("use_log_transform", False)
        if use_log:
            numeric_transformer = Pipeline(
                [
                    ("log", FunctionTransformer(np.log1p, validate=False)),
                    ("scaler", StandardScaler()),
                ]
            )
        else:
            numeric_transformer = StandardScaler()

        preprocessor = ColumnTransformer(
            transformers=[
                ("num", numeric_transformer, numeric_features_extended),
                (
                    "cat",
                    OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                    CATEGORICAL_FEATURES,
                ),
            ]
        )
        pipeline = Pipeline(
            [
                ("preprocessor", preprocessor),
                ("classifier", _build_classifier(params)),
            ]
        )
    else:
        pipeline = build_pipeline(params)

    mlflow.set_experiment("churn-prediction")

    with mlflow.start_run(run_name="train") as run:
        mlflow.log_params({k: v for k, v in params.items() if v is not None})
        mlflow.log_param("n_features", X.shape[1])
        mlflow.log_param("n_train_samples", X.shape[0])

        pipeline.fit(X, y)

        Path(model_path).parent.mkdir(exist_ok=True)
        with open(model_path, "wb") as f:
            pickle.dump(pipeline, f)

        # evaluate.py reads run_id.txt to log metrics on the exact training run.
        run_id_path = Path(model_path).parent / "run_id.txt"
        run_id_path.write_text(run.info.run_id)

        mlflow.sklearn.log_model(
            pipeline,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
        )

        print(f"Model saved to {model_path}")
        print(f"MLflow run ID: {run.info.run_id}")


if __name__ == "__main__":
    train()
