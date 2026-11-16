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
FROM node:22@sha256:18a6d90fd60542269850c80ca03b29b1d9b14d6b221ac7ee8e35b0d33ef904bd AS node

# The GCS emulator of the smoke job (.github/workflows/ci.yml) and of
# backend/tests/integration/test_gcs_storage_integration.py.
FROM fsouza/fake-gcs-server:1.56.1@sha256:797ce226d62f947c009dc40246b30cfb456b8473d8241407f9d6f2c04e4d69ef AS fake-gcs-server

# The builder that ci/cloudbuild.backend.yaml and ci/cloudbuild.frontend.yaml run.
FROM gcr.io/cloud-builders/docker:20.10.24@sha256:91eaff0806a70dc0b3c853191a2da9995b2aa88d9b24196182344a4cef633603 AS cloud-build-docker

# The Terraform version .github/workflows/build.yml installs (terraform_version).
FROM hashicorp/terraform:1.17.1@sha256:a5d5a5a892c98f60fcbfa81eac2a40fc19e7b3fa7719bccae10ff6111d22524a AS terraform

# The metrics collector beside the backend on Cloud Run (terraform/variables.tf,
# metrics_collector_image).
FROM us-docker.pkg.dev/cloud-ops-agents-artifacts/google-cloud-opentelemetry-collector/otelcol-google:0.164.0@sha256:871b9e716d8749660c0e5fd7e7fd5a21b04d412919ab86792528ee4cfa258f89 AS metrics-collector
