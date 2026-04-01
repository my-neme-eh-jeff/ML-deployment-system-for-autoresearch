.PHONY: repro train serve clean mlflow promote test lint compile-kfp \
       kfp-ui argocd-ui argocd-password deploy-argocd k8s-status

# ── Local development ──────────────────────────────────────────────

repro:
	uv run dvc repro

train:
	uv run python src/train.py

serve:
	uv run uvicorn src.api:app --reload --port 8000

mlflow:
	uv run mlflow ui --port 5000

promote:
	uv run python src/promote.py

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/ pipelines/
	uv run ruff format --check src/ tests/ pipelines/

compile-kfp:
	uv run python pipelines/churn_pipeline.py

clean:
	rm -rf data/processed models metrics.json mlruns/

# ── Docker ─────────────────────────────────────────────────────────

docker-build:
	docker build -t churn-api:latest .

docker-run:
	docker run --rm -p 8000:8000 churn-api:latest

# ── Kubernetes (vind cluster) ──────────────────────────────────────

kfp-ui:
	@echo "KFP UI at http://localhost:8080"
	kubectl port-forward -n kubeflow svc/ml-pipeline-ui 8080:80

argocd-ui:
	@echo "ArgoCD UI at http://localhost:8090"
	kubectl port-forward -n argocd svc/argocd-server 8090:443

argocd-password:
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo

deploy-argocd:
	kubectl apply -f argocd/application.yaml

k8s-status:
	@echo "── KFP pods ──"
	@kubectl get pods -n kubeflow --no-headers 2>/dev/null || echo "  namespace not found"
	@echo "── ArgoCD pods ──"
	@kubectl get pods -n argocd --no-headers 2>/dev/null || echo "  namespace not found"
	@echo "── Churn serving ──"
	@kubectl get pods -n churn-serving --no-headers 2>/dev/null || echo "  namespace not found"
