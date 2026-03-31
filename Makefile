.PHONY: repro train serve clean

repro:
	uv run dvc repro

train:
	uv run python src/train.py

serve:
	uv run uvicorn src.api:app --reload --port 8000

clean:
	rm -rf data/processed models metrics.json

docker-build:
	docker build -t churn-api:latest .

docker-run:
	docker run --rm -p 8000:8000 churn-api:latest
