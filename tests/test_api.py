"""Tests for the FastAPI inference contract.

Covers the public surface — health endpoints + /predict response shapes
under three states (model unloaded, model OK, model raises). The actual
MLflow load is a background thread on FastAPI startup; we sidestep it
here by patching the `model` module global with a tiny fake before the
TestClient triggers `lifespan`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src import api as api_module


class _FakeModel:
    """Minimal sklearn-shaped fake: returns deterministic preds."""

    def __init__(self, predict=1, proba=0.83):
        self._predict = predict
        self._proba = proba

    def predict(self, df):  # noqa: D401
        return [self._predict for _ in range(len(df))]

    def predict_proba(self, df):
        # sklearn convention: rows × n_classes. We only ever read [:, 1].
        return [[1 - self._proba, self._proba] for _ in range(len(df))]


class _RaisingModel:
    def predict(self, df):
        raise ValueError(
            "X has 2 features but expected 339 — column 'TransactionID' missing"
        )

    def predict_proba(self, df):
        return self.predict(df)


@pytest.fixture()
def client_without_model(monkeypatch):
    """Inference server with model never loaded (cold pod, MLflow down)."""
    monkeypatch.setattr(api_module, "model", None)
    monkeypatch.setattr(api_module, "model_version", None)
    # Stub the background loader to a no-op so lifespan doesn't actually hit
    # an MLflow that doesn't exist in the test process.
    monkeypatch.setattr(api_module, "_load_model_in_background", lambda: None)
    with TestClient(api_module.app) as c:
        yield c


@pytest.fixture()
def client_with_model(monkeypatch):
    """Inference server with a fake model already loaded."""
    monkeypatch.setattr(api_module, "model", _FakeModel())
    monkeypatch.setattr(api_module, "model_version", "1")
    monkeypatch.setattr(api_module, "_load_model_in_background", lambda: None)
    with TestClient(api_module.app) as c:
        yield c


@pytest.fixture()
def client_with_raising_model(monkeypatch):
    monkeypatch.setattr(api_module, "model", _RaisingModel())
    monkeypatch.setattr(api_module, "model_version", "1")
    monkeypatch.setattr(api_module, "_load_model_in_background", lambda: None)
    with TestClient(api_module.app) as c:
        yield c


def test_liveness_always_200(client_without_model):
    r = client_without_model.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def test_health_503_when_model_unloaded(client_without_model):
    r = client_without_model.get("/health")
    assert r.status_code == 503
    assert r.json()["model_loaded"] is False


def test_health_200_when_model_loaded(client_with_model):
    r = client_with_model.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["model_loaded"] is True
    assert body["model_version"] == "1"


def test_predict_503_when_model_unloaded(client_without_model):
    r = client_without_model.post("/predict", json={"data": {"foo": 1}})
    assert r.status_code == 503
    assert "Model not loaded" in r.json()["error"]


def test_predict_returns_prediction_shape(client_with_model):
    r = client_with_model.post("/predict", json={"data": {"TransactionAmt": 12.5}})
    assert r.status_code == 200
    body = r.json()
    assert body["prediction"] == 1
    assert body["probability"] == 0.83
    assert body["model_version"] == "1"


def test_predict_422_does_not_leak_exception_text(client_with_raising_model):
    """Audit fix #5: /predict must not echo raw sklearn/pandas errors.

    The fake model raises a ValueError whose message includes a column name
    that looks like internal schema info. The response body must be a
    generic 'see server logs' line, not the exception's str(e).
    """
    r = client_with_raising_model.post(
        "/predict", json={"data": {"TransactionAmt": 1.0}}
    )
    assert r.status_code == 422
    body = r.json()
    # Generic envelope, no leakage.
    assert body["error"] == "Prediction failed; see server logs."
    # Negative checks: the leak shouldn't appear anywhere.
    serialized = repr(body)
    assert "TransactionID" not in serialized
    assert "339" not in serialized
    assert "ValueError" not in serialized
