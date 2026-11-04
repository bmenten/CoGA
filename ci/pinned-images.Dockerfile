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
FROM python:3.12@sha256:17b9724493d916f56565c131f389faff2597f46dec6ce09516f95125bc9f519f AS python

# generate-sbom.sh: the frontend's SBOM, outside CI (CI runs it --native).
FROM node:22@sha256:18a6d90fd60542269850c80ca03b29b1d9b14d6b221ac7ee8e35b0d33ef904bd AS node

# The GCS emulator of the smoke job (.github/workflows/ci.yml) and of
# backend/tests/integration/test_gcs_storage_integration.py.
FROM fsouza/fake-gcs-server:1.56.1@sha256:797ce226d62f947c009dc40246b30cfb456b8473d8241407f9d6f2c04e4d69ef AS fake-gcs-server

# The builder that ci/cloudbuild.backend.yaml and ci/cloudbuild.frontend.yaml run.
FROM gcr.io/cloud-builders/docker:20.10.24@sha256:fbc05705e6f24467e40ae8a4097ab9b8fae1116551f838fa2c55f4367427fc36 AS cloud-build-docker

# The Terraform version .github/workflows/build.yml installs (terraform_version).
FROM hashicorp/terraform:1.16.6@sha256:bfffc1ded5fbf0a17beaaf22eb4793980abdb19d0761ff6a3a0efb2ab4d9b316 AS terraform

# The metrics collector beside the backend on Cloud Run (terraform/variables.tf,
# metrics_collector_image).
FROM us-docker.pkg.dev/cloud-ops-agents-artifacts/google-cloud-opentelemetry-collector/otelcol-google:0.160.0@sha256:2bebb885ef33c00e1d999dc0b0dd13b089f8ddb1734b62c0a0524b2cf20c515e AS metrics-collector
