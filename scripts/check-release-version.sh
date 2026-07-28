#!/usr/bin/env bash
#
# Assert that VERSION is a well-formed semantic version, and — when a tag ref is given —
# that the tag being released is exactly `v<VERSION>`.
#
# Why this exists: the image build stamps `APP_VERSION=$(cat VERSION)` (build.yml), and that
# value is what ends up in the signed clinical report footer and in `/api/version`. Nothing
# previously tied it to the tag, so tagging `v0.1.0-beta.1` while VERSION still read `0.1.0`
# would have produced a build labelled `0.1.0` — indistinguishable from every other build cut
# from main. For a device where the report footer is the record of which software produced a
# result (TF-18 §2), that is a traceability defect, not a cosmetic one.
#
# VERSION stays the single source of truth; the tag mirrors it. The release commit bumps
# VERSION, and the tag is then `v` + that value.
#
#   Usage:
#     ./scripts/check-release-version.sh                 # validate VERSION format only
#     ./scripts/check-release-version.sh v0.1.0-beta.1   # also assert the tag matches
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -f VERSION ]; then
  echo "❌ VERSION file not found at the repository root."
  exit 1
fi

version="$(tr -d '[:space:]' < VERSION)"

if [ -z "$version" ]; then
  echo "❌ VERSION is empty."
  exit 1
fi

# Semantic Versioning 2.0.0, transcribed to POSIX ERE (no non-capturing groups in grep -E).
# CHANGELOG.md states the project follows SemVer, so a malformed value is a real defect —
# it would otherwise flow straight into the report footer.
SEMVER='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-(0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)(\.(0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*)?(\+([0-9a-zA-Z-]+(\.[0-9a-zA-Z-]+)*))?$'

if ! printf '%s' "$version" | grep -Eq "$SEMVER"; then
  echo "❌ VERSION is not a valid semantic version: '${version}'"
  echo "   Expected MAJOR.MINOR.PATCH with an optional pre-release, e.g. 0.1.0 or 0.1.0-beta.1"
  exit 1
fi

# No tag to check against — format validation only (useful before cutting a release).
if [ $# -eq 0 ] || [ -z "${1:-}" ]; then
  echo "✓ VERSION is a valid semantic version (${version})."
  exit 0
fi

tag="$1"
expected="v${version}"

if [ "$tag" != "$expected" ]; then
  echo "❌ Tag does not match VERSION."
  echo
  echo "     tag being built : ${tag}"
  echo "     VERSION file    : ${version}"
  echo "     expected tag    : ${expected}"
  echo
  echo "   The release commit must bump VERSION first, then be tagged 'v<VERSION>'."
  echo "   Otherwise the built image — and the report footer it stamps — carries a version"
  echo "   that does not identify this release (TF-18 section 2)."
  echo
  echo "   To fix: set VERSION to '${tag#v}', commit, then re-tag that commit as '${tag}'."
  exit 1
fi

echo "✓ Tag ${tag} matches VERSION ${version}."
