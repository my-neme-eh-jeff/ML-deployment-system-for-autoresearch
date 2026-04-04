#!/usr/bin/env bash
# GCP infrastructure setup for the churn MLOps stack on GKE.
# Run this once before deploying to GKE.
# Safe to re-run — most gcloud commands are idempotent.
#
# Usage:
#   export PROJECT_ID=your-gcp-project-id
#   bash scripts/setup-gcp.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var before running}"
REGION="${REGION:-us-central1}"
CLUSTER="${CLUSTER:-mlops-cluster}"
CLOUDSQL_INSTANCE="churn-mlflow"
MLFLOW_ARTIFACTS_BUCKET="churn-mlflow-artifacts-${PROJECT_ID}"
ARTIFACT_REGISTRY_REPO="churn-repo"

echo "==> Project:  $PROJECT_ID"
echo "==> Region:   $REGION"
echo "==> Cluster:  $CLUSTER"
echo ""

# ── 1. Enable required APIs ────────────────────────────────────────────────
echo "[1/8] Enabling GCP APIs..."
gcloud services enable \
  container.googleapis.com \
  sqladmin.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project="$PROJECT_ID"

# ── 2. GKE Autopilot cluster ───────────────────────────────────────────────
echo "[2/8] Creating GKE Autopilot cluster (takes ~5 min)..."
if ! gcloud container clusters describe "$CLUSTER" --region="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  gcloud container clusters create-auto "$CLUSTER" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --workload-pool="$PROJECT_ID.svc.id.goog"
else
  echo "  Cluster $CLUSTER already exists — skipping creation."
fi

# ── 3. CloudSQL PostgreSQL ─────────────────────────────────────────────────
echo "[3/8] Creating CloudSQL PostgreSQL instance..."
if ! gcloud sql instances describe "$CLOUDSQL_INSTANCE" --project="$PROJECT_ID" &>/dev/null; then
  gcloud sql instances create "$CLOUDSQL_INSTANCE" \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region="$REGION" \
    --no-backup \
    --project="$PROJECT_ID"
else
  echo "  CloudSQL instance $CLOUDSQL_INSTANCE already exists — skipping."
fi

gcloud sql databases create mlflow_db \
  --instance="$CLOUDSQL_INSTANCE" \
  --project="$PROJECT_ID" 2>/dev/null || echo "  Database mlflow_db already exists."

echo ""
echo "  IMPORTANT: Set the MLflow DB password now:"
echo "  gcloud sql users create mlflow_user --instance=$CLOUDSQL_INSTANCE --password=YOUR_SECURE_PASSWORD --project=$PROJECT_ID"
echo "  Then add it to GitHub Secrets as CLOUDSQL_PASSWORD."
echo ""

# ── 4. GCS bucket for MLflow artifacts ────────────────────────────────────
echo "[4/8] Creating GCS bucket for MLflow artifacts..."
if ! gcloud storage buckets describe "gs://$MLFLOW_ARTIFACTS_BUCKET" &>/dev/null; then
  gcloud storage buckets create "gs://$MLFLOW_ARTIFACTS_BUCKET" \
    --location="$REGION" \
    --uniform-bucket-level-access \
    --project="$PROJECT_ID"
else
  echo "  Bucket gs://$MLFLOW_ARTIFACTS_BUCKET already exists — skipping."
fi

# ── 5. Artifact Registry (replace ghcr.io) ────────────────────────────────
echo "[5/8] Creating Artifact Registry Docker repo..."
if ! gcloud artifacts repositories describe "$ARTIFACT_REGISTRY_REPO" \
  --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  gcloud artifacts repositories create "$ARTIFACT_REGISTRY_REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Churn MLOps container images" \
    --project="$PROJECT_ID"
else
  echo "  Artifact Registry repo $ARTIFACT_REGISTRY_REPO already exists — skipping."
fi
echo "  Image path: $REGION-docker.pkg.dev/$PROJECT_ID/$ARTIFACT_REGISTRY_REPO/churn-api"

# ── 6. GCP Service Accounts ────────────────────────────────────────────────
echo "[6/8] Creating GCP Service Accounts..."
for SA in mlflow-sa kfp-sa github-cicd; do
  if ! gcloud iam service-accounts describe "$SA@$PROJECT_ID.iam.gserviceaccount.com" \
    --project="$PROJECT_ID" &>/dev/null; then
    gcloud iam service-accounts create "$SA" \
      --display-name="$SA" \
      --project="$PROJECT_ID"
  else
    echo "  Service account $SA already exists — skipping."
  fi
done

# ── 7. IAM bindings ────────────────────────────────────────────────────────
echo "[7/8] Setting IAM bindings..."

# MLflow SA: CloudSQL client + GCS object admin (for artifacts)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:mlflow-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/cloudsql.client --condition=None 2>/dev/null || true

gcloud storage buckets add-iam-policy-binding "gs://$MLFLOW_ARTIFACTS_BUCKET" \
  --member="serviceAccount:mlflow-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/storage.objectAdmin 2>/dev/null || true

# KFP SA: GCS read on DVC bucket + GCS write on MLflow artifacts bucket
gcloud storage buckets add-iam-policy-binding gs://customer-churn-dvc-remote \
  --member="serviceAccount:kfp-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/storage.objectUser 2>/dev/null || true

gcloud storage buckets add-iam-policy-binding "gs://$MLFLOW_ARTIFACTS_BUCKET" \
  --member="serviceAccount:kfp-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/storage.objectUser 2>/dev/null || true

# GitHub CI SA: GKE developer + Artifact Registry writer + GCS admin on DVC bucket
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:github-cicd@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/container.developer --condition=None 2>/dev/null || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:github-cicd@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/artifactregistry.writer --condition=None 2>/dev/null || true

gcloud storage buckets add-iam-policy-binding gs://customer-churn-dvc-remote \
  --member="serviceAccount:github-cicd@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/storage.objectAdmin 2>/dev/null || true

# GitHub CI SA also needs to read MLflow IP from GKE (already covered by container.developer)

# ── 8. Workload Identity pool + GitHub provider ────────────────────────────
echo "[8/8] Setting up Workload Identity Federation for GitHub Actions..."

POOL_NAME="github-pool"
PROVIDER_NAME="github-provider"
GITHUB_ORG="my-neme-eh-jeff"

# Create WIF pool (idempotent)
gcloud iam workload-identity-pools create "$POOL_NAME" \
  --location=global \
  --display-name="GitHub Actions pool" \
  --project="$PROJECT_ID" 2>/dev/null || echo "  Pool $POOL_NAME already exists."

# Create OIDC provider for GitHub
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_NAME" \
  --workload-identity-pool="$POOL_NAME" \
  --location=global \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository_owner=assertion.repository_owner,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository_owner=='$GITHUB_ORG'" \
  --project="$PROJECT_ID" 2>/dev/null || echo "  Provider $PROVIDER_NAME already exists."

# Get the full WIF provider path
PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
WIF_PROVIDER="projects/$PROJECT_NUM/locations/global/workloadIdentityPools/$POOL_NAME/providers/$PROVIDER_NAME"

# Allow GitHub Actions to impersonate github-cicd SA
gcloud iam service-accounts add-iam-policy-binding \
  "github-cicd@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUM/locations/global/workloadIdentityPools/$POOL_NAME/attribute.repository_owner/$GITHUB_ORG" \
  --project="$PROJECT_ID" 2>/dev/null || true

echo ""
echo "====================================================================="
echo "GCP infrastructure setup complete."
echo ""
echo "Next steps:"
echo "1. Set CloudSQL user password:"
echo "   gcloud sql users create mlflow_user --instance=$CLOUDSQL_INSTANCE \\"
echo "     --password=YOUR_SECURE_PASSWORD --project=$PROJECT_ID"
echo ""
echo "2. Get cluster credentials:"
echo "   gcloud container clusters get-credentials $CLUSTER --region=$REGION --project=$PROJECT_ID"
echo ""
echo "3. Add these GitHub Secrets to your repo:"
echo "   GCP_PROJECT_ID=$PROJECT_ID"
echo "   GCP_WORKLOAD_IDENTITY_PROVIDER=$WIF_PROVIDER"
echo "   GCP_SERVICE_ACCOUNT=github-cicd@$PROJECT_ID.iam.gserviceaccount.com"
echo "   GKE_CLUSTER_NAME=$CLUSTER"
echo "   GKE_REGION=$REGION"
echo "   ARTIFACT_REGISTRY_REPO=$REGION-docker.pkg.dev/$PROJECT_ID/$ARTIFACT_REGISTRY_REPO"
echo "   MLFLOW_ARTIFACTS_BUCKET=$MLFLOW_ARTIFACTS_BUCKET"
echo "   CLOUDSQL_INSTANCE=$PROJECT_ID:$REGION:$CLOUDSQL_INSTANCE"
echo "   CLOUDSQL_PASSWORD=<your-secure-password>"
echo ""
echo "4. Deploy to GKE: make gke-setup"
echo "====================================================================="
