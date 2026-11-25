# The container images CoGA runs that none of its own Dockerfiles or compose files names,
# each pinned by tag and digest on a FROM line of its own, so that Dependabot's docker
# ecosystem refreshes them (.github/dependabot.yml, directory /ci). Nothing builds this file.
#
# - scripts/*.sh read their image from here (scripts/pinned-image.sh): a refresh reaches
#   them as it merges.
# - The CI workflow (fake-gcs-server), the Cloud Build configs (the builder), the build
#   workflow (the Terraform version) and the Terraform variables (the metrics collector)
#   name theirs in their own files. backend/tests/test_pinned_images.py fails until each
#   names what this file pins, so a Dependabot pull request shows what to move with it;
#   a workflow file is the owner's to change.
#
# Dependabot proposes a new digest for each tag here. It proposes a new version only for
# fake-gcs-server and Terraform (no major): a new Python, Node, builder or collector line is
# a change of its own.

# compile-requirements.sh, verify-requirements.sh and generate-sbom.sh: the Python the
# backend locks are compiled and checked with (linux/amd64).
FROM python:3.12@sha256:cad37dd20c7cac861af9003af8796d03ea7a998ec1e3a664429740fc9d7d2691 AS python

# generate-sbom.sh: the frontend's SBOM, outside CI (CI runs it --native).
FROM node:22@sha256:e13726ca604184d7a91fcacab1fc09eefd642dc28e79ac34e85540dd93b78cfb AS node

# The GCS emulator of the smoke job (.github/workflows/ci.yml) and of
# backend/tests/integration/test_gcs_storage_integration.py.
FROM fsouza/fake-gcs-server:1.56.1@sha256:797ce226d62f947c009dc40246b30cfb456b8473d8241407f9d6f2c04e4d69ef AS fake-gcs-server

# The builder that ci/cloudbuild.backend.yaml and ci/cloudbuild.frontend.yaml run.
FROM gcr.io/cloud-builders/docker:20.10.24@sha256:2d89b48c704ddcc4a199dd1cc70c21813fb675227600f19cb77e5bbf368012bc AS cloud-build-docker

# The Terraform version .github/workflows/build.yml installs (terraform_version).
FROM hashicorp/terraform:1.17.2@sha256:6e9c1e647c31a960dc9b59e13272f9111d90010c10b717f77ffb324767660475 AS terraform

# The metrics collector beside the backend on Cloud Run (terraform/variables.tf,
# metrics_collector_image).
FROM us-docker.pkg.dev/cloud-ops-agents-artifacts/google-cloud-opentelemetry-collector/otelcol-google:0.165.1@sha256:8cdd5c85128a7f0b73710761487ee747ff9eadc7d7c090d91873aa3fdc1663cd AS metrics-collector
