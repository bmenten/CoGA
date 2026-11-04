# shellcheck shell=bash
#
# Sourced by the scripts that run a container image. `pinned_image <name:tag>` prints that
# image at the digest ci/pinned-images.Dockerfile pins for it (<name:tag>@sha256:<digest>),
# where Dependabot refreshes the digest (.github/dependabot.yml, directory /ci). A script
# keeps the tag it runs, and runs it at the digest pinned there:
#
#   source "${REPO_ROOT}/scripts/pinned-image.sh"
#   PYTHON_IMAGE="python:3.12"
#   PYTHON_IMAGE="$(pinned_image "${PYTHON_IMAGE}")"
#
# A tag the file does not pin fails, so a script never runs an image by its tag alone.

pinned_image() {
  local tag="$1"
  local file
  file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/ci/pinned-images.Dockerfile"
  local image
  image="$(awk -v tag="${tag}" '
    $1 == "FROM" {
      at = index($2, "@")
      if (at > 0 && substr($2, 1, at - 1) == tag && substr($2, at + 1) ~ /^sha256:[0-9a-f]+$/ && length($2) - at == 71) {
        print $2
        exit
      }
    }' "${file}")"
  if [ -z "${image}" ]; then
    echo "ci/pinned-images.Dockerfile pins no ${tag}@sha256:<digest>" >&2
    return 1
  fi
  printf '%s\n' "${image}"
}
