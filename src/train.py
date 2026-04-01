"""Train a customer churn prediction model and log to MLflow."""

import pickle
from pathlib import Path

import mlflow
import pandas as pd
import yaml
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

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


def build_pipeline(n_estimators: int = 100, random_state: int = 42) -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), NUMERIC_FEATURES),
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
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=n_estimators, random_state=random_state
                ),
            ),
        ]
    )


def train(
    train_path: str = "data/processed/train.csv",
    model_path: str = "models/churn_model.pkl",
    n_estimators: int | None = None,
):
    if n_estimators is None:
        with open("configs/params.yaml") as f:
            params = yaml.safe_load(f)["train"]
        n_estimators = params["n_estimators"]

    df = pd.read_csv(train_path)
    X = df.drop(columns=[TARGET])
    y = df[TARGET]

    mlflow.set_experiment("churn-prediction")

    with mlflow.start_run(run_name="train") as run:
        # Log parameters
        mlflow.log_param("n_estimators", n_estimators)
        mlflow.log_param("model_type", "RandomForestClassifier")
        mlflow.log_param("n_features", X.shape[1])
        mlflow.log_param("n_train_samples", X.shape[0])

        pipeline = build_pipeline(n_estimators=n_estimators)
        pipeline.fit(X, y)

        # Save pickle for DVC pipeline compatibility
        Path(model_path).parent.mkdir(exist_ok=True)
        with open(model_path, "wb") as f:
            pickle.dump(pipeline, f)

        # Log model to MLflow and register it
        mlflow.sklearn.log_model(
            pipeline,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
        )

        print(f"Model saved to {model_path}")
        print(f"MLflow run ID: {run.info.run_id}")


if __name__ == "__main__":
    train()
