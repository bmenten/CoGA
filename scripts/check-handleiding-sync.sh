#!/usr/bin/env bash
#
# Assert that docs/handleiding/coga-handleiding.html is the current build of the
# handleiding Markdown, so the page the review board reads can never drift from the
# chapters under review.
#
# The HTML is generated, but it is committed (it is the deliverable operators open in a
# browser), so nothing stops a Markdown edit from landing without a regeneration — which
# is exactly what happened in #387, where a heading changed and the page kept the old
# one until #389 happened to rebuild it.
#
# Method: regenerate in place and fail if that changes the file. build_site.py embeds no
# timestamps and is idempotent, so a clean tree means in sync, and any diff is real
# drift. On failure the regenerated file is LEFT IN PLACE — the fix is to commit it.
#
#   Usage: ./scripts/check-handleiding-sync.sh
#   Needs: python3 with the `markdown` package (pip install markdown)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
GENERATOR="docs/handleiding/build_site.py"
TARGET="docs/handleiding/coga-handleiding.html"
PYTHON="${PYTHON:-python3}"

# A pre-existing local edit to the HTML would otherwise be reported as drift caused by
# this check. Fail early with a clear cause instead of a confusing diff.
if ! git diff --quiet -- "$TARGET"; then
  echo "❌ $TARGET already has uncommitted changes before regeneration."
  echo "   Commit or discard them first, then re-run this check."
  exit 1
fi

if ! "$PYTHON" -c 'import markdown' 2>/dev/null; then
  echo "❌ The 'markdown' package is required to rebuild the handleiding."
  echo "   Install it with: pip install markdown"
  exit 1
fi

"$PYTHON" "$GENERATOR" >/dev/null

if git diff --quiet -- "$TARGET"; then
  echo "✓ $TARGET is in sync with the handleiding Markdown."
  exit 0
fi

echo "❌ $TARGET is out of date with the handleiding Markdown."
echo
echo "   A chapter changed without regenerating the page. The rebuilt file has been"
echo "   written for you — review and commit it:"
echo
echo "     python $GENERATOR"
echo "     git add $TARGET"
echo
echo "   Drift (regenerated vs committed):"
git --no-pager diff --stat -- "$TARGET" | sed 's/^/     /'
exit 1
