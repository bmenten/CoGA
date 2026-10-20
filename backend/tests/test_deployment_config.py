"""Pre-deployment hardening that lives in configuration files (#520).

These guard the settings against quiet regression: a review of a compose or Terraform
diff does not always catch a port that is published again or a mount that is writable.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path
from secrets import token_hex, token_urlsafe

import pytest
import yaml
from pydantic import ValidationError

from app.core.config import Settings, _development_version
from backend.tests._release_repo import GIT_ENV as _GIT_ENV
from backend.tests._release_repo import commit as _commit
from backend.tests._release_repo import init_release_repo

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
    """Run the workflow's build-metadata step in ``repo`` (a repository from
    ``init_release_repo``, which holds the scripts it runs), as the runner would; return its
    outputs."""
    step = next(s for s in _build_workflow()["jobs"]["prepare"]["steps"] if s.get("id") == "meta")
    outputs, summary = repo.parent / "github_output", repo.parent / "step_summary"
    outputs.write_text("")
    env = {
        "PATH": os.environ["PATH"],
        "REPO_NAME": "CoGA",
        "REF_NAME": ref_name,
        "REF_TYPE": ref_type,
        "GITHUB_OUTPUT": str(outputs),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]], cwd=repo, env=env, check=True)
    return dict(line.split("=", 1) for line in outputs.read_text().splitlines())


def test_each_main_build_pushes_an_image_of_its_own_and_deploys_it_by_digest(tmp_path: Path) -> None:
    # Terraform deploys the image string it is given. One every main build reuses (`:main`)
    # leaves it no change to apply: Cloud Run keeps the image it runs, and the db-migrate job,
    # keyed on the backend image, never runs again after the first deploy. Each build pushes
    # its image under a tag of its own, and deploy hands Terraform the digest the build pushed
    # (test_release_mechanics.py), which every build changes.
    repo = init_release_repo(tmp_path / "repo", "1.4.0")
    first = _run_build_metadata(repo, ref_name="main", ref_type="branch")
    _commit(repo)
    second = _run_build_metadata(repo, ref_name="main", ref_type="branch")

    assert first["tag"] == f"main-{first['git_sha']}" and len(first["git_sha"]) == 12
    assert second["tag"] != first["tag"], "two commits on main must push two different images"
    assert _run_build_metadata(repo, ref_name="v1.4.0", ref_type="tag")["tag"] == "v1.4.0"

    # The images are pushed under that tag, and deployed by the digest of what was pushed.
    jobs = _build_workflow()["jobs"]
    image_tag = ":${{ needs.prepare.outputs.tag }}"
    for job in ("build-backend", "build-frontend"):
        assert any(image_tag in step.get("run", "") for step in jobs[job]["steps"]), job
    plan = next(step for step in jobs["deploy"]["steps"] if step.get("name") == "Terraform Plan")
    assert image_tag not in plan["run"], "no image reaches Terraform by its tag"
    assert '-backend@${BACKEND_DIGEST}"' in plan["run"] and '-frontend@${FRONTEND_DIGEST}"' in plan["run"]
    assert "triggers_replace = [var.backend_image]" in _terraform("migrate.tf")


def _ci_workflow() -> dict:
    return yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())


def _ci_images_job() -> dict:
    job = _ci_workflow()["jobs"].get("images")
    assert job, "no CI job builds the production images"
    return job


def _docker_build(argv: list[str]) -> dict[str, object]:
    """A `docker build` command line without its image tag: the Dockerfile, the target, the
    build arguments (in any order), and the context with anything else it passes."""
    assert argv[:1] == ["build"], argv
    dockerfile = target = None
    build_args: list[str] = []
    rest: list[str] = []
    args = iter(argv[1:])
    for arg in args:
        if arg in ("-f", "--file"):
            dockerfile = next(args)
        elif arg == "--target":
            target = next(args)
        elif arg == "--build-arg":
            build_args.append(next(args))
        elif arg in ("-t", "--tag"):
            next(args)
        else:
            rest.append(arg)
    return {"file": dockerfile, "target": target, "build_args": sorted(build_args), "rest": rest}


def _cloud_build(name: str, **substitutions: str) -> dict[str, object]:
    """The `docker build` that ci/cloudbuild.<name>.yaml runs when build.yml passes it these
    substitutions; the others keep the config's defaults."""
    config = yaml.safe_load((REPO / "ci" / f"cloudbuild.{name}.yaml").read_text())
    (step,) = config["steps"]
    assert step["name"] == "gcr.io/cloud-builders/docker"
    values = {**(config.get("substitutions") or {}), "_IMAGE": "registry.example/coga:tag", **substitutions}
    return _docker_build([re.sub(r"\$\{(_\w+)\}", lambda match: values[match.group(1)], arg) for arg in step["args"]])


def _run_ci_step(tmp_path: Path, repo: Path, step: dict, variables: dict[str, str]) -> list[str]:
    """Run a step of the CI `images` job in ``repo`` as the runner would, with a `docker` that
    only records its arguments. ``variables`` stand in for the repository variables."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    recorded = tmp_path / "docker-argv"
    docker = fake_bin / "docker"
    docker.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$RECORDED_ARGV"\n')
    docker.chmod(0o755)
    env = {**_GIT_ENV, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "RECORDED_ARGV": str(recorded)}
    for name, value in (step.get("env") or {}).items():
        resolved = re.sub(r"\$\{\{\s*vars\.(\w+)\s*\}\}", lambda match: variables.get(match.group(1), ""), str(value))
        assert "${{" not in resolved, f"{name}: {value} is not a repository variable"
        env[name] = resolved
    subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]], cwd=repo, env=env, check=True)
    return recorded.read_text().splitlines()


@pytest.mark.parametrize("problem_report_url", ["", "https://problems.example.org/report"])
def test_ci_builds_the_production_images_as_the_release_build_does(tmp_path: Path, problem_report_url: str) -> None:
    # build.yml builds the images in Cloud Build only after a merge, and not at all until
    # Google Cloud is configured. The CI `images` job builds them on every pull request, and
    # that proves something only while it is the release build: the same Dockerfile, target,
    # context and build arguments, the backend stamped with what build.yml's prepare job
    # computes for a build of main (TF-18 §2): VERSION as a development build.
    repo = init_release_repo(tmp_path / "repo", "1.4.0")
    release = _run_build_metadata(repo, ref_name="main", ref_type="branch")
    assert release["app_version"] == "1.4.0-dev"

    built = {}
    for step in _ci_images_job()["steps"]:
        if "docker build" in step.get("run", ""):
            build = _docker_build(_run_ci_step(tmp_path, repo, step, {"COGA_PROBLEM_REPORT_URL": problem_report_url}))
            assert build["file"] not in built, f"{build['file']} is built twice"
            built[build["file"]] = build

    # build.yml passes the backend its version and commit, and the frontend the problem-report
    # route when the repository variable is set; the GitHub links keep their empty defaults.
    assert built == {
        "backend/Dockerfile": _cloud_build("backend", _APP_VERSION=release["app_version"], _GIT_SHA=release["git_sha"]),
        "frontend/Dockerfile": _cloud_build("frontend", _PROBLEM_REPORT_URL=problem_report_url),
    }
    assert built["backend/Dockerfile"]["target"] == built["frontend/Dockerfile"]["target"] == "production"
    assert f"GIT_SHA={release['git_sha']}" in built["backend/Dockerfile"]["build_args"]


def test_the_ci_image_build_runs_on_every_change_and_pushes_nothing() -> None:
    workflow = _ci_workflow()
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
    assert "pull_request" in triggers and triggers["push"]["branches"] == ["main"]
    job = _ci_images_job()
    assert "if" not in job, "the image build must not be skipped"
    # The workflow's read-only token and no registry credentials: nothing can be pushed.
    assert workflow["permissions"] == {"contents": "read"} and "permissions" not in job
    assert [step["uses"].split("@")[0] for step in job["steps"] if "uses" in step] == ["actions/checkout"]
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    for push in ("docker push", "docker login", "--push", "type=registry", "push=true"):
        assert push not in scripts, push


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


def test_an_unstamped_image_reports_the_version_file() -> None:
    # Settings reads VERSION from the directory above backend/ (app/core/config.py's
    # parents[3]). In the image config.py is /app/app/core/config.py, so that is /, where the
    # Dockerfile copies VERSION. Compose leaves APP_VERSION empty unless it is exported, so
    # a local build names the version it was built from, as a development build (TF-18 §2).
    dockerfile = (REPO / "backend" / "Dockerfile").read_text()
    assert "COPY VERSION /VERSION" in dockerfile
    assert "WORKDIR /app" in dockerfile and "COPY backend/app ./app" in dockerfile
    assert re.search(r"^ARG APP_VERSION=$", dockerfile, re.M), "an unstamped build must leave APP_VERSION empty"
    args = _compose()["services"]["backend"]["build"]["args"]
    assert args["APP_VERSION"] == "${APP_VERSION:-}"
    assert args["GIT_SHA"] == "${GIT_SHA:-unknown}"


def test_the_backend_image_ships_only_the_scripts_it_runs() -> None:
    # The image carries the knowledgebase build the admin rebuild runs and the DGV import an
    # operator runs in the container; never the test seeders (one creates a sign-in with a
    # default password), the demo loaders or the CI checks.
    dockerfile = (REPO / "backend" / "Dockerfile").read_text()
    copies = [line.split()[1:] for line in dockerfile.splitlines() if line.startswith("COPY ")]
    shipped = {source for args in copies for source in args[:-1] if source.startswith("scripts")}
    assert shipped == {"scripts/clinical_cnv_knowledgebase.py", "scripts/import_dgv.py"}
    for source in shipped:
        assert (REPO / source).is_file(), source
    # The admin rebuild runs the script where the image puts it.
    assert Settings(_env_file=None).clinical_cnv_kb_script_path == "/app/scripts/clinical_cnv_knowledgebase.py"


# ---- The environment of every container that runs the backend image ------------------
#
# Each Cloud Run service and job that runs var.backend_image loads the backend's settings
# first, and outside development they refuse to load without real secrets, the app's own
# origin and the build's commit (Settings.validate_security_defaults). These read each such
# container's environment from the .tf files, as Terraform would give it.

# Strings (with their ${...} interpolations), heredocs and comments: the braces, commas and
# names in them are not the configuration's own.
_HCL_TEXT = re.compile(
    r'"(?:[^"\\$]|\\.|\$\{[^}]*\}|\$)*"|<<-?(\w+)\n.*?\n\s*\1(?=\n)|#[^\n]*|//[^\n]*|/\*.*?\*/',
    re.S,
)

# What Terraform computes from resources and the deployment's choices, given plausible values
# here: the settings check none of them beyond their form. A reference the test cannot
# evaluate fails it, so a new one is looked at and added on purpose.
_COMPUTED: dict[str, object] = {
    # db_runtime_role = "coga_app", the mode in which the db-migrate job exists.
    "local.app_db_user": "coga_app",
    "local.app_runs_migrations": False,
    "local.family_import_roots": ["gs://coga-phi/imports"],
    "google_storage_bucket.phi.name": "coga-phi",
    "google_sql_database.coga.name": "coga",
    "google_sql_user.coga.name": "coga_admin",
    "google_sql_database_instance.postgres.private_ip_address": "10.10.0.3",
    "google_sql_database_instance.postgres.connection_name": "coga-project:europe-west1:coga-postgres",
    "google_compute_address.clickhouse.address": "10.10.0.4",
    "tls_self_signed_cert.ca.cert_pem": "the private CA's certificate",
}


def _skeleton(text: str) -> str:
    """``text`` with the insides of its strings, heredocs and comments blanked out, at the same
    offsets, so the braces, commas and names left are the configuration's own."""

    def blank(match: re.Match[str]) -> str:
        inside = re.sub(r"[^\n]", " ", match.group())
        return f'"{inside[1:-1]}"' if match.group().startswith('"') else inside

    return _HCL_TEXT.sub(blank, text)


def _block_body(text: str, opening: int) -> str:
    """The body of the block or object whose ``{`` is at ``opening`` in ``text``."""
    depth = 0
    for index, char in enumerate(_skeleton(text)[opening:], start=opening):
        depth += (char == "{") - (char == "}")
        if depth == 0:
            return text[opening + 1 : index]
    raise AssertionError(f"no closing brace for {text[max(opening - 60, 0) : opening + 1]!r}")


def _attributes(body: str) -> dict[str, str]:
    """The attributes set at the top level of a block's or object's ``body``, as {name:
    expression}: an object value whole, any other value as its line holds it."""
    top, depth = [], 0
    for char in _skeleton(body):
        depth -= char == "}"
        top.append(char if depth == 0 or char == "\n" else " ")
        depth += char == "{"
    attributes = {}
    for match in re.finditer(r"^[ \t]*(\w+)[ \t]*=(?!=)[ \t]*(\S.*?)[ \t]*$", "".join(top), re.M):
        start = match.start(2)
        value = body[start : match.end(2)]
        attributes[match[1]] = "{" + _block_body(body, start) + "}" if value.startswith("{") else value
    return attributes


def _blocks(text: str, kind: str) -> list[tuple[str | None, str]]:
    """Every ``kind`` block in ``text``, at any depth, as (for_each, body): None and its body for
    a plain block, and for a ``dynamic "kind"`` block its for_each and its content's body."""
    blocks: list[tuple[str | None, str]] = []
    for match in re.finditer(r'\b(?:(\w+)|dynamic\s+"([^"]*)")\s*\{', _skeleton(text)):
        dynamic = match[1] is None
        label = text[match.start(2) : match.end(2)] if dynamic else match[1]
        if label != kind:
            continue
        body = _block_body(text, match.end() - 1)
        if dynamic:
            blocks.append((_attributes(body)["for_each"], _blocks(body, "content")[0][1]))
        else:
            blocks.append((None, body))
    return blocks


def _local(name: str) -> str:
    """The expression a ``locals`` block of the configuration gives ``name``."""
    for path in sorted((REPO / "terraform").glob("*.tf")):
        for _, body in _blocks(path.read_text(), "locals"):
            if name in (values := _attributes(body)):
                return values[name]
    raise AssertionError(f"no local {name}")


def _items(text: str) -> list[str]:
    """``text`` split at its top-level commas: a list's items, or a call's arguments."""
    items, depth, start = [], 0, 0
    for index, char in enumerate(_skeleton(text)):
        depth += (char in "([{") - (char in ")]}")
        if char == "," and depth == 0:
            items.append(text[start:index])
            start = index + 1
    items.append(text[start:])
    return [item.strip() for item in items if item.strip()]


def _env_string(value: object) -> str:
    """``value`` as an environment variable holds it, converted as Terraform converts it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    assert isinstance(value, (str, int)), f"{value!r} is no value an environment variable holds"
    return str(value)


def _evaluate(expression: str) -> object:
    """``expression`` as Terraform evaluates it, for the forms the environments use: string
    templates, lists, jsonencode/tostring/join, variables (their defaults), locals, and the
    values Terraform computes (_COMPUTED)."""
    expression = expression.strip()
    if expression in _COMPUTED:
        return _COMPUTED[expression]
    if reference := re.fullmatch(r"(var|local)\.(\w+)", expression):
        if reference[1] == "var":
            return json.loads(_variable_default(reference[2]))
        return _evaluate(_local(reference[2]))
    if call := re.fullmatch(r"(jsonencode|tostring|join)\((.*)\)", expression, re.S):
        arguments = [_evaluate(argument) for argument in _items(call[2])]
        if call[1] == "jsonencode":
            return json.dumps(*arguments, separators=(",", ":"))
        if call[1] == "tostring":
            return _env_string(*arguments)
        separator, values = arguments
        return separator.join(values)
    if expression.startswith("[") and expression.endswith("]"):
        return [_evaluate(item) for item in _items(expression[1:-1])]
    if expression.startswith('"') and _HCL_TEXT.fullmatch(expression) and "\\" not in expression:
        return re.sub(r"\$\{([^}]*)\}", lambda match: _env_string(_evaluate(match[1])), expression[1:-1])
    raise AssertionError(f"this test cannot evaluate {expression!r}: give it a value in _COMPUTED")


def _map(expression: str) -> dict[str, str]:
    """The map a dynamic env block is repeated over, as {key: value expression}: a local written
    as an object, or migrate.tf's, which names each app secret by the setting that reads it."""
    local = re.fullmatch(r"local\.(\w+)", expression)
    assert local, f"this test reads no environment from for_each = {expression}"
    definition = _local(local[1])
    assert definition.startswith("{"), f"local.{local[1]} is no map: {definition}"
    renamed = re.fullmatch(
        r'\{\s*for (\w+) in keys\(local\.(\w+)\)\s*:\s*\(\1 == "(\w+)" \? "(\w+)" : upper\(\1\)\) => \1\s*\}',
        definition,
    )
    if renamed:
        _, source, special, setting = renamed.groups()
        return {(setting if key == special else key.upper()): f'"{key}"' for key in _map(f"local.{source}")}
    return _attributes(definition[1:-1])


def _secret(setting: str) -> str:
    """A strong value for ``setting`` read from Secret Manager, as deployment-gcp.md 5.5 has the
    operator create it. Made at run time: the secret scan catches key-shaped literals."""
    if setting == "INTEGRITY_ANCHOR_SIGNING_KEY":
        return base64.b64encode(os.urandom(32)).decode()
    return token_urlsafe(48)


def _containers(image: str) -> dict[str, str]:
    """Each container of a Cloud Run service or job that runs ``image`` (``var.backend_image`` or
    ``var.frontend_image``), by the address of its resource."""
    containers: dict[str, str] = {}
    for path in sorted((REPO / "terraform").glob("*.tf")):
        text = path.read_text()
        for resource in re.finditer(r'^resource "(google_cloud_run_v2_(?:service|job))" "(\w+)" \{', text, re.M):
            for _, container in _blocks(_block_body(text, resource.end() - 1), "containers"):
                if _attributes(container).get("image") == image:
                    address = f"{resource[1]}.{resource[2]}"
                    assert address not in containers, f"{address} runs {image} twice"
                    containers[address] = container
    return containers


def _backend_containers() -> dict[str, str]:
    """Each container of a Cloud Run service or job that runs the backend image, by the address
    of its resource."""
    return _containers("var.backend_image")


def _environment(container: str, *, optional: bool) -> dict[str, str]:
    """The environment Terraform gives ``container``, with a strong stand-in for each value read
    from Secret Manager. ``optional`` sets the entries repeated over `<condition> ? [1] : []`
    (the metrics token, the import roots), as their conditions do when they hold."""
    environment: dict[str, str] = {}
    for for_each, entry in _blocks(container, "env"):
        if for_each is None:
            repeats: dict = {None: None}
        elif re.fullmatch(r".+ \? \[1\] : \[\]", for_each):
            repeats = {None: None} if optional else {}
        else:
            repeats = _map(for_each)
        attributes = _attributes(entry)
        for key, value in repeats.items():
            name = key if attributes["name"] == "env.key" else _env_string(_evaluate(attributes["name"]))
            assert name not in environment, f"{name} is set twice"
            if _blocks(entry, "value_source"):
                environment[name] = _secret(name)
            else:
                expression = value if attributes["value"] == "env.value" else attributes["value"]
                environment[name] = _env_string(_evaluate(expression))
    return environment


def _image_environment() -> dict[str, str]:
    """What the backend image sets itself: the build identity build.yml stamps into it, through
    ci/cloudbuild.backend.yaml's build arguments and the Dockerfile's ENV."""
    dockerfile = (REPO / "backend" / "Dockerfile").read_text()
    assert re.search(r"^ENV APP_VERSION=\$\{APP_VERSION\} \\\n\s*GIT_SHA=\$\{GIT_SHA\}$", dockerfile, re.M), (
        "the image no longer sets the build identity it is built with"
    )
    cloudbuild = (REPO / "ci" / "cloudbuild.backend.yaml").read_text()
    assert "- APP_VERSION=${_APP_VERSION}" in cloudbuild and "- GIT_SHA=${_GIT_SHA}" in cloudbuild, (
        "Cloud Build no longer passes the build identity to the image"
    )
    workflow = (REPO / ".github" / "workflows" / "build.yml").read_text()
    stamp = "_APP_VERSION=${{ needs.prepare.outputs.app_version }},_GIT_SHA=${{ needs.prepare.outputs.git_sha }}"
    assert stamp in workflow, "build.yml no longer stamps the backend image with its version and commit"
    # What build.yml's metadata step stamps into a build of main: VERSION as a development
    # build (scripts/build-version.sh), and `git rev-parse --short=12 HEAD`.
    return {"APP_VERSION": _development_version((REPO / "VERSION").read_text().strip()), "GIT_SHA": token_hex(6)}


def _load_settings(monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]) -> Settings:
    """The backend's settings as a process whose environment is exactly ``environment`` loads
    them: nothing of this test run's own environment reaches them."""
    names = {name.lower() for key, field in Settings.model_fields.items() for name in (key, field.alias) if name}
    for name in list(os.environ):
        if name.lower() in names:
            monkeypatch.delenv(name)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_every_container_of_the_backend_image_loads_its_settings_in_production(monkeypatch) -> None:
    # The API and the db-migrate job. Terraform gave the job production mode without the
    # cross-origin settings, so `python -m app.db_migrate` stopped at import and the switch to
    # the restricted database role could not run. Each container's environment as the .tf
    # files define it, with and without its optional entries, with strong stand-ins for its
    # Secret Manager values and the commit build.yml stamps into the image.
    containers = _backend_containers()
    assert {"google_cloud_run_v2_service.backend", "google_cloud_run_v2_job.db_migrate"} <= containers.keys()
    image = _image_environment()
    for address, container in containers.items():
        for optional in (True, False):
            environment = _environment(container, optional=optional)
            assert environment.keys().isdisjoint(image), f"{address} overrides the build identity of its image"
            try:
                settings = _load_settings(monkeypatch, {**image, **environment})
            except ValidationError as error:
                pytest.fail(f"{address} cannot start: {error}")
            assert settings.is_development is False, f"{address} does not run in production mode"


def test_terraform_gives_every_container_of_the_backend_image_no_cross_origin_access() -> None:
    # Same-origin behind the load balancer: the app's own origin only, and no origin pattern
    # (the backend refuses to start outside development with the localhost default), for the
    # API and the db-migrate job alike.
    domain = json.loads(_variable_default("app_domain"))
    for address, container in _backend_containers().items():
        environment = _environment(container, optional=True)
        assert json.loads(environment.get("CORS_ORIGINS", "null")) == [f"https://{domain}"], address
        assert environment.get("CORS_ORIGIN_REGEX") == "", address


def _frontend_hsts_switch() -> str:
    """The ENABLE_HSTS value with which the frontend server sends HSTS. securityHeaders.mjs
    compares the variable with one string, where the backend's settings read it as a boolean:
    "1" or "True" would turn on the API's HSTS and not the page's."""
    switches = re.findall(r"process\.env\.ENABLE_HSTS === '([^']*)'", (REPO / "frontend" / "securityHeaders.mjs").read_text())
    assert len(switches) == 1, f"the frontend server no longer reads ENABLE_HSTS as this test expects: {switches}"
    return switches[0]


def _hsts_policy(source: str) -> str:
    """The Strict-Transport-Security value the server in ``source`` sends when HSTS is on."""
    policies = re.findall(r"""['"]Strict-Transport-Security['"],\s*['"]([^'"]+)['"]""", (REPO / source).read_text())
    assert len(policies) == 1, f"{source} sends no single HSTS value: {policies}"
    return policies[0]


def test_the_app_page_and_the_api_both_send_hsts_on_google_cloud(monkeypatch) -> None:
    # TLS ends at the load balancer for the page and for /api alike (loadbalancer.tf).
    # Terraform turned HSTS on for the backend only, so the app page went without it (SEC-5).
    # Each container of the frontend image sets ENABLE_HSTS to the value its server compares
    # with, and does so unconditionally: the optional entries are left out here.
    frontend = _containers("var.frontend_image")
    assert "google_cloud_run_v2_service.frontend" in frontend
    for address, container in frontend.items():
        environment = _environment(container, optional=False)
        assert environment.get("ENABLE_HSTS") == _frontend_hsts_switch(), f"{address} sends no HSTS"
    backend = _backend_containers()["google_cloud_run_v2_service.backend"]
    settings = _load_settings(monkeypatch, {**_image_environment(), **_environment(backend, optional=False)})
    assert settings.enable_hsts is True, "the API sends no HSTS"
    # One host, so one policy: a browser keeps the one the host's latest answer sent.
    assert _hsts_policy("frontend/securityHeaders.mjs") == _hsts_policy("backend/app/middleware/security_headers.py")


def _rendered_collector_config() -> dict:
    """The collector config as Terraform's templatefile() renders it, from monitoring.tf's own
    arguments ($${ is a literal ${ for the collector)."""
    monitoring = _terraform("monitoring.tf")
    call = re.search(r'templatefile\("\$\{path\.module\}/scripts/metrics-collector\.yaml\.tftpl", \{(.*?)\}\)', monitoring, re.S)
    assert call, "monitoring.tf renders no metrics-collector template"
    local_values = {
        "local.backend_service_name": "coga-backend",
        "local.backend_port": "8000",
        "local.metrics_collector_health_port": "13133",
        "var.project_id": "coga-test-project",
    }
    arguments = {}
    for name, value in re.findall(r"^\s*(\w+)\s*=\s*(.+?)\s*$", call.group(1), re.M):
        arguments[name] = value.strip('"') if value.startswith('"') else local_values[value]
    template = (REPO / "terraform" / "scripts" / "metrics-collector.yaml.tftpl").read_text()
    rendered = re.sub(r"(?<!\$)\$\{(\w+)\}", lambda match: arguments[match.group(1)], template).replace("$${", "${")
    assert "%{" not in rendered
    return yaml.safe_load(rendered)


def test_the_metrics_collector_scrapes_the_backend_with_the_token() -> None:
    # Over the instance's loopback, the backend's own port, the endpoint outside /api, and the
    # token from the environment (never in the config, which Terraform puts in plain sight).
    config = _rendered_collector_config()
    scrape = config["receivers"]["prometheus"]["config"]["scrape_configs"]
    assert len(scrape) == 1
    assert scrape[0]["metrics_path"] == "/metrics"
    assert scrape[0]["static_configs"] == [{"targets": ["localhost:8000"]}]
    assert scrape[0]["authorization"] == {"type": "Bearer", "credentials": "${env:METRICS_TOKEN}"}
    assert scrape[0]["scrape_interval"] == "60s"
    assert config["exporters"] == {"googlemanagedprometheus": {"project": "coga-test-project"}}
    pipeline = config["service"]["pipelines"]["metrics"]
    assert pipeline["receivers"] == ["prometheus"] and pipeline["exporters"] == ["googlemanagedprometheus"]
    assert config["extensions"]["health_check"]["endpoint"] == "0.0.0.0:13133"
    assert "8000" in _terraform("monitoring.tf").split("backend_port                  =")[1].splitlines()[0]


def test_the_metrics_collector_runs_beside_the_backend_with_the_token_from_secret_manager() -> None:
    cloudrun = _terraform("cloudrun.tf")
    # Both containers read the same secret; the backend's port is the one the collector scrapes.
    assert cloudrun.count("secret  = google_secret_manager_secret.metrics_token.secret_id") == 2
    assert 'name  = "backend"' in cloudrun and "container_port = local.backend_port" in cloudrun
    assert 'name  = "metrics-collector"' in cloudrun
    assert 'args  = ["--config=env:OTELCOL_CONFIG"]' in cloudrun
    assert "value = local.metrics_collector_config" in cloudrun
    assert cloudrun.count("for_each = var.metrics_collection_enabled ? [1] : []") == 2
    assert "google_secret_manager_secret_iam_member.backend_metrics_token," in cloudrun
    monitoring = _terraform("monitoring.tf")
    assert 'secret_id = "${local.name_prefix}-metrics-token"' in monitoring
    assert 'member    = "serviceAccount:${local.backend_sa_email}"' in monitoring
    # The token never reaches the migration job, which does not need it.
    assert "metrics_token" not in _terraform("migrate.tf")
    # The operator adds the token's value with the other secrets.
    assert "coga-metrics-token" in (REPO / "docs" / "deployment-gcp.md").read_text()


def test_the_metrics_collector_image_is_pinned_by_digest() -> None:
    variables = _terraform("variables.tf")
    default = re.search(r'variable "metrics_collector_image"\s*\{[^}]*default\s*=\s*"([^"]+)"', variables)
    assert default, "no metrics_collector_image default"
    assert re.search(r"@sha256:[0-9a-f]{64}$", default.group(1)), default.group(1)
    assert 'can(regex("@sha256:[0-9a-f]{64}$", var.metrics_collector_image))' in variables


def test_the_alerting_warns_when_it_would_be_toothless() -> None:
    # check blocks warn at plan and apply without failing them.
    monitoring = _terraform("monitoring.tf")
    assert 'check "alerts_reach_someone"' in monitoring
    assert "length(var.alert_notification_emails) > 0" in monitoring
    assert 'check "uptime_checkers_pass_the_allowlist"' in monitoring
    assert "!(var.uptime_check_enabled && length(var.allowed_ingress_cidrs) > 0)" in monitoring
    assert monitoring.count("notification_channels = local.alert_channels") == 2


def test_the_repository_carries_one_version() -> None:
    # build.yml runs the release check only after a merge; running it here fails a pull
    # request that bumps VERSION without the frontend package, or makes VERSION malformed.
    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "check-release-version.sh")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ---- No password or private key in the Terraform state (OPS-10) -----------------------------
#
# Terraform writes every argument of a resource, and everything a data source reads, into its
# state and its plan, in clear. A password or a private key therefore reaches a resource only
# through a write-only argument (`*_wo`, never stored), from an ephemeral source (never stored
# either). `terraform validate` refuses an ephemeral value anywhere else; these keep the
# configuration from going back to a stored one.


def _top_level_blocks(kind: str) -> dict[str, str]:
    """Every ``kind`` block of the configuration ("resource", "data" or "ephemeral"), by its
    address (``type.name``), as its body."""
    blocks: dict[str, str] = {}
    for path in sorted((REPO / "terraform").glob("*.tf")):
        text = path.read_text()
        for match in re.finditer(rf'^{kind} "(\w+)" "(\w+)" \{{', text, re.M):
            address = f"{match[1]}.{match[2]}"
            assert address not in blocks, f"{kind} {address} is defined twice"
            blocks[address] = _block_body(text, match.end() - 1)
    return blocks


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


# The arguments that keep a password or a private key in the state.
_ARGUMENTS_KEPT_IN_THE_STATE = {"password", "root_password", "secret_data", "private_key_pem", "ca_private_key_pem"}


def test_no_password_or_private_key_enters_the_terraform_state() -> None:
    resources = _top_level_blocks("resource")
    for address, body in resources.items():
        stored = _ARGUMENTS_KEPT_IN_THE_STATE & set(_attributes(body))
        if address == "google_secret_manager_secret_version.clickhouse_tls_cert":
            stored.discard("secret_data")  # the server certificate, which is public
        assert not stored, f"{address} keeps {sorted(stored)} in the state"
    # Nothing generates a key or a password as a resource, which would keep it in the state.
    generators = {"tls_private_key", "random_password", "random_string", "random_bytes"}
    assert not [address for address in resources if address.split(".")[0] in generators]
    assert {"tls_private_key.ca", "tls_private_key.clickhouse"} <= set(_top_level_blocks("ephemeral"))
    # A data source that looks up a secret version reads which version it is, not its value.
    for address, body in _top_level_blocks("data").items():
        assert not address.startswith("google_secret_manager_secret_version_access."), address
        if address.startswith("google_secret_manager_secret_version."):
            assert _attributes(body).get("fetch_secret_data") == "false", f"data.{address} reads the value"


def test_terraform_sets_the_owner_password_write_only_from_the_latest_secret_version() -> None:
    # The value is read ephemerally, for the version the data source names. The state keeps that
    # version's number, so a new version of coga-postgres-password changes the password on the
    # next apply, as it did when the state kept the value (deployment-gcp.md 12.2).
    user = _attributes(_top_level_blocks("resource")["google_sql_user.coga"])
    assert user.get("password_wo") == "ephemeral.google_secret_manager_secret_version.postgres_password.secret_data"
    assert (
        user.get("password_wo_version")
        == "tonumber(data.google_secret_manager_secret_version.postgres_password.version)"
    )
    value = _attributes(_top_level_blocks("ephemeral")["google_secret_manager_secret_version.postgres_password"])
    latest = _attributes(_top_level_blocks("data")["google_secret_manager_secret_version.postgres_password"])
    assert value["secret"] == latest["secret"] == 'google_secret_manager_secret.app["postgres_password"].id'
    assert value.get("version") == "data.google_secret_manager_secret_version.postgres_password.version"


def test_the_clickhouse_tls_keys_are_ephemeral_and_the_ca_is_issued_with_the_server_cert() -> None:
    resources = _top_level_blocks("resource")
    ca_pem = "ephemeral.tls_private_key.ca.private_key_pem"
    server_pem = "ephemeral.tls_private_key.clickhouse.private_key_pem"
    version = "local.clickhouse_tls_key_version"
    ca = _attributes(resources["tls_self_signed_cert.ca"])
    request = _attributes(resources["tls_cert_request.clickhouse"])
    cert = _attributes(resources["tls_locally_signed_cert.clickhouse"])
    stored_pem = _attributes(resources["google_secret_manager_secret_version.clickhouse_tls_key"])
    assert (ca.get("private_key_pem_wo"), ca.get("private_key_pem_wo_version")) == (ca_pem, version)
    assert (request.get("private_key_pem_wo"), request.get("private_key_pem_wo_version")) == (server_pem, version)
    assert (cert.get("ca_private_key_pem_wo"), cert.get("ca_private_key_pem_wo_version")) == (ca_pem, version)
    assert (stored_pem.get("secret_data_wo"), stored_pem.get("secret_data_wo_version")) == (
        server_pem,
        f"tostring({version})",
    )
    assert re.fullmatch(r"[1-9]\d*", _local("clickhouse_tls_key_version"))
    # The CA's key is gone after the run that creates the CA, so a new certificate request (a
    # new server key) re-issues the CA in the same run, and Secret Manager stores that key.
    for address in ("tls_self_signed_cert.ca", "google_secret_manager_secret_version.clickhouse_tls_key"):
        lifecycle = _blocks(resources[address], "lifecycle")
        assert lifecycle, f"{address} has no lifecycle block"
        assert _attributes(lifecycle[0][1]).get("replace_triggered_by") == "[tls_cert_request.clickhouse]", address
    # Near expiry the CA, issued first, renews first and re-issues the server cert with it, as
    # long as both last as long and renew as early.
    for setting in ("validity_period_hours", "early_renewal_hours"):
        assert ca[setting] == cert[setting], setting
    assert "value = tls_self_signed_cert.ca.cert_pem" in _terraform("cloudrun.tf")


def test_terraform_and_its_providers_support_write_only_arguments() -> None:
    versions = _terraform("versions.tf")
    terraform = re.search(r'required_version = ">= ([\d.]+)"', versions)
    assert terraform and _version(terraform[1]) >= (1, 11, 0), "write-only arguments need Terraform 1.11"
    # google 8.0: secret_data_wo_version is a string (and 7.6 brought the ephemeral secret
    # version); tls 4.4: the certificates' write-only private keys.
    google = re.search(r'google = \{[^}]*version = ">= ([\d.]+), < ([\d.]+)"', versions)
    tls = re.search(r'tls = \{[^}]*version = "~> ([\d.]+)"', versions)
    assert google and _version(google[1]) >= (8, 0, 0) and _version(google[2]) == (9, 0, 0)
    assert tls and _version(tls[1]) >= (4, 4)
    # The pull-request validation and the deploy run one pinned Terraform, new enough.
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "build.yml").read_text())
    setups = {
        name: step
        for name, job in workflow["jobs"].items()
        for step in job.get("steps", [])
        if "hashicorp/setup-terraform" in step.get("uses", "")
    }
    assert set(setups) == {"tf-validate", "deploy"}
    pinned = {str(step.get("with", {}).get("terraform_version")) for step in setups.values()}
    assert len(pinned) == 1, pinned
    assert _version(pinned.pop()) >= _version(terraform[1])


def test_the_rollback_to_the_owner_role_can_still_remove_the_migration_job() -> None:
    # The google provider protects a Cloud Run job from deletion unless told otherwise (6.0+),
    # and rolling back to db_runtime_role = "owner" removes this one (db-runtime-role-runbook.md).
    job = _attributes(_top_level_blocks("resource")["google_cloud_run_v2_job.db_migrate"])
    assert job["count"] == "local.restricted_db_role ? 1 : 0"
    assert job.get("deletion_protection") == "false"
