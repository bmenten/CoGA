"""Run the release steps of the workflows as a runner would, in a throwaway git repository.

For the tests of build.yml's and ci.yml's release mechanics (test_release_mechanics.py,
test_deployment_config.py): a repository on branch ``main`` that holds a VERSION, a frontend
package of that version and this checkout's release scripts, and a runner for a workflow
step's shell script. Import as ``from backend.tests._release_repo import ...``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# The scripts the release steps run.
RELEASE_SCRIPTS = ("check-release-version.sh", "build-version.sh", "check-release-on-main.sh")

# git without user or system configuration, with a fixed identity.
GIT_ENV = {
    "PATH": os.environ["PATH"],
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "CI",
    "GIT_AUTHOR_EMAIL": "ci@example.org",
    "GIT_COMMITTER_NAME": "CI",
    "GIT_COMMITTER_EMAIL": "ci@example.org",
}


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo``; its output, stripped."""
    result = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def set_version(repo: Path, version: str) -> None:
    """Write VERSION, and the frontend package with the same version, as a release commit does.
    npm writes package.json with two-space indentation, which check-release-version.sh reads."""
    (repo / "VERSION").write_text(f"{version}\n")
    package = repo / "frontend" / "package.json"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text(json.dumps({"name": "coga-frontend", "version": version}, indent=2) + "\n")


def init_release_repo(repo: Path, version: str = "1.4.0") -> Path:
    """A repository on branch ``main`` whose first commit holds VERSION, the frontend package of
    that version and this checkout's release scripts."""
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "scripts").mkdir()
    for name in RELEASE_SCRIPTS:
        shutil.copy2(REPO / "scripts" / name, repo / "scripts" / name)
    set_version(repo, version)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"release {version}")
    return repo


def commit(repo: Path, message: str = "change") -> str:
    """Commit what changed in ``repo`` (or nothing) on its current branch; the commit's hash."""
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def fake_command(bin_dir: Path, name: str, body: str) -> Path:
    """A ``name`` on ``bin_dir`` that runs the sh script ``body``, in place of the real one."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def resolve_expressions(script: str, values: dict[str, str]) -> str:
    """``script`` with each ``${{ name }}`` replaced by ``values[name]``, as Actions does before
    the shell runs. A name without a value fails the test."""

    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        assert name in values, f"no value for ${{{{ {name} }}}}"
        return values[name]

    return re.sub(r"\$\{\{\s*([\w.\-]+)\s*\}\}", value, script)


def run_step(
    step: dict, cwd: Path, env: dict[str, str], values: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a workflow step's script in ``cwd`` under the shell the runner gives it: without a
    ``shell`` key ``bash -e`` (no pipefail), with ``shell: bash`` ``bash -eo pipefail``.
    ``values`` stand in for the ``${{ }}`` expressions in the script. Never raises on a
    failing step: the result says how it ended."""
    shell = step.get("shell")
    assert shell in (None, "bash"), f"a step runs under {shell!r}"
    flags = ["-e"] if shell is None else ["--noprofile", "--norc", "-eo", "pipefail"]
    script = resolve_expressions(step["run"], values or {})
    return subprocess.run(["bash", *flags, "-c", script], cwd=cwd, env=env, capture_output=True, text=True)
