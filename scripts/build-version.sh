#!/usr/bin/env bash
#
# Print the version a build stamps into the backend image (its APP_VERSION build argument),
# which /api/version, the app and report footers and every signed report then name
# (TF-18 section 2).
#
# Only a release build reports a release's version: build.yml building a published release
# runs `--release <tag>`, which checks that the tag is `v<VERSION>` and prints VERSION as it
# is. Every other build (a push to main, the CI image check, a manual or local build) prints
# VERSION with the SemVer pre-release identifier `dev` added:
#
#     VERSION 0.1.0        ->  0.1.0-dev
#     VERSION 0.1.0-rc.1   ->  0.1.0-rc.1.dev
#
# A build of main after the release candidate's tag therefore reports 0.1.0-rc.1.dev, never
# 0.1.0-rc.1, and SemVer orders it after rc.1 and before rc.2, where it belongs. The commit
# is not repeated in the version: every place that shows the version shows the commit beside
# it. check-release-version.sh refuses a VERSION that carries the identifier `dev` or build
# metadata, so no release can carry a version a development build reports. The backend
# applies the same rule to an image built without APP_VERSION (app/core/config.py).
#
#   Usage:
#     ./scripts/build-version.sh                         # any build that is not a release
#     ./scripts/build-version.sh --release v0.1.0-rc.1   # the release build of that tag
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  echo "Usage: $0 [--release v<VERSION>]" >&2
  exit 2
}

# The checks report on stdout; this script's stdout is the version alone.
if [ "${1:-}" = "--release" ]; then
  [ $# -eq 2 ] && [ -n "$2" ] || usage
  ./scripts/check-release-version.sh "$2" >&2
  tr -d '[:space:]' < VERSION
  echo
  exit 0
fi

[ $# -eq 0 ] || usage
./scripts/check-release-version.sh >&2
version="$(tr -d '[:space:]' < VERSION)"
case "$version" in
  *-*) echo "${version}.dev" ;;  # a pre-release: add the identifier to it
  *) echo "${version}-dev" ;;
esac
