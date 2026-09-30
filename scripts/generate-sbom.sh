#!/usr/bin/env bash
#
# Generate CycloneDX Software Bill of Materials (SBOM) for the CoGA backend and
# frontend, from the pinned lockfiles, using pinned generator tools inside
# Docker (matching the CI/deploy runtimes). Outputs to sbom/.
#
#   Usage: ./scripts/generate-sbom.sh            # in Docker (python:3.12, node:22)
#          ./scripts/generate-sbom.sh --native   # with the Python and Node on PATH (CI)
#
# The SBOM is part of the cybersecurity evidence for the in-house IVD technical
# file (TF-13) and underpins vulnerability monitoring of the SOUP register
# (TF-08). Regenerate whenever the locked dependencies change. CI runs this script
# with --native, so the generator pins below are the only ones.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM="linux/amd64"
PYTHON_IMAGE="python:3.12"
NODE_IMAGE="node:22"
CYCLONEDX_PY_VERSION="7.3.0"            # pip: cyclonedx-bom
CYCLONEDX_NPM_VERSION="5.0.0"           # npm: @cyclonedx/cyclonedx-npm

# Run from the repository root.
BACKEND_CMD="pip install --quiet 'cyclonedx-bom==${CYCLONEDX_PY_VERSION}'
cyclonedx-py requirements backend/requirements.txt --output-format json > sbom/backend.cdx.json"
# Run from frontend/. --ignore-npm-errors: Tailwind v4 pulls in the optional package
# @tailwindcss/oxide-wasm32-wasi, whose transitive deps npm omits from the lockfile on
# platforms that don't use the wasm binary. `npm ls` (run internally by cyclonedx-npm)
# then exits non-zero on those "missing" optional sub-deps; the flag tolerates that while
# still emitting a complete SBOM for everything that is installed.
FRONTEND_CMD="npx --yes '@cyclonedx/cyclonedx-npm@${CYCLONEDX_NPM_VERSION}' \
  --package-lock-only --output-format json --output-reproducible --ignore-npm-errors \
  --output-file ../sbom/frontend.cdx.json"

mkdir -p "${REPO_ROOT}/sbom"

if [ "${1:-}" = "--native" ]; then
  echo "Generating backend SBOM from backend/requirements.txt ..."
  (cd "${REPO_ROOT}" && bash -euo pipefail -c "${BACKEND_CMD}")
  echo "Generating frontend SBOM from frontend/package-lock.json ..."
  (cd "${REPO_ROOT}/frontend" && bash -euo pipefail -c "${FRONTEND_CMD}")
else
  echo "Generating backend SBOM from backend/requirements.txt ..."
  docker run --rm --platform="${PLATFORM}" -v "${REPO_ROOT}:/repo" -w /repo \
    "${PYTHON_IMAGE}" bash -euo pipefail -c "${BACKEND_CMD}"
  echo "Generating frontend SBOM from frontend/package-lock.json ..."
  docker run --rm --platform="${PLATFORM}" -v "${REPO_ROOT}:/repo" -w /repo/frontend \
    "${NODE_IMAGE}" bash -euo pipefail -c "${FRONTEND_CMD}"
fi

echo "Wrote sbom/backend.cdx.json and sbom/frontend.cdx.json (CycloneDX 1.6)."
