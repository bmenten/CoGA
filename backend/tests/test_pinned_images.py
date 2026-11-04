"""What Dependabot keeps current outside the Dockerfiles and compose files (OPS-12).

``ci/pinned-images.Dockerfile`` pins, one FROM line each, the images no Dockerfile or
compose file of CoGA's names: the scripts' Python and Node, the CI's fake-gcs-server, the
Cloud Build builder, the Terraform version the build workflow installs and the metrics
collector Terraform deploys. ``ci/requirements-*.txt`` pin the Python tools the workflows
install by name. Dependabot's docker and pip ecosystems read both (``.github/dependabot.yml``,
directory ``/ci``), and its terraform ecosystem the providers.

The scripts read their image from the file, so a refresh reaches them as it merges. Every
other place names its pin in its own words: these tests fail until it names what the file
pins, so a Dependabot pull request shows what has to move with it.
"""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from backend.tests._pinned_images import PINNED_IMAGES, pinned_image, pinned_images
from backend.tests._release_repo import REPO

WORKFLOWS = REPO / ".github" / "workflows"
HELPER = REPO / "scripts" / "pinned-image.sh"
OWNER = "a workflow file: the owner changes it"


def _resolve(tag: str) -> subprocess.CompletedProcess[str]:
    """``pinned_image <tag>`` as a script runs it, from scripts/pinned-image.sh."""
    return subprocess.run(
        ["bash", "-c", 'source "$1" && pinned_image "$2"', "pinned-image", str(HELPER), tag],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_file_pins_each_image_by_tag_and_digest() -> None:
    assert set(pinned_images()) == {
        "python",
        "node",
        "fake-gcs-server",
        "cloud-build-docker",
        "terraform",
        "metrics-collector",
    }
    for image in pinned_images().values():
        name_and_tag = image.split("@")[0]
        assert ":" in name_and_tag.rsplit("/", 1)[-1], f"{image} has no tag for Dependabot to read"


@pytest.mark.parametrize("script", ["compile-requirements.sh", "verify-requirements.sh", "generate-sbom.sh"])
def test_the_scripts_run_their_images_at_the_pinned_digest(script: str) -> None:
    text = (REPO / "scripts" / script).read_text()
    tags = dict(re.findall(r'^([A-Z]+_IMAGE)="([^"$@]+)"$', text, re.M))
    assert tags, f"{script} names no image"
    for variable, tag in tags.items():
        # The tag the script runs, then that tag at the digest the file pins.
        assert f'{variable}="$(pinned_image "${{{variable}}}")"' in text, f"{script} runs {tag} by its tag alone"
        resolved = _resolve(tag)
        assert resolved.returncode == 0, resolved.stderr
        assert resolved.stdout.strip() in pinned_images().values()
        assert resolved.stdout.strip().startswith(f"{tag}@sha256:")
    # Every image the script runs is one of those variables.
    runs = re.findall(r"docker run(?:[^\n]*\\\n)*[^\n]*", text)
    assert runs, f"{script} runs no image"
    for run in runs:
        images = re.findall(r'"\$\{([A-Z]+_IMAGE)\}"', run)
        assert images and set(images) <= set(tags), run


def test_a_tag_the_file_does_not_pin_stops_the_script() -> None:
    resolved = _resolve("python:3.99")
    assert resolved.returncode != 0
    assert resolved.stdout == ""
    assert "pins no python:3.99@sha256:<digest>" in resolved.stderr


def test_ci_starts_the_pinned_fake_gcs_server() -> None:
    started = re.findall(r"docker run[^\n]*?(fsouza/fake-gcs-server\S*)", (WORKFLOWS / "ci.yml").read_text())
    assert started, "ci.yml starts no fake-gcs-server"
    assert set(started) == {pinned_image("fake-gcs-server")}, f".github/workflows/ci.yml, {OWNER}"


@pytest.mark.parametrize("name", ["backend", "frontend"])
def test_cloud_build_runs_the_pinned_builder(name: str) -> None:
    config = yaml.safe_load((REPO / "ci" / f"cloudbuild.{name}.yaml").read_text())
    assert [step["name"] for step in config["steps"]] == [pinned_image("cloud-build-docker")]


def test_the_build_workflow_installs_the_pinned_terraform() -> None:
    versions = re.findall(r"terraform_version:\s*(\S+)", (WORKFLOWS / "build.yml").read_text())
    assert versions, "build.yml installs no pinned Terraform"
    pinned = re.fullmatch(r"hashicorp/terraform:([^@]+)@sha256:[0-9a-f]{64}", pinned_image("terraform"))
    assert pinned
    assert set(versions) == {pinned.group(1)}, f".github/workflows/build.yml, {OWNER}"


def test_terraform_deploys_the_pinned_metrics_collector() -> None:
    variables = (REPO / "terraform" / "variables.tf").read_text()
    default = re.search(r'variable "metrics_collector_image"\s*\{[^}]*?default\s*=\s*"([^"]+)"', variables)
    assert default, "no metrics_collector_image default in variables.tf"
    assert default.group(1) == pinned_image("metrics-collector")


@pytest.mark.parametrize(
    ("requirements", "workflow"),
    [("requirements-pip-audit.txt", "security.yml"), ("requirements-handleiding.txt", "ci.yml")],
)
def test_the_workflows_install_the_pinned_python_tools(requirements: str, workflow: str) -> None:
    pins = re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)$", (REPO / "ci" / requirements).read_text(), re.M)
    assert len(pins) == 1, f"ci/{requirements} pins one tool"
    ((package, version),) = pins
    installed = re.findall(rf"pip install {re.escape(package)}==(\S+)", (WORKFLOWS / workflow).read_text())
    assert installed, f"{workflow} does not install {package}"
    assert set(installed) == {version}, f".github/workflows/{workflow}, {OWNER}"


def test_dependabot_reads_the_pins() -> None:
    updates = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text())["updates"]
    read = {(update["package-ecosystem"], update.get("directory")) for update in updates}
    assert {("docker", "/ci"), ("pip", "/ci"), ("terraform", "/terraform")} <= read
    # The file Dependabot's docker ecosystem reads in /ci: a name with "dockerfile" in it.
    assert re.search("dockerfile", PINNED_IMAGES.name, re.I)
    (docker,) = [u for u in updates if (u["package-ecosystem"], u.get("directory")) == ("docker", "/ci")]
    ignored_versions = {
        rule["dependency-name"]: set(rule["update-types"]) for rule in docker["ignore"] if rule["dependency-name"] != "*"
    }
    # fake-gcs-server and Terraform also take minor and patch versions; the rest digests only.
    for stage in ("fake-gcs-server", "terraform"):
        assert pinned_image(stage).split(":")[0] not in ignored_versions
    for stage in ("python", "node", "cloud-build-docker", "metrics-collector"):
        name = pinned_image(stage).split(":")[0]
        assert ignored_versions.get(name) == {"version-update:semver-minor", "version-update:semver-patch"}, stage
