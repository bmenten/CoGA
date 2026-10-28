"""The release mechanics (OPS-8): the version a build reports, the commits that may be released,
the image a deploy runs, and where a release keeps its SBOM.

TF-18 §2: only a release build reports a release's version; every other build reports VERSION
marked as a development build (scripts/build-version.sh), so a build of main after a release
tag is never taken for that release in /api/version, the footers or a signed report. TF-18
§5–§6a: a release is built from a commit on main, each image is deployed by the digest its
build pushed, and the release's CycloneDX SBOMs are attached to the release by its own build.

The workflow steps run here as a runner runs them (backend/tests/_release_repo.py): in a
throwaway git repository, with stand-ins for gcloud, terraform and gh that record what they
are given.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app.core import config
from backend.tests._release_repo import (
    GIT_ENV,
    REPO,
    commit,
    fake_command,
    git,
    init_release_repo,
    run_step,
    set_version,
)


def _workflow(name: str) -> dict:
    return yaml.safe_load((REPO / ".github" / "workflows" / name).read_text())


def _step(job: dict, *, step_id: str | None = None, name: str | None = None) -> dict:
    matches = [s for s in job["steps"] if (step_id and s.get("id") == step_id) or (name and s.get("name") == name)]
    assert len(matches) == 1, f"{step_id or name}: {len(matches)} steps"
    return matches[0]


def _script(repo: Path, name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "scripts" / name), *args], cwd=repo, env=GIT_ENV, capture_output=True, text=True
    )


def _outputs(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def _digest(seed: str) -> str:
    """A well-formed image digest, built at run time."""
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


# ---- Version identity (TF-18 §2) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "development"),
    [
        ("1.4.0", "1.4.0-dev"),
        ("1.4.0-rc.2", "1.4.0-rc.2.dev"),
        ("1.4.0-beta.1", "1.4.0-beta.1.dev"),
        ("2.0.0-rc-1", "2.0.0-rc-1.dev"),
    ],
)
def test_a_build_that_is_not_a_release_reports_its_version_as_a_development_build(
    tmp_path: Path, version: str, development: str
) -> None:
    # The pre-release identifier `dev` added to VERSION: a separate SemVer version, after the
    # pre-release it extends and before the next, never the release itself.
    repo = init_release_repo(tmp_path / "repo", version)
    result = _script(repo, "build-version.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == f"{development}\n"
    # The backend applies the same rule to an image built without APP_VERSION.
    assert config._development_version(version) == development


def test_a_release_build_reports_exactly_its_tags_version(tmp_path: Path) -> None:
    repo = init_release_repo(tmp_path / "repo", "1.4.0-rc.2")
    result = _script(repo, "build-version.sh", "--release", "v1.4.0-rc.2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "1.4.0-rc.2\n"
    # A tag that is not exactly v<VERSION> stamps nothing.
    for tag in ("v1.4.0", "1.4.0-rc.2", "v1.4.0-rc.2.dev", "v1.4.0-rc.3"):
        refused = _script(repo, "build-version.sh", "--release", tag)
        assert refused.returncode == 1 and refused.stdout == "", tag
    for arguments in (["--release"], ["--release", ""], ["v1.4.0-rc.2"]):
        assert _script(repo, "build-version.sh", *arguments).returncode == 2, arguments


@pytest.mark.parametrize("version", ["1.4.0", "1.4.0-rc.2", "0.1.0-beta.1"])
def test_no_release_can_carry_a_version_a_development_build_reports(tmp_path: Path, version: str) -> None:
    # check-release-version.sh refuses a VERSION with the identifier `dev` or build metadata,
    # so the development version of any VERSION can never itself be released.
    repo = init_release_repo(tmp_path / "repo", version)
    development = _script(repo, "build-version.sh").stdout.strip()
    assert development and development != version
    set_version(repo, development)
    refused = _script(repo, "check-release-version.sh")
    assert refused.returncode == 1 and "'dev'" in refused.stdout, refused.stdout
    assert _script(repo, "build-version.sh").returncode == 1


@pytest.mark.parametrize("version", ["1.4.0-dev", "1.4.0-dev.1", "1.4.0-rc.1.dev", "1.4.0+build.7", "1.4.0-rc.1+abc"])
def test_version_carries_neither_the_development_identifier_nor_build_metadata(tmp_path: Path, version: str) -> None:
    repo = init_release_repo(tmp_path / "repo")
    set_version(repo, version)
    refused = _script(repo, "check-release-version.sh")
    assert refused.returncode == 1, refused.stdout
    assert "build metadata" in refused.stdout or "'dev'" in refused.stdout


def _run_build_metadata(repo: Path, *, ref_name: str, ref_type: str) -> tuple[dict[str, str], str]:
    """build.yml's build-metadata step, run in ``repo`` as the runner runs it: its outputs and
    what it wrote in the run's summary."""
    step = _step(_workflow("build.yml")["jobs"]["prepare"], step_id="meta")
    outputs, summary = repo.parent / "github_output", repo.parent / "step_summary"
    outputs.write_text("")
    summary.write_text("")
    env = {
        **GIT_ENV,
        "REPO_NAME": "CoGA",
        "REF_NAME": ref_name,
        "REF_TYPE": ref_type,
        "GITHUB_OUTPUT": str(outputs),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    result = run_step(step, repo, env)
    assert result.returncode == 0, result.stdout + result.stderr
    return _outputs(outputs), summary.read_text()


def test_after_a_release_tag_a_build_of_main_never_reports_that_releases_version(tmp_path: Path) -> None:
    # The release candidate is tagged v1.4.0-rc.1 with VERSION 1.4.0-rc.1, and main moves on
    # without touching VERSION. Every build of main used to stamp 1.4.0-rc.1 too, so /api/version
    # and every report it signed named the release candidate.
    repo = init_release_repo(tmp_path / "repo", "1.4.0-rc.1")
    release, release_summary = _run_build_metadata(repo, ref_name="v1.4.0-rc.1", ref_type="tag")
    commit(repo)
    after, after_summary = _run_build_metadata(repo, ref_name="main", ref_type="branch")
    assert release["app_version"] == "1.4.0-rc.1"
    assert after["app_version"] == "1.4.0-rc.1.dev"
    assert after["git_sha"] == git(repo, "rev-parse", "--short=12", "HEAD")
    # The run's summary names the version each build stamped.
    assert "`1.4.0-rc.1`" in release_summary and "`1.4.0-rc.1.dev`" in after_summary
    # Nor does a release tag that is not v<VERSION> stamp anything.
    with pytest.raises(AssertionError):
        _run_build_metadata(repo, ref_name="v1.4.0", ref_type="tag")


def test_every_build_stamps_the_version_the_build_script_computes() -> None:
    # The CI image check, build.yml and the manual build in the deployment guide all stamp
    # what scripts/build-version.sh prints, never VERSION read directly.
    prepare = _workflow("build.yml")["jobs"]["prepare"]
    meta = _step(prepare, step_id="meta")["run"]
    assert 'APP_VERSION=$(./scripts/build-version.sh --release "$REF_NAME")' in meta
    # A build of main; prepare builds no other branch (test_ci_workflows.py).
    assert meta.count("APP_VERSION=$(./scripts/build-version.sh)") == 1
    assert "cat VERSION" not in meta
    images = "\n".join(step.get("run", "") for step in _workflow("ci.yml")["jobs"]["images"]["steps"])
    assert images.count('"$(./scripts/build-version.sh)"') == 2, "the image is built and checked with it"
    assert "cat VERSION" not in images
    guide = (REPO / "docs" / "deployment-gcp.md").read_text()
    assert "_APP_VERSION=$(./scripts/build-version.sh)" in guide
    assert "_APP_VERSION=$(cat VERSION)" not in guide


# ---- A release tag is on main (TF-18 §6) -----------------------------------------------------


def _origin_with_a_branch_off_main(tmp_path: Path) -> Path:
    """An origin whose main carries v1.4.0 (on an older commit) and v1.4.2 (at its head), and
    whose branch `side`, never merged, carries v1.4.1."""
    origin = init_release_repo(tmp_path / "origin", "1.4.0")
    git(origin, "tag", "-a", "v1.4.0", "-m", "CoGA 1.4.0")
    commit(origin, "on main")
    git(origin, "switch", "-q", "-c", "side")
    set_version(origin, "1.4.1")
    commit(origin, "never merged")
    git(origin, "tag", "-a", "v1.4.1", "-m", "CoGA 1.4.1")
    git(origin, "switch", "-q", "main")
    set_version(origin, "1.4.2")
    commit(origin, "release 1.4.2")
    git(origin, "tag", "-a", "v1.4.2", "-m", "CoGA 1.4.2")
    return origin


def _clone(origin: Path, target: Path, *options: str) -> Path:
    subprocess.run(
        ["git", "clone", "-q", *options, origin.as_uri(), str(target)], env=GIT_ENV, check=True, capture_output=True
    )
    return target


def test_a_release_tag_on_main_passes_and_one_off_main_is_refused(tmp_path: Path) -> None:
    clone = _clone(_origin_with_a_branch_off_main(tmp_path), tmp_path / "clone")
    for tag in ("v1.4.0", "v1.4.2"):
        result = _script(clone, "check-release-on-main.sh", tag)
        assert result.returncode == 0, result.stdout
        assert f"✓ Tag {tag}" in result.stdout

    refused = _script(clone, "check-release-on-main.sh", "v1.4.1")
    assert refused.returncode == 1
    side = git(clone, "rev-parse", "v1.4.1^{commit}")
    assert "Release tag v1.4.1 is not on main" in refused.stdout and side in refused.stdout
    assert "gh release delete v1.4.1 --cleanup-tag" in refused.stdout

    # The same commit is on main once main has merged it.
    origin = tmp_path / "origin"
    git(origin, "merge", "-q", "--no-edit", "-s", "ours", "side")
    git(clone, "fetch", "-q", "origin")
    assert _script(clone, "check-release-on-main.sh", "v1.4.1").returncode == 0


def test_the_main_check_refuses_to_answer_without_the_whole_history(tmp_path: Path) -> None:
    origin = _origin_with_a_branch_off_main(tmp_path)
    shallow = _clone(origin, tmp_path / "shallow", "--depth", "1", "--no-single-branch")
    git(shallow, "fetch", "-q", "--depth", "1", "origin", "tag", "v1.4.0")
    for tag in ("v1.4.0", "v1.4.2"):
        refused = _script(shallow, "check-release-on-main.sh", tag)
        assert refused.returncode == 1 and "shallow" in refused.stdout, refused.stdout

    clone = _clone(origin, tmp_path / "clone")
    unknown = _script(clone, "check-release-on-main.sh", "v9.9.9")
    assert unknown.returncode == 1 and "no tag v9.9.9" in unknown.stdout
    no_main = _script(clone, "check-release-on-main.sh", "v1.4.0", "origin/no-such-branch")
    assert no_main.returncode == 1 and "origin/no-such-branch is not known" in no_main.stdout
    for arguments in ([], [""], ["v1.4.0", "origin/main", "extra"]):
        assert _script(clone, "check-release-on-main.sh", *arguments).returncode == 2, arguments


def test_the_release_build_refuses_a_tag_that_is_not_on_main_before_it_stamps_anything(tmp_path: Path) -> None:
    jobs = _workflow("build.yml")["jobs"]
    prepare = jobs["prepare"]
    checkout = prepare["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@") and checkout["with"]["fetch-depth"] == 0
    on_main = _step(prepare, name="Check the release tag is on main")
    assert on_main["if"] == "github.ref_type == 'tag'"
    assert on_main["env"] == {"REF_NAME": "${{ github.ref_name }}"} and "${{" not in on_main["run"]
    order = prepare["steps"].index
    assert order(on_main) < order(_step(prepare, name="Check the release version identifies this build"))
    assert order(on_main) < order(_step(prepare, step_id="meta"))
    # Nothing is built, deployed or archived unless prepare passed.
    for name in ("build-backend", "build-frontend", "deploy", "release-sbom"):
        needs = jobs[name]["needs"]
        assert "prepare" in ([needs] if isinstance(needs, str) else needs), name

    # The step itself, in the checkout of the tag, as the runner runs it after actions/checkout.
    origin = _origin_with_a_branch_off_main(tmp_path)
    for tag, expected in (("v1.4.1", 1), ("v1.4.2", 0)):
        clone = _clone(origin, tmp_path / f"checkout-{tag}")
        git(clone, "checkout", "-q", "--detach", tag)
        result = run_step(on_main, clone, {**GIT_ENV, "REF_NAME": tag})
        assert result.returncode == expected, result.stdout + result.stderr
        if expected:
            assert f"Release tag {tag} is not on main" in result.stdout


# ---- Deploy by digest (TF-18 §5) ---------------------------------------------------------------

_BUILD_VALUES = {
    "env.REGISTRY_PROJECT_ID": "registry-project",
    "env.GCP_REGION": "europe-west1",
    "env.REGISTRY_HOST": "europe-west1-docker.pkg.dev",
    "env.AR_REPO": "images",
    "env.CLOUDBUILD_BUCKET": "build-staging",
    "env.GCP_ENV": "dev",
    "env.GCP_REGION_SHORT": "euw1",
    "needs.prepare.outputs.repo_name": "coga",
    "needs.prepare.outputs.tag": "main-abc123def456",
    "needs.prepare.outputs.app_version": "1.4.0-dev",
    "needs.prepare.outputs.git_sha": "abc123def456",
}


def _run_build_step(tmp_path: Path, component: str, output: str, status: int = 0) -> tuple:
    """build-<component>'s build step, with a gcloud that prints a build log, then ``output``,
    and exits ``status``. Its result, outputs, summary and the arguments gcloud was given."""
    step = _step(_workflow("build.yml")["jobs"][f"build-{component}"], step_id="build")
    bin_dir, argv = tmp_path / "bin", tmp_path / "gcloud-argv"
    fake_command(
        bin_dir,
        "gcloud",
        'printf \'%s\\n\' "$@" > "$RECORDED_ARGV"\n'
        "echo 'starting build'\necho 'Step #0: Successfully built'\necho DONE\n"
        'printf \'%s\\n\' "$FAKE_OUTPUT"\nexit "$FAKE_STATUS"\n',
    )
    outputs, summary = tmp_path / "github_output", tmp_path / "step_summary"
    outputs.write_text("")
    summary.write_text("")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RECORDED_ARGV": str(argv),
        "FAKE_OUTPUT": output,
        "FAKE_STATUS": str(status),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(outputs),
        "GITHUB_STEP_SUMMARY": str(summary),
        "PROBLEM_REPORT_URL": "",
    }
    result = run_step(step, tmp_path, env, _BUILD_VALUES)
    recorded = argv.read_text().splitlines() if argv.exists() else []
    return result, _outputs(outputs), summary.read_text(), recorded


@pytest.mark.parametrize("component", ["backend", "frontend"])
def test_each_build_job_hands_deploy_the_digest_its_build_pushed(tmp_path: Path, component: str) -> None:
    job = _workflow("build.yml")["jobs"][f"build-{component}"]
    # Only the digest leaves the job: the image name holds the registry project, a secret.
    assert job["outputs"] == {"digest": "${{ steps.build.outputs.digest }}"}
    digest = _digest(component)
    result, outputs, summary, argv = _run_build_step(tmp_path, component, digest)
    assert result.returncode == 0, result.stdout + result.stderr
    assert outputs == {"digest": digest}
    assert digest in summary
    # Cloud Build pushes the image under the build's tag, and gcloud reports its digest.
    assert "--format=value(results.images[0].digest)" in argv
    assert any(f"/images/coga-{component}:main-abc123def456" in arg for arg in argv), argv


@pytest.mark.parametrize("component", ["backend", "frontend"])
def test_a_build_without_the_digest_of_its_image_fails(tmp_path: Path, component: str) -> None:
    digest = _digest(component)
    for output, status in (
        ("Build finished", 0),  # no digest at all
        ("sha256:abc", 0),  # not a digest
        (digest.upper(), 0),
        (f"{digest}\nsomething after it", 0),  # the digest is not what gcloud printed last
        (digest, 1),  # the build failed: its exit status survives the pipe into tee
    ):
        result, outputs, summary, _argv = _run_build_step(tmp_path, component, output, status)
        assert result.returncode != 0, (output, status)
        assert outputs == {} and digest not in summary


def _run_terraform_plan(tmp_path: Path, backend_digest: str, frontend_digest: str) -> tuple:
    """The deploy job's plan step with a terraform that records its arguments: the result and
    the -var values it was given (none when it never ran)."""
    workflow = _workflow("build.yml")
    plan = _step(workflow["jobs"]["deploy"], name="Terraform Plan")
    bin_dir, argv = tmp_path / "bin", tmp_path / "terraform-argv"
    fake_command(bin_dir, "terraform", 'printf \'%s\\n\' "$@" > "$RECORDED_ARGV"\n')
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RECORDED_ARGV": str(argv),
        # The workflow's env, with stand-ins for its secrets.
        "REGISTRY_HOST": workflow["env"]["REGISTRY_HOST"],
        "AR_REPO": workflow["env"]["AR_REPO"],
        "REGISTRY_PROJECT_ID": "registry-project",
        # The step's env, as the build jobs and prepare hand it over.
        "BACKEND_DIGEST": backend_digest,
        "FRONTEND_DIGEST": frontend_digest,
        "REPO_NAME": "coga",
    }
    values = {"env.COGA_PROJECT_ID": "coga-project", "env.GCP_REGION": "europe-west1"}
    # Each secret the step names, as a stand-in.
    values.update((name, "stand-in") for name in re.findall(r"\$\{\{\s*(secrets\.\w+)\s*\}\}", plan["run"]))
    result = run_step(plan, tmp_path, env, values)
    variables = {}
    if argv.exists():
        for arg in argv.read_text().splitlines():
            if arg.startswith("-var="):
                name, value = arg.removeprefix("-var=").split("=", 1)
                variables[name] = value
    return result, variables


def test_deploy_hands_terraform_each_image_by_the_digest_its_build_pushed(tmp_path: Path) -> None:
    workflow = _workflow("build.yml")
    deploy = workflow["jobs"]["deploy"]
    plan = _step(deploy, name="Terraform Plan")
    assert plan["env"]["BACKEND_DIGEST"] == "${{ needs.build-backend.outputs.digest }}"
    assert plan["env"]["FRONTEND_DIGEST"] == "${{ needs.build-frontend.outputs.digest }}"
    assert "needs.prepare.outputs.tag" not in plan["run"], "no image reaches Terraform by its tag"

    backend, frontend = _digest("backend"), _digest("frontend")
    result, variables = _run_terraform_plan(tmp_path, backend, frontend)
    assert result.returncode == 0, result.stdout + result.stderr
    registry = f"{workflow['env']['REGISTRY_HOST']}/registry-project/{workflow['env']['AR_REPO']}/coga"
    assert variables["backend_image"] == f"{registry}-backend@{backend}"
    assert variables["frontend_image"] == f"{registry}-frontend@{frontend}"
    # Cloud Run's revision and the db-migrate job (triggers_replace) follow the image string.
    assert "triggers_replace = [var.backend_image]" in (REPO / "terraform" / "migrate.tf").read_text()


def test_deploy_stops_before_terraform_without_both_digests(tmp_path: Path) -> None:
    good = _digest("image")
    for backend, frontend in (("", good), (good, ""), ("main-abc123def456", good), (good, "sha256:abc"), (good, good.upper())):
        result, variables = _run_terraform_plan(tmp_path, backend, frontend)
        assert result.returncode == 1, (backend, frontend)
        assert variables == {}, "terraform must not run"
        assert "passed no image digest" in result.stdout


# ---- The release's SBOM, archived by its build (TF-18 §6a) -------------------------------------


def _generation_steps(job: dict) -> list[dict]:
    """The steps that make the SBOMs: what each runs or uses, with its inputs."""
    keep = ("uses", "with", "run")
    return [
        {key: step[key] for key in keep if key in step}
        for step in job["steps"]
        if "upload-artifact" not in step.get("uses", "") and "gh release upload" not in step.get("run", "")
    ]


def test_a_release_build_attaches_the_sbom_ci_makes_to_the_release() -> None:
    workflow = _workflow("build.yml")
    job = workflow["jobs"]["release-sbom"]
    assert job["if"] == "github.event_name == 'release'"
    assert job["needs"] == "prepare"
    # Made as ci.yml's sbom job makes it: the same actions, runtimes and command.
    ci_sbom = _workflow("ci.yml")["jobs"]["sbom"]
    assert _generation_steps(job) == _generation_steps(ci_sbom)
    assert {"run": "./scripts/generate-sbom.sh --native"} in _generation_steps(job)
    attach = job["steps"][-1]
    assert "gh release upload" in attach["run"]
    assert "--clobber" not in attach["run"], "an archived SBOM is never replaced"
    assert "${{" not in attach["run"], "values reach the script through its environment"
    assert attach["env"]["TAG"] == "${{ github.event.release.tag_name }}"
    # Only this job may write to the repository, and only to attach the files.
    assert job["permissions"] == {"contents": "write"}
    assert (workflow.get("permissions") or {}).get("contents") != "write"
    for name, other in workflow["jobs"].items():
        if name != "release-sbom":
            assert (other.get("permissions") or {}).get("contents") != "write", name


def _run_attach(tmp_path: Path, gh_status: int) -> tuple:
    attach = _workflow("build.yml")["jobs"]["release-sbom"]["steps"][-1]
    sbom = tmp_path / "sbom"
    sbom.mkdir(exist_ok=True)
    (sbom / "backend.cdx.json").write_text('{"bomFormat": "CycloneDX", "component": "backend"}\n')
    (sbom / "frontend.cdx.json").write_text('{"bomFormat": "CycloneDX", "component": "frontend"}\n')
    bin_dir, argv, summary = tmp_path / "bin", tmp_path / "gh-argv", tmp_path / "step_summary"
    fake_command(bin_dir, "gh", f'printf \'%s\\n\' "$@" > "$RECORDED_ARGV"\nexit {gh_status}\n')
    if shutil.which("sha256sum") is None:  # macOS before 15
        fake_command(bin_dir, "sha256sum", 'exec shasum -a 256 "$@"\n')
    summary.write_text("")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RECORDED_ARGV": str(argv),
        "GITHUB_STEP_SUMMARY": str(summary),
        "REPOSITORY": "example-org/CoGA",
        "TAG": "v1.4.0",
    }
    result = run_step(attach, tmp_path, env)
    return result, sbom, argv.read_text().splitlines(), summary.read_text()


def test_the_sboms_are_attached_with_their_sha256_sums(tmp_path: Path) -> None:
    result, sbom, argv, summary = _run_attach(tmp_path, gh_status=0)
    assert result.returncode == 0, result.stdout + result.stderr
    sums = {
        name: hashlib.sha256((sbom / name).read_bytes()).hexdigest() for name in ("backend.cdx.json", "frontend.cdx.json")
    }
    assert (sbom / "sbom-sha256sums.txt").read_text() == "".join(f"{sums[n]}  {n}\n" for n in sums)
    assert argv == [
        "release",
        "upload",
        "v1.4.0",
        "--repo",
        "example-org/CoGA",
        "sbom/backend.cdx.json",
        "sbom/frontend.cdx.json",
        "sbom/sbom-sha256sums.txt",
    ]
    assert all(value in summary for value in sums.values())


def test_a_failed_attach_fails_the_job_and_claims_nothing(tmp_path: Path) -> None:
    result, _sbom, argv, summary = _run_attach(tmp_path, gh_status=1)
    assert result.returncode != 0
    assert argv[:2] == ["release", "upload"]
    assert summary == "", "the run's summary does not say the SBOMs were attached"
