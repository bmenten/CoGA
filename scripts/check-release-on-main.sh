#!/usr/bin/env bash
#
# Assert that a release tag names a commit on main.
#
# Why this exists: a release build stamps the tag's version into the image, and every report
# that build signs freezes it (TF-18 section 2). A tag on a commit that never reached main (a
# branch that was not merged, a commit a rebase replaced) would release code that passed none
# of main's required checks and that QA's four-eyes review of the pull requests merged into
# main never saw (TF-18 section 6). build.yml runs this before it builds a release; run it
# yourself after tagging, before you publish the release.
#
# It needs main's whole history: in a shallow clone the answer could be wrong either way, so
# it refuses to give one.
#
#   Usage:
#     ./scripts/check-release-on-main.sh v0.1.0-beta.1               # against origin/main
#     ./scripts/check-release-on-main.sh v0.1.0-beta.1 <main-ref>    # against another ref
#   Fetch main first (git fetch origin main), so origin/main is current.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ $# -lt 1 ] || [ $# -gt 2 ] || [ -z "$1" ]; then
  echo "Usage: $0 v<VERSION> [main-ref]" >&2
  exit 2
fi

tag="$1"
main_ref="${2:-origin/main}"

if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
  echo "❌ This clone is shallow, so whether ${tag} is on main cannot be told."
  echo
  echo "   The check needs main's whole history: run 'git fetch --unshallow origin'"
  echo "   (in a workflow, check out with fetch-depth: 0) and run it again."
  exit 1
fi

if ! commit="$(git rev-parse --verify --quiet "refs/tags/${tag}^{commit}")"; then
  echo "❌ There is no tag ${tag} here. Fetch the tags (git fetch --tags origin) and run this again."
  exit 1
fi

if ! main="$(git rev-parse --verify --quiet "${main_ref}^{commit}")"; then
  echo "❌ ${main_ref} is not known here. Fetch it (git fetch origin main) and run this again."
  exit 1
fi

set +e
git merge-base --is-ancestor "$commit" "$main"
status=$?
set -e

case "$status" in
  0)
    echo "✓ Tag ${tag} (commit ${commit:0:12}) is on ${main_ref}."
    ;;
  1)
    echo "❌ Release tag ${tag} is not on main."
    echo
    echo "     tag ${tag} names commit : ${commit}"
    echo "     ${main_ref} is at        : ${main}"
    echo
    echo "   A release is built only from a commit that is on main: one that passed main's"
    echo "   required checks and that QA's four-eyes review covers (TF-18 section 6). This"
    echo "   commit is not in main's history."
    echo
    echo "   To fix: delete the release and the tag (gh release delete ${tag} --cleanup-tag),"
    echo "   tag the release commit on main, and publish the release again (RELEASING.md)."
    exit 1
    ;;
  *)
    echo "❌ Could not tell whether ${tag} is on ${main_ref} (git merge-base exited ${status})."
    exit 1
    ;;
esac
