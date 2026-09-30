#!/usr/bin/env bash
#
# Assert that docs/testing.md catalogues exactly the test files in the tree —
# no uncatalogued test files, and no stale rows pointing at deleted tests — so
# the Test Overview never silently drifts. Runs in CI and locally (git + grep
# only; no Python/Node needed).
#
#   Usage: ./scripts/check-test-catalogue.sh
#
# Test-file conventions matched:
#   backend  — backend/tests/…/test_*.py (the only pytest testpath; a test file under a
#              top-level tests/ folder is reported, since pytest would never run it)
#   frontend — frontend/src/**/*.test.ts(x), and the Playwright journeys in frontend/e2e/*.spec.ts
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DOC="docs/testing.md"
PATTERN='^(backend/tests|tests)/.*test_.*\.py$|^frontend/src/.*\.test\.tsx?$|^frontend/e2e/.*\.spec\.ts$'

tree_files="$(mktemp)"
doc_files="$(mktemp)"
trap 'rm -f "$tree_files" "$doc_files"' EXIT

# Authoritative: test files tracked in the tree.
git ls-files | grep -E "$PATTERN" | sort -u > "$tree_files"

# Catalogued: test-file links in the doc (links are ../<path> relative to docs/).
grep -oE '\]\(\.\./[^)]+\)' "$DOC" \
  | sed -E 's/^\]\(\.\.\///; s/\)$//' \
  | grep -E "$PATTERN" | sort -u > "$doc_files"

missing="$(comm -23 "$tree_files" "$doc_files")"
stale="$(comm -13 "$tree_files" "$doc_files")"
# pytest collects backend tests from backend/tests only (pyproject.toml testpaths, #530).
stray="$(grep -E '^tests/' "$tree_files" || true)"
status=0

if [ -n "$missing" ]; then
  echo "❌ Test files NOT catalogued in $DOC — add a row for each:"
  printf '%s\n' "$missing" | sed 's/^/   - /'
  status=1
fi
if [ -n "$stale" ]; then
  echo "❌ $DOC references test files that no longer exist — remove/rename the row:"
  printf '%s\n' "$stale" | sed 's/^/   - /'
  status=1
fi
if [ -n "$stray" ]; then
  echo "❌ Test files outside backend/tests are never collected by pytest — move them under backend/tests/:"
  printf '%s\n' "$stray" | sed 's/^/   - /'
  status=1
fi
if [ "$status" -eq 0 ]; then
  echo "✓ $DOC is in sync with the test tree ($(wc -l < "$tree_files" | tr -d ' ') test files)."
fi
exit "$status"
