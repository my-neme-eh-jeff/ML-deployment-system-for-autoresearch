"""Manually promote the current challenger model to champion."""

import mlflow

MODEL_NAME = "classifier"


def promote():
    client = mlflow.MlflowClient()

    try:
        challenger = client.get_model_version_by_alias(MODEL_NAME, "challenger")
    except mlflow.exceptions.MlflowException:
        print("No challenger model found. Nothing to promote.")
        return

    # Get current champion info for comparison
    try:
        champion = client.get_model_version_by_alias(MODEL_NAME, "champion")
        champion_run = client.get_run(champion.run_id)
        challenger_run = client.get_run(challenger.run_id)

        print(f"Current champion: v{champion.version}")
        print(f"  AUC-ROC: {champion_run.data.metrics.get('auc_roc', 'N/A')}")
        print(f"Challenger:       v{challenger.version}")
        print(f"  AUC-ROC: {challenger_run.data.metrics.get('auc_roc', 'N/A')}")
    except mlflow.exceptions.MlflowException:
        print(f"Promoting challenger v{challenger.version} (no existing champion)")

    client.set_registered_model_alias(MODEL_NAME, "champion", challenger.version)
    print(f"\nVersion {challenger.version} is now the champion.")


if __name__ == "__main__":
    promote()
