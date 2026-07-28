#!/usr/bin/env bash
#
# Generate CycloneDX Software Bill of Materials (SBOM) for the CoGA backend and
# frontend, from the pinned lockfiles, using pinned generator tools inside
# Docker (matching the CI/deploy runtimes). Outputs to sbom/.
#
#   Usage: ./scripts/generate-sbom.sh
#
# The SBOM is part of the cybersecurity evidence for the in-house IVD technical
# file (TF-13) and underpins vulnerability monitoring of the SOUP register
# (TF-08). Regenerate whenever the locked dependencies change.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM="linux/amd64"
PYTHON_IMAGE="python:3.10"
NODE_IMAGE="node:22"
CYCLONEDX_PY_VERSION="7.3.0"            # pip: cyclonedx-bom
CYCLONEDX_NPM_VERSION="5.0.0"           # npm: @cyclonedx/cyclonedx-npm

mkdir -p "${REPO_ROOT}/sbom"

echo "Generating backend SBOM from backend/requirements.txt ..."
docker run --rm --platform="${PLATFORM}" -v "${REPO_ROOT}:/repo" -w /repo \
  "${PYTHON_IMAGE}" bash -euo pipefail -c "
    pip install --quiet 'cyclonedx-bom==${CYCLONEDX_PY_VERSION}'
    cyclonedx-py requirements backend/requirements.txt --output-format json > sbom/backend.cdx.json
  "

echo "Generating frontend SBOM from frontend/package-lock.json ..."
docker run --rm --platform="${PLATFORM}" -v "${REPO_ROOT}:/repo" -w /repo/frontend \
  "${NODE_IMAGE}" bash -euo pipefail -c "
    npx --yes '@cyclonedx/cyclonedx-npm@${CYCLONEDX_NPM_VERSION}' \
      --package-lock-only --output-format json --output-file /repo/sbom/frontend.cdx.json
  "

echo "Wrote sbom/backend.cdx.json and sbom/frontend.cdx.json (CycloneDX 1.6)."
