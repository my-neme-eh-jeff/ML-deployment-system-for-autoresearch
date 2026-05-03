"""GitHub App auth + GraphQL `createCommitOnBranch` for the in-cluster loop.

PEM → JWT → 1-hour install token → atomic multi-file commit. The PEM lives in
GCP Secret Manager and is fetched via Workload Identity at run time.
"""

import base64
import os
import re
import time
from pathlib import Path

import jwt as pyjwt
import requests

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"


def load_pem_from_secret_manager(project: str, secret: str) -> bytes:
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret}/versions/latest"
    return client.access_secret_version(request={"name": name}).payload.data


def mint_app_jwt(app_id: str, pem: bytes) -> str:
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 600, "iss": str(app_id)}
    return pyjwt.encode(payload, pem, algorithm="RS256")


def get_installation_token(
    app_id: str, installation_id: str, project: str, secret: str
) -> str:
    pem = load_pem_from_secret_manager(project, secret)
    app_jwt = mint_app_jwt(app_id, pem)
    r = requests.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]


def create_branch_from_main(token: str, owner: str, repo: str, branch: str) -> str:
    """Idempotent: returns the existing branch SHA if it already exists.

    Always creates from the *current* main HEAD, so each per-iter branch starts
    from the latest state (including any [skip ci] commits CI pushed back from
    a previous iter's merge).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    r = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{branch}",
        headers=headers,
        timeout=30,
    )
    if r.status_code == 200:
        return r.json()["object"]["sha"]
    r = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/main",
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    main_sha = r.json()["object"]["sha"]
    r = requests.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/refs",
        headers=headers,
        json={"ref": f"refs/heads/{branch}", "sha": main_sha},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["object"]["sha"]


def fetch_file_from_main(
    token: str, owner: str, repo: str, path: str
) -> tuple[str, str]:
    """Returns (content, sha) for a file on the main branch.

    Used by the autoresearch loop to read the *current* `k8s/deployment.yaml`
    annotations before bumping them — so even if main moved between iters
    (CI pushing a [skip ci] image-SHA bump), our bump applies on top of the
    latest content rather than fighting it in a 3-way merge.
    """
    r = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
        params={"ref": "main"},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    return base64.b64decode(body["content"]).decode("utf-8"), body["sha"]


def bump_deployment_annotations(content: str, version: str, run_id: str) -> str:
    """Update `mlops/classifier-version` + `mlops/classifier-run-id` in
    `k8s/deployment.yaml` content, in place.

    Matches the existing two annotation lines (under `spec.template.metadata.annotations`)
    and replaces just the value. Other deployment.yaml fields — image SHA bumped
    by CI, replicas patched by HPA — are left untouched.
    """
    new = re.sub(
        r'(mlops/classifier-version:\s*)"[^"]*"',
        rf'\1"{version}"',
        content,
        count=1,
    )
    new = re.sub(
        r'(mlops/classifier-run-id:\s*)"[^"]*"',
        rf'\1"{run_id}"',
        new,
        count=1,
    )
    return new


def commit_files_to_branch(
    token: str,
    owner: str,
    repo: str,
    branch: str,
    message: str,
    files: list[tuple[str, bytes]],
) -> str:
    """Atomic multi-file commit. `files`: list of (relative_path, bytes). Returns commit OID."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    branch_q = """
    query($owner:String!, $repo:String!, $branch:String!) {
      repository(owner:$owner, name:$repo) {
        ref(qualifiedName:$branch) { id target { oid } }
      }
    }
    """
    r = requests.post(
        GITHUB_GRAPHQL,
        headers=headers,
        json={
            "query": branch_q,
            "variables": {
                "owner": owner,
                "repo": repo,
                "branch": f"refs/heads/{branch}",
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    ref = r.json()["data"]["repository"]["ref"]
    if ref is None:
        raise RuntimeError(f"Branch {branch} not found on {owner}/{repo}")
    branch_id = ref["id"]
    head_oid = ref["target"]["oid"]

    additions = [
        {"path": p, "contents": base64.b64encode(c).decode("ascii")} for p, c in files
    ]
    mutation = """
    mutation($branch:ID!, $message:String!, $files:[FileAddition!], $parent:GitObjectID!) {
      createCommitOnBranch(input:{
        branch:{id:$branch},
        message:{headline:$message},
        fileChanges:{additions:$files},
        expectedHeadOid:$parent
      }) {
        commit { oid url }
      }
    }
    """
    r = requests.post(
        GITHUB_GRAPHQL,
        headers=headers,
        json={
            "query": mutation,
            "variables": {
                "branch": branch_id,
                "message": message,
                "files": additions,
                "parent": head_oid,
            },
        },
        timeout=60,
    )
    r.raise_for_status()
    result = r.json()
    if result.get("errors"):
        raise RuntimeError(f"GraphQL errors: {result['errors']}")
    return result["data"]["createCommitOnBranch"]["commit"]["oid"]


def open_pull_request(
    token: str,
    owner: str,
    repo: str,
    branch: str,
    title: str,
    body: str,
    auto_merge: bool = True,
) -> tuple[str, str]:
    """Open a PR; optionally enable auto-merge. Returns (html_url, node_id)."""
    r = requests.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "head": branch, "base": "main", "body": body},
        timeout=30,
    )
    r.raise_for_status()
    pr = r.json()
    if auto_merge:
        try:
            _enable_auto_merge(token, pr["node_id"], title)
        except Exception as e:
            print(f"  WARN: failed to enable auto-merge: {e}")
    return pr["html_url"], pr["node_id"]


def _enable_auto_merge(token: str, pr_node_id: str, commit_headline: str) -> None:
    """Tell GitHub to merge the PR automatically when required checks pass.

    With branch protection requiring `lint-and-test` + `compile-kfp`, those run
    on every PR (~1 min) and the merge fires immediately after they go green.
    Without required checks, GitHub merges right away.
    """
    mutation = """
    mutation($pr:ID!, $headline:String!) {
      enablePullRequestAutoMerge(input:{
        pullRequestId:$pr,
        mergeMethod:SQUASH,
        commitHeadline:$headline
      }) { clientMutationId }
    }
    """
    r = requests.post(
        GITHUB_GRAPHQL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "query": mutation,
            "variables": {"pr": pr_node_id, "headline": commit_headline},
        },
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"auto-merge GraphQL errors: {body['errors']}")


def github_config_from_env() -> dict | None:
    """Returns None if any of the 6 required env vars is missing (local dev)."""
    required = [
        "GITHUB_APP_ID",
        "GITHUB_INSTALLATION_ID",
        "GITHUB_OWNER",
        "GITHUB_REPO",
        "GCP_PROJECT",
        "GITHUB_PEM_SECRET",
    ]
    if not all(os.environ.get(k) for k in required):
        return None
    return {
        "app_id": os.environ["GITHUB_APP_ID"],
        "installation_id": os.environ["GITHUB_INSTALLATION_ID"],
        "owner": os.environ["GITHUB_OWNER"],
        "repo": os.environ["GITHUB_REPO"],
        "project": os.environ["GCP_PROJECT"],
        "secret": os.environ["GITHUB_PEM_SECRET"],
    }


def collect_changed_files(
    project_root: Path, proposal: dict, history_path: Path
) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for field, rel_path in [
        ("params_yaml", "configs/params.yaml"),
        ("train_py", "src/train.py"),
        ("preprocess_py", "src/preprocess.py"),
    ]:
        if proposal.get(field):
            p = project_root / rel_path
            files.append((rel_path, p.read_bytes()))
    for rel in ("metrics.json", "dvc.lock"):
        p = project_root / rel
        if p.exists():
            files.append((rel, p.read_bytes()))
    if history_path.exists():
        files.append(
            (str(history_path.relative_to(project_root)), history_path.read_bytes())
        )
    return files
