FROM python:3.12-slim

# Non-root user (uid 10001 matches k8s/deployment.yaml securityContext).
# Pod Security `restricted` profile requires runAsNonRoot; this is the
# image-side half of that contract.
RUN useradd --create-home --uid 10001 app
WORKDIR /app

RUN pip install uv

COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen && chown -R app:app /app/.venv

COPY --chown=app:app src/__init__.py src/__init__.py
COPY --chown=app:app src/api.py src/api.py
COPY --chown=app:app src/features.py src/features.py

USER app
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
CMD ["/app/.venv/bin/uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
