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


def _terraform(name: str) -> str:
    return (REPO / "terraform" / name).read_text()


def _variable_default(name: str) -> str:
    block = re.search(rf'variable "{name}"\s*\{{(.*?)\n\}}', _terraform("variables.tf"), re.S)
    assert block, f"no variable {name}"
    default = re.search(r"^\s*default\s*=\s*(.+)$", block.group(1), re.M)
    assert default, f"variable {name} has no default"
    return default.group(1).strip()


def test_the_deploy_job_refuses_to_run_without_required_reviewers() -> None:
    # The gcp-deploy environment gates `terraform apply -auto-approve` only once it has
    # required reviewers, so the job checks for them before it touches the cloud (#364).
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "build.yml").read_text())
    deploy = workflow["jobs"]["deploy"]
    assert deploy["environment"] == "gcp-deploy"
    assert deploy["permissions"].get("actions") == "read", "reading the environment needs actions: read"
    first = deploy["steps"][0]
    script = first.get("run", "")
    assert "environments/gcp-deploy" in script and "required_reviewers" in script
    assert "exit 1" in script
    # Values reach the script through the environment, not interpolated into it.
    assert "${{" not in script
    auth = next(i for i, step in enumerate(deploy["steps"]) if "google-github-actions/auth" in step.get("uses", ""))
    assert auth > 0, "the reviewer check must run before the job authenticates to Google Cloud"


def test_the_restricted_db_role_keeps_the_owner_password_from_the_api() -> None:
    # Off by default. When on, the API runs as coga_app and cannot read the owner's password,
    # not even through Secret Manager; the migration job runs as its own account (#364).
    assert _variable_default("db_runtime_role") == '"owner"'
    main = _terraform("main.tf")
    logins = re.search(r"db_login_secret = \{(.*?)\}", main, re.S)
    assert logins, "no db_login_secret map in main.tf"
    assert re.search(r'owner\s+= "postgres_password"', logins.group(1))
    assert re.search(r'coga_app = "postgres_app_password"', logins.group(1))
    assert "app_db_password_secret = local.db_login_secret[var.db_runtime_role]" in main
    assert 'app_db_user            = local.restricted_db_role ? "coga_app"' in main
    secrets = _terraform("secrets.tf")
    assert (
        'setsubtract(keys(local.secret_ids), [local.restricted_db_role ? "postgres_password" : "postgres_app_password"])'
        in secrets
    ), "the backend must lose access to the owner's password with the restricted role"
    cloudrun = _terraform("cloudrun.tf")
    assert "google_secret_manager_secret.app[local.app_db_password_secret].secret_id" in cloudrun
    assert "value = local.app_db_user" in cloudrun
    assert "terraform_data.db_migrate," in cloudrun, "a new image must not serve before its schema is applied"

    migrate = _terraform("migrate.tf")
    assert "service_account = local.db_migrate_sa_email" in migrate
    assert "local.backend_sa_email" not in migrate
    assert 'command = ["python", "-m", "app.db_migrate"]' in migrate
    assert (REPO / "backend" / "app" / "db_migrate.py").is_file()
    # The job reads every app secret under its setting name, coga_app's password included.
    assert "for key in keys(local.secret_ids) :" in migrate
    assert '(key == "integrity_anchor" ? "INTEGRITY_ANCHOR_SIGNING_KEY" : upper(key)) => key' in migrate
    assert "postgres_app_password" in _terraform("main.tf")
    assert "triggers_replace = [var.backend_image]" in migrate


def test_the_clickhouse_egress_lockdown_is_complete_when_on() -> None:
    assert _variable_default("clickhouse_restrict_egress") == "false"
    egress = _terraform("egress.tf")
    allow = re.search(r'resource "google_compute_firewall" "clickhouse_egress_google_apis"\s*\{(.*?)\n\}', egress, re.S)
    deny = re.search(r'resource "google_compute_firewall" "clickhouse_egress_deny_all"\s*\{(.*?)\n\}', egress, re.S)
    assert allow and deny
    assert "local.private_google_apis_range" in allow.group(1) and 'ports    = ["443"]' in allow.group(1)
    assert 'protocol = "all"' in deny.group(1) and '"0.0.0.0/0"' in deny.group(1)
    for rule in (allow.group(1), deny.group(1)):
        assert 'target_tags        = ["clickhouse"]' in rule
        assert 'direction = "EGRESS"' in rule
    assert 'private_google_apis_range = "199.36.153.8/30"' in egress
    # Docker Hub is unreachable with the lockdown, so the plan refuses such an image, and
    # an Artifact Registry image gets the VM's credentials for its pull.
    database = _terraform("database.tf")
    assert 'condition     = !var.clickhouse_restrict_egress || local.clickhouse_image_registry != ""' in database
    startup = _terraform("scripts/clickhouse-startup.sh.tftpl")
    assert '%{ if registry_host != "" ~}' in startup
    assert 'docker-credential-gcr configure-docker --registries="${registry_host}"' in startup
    assert "registry_host    = local.clickhouse_image_registry" in database


def test_ingress_ranges_are_validated_as_cidrs() -> None:
    variables = _terraform("variables.tf")
    block = re.search(r'variable "allowed_ingress_cidrs"\s*\{(.*?)\n\}', variables, re.S)
    assert block and "can(cidrhost(cidr, 0))" in block.group(1)
