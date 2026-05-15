"""FastAPI inference server. Loads `@champion` classifier from MLflow."""

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
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

# Note: mlflow.set_tracking_uri() is intentionally NOT called at module
# import time. Doing so pollutes mlflow's process-global tracking URI for
# any other code that imports this module (e.g. tests that pre-set
# MLFLOW_TRACKING_URI to a sqlite path). The loader below sets it just
# before the first load attempt.

model = None
model_version: str | None = None


class PredictRequest(BaseModel):
    """Generic single-row predict request. `data` is one row keyed by column name.

    The sklearn pipeline saved in MLflow encodes the schema; column mismatches
    surface as a 422-shaped error from the model itself.
    """

    data: dict[str, Any]


def _load_model_in_background():
    """Load @champion from MLflow with retry+backoff.

    Previously this ran once; a transient MLflow outage at pod startup left
    `model = None` forever and the pod stuck at 503 (the liveness probe is
    /health/live which always returns 200, so K8s never restarts it). Now we
    retry indefinitely with capped backoff — when MLflow recovers, the pod
    catches up automatically.
    """
    global model, model_version
    # Set the tracking URI here (not at module import) so importing this
    # module doesn't clobber other test/runtime mlflow configuration.
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    attempt = 0
    while True:
        try:
            logger.info(
                f"Loading {MODEL_URI} from {MLFLOW_TRACKING_URI} "
                f"(attempt {attempt + 1})..."
            )
            loaded = mlflow.sklearn.load_model(MODEL_URI)
            try:
                client = mlflow.MlflowClient()
                v = client.get_model_version_by_alias(MODEL_NAME, "champion")
                model_version = v.version
                logger.info(f"Loaded {MODEL_NAME} v{model_version}.")
            except Exception:
                logger.info("Loaded model (version metadata unavailable).")
            model = loaded
            return
        except Exception as e:
            attempt += 1
            # 2, 4, 8, 16, 32, capped at 60s. After ~20 minutes of failures
            # the pod has tried 20+ times — still no harm in keeping going.
            backoff = min(60, 2 ** min(attempt, 6))
            logger.warning(
                f"Model load failed (attempt {attempt}): {e}. Retrying in {backoff}s..."
            )
            time.sleep(backoff)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_load_model_in_background, daemon=True).start()
    yield


app = FastAPI(
    title="ML Deployment System for Autoresearch — Inference API",
    lifespan=lifespan,
)


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
        # Don't echo `e` back to callers — sklearn / pandas error text leaks
        # column names, dtypes, package versions, and sometimes file paths.
        # The full exception is in the log line above for ops.
        return JSONResponse(
            status_code=422,
            content={"error": "Prediction failed; see server logs."},
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
