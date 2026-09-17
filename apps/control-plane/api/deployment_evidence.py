"""Reconcile protected GitHub build and deploy artifacts with host observation."""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from packages.connectors.github import GitHubAppCredentials

SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def incomplete(state: str, detail: str, **extra) -> dict:
    return {"state": state, "detail": detail, **extra}


def reconcile(observation: dict, candidate: dict, deploy_run: dict, build_run: dict) -> dict:
    """Return deployed only after all independently recorded exact IDs agree."""
    source = observation.get("source_sha")
    digest = observation.get("image_digest")
    if (observation.get("schema_version") != 1 or observation.get("environment") != "unstable"
            or not isinstance(source, str) or not SHA.fullmatch(source)
            or not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
        return incomplete("mismatch", "Host observation has invalid release evidence.")
    if (str(observation.get("deploy_run_id")) != str(deploy_run.get("id"))
            or str(observation.get("deploy_run_attempt")) != str(deploy_run.get("run_attempt"))
            or observation.get("infra_sha") != deploy_run.get("head_sha")):
        return incomplete("mismatch", "Host observation does not match the deployment workflow revision and attempt.")
    if (str(observation.get("build_run_id")) != str(build_run.get("id"))
            or str(candidate.get("build_run_id")) != str(build_run.get("id"))
            or candidate.get("commit_sha") != build_run.get("head_sha")):
        return incomplete("mismatch", "Candidate does not match its build workflow run.")
    if (candidate.get("commit_sha") != source or candidate.get("image_digest") != digest
            or candidate.get("image_ref") != f"ghcr.io/life2launchlabs/launch-lms@{digest}"):
        return incomplete("mismatch", "Candidate SHA or image digest differs from the running host observation.")
    try:
        observed = datetime.fromisoformat(observation["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("naive timestamp")
    except (KeyError, AttributeError, ValueError):
        return incomplete("mismatch", "Host observation has no valid timestamp.")
    return {
        "state": "deployed", "detail": "Candidate, successful workflows, and host observation agree.",
        "source_sha": source, "image_digest": digest,
        "observed_at": observed.isoformat(),
        "build_run_url": f"https://github.com/Life2LaunchLabs/launch-lms/actions/runs/{build_run['id']}",
        "deploy_run_url": f"https://github.com/Life2LaunchLabs/launch-lms-infra/actions/runs/{deploy_run['id']}",
    }


async def deployment_status(settings, manifest: dict) -> dict:
    if not settings.github_app_id or not settings.github_installation_id or not settings.github_app_private_key:
        return incomplete("unavailable", "GitHub App installation is unavailable.")
    app = GitHubAppCredentials(settings.github_app_id, settings.github_installation_id, settings.github_app_private_key)
    infra_repo = manifest["deployment"]["repository"]
    app_repo = manifest["source"]["repository"]
    workflow = manifest["deployment"]["workflow"]
    try:
        infra = await app.connector(infra_repo, {"actions": "read"})
        runs = await infra.workflow_runs(infra_repo, workflow, manifest["deployment"]["ref"], 20)
        deploy_run = next((run for run in runs if run.get("event") in {"repository_dispatch", "workflow_dispatch"}
                           and run.get("path", "").split("@", 1)[0] == f".github/workflows/{workflow}"), None)
        if not deploy_run:
            return incomplete("workflow_missing", "No unstable deployment workflow run is available.")
        if deploy_run.get("status") != "completed":
            return incomplete("workflow_pending", "The latest deployment workflow has not finished.")
        if deploy_run.get("conclusion") != "success":
            return incomplete("workflow_failed", "The latest deployment workflow did not succeed.")
        observation = await infra.workflow_artifact_json(
            infra_repo, deploy_run["id"], f"host-observation-{deploy_run['run_attempt']}", "host-observation.json",
        )
        if observation is None:
            return incomplete("attestation_missing", "The successful workflow has no host observation artifact.")
        try:
            build_id = int(observation["build_run_id"])
        except (KeyError, TypeError, ValueError):
            return incomplete("mismatch", "Host observation has no valid build run ID.")
        if build_id < 1:
            return incomplete("mismatch", "Host observation has no valid build run ID.")
        build = await app.connector(app_repo, {"actions": "read"})
        build_run = await build.workflow_run(app_repo, build_id)
        if (build_run.get("path", "").split("@", 1)[0] != ".github/workflows/build-community.yaml"
                or build_run.get("head_branch") != manifest["environments"]["unstable"]["source_branch"]):
            return incomplete("candidate_invalid", "Build run is not the registered unstable candidate workflow.")
        if build_run.get("status") != "completed" or build_run.get("conclusion") != "success":
            return incomplete("candidate_failed", "Candidate build workflow did not succeed.")
        candidate = await build.workflow_artifact_json(
            app_repo, build_id, manifest["candidate"]["artifact_name"], "candidate.json",
        )
        if candidate is None:
            return incomplete("candidate_missing", "The successful build has no candidate artifact.")
        return reconcile(observation, candidate, deploy_run, build_run)
    except (httpx.HTTPError, KeyError, TypeError, ValueError, OSError):
        return incomplete("unavailable", "Deployment evidence could not be read from GitHub.")
