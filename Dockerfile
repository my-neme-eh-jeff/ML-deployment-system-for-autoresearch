FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY src/__init__.py src/__init__.py
COPY src/api.py src/api.py
COPY src/features.py src/features.py

EXPOSE 8000
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
CMD ["/app/.venv/bin/uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
