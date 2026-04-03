"""FastAPI inference server for churn prediction."""

import logging
import os
import threading

import mlflow.sklearn
import pandas as pd
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_URI = "models:/churn-model@champion"

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

app = FastAPI(title="Churn Prediction API")
model = None


class CustomerInput(BaseModel):
    gender: str
    SeniorCitizen: int
    Partner: str
    Dependents: str
    tenure: int
    PhoneService: str
    MultipleLines: str
    InternetService: str
    OnlineSecurity: str
    OnlineBackup: str
    DeviceProtection: str
    TechSupport: str
    StreamingTV: str
    StreamingMovies: str
    Contract: str
    PaperlessBilling: str
    PaymentMethod: str
    MonthlyCharges: float
    TotalCharges: float


def _load_model_in_background():
    global model
    try:
        logger.info(f"Loading champion model from {MLFLOW_TRACKING_URI} ...")
        model = mlflow.sklearn.load_model(MODEL_URI)
        logger.info("Champion model loaded successfully.")
    except Exception as e:
        logger.error(
            f"Failed to load model: {e}. /health will return 503 until resolved."
        )


@app.on_event("startup")
def load_model():
    # Run in a background thread so uvicorn binds to the port immediately.
    # Health returns 503 while loading (pod alive, not ready) → readiness probe
    # fails gracefully instead of the liveness probe seeing connection refused.
    threading.Thread(target=_load_model_in_background, daemon=True).start()


@app.get("/health")
def health():
    if model is None:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "model_loaded": False},
        )
    return {"status": "healthy", "model_loaded": True}


@app.post("/predict")
def predict(customer: CustomerInput):
    if model is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Model not loaded. Check /health for status."},
        )
    df = pd.DataFrame([customer.model_dump()])
    prediction = model.predict(df)[0]
    probability = model.predict_proba(df)[0][1]
    return {
        "churn": int(prediction),
        "churn_probability": round(float(probability), 4),
    }
