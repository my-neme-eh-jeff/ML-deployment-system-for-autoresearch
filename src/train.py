"""Train a binary classifier — schema and hyperparameters from configs/params.yaml."""

import os
import pickle
from functools import partial
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
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeClassifier

from src.features import apply_feature_engineering, derived_numeric_features

MODEL_NAME = "classifier"
EXPERIMENT_NAME = "training"

_CLASSIFIERS = {
    "DecisionTreeClassifier": DecisionTreeClassifier,
    "RandomForestClassifier": RandomForestClassifier,
    "ExtraTreesClassifier": ExtraTreesClassifier,
    "GradientBoostingClassifier": GradientBoostingClassifier,
    "HistGradientBoostingClassifier": HistGradientBoostingClassifier,
    "LogisticRegression": LogisticRegression,
}


def _build_classifier(params: dict):
    model_type = params.get("model_type", "DecisionTreeClassifier")
    if model_type not in _CLASSIFIERS:
        raise ValueError(
            f"Unknown model_type {model_type!r}. Choose from: {list(_CLASSIFIERS)}"
        )
    cls = _CLASSIFIERS[model_type]
    rs = params.get("random_state", 42)

    if model_type == "DecisionTreeClassifier":
        return cls(
            random_state=rs,
            max_depth=params.get("max_depth") or None,
            min_samples_split=params.get("min_samples_split", 2),
            min_samples_leaf=params.get("min_samples_leaf", 1),
            max_features=params.get("max_features"),
            class_weight=params.get("class_weight") or None,
        )
    if model_type in ("RandomForestClassifier", "ExtraTreesClassifier"):
        return cls(
            n_estimators=params.get("n_estimators", 100),
            random_state=rs,
            max_depth=params.get("max_depth") or None,
            min_samples_split=params.get("min_samples_split", 2),
            min_samples_leaf=params.get("min_samples_leaf", 1),
            max_features=params.get("max_features", "sqrt"),
            class_weight=params.get("class_weight") or None,
            bootstrap=params.get("bootstrap", True),
        )
    if model_type == "GradientBoostingClassifier":
        return cls(
            n_estimators=params.get("n_estimators", 100),
            learning_rate=params.get("learning_rate", 0.1),
            max_depth=params.get("max_depth") or 3,
            subsample=params.get("subsample", 1.0),
            random_state=rs,
        )
    if model_type == "HistGradientBoostingClassifier":
        return cls(
            max_iter=params.get("n_estimators", 100),
            learning_rate=params.get("learning_rate", 0.1),
            max_depth=params.get("max_depth") or None,
            min_samples_leaf=params.get("min_samples_leaf", 20),
            l2_regularization=params.get("l2_regularization", 0.0),
            early_stopping=params.get("early_stopping", False),
            validation_fraction=params.get("validation_fraction", 0.1),
            n_iter_no_change=params.get("n_iter_no_change", 10),
            random_state=rs,
        )
    if model_type == "LogisticRegression":
        return cls(
            C=params.get("C", 1.0),
            random_state=rs,
            class_weight=params.get("class_weight") or None,
            max_iter=params.get("max_iter", 1000),
        )


def build_pipeline(dataset: dict, params: dict) -> Pipeline:
    """Build the full sklearn Pipeline.

    Three steps, in order:
      1. feature_eng — applies apply_feature_engineering(X, params). Derives
         columns like charges_per_month from raw input. This step is part of
         the saved pipeline so train, evaluate, AND inference apply the same
         transformation — no drift possible.
      2. preprocessor — ColumnTransformer with SimpleImputer(median) +
         StandardScaler on numeric, OneHotEncoder on categorical. The imputer
         fits ON TRAIN ONLY (via Pipeline contract) so test/inference rows
         can't leak into the median.
      3. classifier — whatever model_type says.
    """
    base_numeric = list(dataset.get("numeric_features", []))
    derived = [c for c in derived_numeric_features(params) if c not in base_numeric]
    numeric = base_numeric + derived
    categorical = list(dataset.get("categorical_features", []))

    # Step 1: feature engineering bound to current `params`. `partial` is
    # picklable; lambdas are not — and the whole pipeline is pickled to disk.
    feature_eng = FunctionTransformer(
        partial(apply_feature_engineering, train_params=params),
        validate=False,
    )

    # Step 2's numeric branch: impute → (optional log) → scale.
    imputer_step = ("imputer", SimpleImputer(strategy="median"))
    if params.get("use_log_transform"):
        numeric_transformer = Pipeline(
            [
                imputer_step,
                ("log", FunctionTransformer(np.log1p, validate=False)),
                ("scaler", StandardScaler()),
            ]
        )
    else:
        numeric_transformer = Pipeline([imputer_step, ("scaler", StandardScaler())])

    transformers = []
    if numeric:
        transformers.append(("num", numeric_transformer, numeric))
    if categorical:
        transformers.append(
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                categorical,
            )
        )
    if not transformers:
        raise ValueError(
            "No features declared — set numeric_features or categorical_features in params.dataset."
        )

    preprocessor = ColumnTransformer(transformers=transformers, remainder="drop")
    return Pipeline(
        [
            ("feature_eng", feature_eng),
            ("preprocessor", preprocessor),
            ("classifier", _build_classifier(params)),
        ]
    )


def load_params(params_path: str) -> tuple[dict, dict]:
    with open(params_path) as f:
        cfg = yaml.safe_load(f)
    return cfg["dataset"], cfg["train"]


def train(
    train_path: str = "data/processed/train.csv",
    model_path: str = "models/classifier.pkl",
    params_path: str = "configs/params.yaml",
):
    dataset, params = load_params(params_path)
    target = dataset["target_column"]

    df = pd.read_csv(train_path)
    X = df.drop(columns=[target])
    y = df[target]
    # Note: apply_feature_engineering is NO LONGER called here — it's the
    # first step of the sklearn Pipeline (built in build_pipeline below),
    # so the saved model auto-applies it at inference too. Calling it
    # manually here would double-apply it.

    # Filter the schema against the actual training frame: any column the
    # autoresearch loop proposed that didn't survive preprocess (e.g. a
    # dropped-during-prep V or D feature) is silently dropped here too. The
    # ColumnTransformer would otherwise raise on the first missing column.
    dataset = {
        **dataset,
        "numeric_features": [
            c for c in dataset.get("numeric_features", []) if c in X.columns
        ],
        "categorical_features": [
            c for c in dataset.get("categorical_features", []) if c in X.columns
        ],
    }

    pipeline = build_pipeline(dataset, params)

    mlflow.set_experiment(EXPERIMENT_NAME)
    with mlflow.start_run(run_name="train") as run:
        # Tag the run with the KFP run id (when running inside a KFP pod) so
        # the autoresearch loop can fetch *this exact run's* metrics by tag
        # instead of grabbing the latest run in the experiment — the latter
        # races with concurrent training (other autoresearch jobs, manual
        # `make repro`, CI builds, retries).
        kfp_run_id = os.environ.get("KFP_RUN_ID")
        if kfp_run_id:
            mlflow.set_tag("kfp_run_id", kfp_run_id)
        mlflow.log_params({k: v for k, v in params.items() if v is not None})
        mlflow.log_param("n_features", X.shape[1])
        mlflow.log_param("n_train_samples", X.shape[0])
        mlflow.log_param("dataset_csv", dataset.get("csv_path", "?"))

        pipeline.fit(X, y)

        Path(model_path).parent.mkdir(exist_ok=True)
        with open(model_path, "wb") as f:
            pickle.dump(pipeline, f)

        # evaluate.py reads run_id.txt to log metrics on this exact run.
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
