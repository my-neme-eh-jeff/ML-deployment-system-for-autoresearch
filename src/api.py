"""FastAPI inference server. Loads `@champion` classifier from MLflow."""

import logging
import os
import threading
from typing import Any

import mlflow.sklearn
import pandas as pd
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_NAME = os.getenv("MODEL_NAME", "classifier")
MODEL_URI = f"models:/{MODEL_NAME}@champion"

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

app = FastAPI(title="ML Deployment System for Autoresearch — Inference API")
model = None
model_version: str | None = None


class PredictRequest(BaseModel):
    """Generic single-row predict request. `data` is one row keyed by column name.

    The sklearn pipeline saved in MLflow encodes the schema; column mismatches
    surface as a 422-shaped error from the model itself.
    """

    data: dict[str, Any]


def _load_model_in_background():
    global model, model_version
    try:
        logger.info(f"Loading {MODEL_URI} from {MLFLOW_TRACKING_URI} ...")
        model = mlflow.sklearn.load_model(MODEL_URI)
        try:
            client = mlflow.MlflowClient()
            v = client.get_model_version_by_alias(MODEL_NAME, "champion")
            model_version = v.version
            logger.info(f"Loaded {MODEL_NAME} v{model_version}.")
        except Exception:
            logger.info("Loaded model (version metadata unavailable).")
    except Exception as e:
        logger.error(f"Failed to load model: {e}. /health stays 503.")


@app.on_event("startup")
def load_model():
    threading.Thread(target=_load_model_in_background, daemon=True).start()


@app.get("/health/live")
def liveness():
    return {"status": "alive"}


@app.get("/health")
def health():
    if model is None:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "model_loaded": False},
        )
    return {"status": "healthy", "model_loaded": True, "model_version": model_version}


@app.post("/predict")
def predict(req: PredictRequest):
    if model is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Model not loaded. Check /health for status."},
        )
    try:
        df = pd.DataFrame([req.data])
        prediction = int(model.predict(df)[0])
        probability = float(model.predict_proba(df)[0][1])
    except Exception as e:
        logger.warning(
            "predict failed model_version=%s features=%s err=%s",
            model_version,
            list(req.data.keys()),
            e,
        )
        return JSONResponse(
            status_code=422,
            content={"error": f"Prediction failed: {e}"},
        )
    # Per-request attribution: which model version served this prediction,
    # what input shape, what was returned. Lets ops trace any complaint about
    # a specific prediction back to a specific model version (and through
    # MLflow, to the autoresearch iteration that produced it).
    logger.info(
        "predict ok model_version=%s features=%d prediction=%d probability=%.4f",
        model_version,
        len(req.data),
        prediction,
        probability,
    )
    return {
        "prediction": prediction,
        "probability": round(probability, 4),
        "model_version": model_version,
    }
