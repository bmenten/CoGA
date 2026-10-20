#!/usr/bin/env bash
# The on-demand Cloud SQL backup a deploy takes before each schema migration it starts: the
# restore point if that migration has to be undone (docs/deployment-gcp.md, 12.3). Terraform
# runs it on the machine that applies (migrate.tf), with gcloud and that machine's credentials:
# in CI the deploy pipeline's account, which needs cloudsql.backupRuns.create and
# cloudsql.instances.get (terraform/main-repo-reference/coga-prerequisites.tf.example).
#
# It exits non-zero, which stops the apply before the migration runs, when gcloud cannot start
# the backup, the backup fails, or it is not done within an hour.
#
# Environment: INSTANCE (the Cloud SQL instance), PROJECT (its project) and IMAGE (the backend
# image whose migration follows, which the backup's description names).
set -euo pipefail

: "${INSTANCE:?the Cloud SQL instance to back up}"
: "${PROJECT:?the project of the Cloud SQL instance}"
: "${IMAGE:?the backend image whose migration follows}"

# A backup still running after this many seconds fails the apply (gcloud's default is 300).
timeout=3600

fail() {
  echo "The backup of $INSTANCE before the schema migration failed: $1. The migration does not run." >&2
  exit 1
}

# The image's last path element, as the deploy names it: coga-backend@sha256:<digest>.
description="Before the schema migration of ${IMAGE##*/}"

operation=$(gcloud sql backups create --instance="$INSTANCE" --project="$PROJECT" \
  --description="$description" --async --format='value(name)') || fail "gcloud could not start it"
if [ -z "$operation" ]; then
  fail "gcloud named no operation for it"
fi
echo "Backing up $INSTANCE before the schema migration (operation $operation)."

# Exits non-zero when the backup ends with an error or is still running at the timeout.
gcloud sql operations wait "$operation" --project="$PROJECT" --timeout="$timeout" ||
  fail "it ended with an error, or was not done within ${timeout}s (operation $operation)"

# What the operation reports, rather than gcloud's exit status alone.
status=$(gcloud sql operations describe "$operation" --project="$PROJECT" --format='value(status)') ||
  fail "gcloud could not read operation $operation"
error=$(gcloud sql operations describe "$operation" --project="$PROJECT" --format='value(error.errors[0].code)') ||
  fail "gcloud could not read operation $operation"
if [ "$status" != "DONE" ] || [ -n "$error" ]; then
  fail "operation $operation ended ${status:-in no known state}${error:+ with error $error}"
fi

echo "Backed up $INSTANCE: \"$description\" (operation $operation)."
