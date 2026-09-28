#!/usr/bin/env bash
#
# Regenerate the fully-pinned, hash-locked backend requirements from the
# human-edited *.in files, using Python 3.12 (the deployed/CI runtime) inside
# Docker so the lock is faithful regardless of the host's Python version.
#
#   Usage: ./scripts/compile-requirements.sh
#
# Inputs : backend/requirements.in, backend/requirements-dev.in
# Outputs: backend/requirements.txt, backend/requirements-dev.txt (compiled,
#          pinned, --generate-hashes). Do not edit the outputs by hand.
#
# Rationale: CoGA is an in-house IVD (IVDR Art. 5(5)); a clinical build must be
# byte-for-byte reproducible and supply-chain-verifiable (technical file TF-08,
# TF-13, TF-18).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_IMAGE="python:3.12"
# Resolve on the deploy/CI architecture (linux/amd64) so the committed lock is
# faithful to where it runs, not to the developer's host arch. On Apple Silicon
# this runs under emulation (slower, but the canonical lock must be amd64).
PLATFORM="linux/amd64"
PIP_VERSION="24.2"
PIP_TOOLS_VERSION="7.4.1"

# --strip-extras: expand extras to concrete deps (default in newer pip-tools).
# --allow-unsafe: pin build deps (setuptools/pip) too, for full reproducibility.
COMPILE_FLAGS=(--quiet --generate-hashes --strip-extras --allow-unsafe --resolver=backtracking)

docker run --rm --platform="${PLATFORM}" \
  -v "${REPO_ROOT}:/repo" -w /repo/backend \
  "${PYTHON_IMAGE}" bash -euo pipefail -c "
    pip install --quiet --upgrade 'pip==${PIP_VERSION}' 'pip-tools==${PIP_TOOLS_VERSION}'
    pip-compile ${COMPILE_FLAGS[*]} --output-file requirements.txt requirements.in
    pip-compile ${COMPILE_FLAGS[*]} --output-file requirements-dev.txt requirements-dev.in
  "

echo "Regenerated backend/requirements.txt and backend/requirements-dev.txt."
echo "Review the diff, then verify a clean install: ./scripts/verify-requirements.sh"
