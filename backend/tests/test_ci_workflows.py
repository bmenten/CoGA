"""The CI workflows' own gates (OPS-11, OPS-22).

The unit job's coverage gate is the floor script's global floor; a CI job recompiles the
backend locks with ``scripts/compile-requirements.sh`` and fails on a diff (TF-13 S-7); the
gate workflows run every job for GitHub's merge queue, and the merge queue runs no build,
deploy or release job; the datastore jobs share one block of settings; the coverage job
installs only coverage.py, from the dev lock with its hashes.

The workflow steps run here as a runner runs them (``backend/tests/_release_repo.py``), in
throwaway repositories, with stand-ins for the commands that would reach the network.
"""

from __future__ import annotations

import ast
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from backend.tests._release_repo import GIT_ENV, REPO, fake_command, git, init_release_repo, run_step

WORKFLOWS = REPO / ".github" / "workflows"
LOCKS = ("backend/requirements.txt", "backend/requirements-dev.txt")

# The required status checks of the `main` ruleset that a job of these workflows reports
# (TF-18 §6). The twelfth, `CodeQL`, is code scanning's result of the codeql jobs' uploads.
REQUIRED_CHECKS = {
    "ci.yml": {
        "backend (pytest)",
        "frontend (tsc + eslint + vitest)",
        "coverage (unit + smoke + e2e combined)",
        "smoke (real startup against Postgres + ClickHouse)",
        "e2e (golden-trio pipeline against Postgres + ClickHouse)",
        "e2e-playwright (browser journeys)",
        "catalogue (test overview in sync)",
    },
    "security.yml": {
        "deps (pip-audit + npm audit)",
        "secret-scan (gitleaks)",
        "codeql (python)",
        "codeql (javascript-typescript)",
    },
}


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _triggers(workflow: dict) -> dict:
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
    assert isinstance(triggers, dict)
    return triggers


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _check_names(job_id: str, job: dict) -> set[str]:
    """The names of the check runs a job reports: its name, once for each matrix value."""
    names = {str(job.get("name", job_id))}
    for key, values in ((job.get("strategy") or {}).get("matrix") or {}).items():
        if isinstance(values, list):
            names = {name.replace(f"${{{{ matrix.{key} }}}}", str(value)) for name in names for value in values}
    return names


# ---- The unit job's coverage gate (OPS-11) ----------------------------------------------------


def _floor_script_constant(name: str) -> float:
    tree = ast.parse((REPO / "scripts" / "check-coverage-floor.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return float(ast.literal_eval(node.value))
    raise AssertionError(f"check-coverage-floor.py sets no {name}")


def test_the_unit_jobs_coverage_gate_is_the_floor_scripts_global_floor() -> None:
    # Both read the total of the same unit run: pytest-cov rounds it to whole percent before
    # comparing, the floor script compares coverage.json's unrounded total. The gate stood at
    # 70 while the script held 74 (OPS-11).
    steps = _workflow("ci.yml")["jobs"]["backend"]["steps"]
    tests = [step for step in steps if "python -m pytest" in step.get("run", "")]
    assert len(tests) == 1, "the backend job runs pytest once"
    gate = re.findall(r"--cov-fail-under[= ](\d+(?:\.\d+)?)", tests[0]["run"])
    assert len(gate) == 1, "the unit run has one coverage gate"
    assert float(gate[0]) == _floor_script_constant("GLOBAL_FLOOR")
    assert "--cov-report=json:coverage.json" in tests[0]["run"]
    floor = [step for step in steps if step.get("run", "").startswith("python scripts/check-coverage-floor.py")]
    assert [step["run"].split()[2:] for step in floor] == [["coverage.json"]]


# ---- The locks are what the compile script writes (OPS-11, TF-13 S-7) ------------------------


def _lock_steps() -> tuple[dict, dict, dict]:
    """The CI job that recompiles the locks, its compile step and the step after it."""
    jobs = _workflow("ci.yml")["jobs"]
    found = [
        (job, index)
        for job in jobs.values()
        for index, step in enumerate(job["steps"])
        if step.get("run", "").strip() == "./scripts/compile-requirements.sh"
    ]
    assert len(found) == 1, "one CI job runs ./scripts/compile-requirements.sh (TF-13 S-7)"
    job, index = found[0]
    return job, job["steps"][index], job["steps"][index + 1]


def test_a_ci_job_recompiles_the_backend_locks_on_every_change() -> None:
    job, compile_step, check = _lock_steps()
    for part in (job, compile_step, check):
        assert "if" not in part and not part.get("continue-on-error"), "the lock check is never skipped"
    for lock in LOCKS:
        assert lock in check["run"]
    # pip-compile builds the metadata of a package published only as source, which runs that
    # package's code with the checkout mounted: the checkout keeps no token there.
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["persist-credentials"] is False
    # The script compiles as the committed locks were compiled: pip-tools 7.4.1 under pip 24.2,
    # on Python 3.12 for linux/amd64 (the runner's own platform), in Docker.
    script = (REPO / "scripts" / "compile-requirements.sh").read_text()
    settings = ('PYTHON_IMAGE="python:3.12"', 'PLATFORM="linux/amd64"', 'PIP_VERSION="24.2"', 'PIP_TOOLS_VERSION="7.4.1"')
    for setting in settings:
        assert setting in script, setting
    header = "# This file is autogenerated by pip-compile with Python 3.12"
    for lock in LOCKS:
        assert (REPO / lock).read_text().splitlines()[1] == header


def _lock_repo(tmp_path: Path) -> Path:
    """A repository holding the committed locks, as the lock job's checkout does."""
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    for lock in LOCKS:
        shutil.copy2(REPO / lock, repo / lock)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "locks")
    return repo


def _first_pin(text: str) -> str:
    match = re.search(r"^[A-Za-z0-9_.\-]+==\S+", text, re.M)
    assert match
    return match.group(0)


def _first_pin_changed(text: str) -> str:
    """The lock with its first package pinned at another version."""
    pin = _first_pin(text)
    return text.replace(pin, pin.split("==")[0] + "==0.0.0", 1)


def _header_command_changed(text: str) -> str:
    """The lock with another option list in its header's pip-compile command line."""
    assert re.search(r"^#    pip-compile .* --strip-extras ", text, re.M)
    return text.replace(" --strip-extras ", " --emit-index-url --strip-extras ", 1)


# Differences between the lock the script writes and the committed one that the check fails
# on: a pin (pip-compile changes it when the .in files no longer allow it, with its hashes),
# a hash, the dependency that pulls a package in, the Python it was compiled for (#894
# compiled with 3.13 and kept the 3.12 line), and a pin beside a changed header command line,
# which alone is only a warning.
EDITS = {
    "a pin": _first_pin_changed,
    "a hash": lambda text: re.sub(r"^    --hash=sha256:[0-9a-f]{64} \\\n", "", text, count=1, flags=re.M),
    "a via line": lambda text: re.sub(r"^    # via .*$", "    # via another-package", text, count=1, flags=re.M),
    "the Python": lambda text: text.replace("with Python 3.12", "with Python 3.13", 1),
    "a pin and the header command": lambda text: _first_pin_changed(_header_command_changed(text)),
}


def test_the_lock_check_passes_the_committed_locks(tmp_path: Path) -> None:
    _, _, check = _lock_steps()
    result = run_step(check, _lock_repo(tmp_path), dict(GIT_ENV))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" not in result.stdout and "::error::" not in result.stdout


@pytest.mark.parametrize("lock", LOCKS)
@pytest.mark.parametrize("edit", sorted(EDITS))
def test_the_lock_check_fails_on_a_lock_the_script_would_not_write(tmp_path: Path, lock: str, edit: str) -> None:
    _, _, check = _lock_steps()
    repo = _lock_repo(tmp_path)
    path = repo / lock
    text = path.read_text()
    edited = EDITS[edit](text)
    assert edited != text
    path.write_text(edited)
    result = run_step(check, repo, dict(GIT_ENV))
    assert result.returncode != 0, f"{edit} in {lock} passed"
    assert "::error::" in result.stdout


def test_the_lock_check_warns_when_only_the_header_command_differs(tmp_path: Path) -> None:
    # The command line in the header lists pip-compile's options as click renders them, and
    # the script leaves click unpinned: two runs of the same pinned pip-tools wrote different
    # lists there (click 8.4.2 writes no `--no-index`; the committed header has it). A
    # difference on that line alone is reported, not failed.
    _, _, check = _lock_steps()
    repo = _lock_repo(tmp_path)
    for lock in LOCKS:
        path = repo / lock
        path.write_text(_header_command_changed(path.read_text()))
    result = run_step(check, repo, dict(GIT_ENV))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" in result.stdout and "::error::" not in result.stdout


# ---- GitHub's merge queue (OPS-22) ------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(REQUIRED_CHECKS))
def test_the_gate_workflows_run_every_job_for_the_merge_queue(name: str) -> None:
    # A queued group merges only once every required check has reported on it; a check that
    # never starts for `merge_group` would hold the queue.
    workflow = _workflow(name)
    triggers = _triggers(workflow)
    assert triggers["merge_group"] == {"types": ["checks_requested"]}
    for event in ("pull_request", "merge_group", "push"):
        assert not {"paths", "paths-ignore"} & set(triggers[event] or {}), f"{event} is filtered by path"
    # Each queued group has a ref of its own, so one group's run never cancels another's.
    assert "${{ github.ref }}" in workflow["concurrency"]["group"]
    reported: set[str] = set()
    for job_id, job in workflow["jobs"].items():
        assert "if" not in job, f"{job_id} is skipped on some events"
        reported |= _check_names(job_id, job)
        text = yaml.safe_dump(job)
        for context in ("github.event.pull_request", "github.head_ref", "github.base_ref"):
            assert context not in text, f"{job_id} reads {context}, which a queued group lacks"
    assert REQUIRED_CHECKS[name] <= reported


def test_the_merge_queue_runs_no_build_deploy_or_release_job() -> None:
    workflow = _workflow("build.yml")
    assert _triggers(workflow)["merge_group"] == {"types": ["checks_requested"]}
    jobs = workflow["jobs"]
    assert "if" not in jobs["tf-validate"] and not _needs(jobs["tf-validate"])
    # prepare holds back every job that builds, pushes, deploys or attaches: it runs for a push
    # to main and a published release, never for a pull request or a queued group.
    assert jobs["prepare"]["if"] == "github.event_name == 'push' || github.event_name == 'release'"
    for job_id, job in jobs.items():
        if job_id not in ("tf-validate", "prepare"):
            assert "prepare" in _needs(job), f"{job_id} would run without prepare"


@pytest.mark.parametrize("ref_name", ["feature/x", "gh-readonly-queue/main/pr-7-0123456789abcdef"])
def test_the_build_metadata_refuses_a_branch_prepare_never_builds(tmp_path: Path, ref_name: str) -> None:
    # prepare runs for a push to main and for a release tag. Any other branch reaching the
    # metadata step stops it, instead of tagging an image after the branch's name.
    repo = init_release_repo(tmp_path / "repo", "1.4.0")
    step = next(s for s in _workflow("build.yml")["jobs"]["prepare"]["steps"] if s.get("id") == "meta")
    outputs, summary = tmp_path / "github_output", tmp_path / "step_summary"
    outputs.write_text("")
    summary.write_text("")
    env = {
        **GIT_ENV,
        "REPO_NAME": "CoGA",
        "REF_NAME": ref_name,
        "REF_TYPE": "branch",
        "GITHUB_OUTPUT": str(outputs),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    result = run_step(step, repo, env)
    assert result.returncode != 0, result.stdout
    assert "::error::" in result.stdout
    written = outputs.read_text()
    assert "tag=" not in written and "app_version=" not in written


# ---- One block of datastore settings (OPS-11) -------------------------------------------------

DATASTORE_JOBS = ("smoke", "e2e", "e2e-playwright")
# What differs between the three jobs, set on the steps that start the app.
JOB_SETTINGS = {
    "smoke": {"SECRET_KEY", "RUN_INTEGRATION", "GCS_ENDPOINT_URL"},
    "e2e": {"SECRET_KEY", "RUN_INTEGRATION"},
    "e2e-playwright": {"SECRET_KEY"},
}


def _starts_the_app(step: dict) -> bool:
    run = step.get("run", "")
    return "python -m pytest" in run or "seed_playwright_e2e.py" in run or "npx playwright test" in run


def test_the_datastore_jobs_share_one_block_of_settings() -> None:
    jobs = _workflow("ci.yml")["jobs"]
    shared = jobs["smoke"]["env"]
    for job_id in DATASTORE_JOBS:
        # A YAML alias: the same mapping, written once.
        assert jobs[job_id]["env"] is shared, f"{job_id} repeats the datastore settings"
        assert jobs[job_id]["services"] is jobs["smoke"]["services"]
    assert shared["APP_ENV"] == "test" and shared["POSTGRES_HOST"] == shared["CLICKHOUSE_HOST"] == "localhost"
    assert not set().union(*JOB_SETTINGS.values()) & set(shared)
    for job_id, settings in JOB_SETTINGS.items():
        starting = [step for step in jobs[job_id]["steps"] if _starts_the_app(step)]
        assert starting, f"{job_id} starts no app"
        for step in starting:
            own = set(step.get("env") or {}) - {"COVERAGE_FILE"}
            assert own == settings, f"{job_id}: {step.get('name')} sets {sorted(own)}"
            assert re.fullmatch(r"ci-[a-z0-9]+-not-a-real-secret", step["env"]["SECRET_KEY"])
        if "RUN_INTEGRATION" in settings:
            assert all(step["env"]["RUN_INTEGRATION"] == "1" for step in starting)


# ---- The coverage job installs coverage.py only (OPS-11) --------------------------------------


def _lock_entry(lock_text: str, package: str) -> str:
    """A package's pin and its hashes, as the lock writes them."""
    match = re.search(
        rf"^{re.escape(package)}==\S+ \\\n(?:    --hash=sha256:[0-9a-f]{{64}} \\\n)*    --hash=sha256:[0-9a-f]{{64}}\n",
        lock_text,
        re.M,
    )
    assert match, f"no {package} in the lock"
    return match.group(0)


def test_the_coverage_job_installs_only_coverage_from_the_lock(tmp_path: Path) -> None:
    job = _workflow("ci.yml")["jobs"]["coverage"]
    installs = [step for step in job["steps"] if "pip install" in step.get("run", "")]
    assert len(installs) == 1
    assert "requirements-dev.txt" in installs[0]["run"] and "pip install -r backend/" not in installs[0]["run"]

    # The install step, run with a pip that records what it is asked to install.
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    shutil.copy2(REPO / "backend" / "requirements-dev.txt", repo / "backend" / "requirements-dev.txt")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    recorded_args, recorded_requirements = tmp_path / "pip-args", tmp_path / "pip-requirements"
    fake_command(
        tmp_path / "bin",
        "pip",
        'printf "%s\\n" "$@" > "$RECORDED_ARGS"\n'
        'while [ $# -gt 0 ]; do\n'
        '  if [ "$1" = "-r" ]; then cat "$2" > "$RECORDED_REQUIREMENTS"; fi\n'
        "  shift\n"
        "done\n",
    )
    env = {
        **GIT_ENV,
        "PATH": f"{tmp_path / 'bin'}:{GIT_ENV['PATH']}",
        "RUNNER_TEMP": str(runner_temp),
        "RECORDED_ARGS": str(recorded_args),
        "RECORDED_REQUIREMENTS": str(recorded_requirements),
    }
    result = run_step(installs[0], repo, env)
    assert result.returncode == 0, result.stdout + result.stderr
    args = recorded_args.read_text().splitlines()
    assert args[0] == "install" and "--require-hashes" in args and "--no-deps" in args
    lock = (REPO / "backend" / "requirements-dev.txt").read_text()
    assert recorded_requirements.read_text() == _lock_entry(lock, "coverage")

    # What the job runs needs nothing else: coverage's own command line, and a floor script
    # that imports only the standard library.
    runs = "\n".join(step.get("run", "") for step in job["steps"] if step is not installs[0])
    assert set(re.findall(r"python (-m \w+|scripts/\S+)", runs)) == {"-m coverage", "scripts/check-coverage-floor.py"}
    imported: set[str] = set()
    for node in ast.walk(ast.parse((REPO / "scripts" / "check-coverage-floor.py").read_text())):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported
