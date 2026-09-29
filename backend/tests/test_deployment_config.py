"""Pre-deployment hardening that lives in configuration files (#520).

These guard the settings against quiet regression: a review of a compose or Terraform
diff does not always catch a port that is published again or a mount that is writable.
"""

from __future__ import annotations

import os
import re
import subprocess
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


def test_the_browser_may_read_the_phi_bucket_only_from_the_app() -> None:
    # In gcs mode IGV reads aligned reads straight from the PHI bucket through signed
    # URLs, with range requests. Without a CORS policy naming the app's origin the
    # browser withholds every response; the policy must not open the bucket wider.
    phi = re.search(r'resource "google_storage_bucket" "phi" \{(.*?)\n\}', _terraform("storage.tf"), re.S)
    assert phi, "no PHI bucket"
    cors = re.search(r"\n  cors \{(.*?)\n  \}", phi.group(1), re.S)
    assert cors, "the PHI bucket has no CORS policy, so IGV cannot read alignments in the browser"
    policy = cors.group(1)
    assert re.search(r'origin\s*=\s*\["https://\$\{var\.app_domain\}"\]', policy), "only the app's origin"
    assert re.search(r'method\s*=\s*\["GET", "HEAD"\]', policy), "reads only"
    exposed = re.search(r"response_header\s*=\s*\[([^\]]*)\]", policy)
    assert exposed and {'"Range"', '"Content-Range"', '"Content-Length"'} <= {h.strip() for h in exposed.group(1).split(",")}


def _backend_bucket_roles() -> dict[str, list[str]]:
    """The roles storage.tf grants the backend's account, by bucket resource name."""
    storage = (REPO / "terraform" / "storage.tf").read_text()
    roles: dict[str, list[str]] = {}
    for body in re.findall(r'resource "google_storage_bucket_iam_member" "\w+"\s*\{(.*?)\n\}', storage, re.S):
        bucket = re.search(r"^\s*bucket\s*=\s*google_storage_bucket\.(\w+)\.name$", body, re.M)
        role = re.search(r'^\s*role\s*=\s*"([^"]+)"$', body, re.M)
        member = re.search(r'^\s*member\s*=\s*"([^"]+)"$', body, re.M)
        assert bucket and role and member, f"a bucket grant this test cannot read: {body}"
        if member.group(1) == "serviceAccount:${local.backend_sa_email}":
            roles.setdefault(bucket.group(1), []).append(role.group(1))
    return roles


def test_the_backend_account_can_only_read_the_buckets() -> None:
    # The refdata volume is read-only and its files are loaded out of band, so the
    # write-capable roles/storage.objectUser the account held there was excess privilege.
    assert _backend_bucket_roles() == {
        "phi": ["roles/storage.objectViewer"],
        "refdata": ["roles/storage.objectViewer"],
    }


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


def _build_workflow() -> dict:
    return yaml.safe_load((REPO / ".github" / "workflows" / "build.yml").read_text())


def _run_build_metadata(repo: Path, *, ref_name: str, ref_type: str) -> dict[str, str]:
    """Run the workflow's build-metadata step in ``repo``, as the runner would; return its outputs."""
    step = next(s for s in _build_workflow()["jobs"]["prepare"]["steps"] if s.get("id") == "meta")
    outputs = repo / "github_output"
    outputs.write_text("")
    env = {
        "PATH": os.environ["PATH"],
        "REPO_NAME": "CoGA",
        "REF_NAME": ref_name,
        "REF_TYPE": ref_type,
        "GITHUB_OUTPUT": str(outputs),
    }
    subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]], cwd=repo, env=env, check=True)
    return dict(line.split("=", 1) for line in outputs.read_text().splitlines())


def test_each_main_build_deploys_an_image_tag_of_its_own(tmp_path: Path) -> None:
    # Terraform deploys the image string it is given. A tag every main build reuses
    # (`:main`) leaves it no change to apply: Cloud Run keeps the image it runs, and the
    # db-migrate job, keyed on the backend image, never runs again after the first deploy.
    git_env = {
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "CI",
        "GIT_AUTHOR_EMAIL": "ci@example.org",
        "GIT_COMMITTER_NAME": "CI",
        "GIT_COMMITTER_EMAIL": "ci@example.org",
    }

    def commit() -> None:
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "change"], cwd=tmp_path, env=git_env, check=True)

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, env=git_env, check=True)
    (tmp_path / "VERSION").write_text("1.4.0\n")
    commit()
    first = _run_build_metadata(tmp_path, ref_name="main", ref_type="branch")
    commit()
    second = _run_build_metadata(tmp_path, ref_name="main", ref_type="branch")

    assert first["tag"] == f"main-{first['git_sha']}" and len(first["git_sha"]) == 12
    assert second["tag"] != first["tag"], "two commits on main must deploy two different images"
    assert _run_build_metadata(tmp_path, ref_name="v1.4.0", ref_type="tag")["tag"] == "v1.4.0"

    # The images are built and deployed under that tag.
    jobs = _build_workflow()["jobs"]
    image_tag = ":${{ needs.prepare.outputs.tag }}"
    for job in ("build-backend", "build-frontend"):
        assert any(image_tag in step.get("run", "") for step in jobs[job]["steps"]), job
    plan = next(step for step in jobs["deploy"]["steps"] if step.get("name") == "Terraform Plan")["run"]
    assert plan.count(image_tag) == 2, "both images reach Terraform with the build's tag"
    assert "triggers_replace = [var.backend_image]" in _terraform("migrate.tf")


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
