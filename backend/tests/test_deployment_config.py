"""Pre-deployment hardening that lives in configuration files (#520).

These guard the settings against quiet regression: a review of a compose or Terraform
diff does not always catch a port that is published again or a mount that is writable.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def _compose() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text())


def test_databases_and_the_api_are_published_on_loopback_only() -> None:
    services = _compose()["services"]
    for name in ("postgres", "clickhouse", "backend"):
        for mapping in services[name].get("ports", []):
            assert str(mapping).startswith("127.0.0.1:"), f"{name} publishes {mapping} beyond this machine"


def test_the_frontend_does_not_receive_the_backend_secrets() -> None:
    frontend = _compose()["services"]["frontend"]
    assert "env_file" not in frontend, "the frontend container must not get the whole .env"
    assert "SECRET_KEY" not in (frontend.get("environment") or {})


def test_ci_service_images_are_pinned_by_digest() -> None:
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    images = re.findall(r"image:\s*(\S+)", workflow) + re.findall(r"docker run[^\n]*?(fsouza/\S+)", workflow)
    assert images, "no images found"
    for image in images:
        assert "@sha256:" in image, f"{image} is not pinned by digest"
    assert re.search(r"pip install markdown==\d", workflow), "the handleiding generator's markdown is unpinned"


def test_every_environment_runs_the_same_datastore_images() -> None:
    # The suites verify the device against the CI service images, so compose, every CI job
    # and the Terraform VM must run exactly those (#524). A Dependabot digest bump in
    # compose fails here until CI and Terraform move with it.
    compose = _compose()["services"]
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    ci_images = set(re.findall(r"image:\s*(\S+)", workflow))
    terraform = (REPO / "terraform" / "variables.tf").read_text()
    tf_clickhouse = re.search(r'variable "clickhouse_image"\s*\{[^}]*default\s*=\s*"([^"]+)"', terraform)
    assert tf_clickhouse, "no clickhouse_image default in variables.tf"

    clickhouse = compose["clickhouse"]["image"]
    postgres = compose["postgres"]["image"]
    assert {i for i in ci_images if i.startswith("clickhouse/")} == {clickhouse}
    assert {i for i in ci_images if i.startswith("postgres:")} == {postgres}
    assert tf_clickhouse.group(1) == clickhouse


def test_terraform_mounts_reference_data_read_only_and_requires_modern_tls() -> None:
    cloudrun = (REPO / "terraform" / "cloudrun.tf").read_text()
    volume = re.search(r'volumes\s*\{\s*name\s*=\s*"refdata"\s*gcs\s*\{([^}]*)\}', cloudrun)
    assert volume, "the refdata volume is not declared"
    assert re.search(r"read_only\s*=\s*true", volume.group(1)), "the refdata volume must be read-only"
    lb = (REPO / "terraform" / "loadbalancer.tf").read_text()
    assert re.search(r'min_tls_version\s*=\s*"TLS_1_2"', lb)
    assert re.search(r"ssl_policy\s*=\s*google_compute_ssl_policy\.", lb)


def test_terraform_sets_the_client_ip_hops_and_import_roots() -> None:
    cloudrun = (REPO / "terraform" / "cloudrun.tf").read_text()
    assert 'name  = "TRUSTED_PROXY_HOPS"' in cloudrun
    assert '"FAMILY_IMPORT_ROOTS"' in cloudrun


def test_only_one_trigger_deploys_to_the_single_environment() -> None:
    workflow = (REPO / ".github" / "workflows" / "build.yml").read_text()
    deploy_if = next(line for line in workflow.splitlines() if line.strip().startswith("if:") and "COGA_DEPLOY_TRIGGER" in line)
    assert "vars.COGA_DEPLOY_TRIGGER == 'release'" in deploy_if
    assert "(vars.COGA_DEPLOY_TRIGGER || 'main') == 'main'" in deploy_if
    assert "COGA_TFVARS" in workflow and "ci.auto.tfvars" in workflow
