# Central-repo prerequisites and first rollout

Companion to [`README.md`](README.md) and
[`coga-prerequisites.tf.example`](coga-prerequisites.tf.example): the operator's checklist
for provisioning CoGA's prerequisites in the central infra repo and doing the first CoGA
apply. The step-by-step deployment itself is in
[docs/deployment-gcp.md](../../docs/deployment-gcp.md). Substitute these values below:

```
PROJECT_ID  = <CoGA runtime project, e.g. the value of the GCP_COGA_PROJECT_ID secret>
REGION      = europe-west1
KMS_KEY     = projects/<kms-project>/locations/europe-west1/keyRings/coga/cryptoKeys/coga
DEPLOY_SA   = coga-<env>-<region_short>-gh-actions@${PROJECT_ID}.iam.gserviceaccount.com   # from build.yml
```

---

## Part A — open the central-repo PR

**Title:** `feat(coga): provision CoGA project prerequisites (SAs, project/KMS IAM, APIs, PHI audit)`

**Body (paste):**

> Provisions what the CoGA app repo deliberately does not manage itself: its deploy
> pipeline holds no project-IAM-admin or service-account-admin rights, so it cannot grant
> itself privileges. Lifted from CoGA `terraform/main-repo-reference/coga-prerequisites.tf.example`.
>
> Creates on the CoGA runtime project (`PROJECT_ID`): the APIs CoGA needs; four runtime
> service accounts (`coga-backend-run`, `coga-frontend-run`, `coga-clickhouse-vm`,
> `coga-db-migrate`) with least-privilege project roles; the CMEK grants on `KMS_KEY` for the
> Cloud SQL, Compute and GCS service agents; and project-wide GCS data-access logging (S-4).
>
> **Hand-off contract:** after this applies, the CoGA apply references the accounts by
> email and creates only its own resources and their resource-level IAM. Apply this first.

**Wire the two variables** (`project_id`, `cmek_key_self_link`) per your landing-zone
convention, and drop the `.example` suffix when you copy the file in.

## Part B — apply the central repo

Run `terraform apply` in the central repo. The objects are additive and stay idle until
CoGA refers to them, so a failed apply is safe to retry.

## Part C — verify, and add the grants the template does not make

```bash
gcloud services list --enabled --project PROJECT_ID \
  | grep -E 'run|sqladmin|cloudkms|secretmanager|storage|iamcredentials|dns'
gcloud iam service-accounts list --project PROJECT_ID \
  | grep -E 'coga-backend-run|coga-frontend-run|coga-clickhouse-vm|coga-db-migrate'
gcloud projects get-iam-policy PROJECT_ID --flatten='bindings[].members' \
  --filter='bindings.members:coga-backend-run@PROJECT_ID.iam.gserviceaccount.com' \
  --format='value(bindings.role)'    # expect cloudsql.client, logging.logWriter, monitoring.metricWriter
gcloud kms keys get-iam-policy KMS_KEY --location REGION --keyring coga \
  --format='value(bindings.members)' | grep -E 'gcp-sa-cloud-sql|compute-system|gs-project-accounts'
gcloud projects get-iam-policy PROJECT_ID --format=json \
  | jq '.auditConfigs[] | select(.service=="storage.googleapis.com")'   # DATA_READ + DATA_WRITE
```

**The deploy pipeline must be able to act as the runtime accounts** (the CoGA apply sets
each Cloud Run service's and job's `service_account`, and `DEPLOY_SA` needs
`iam.serviceAccounts.actAs` on each):

```bash
for sa in coga-backend-run coga-frontend-run coga-clickhouse-vm coga-db-migrate; do
  gcloud iam service-accounts get-iam-policy $sa@PROJECT_ID.iam.gserviceaccount.com \
    --format='value(bindings.members)' | grep -q "$DEPLOY_SA" \
    && echo "OK actAs: $sa" || echo "MISSING actAs on $sa — grant it (see below)"
done
```

If missing (and `DEPLOY_SA` does not already hold project-wide `roles/iam.serviceAccountUser`),
add to the central prerequisites, one binding per runtime account:

```hcl
resource "google_service_account_iam_member" "deploy_actas_backend" {
  service_account_id = google_service_account.backend.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deploy_sa_email}"   # add a var for DEPLOY_SA
}
# ...repeat for frontend, clickhouse_vm and db_migrate
```

Also give `DEPLOY_SA` a Cloud Run role that includes `run.jobs.run` (`roles/run.developer`
or `roles/run.admin`), so the apply can run the migration job. If the images live in
another project (the shared registry), give Cloud Run's service agent,
`service-<project-number>@serverless-robot-prod.iam.gserviceaccount.com`,
`roles/artifactregistry.reader` on the image repository.

## Part D — the first CoGA apply

- [ ] Part C passes: APIs, accounts, roles, key grants, audit config, `actAs` and
      `run.jobs.run` all present.
- [ ] `cmek_key_self_link` set for CoGA (required: CMEK is mandatory), and the secret values
      added ([deployment-gcp.md §5.5](../../docs/deployment-gcp.md)).
- [ ] The deploy job's `terraform plan` shows only creates, and no destroys.
- [ ] Approve the `gcp-deploy` environment; the apply succeeds; the backend and frontend come
      up healthy under their runtime accounts
      ([deployment-gcp.md §9](../../docs/deployment-gcp.md)).

## Part E — go-live switches

All default off. Each switch is its own change-controlled deployment (TF-18); the order
below keeps each one small. Details in `docs/deployment-gcp.md` §10 and §12.7–12.10.

- [ ] **Required reviewers on `gcp-deploy`** (GitHub → Settings → Environments). The deploy
      job refuses to run without at least one.
- [ ] **Institutional networks only:** `allowed_ingress_cidrs` = the ranges IT confirms (§12.9).
- [ ] **WAF enforce:** `cloud_armor_waf_enforce = true` after the preview logs show no false
      positives (§12.7).
- [ ] **Restricted database role:** `coga-db-migrate` account and its `actAs` grant (Part C),
      a version in `coga-postgres-app-password`, then `db_runtime_role = "coga_app"` (§12.8,
      `docs/db-runtime-role-runbook.md`, "Google Cloud").
- [ ] **ClickHouse egress lockdown:** image mirrored to Artifact Registry, VM account
      `roles/artifactregistry.reader`, Cloud DNS API, then `clickhouse_restrict_egress = true`
      and a VM reset (§12.10).
- [ ] **Terraform state secrets:** the owner accepts the residual (owner password and
      ClickHouse TLS keys in the private, versioned, CMEK state bucket) or schedules the
      provider upgrade that keeps them out of state (`terraform/README.md`, residuals).
