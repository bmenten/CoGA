#!/usr/bin/env bash
#
# Verify the compiled, hash-locked backend requirements install cleanly in a
# fresh Python 3.12 environment (the deployed/CI runtime), enforcing hashes.
# Run this after ./scripts/compile-requirements.sh and before committing a lock
# change. Exits non-zero if the lock cannot be installed as written.
#
#   Usage: ./scripts/verify-requirements.sh [requirements-dev.txt]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_IMAGE="python:3.12"
PLATFORM="linux/amd64"  # match CI/prod arch (see compile-requirements.sh)
REQ_FILE="${1:-requirements-dev.txt}"

docker run --rm --platform="${PLATFORM}" \
  -v "${REPO_ROOT}:/repo:ro" -w /repo/backend \
  "${PYTHON_IMAGE}" bash -euo pipefail -c "
    pip install --quiet --upgrade 'pip==24.2'
    # --require-hashes is auto-enabled because every entry is hashed; assert it.
    pip install --require-hashes --dry-run -r '${REQ_FILE}'
    echo 'OK: ${REQ_FILE} resolves and all hashes verify in Python 3.12.'
  "
