# CoGA prerequisites — central-repo PR + rollout checklist

Companion to [`README.md`](README.md) and [`coga-prerequisites.tf.example`](coga-prerequisites.tf.example).
This is the operator runbook for landing the CoGA repo's `deploy/gcp-tf-review-followups`
change (PR #326), which **removes** project-level IAM/KMS/API management from the CoGA
pipeline and moves it here. Do the central-repo side (Parts A–B) **before** merging #326
(Part D). Substitute `PROJECT_ID`, `REGION`, `KMS_KEY`, and the deploy-SA email below.

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

> Provisions everything the CoGA app repo can no longer manage itself after CoGA PR #326
> (which drops project-IAM-admin / SA-admin rights from the CoGA deploy pipeline so it
> cannot self-escalate). Lifted verbatim from CoGA `terraform/main-repo-reference/coga-prerequisites.tf.example`.
>
> Creates on the CoGA runtime project (`PROJECT_ID`):
> - **16 APIs** (compute, run, sqladmin, servicenetworking, vpcaccess, secretmanager,
>   cloudkms, storage, artifactregistry, iam, iamcredentials, logging, monitoring,
>   certificatemanager, cloudresourcemanager, dns).
> - **4 runtime service accounts** — `coga-backend-run`, `coga-frontend-run`,
>   `coga-clickhouse-vm`, and `coga-db-migrate` (the schema-migration job, used once the API
>   runs as the restricted database role).
> - **Least-privilege project IAM**: backend → `cloudsql.client`, `logging.logWriter`,
>   `monitoring.metricWriter`, self `iam.serviceAccountTokenCreator`; frontend →
>   `logging.logWriter`; clickhouse-vm → `logging.logWriter`, `monitoring.metricWriter`;
>   db-migrate → `cloudsql.client`.
> - **CMEK grants**: `cloudkms.cryptoKeyEncrypterDecrypter` on `KMS_KEY` for the Cloud
>   SQL, Compute, and GCS service agents.
> - **PHI audit (S-4)**: project-wide GCS `DATA_READ`/`DATA_WRITE` data-access logging.
>
> **Hand-off contract:** after this applies, the CoGA repo apply (PR #326) references the
> SAs by email and creates only its own resources + resource-level IAM. Merge order is
> enforced by that dependency — apply this first.

**Wire the two variables** (`project_id`, `cmek_key_self_link`) per your landing-zone
convention, and drop the `.example` suffix when you copy the file in.

---

## Part B — ⚠️ state migration (READ FIRST — skipping this can delete the running SAs)

CoGA's **current** Terraform state owns the runtime SAs + KMS grants (they were created by
the now-deleted `iam.tf`/`kms.tf`). If you merge #326 with those resources still in CoGA's
state, CoGA's next `terraform apply` will try to **destroy** them — deleting the Cloud Run
service identities and revoking CMEK access. Decide which path you're on:

```bash
# In the CoGA terraform dir, against the CoGA state:
terraform state list | grep -E 'google_service_account\.|google_kms_crypto_key_iam_member\.|google_project_service\.services'
```

- **Empty output → greenfield** (CoGA was never actually applied to this project). No
  migration: the central PR *creates* the SAs/grants fresh. Proceed to Part C.

- **Non-empty → already deployed.** The SAs/grants exist and are state-owned by CoGA. Do a
  hand-off so nothing is destroyed:
  1. In the **central** repo, `terraform import` each existing object instead of creating a
     duplicate (a plain create fails "already exists"). Example:
     ```bash
     terraform import google_service_account.backend \
       projects/PROJECT_ID/serviceAccounts/coga-backend-run@PROJECT_ID.iam.gserviceaccount.com
     terraform import google_service_account.frontend  projects/PROJECT_ID/serviceAccounts/coga-frontend-run@PROJECT_ID.iam.gserviceaccount.com
     terraform import google_service_account.clickhouse_vm projects/PROJECT_ID/serviceAccounts/coga-clickhouse-vm@PROJECT_ID.iam.gserviceaccount.com
     # project IAM members: import id is "PROJECT_ID roles/<role> serviceAccount:<email>"
     # kms members:        import id is "<KMS_KEY> roles/cloudkms.cryptoKeyEncrypterDecrypter serviceAccount:<agent-email>"
     ```
     `terraform plan` in the central repo must then show **no changes** for those objects.
  2. In the **CoGA** repo, drop them from CoGA's state so #326's apply treats them as gone,
     not to-be-destroyed:
     ```bash
     terraform state rm $(terraform state list | grep -E 'google_service_account\.|google_kms_crypto_key_iam_member\.')
     ```
     (`google_project_service.services` is safe to leave — it has `disable_on_destroy=false`,
     so destroying the resource record does not disable the API. `state rm` it too if you
     want a clean plan.)
  3. Confirm CoGA `terraform plan` (with #326 applied) shows **no destroys** of any
     `google_service_account` / `google_kms_crypto_key_iam_member`.

---

## Part C — apply the central repo & verify

```bash
# central repo
terraform apply    # or apply after the imports in Part B

# --- verify (all should return the expected objects) ---
gcloud services list --enabled --project PROJECT_ID \
  | grep -E 'run|sqladmin|cloudkms|secretmanager|storage|iamcredentials'
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

**Also verify the deploy pipeline can `actAs` the runtime SAs** (not created by the
template — the CoGA Cloud Run apply sets `service_account = <runtime SA>`, and the applying
`DEPLOY_SA` needs `iam.serviceAccounts.actAs`):

```bash
for sa in coga-backend-run coga-frontend-run coga-clickhouse-vm coga-db-migrate; do
  gcloud iam service-accounts get-iam-policy $sa@PROJECT_ID.iam.gserviceaccount.com \
    --format='value(bindings.members)' | grep -q "$DEPLOY_SA" \
    && echo "OK actAs: $sa" || echo "MISSING actAs on $sa — grant it (see below)"
done
```

If missing (and `DEPLOY_SA` doesn't already hold project-wide `roles/iam.serviceAccountUser`),
add to the central prerequisites — one binding per runtime SA:

```hcl
resource "google_service_account_iam_member" "deploy_actas_backend" {
  service_account_id = google_service_account.backend.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deploy_sa_email}"   # add a var for DEPLOY_SA
}
# ...repeat for frontend, clickhouse_vm and db_migrate
```

---

## Part D — merge CoGA #326 & verify the app apply

Merging #326 to `main` triggers `.github/workflows/build.yml` → `terraform apply` for the
CoGA config (now behind the `gcp-deploy` environment gate from #363 — approve it there).

- [ ] Part B migration done (plan shows **no destroys** of SAs/KMS grants).
- [ ] Part C central apply green; all `gcloud` verifications pass; `actAs` present.
- [ ] `cmek_key_self_link` set in CoGA tfvars (now **required** — CMEK is mandatory).
- [ ] Merge #326 → CoGA `terraform plan` in the deploy job shows only expected
      creates/updates and **zero** `google_service_account` / `google_kms_crypto_key_iam_member`
      destroys.
- [ ] Approve the `gcp-deploy` environment; apply succeeds; Cloud Run backend/frontend come
      up healthy under their runtime SAs.
- [ ] (Optional) set `allowed_ingress_cidrs` to the UGent/UZ + VPN ranges and, after
      reviewing WAF-preview hits, `cloud_armor_waf_enforce = true` (closes #364's WAF item).

---

## Part F — go-live switches (#364)

All default off. Each switch is its own change-controlled deployment (TF-18); the order
below keeps each one small. Details in `docs/deployment-gcp.md` §10 and §12.8–12.10.

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

---

## Part E — rollback

- **Central apply fails:** safe to retry; the objects are additive and idle until CoGA
  references them. No CoGA impact (CoGA not yet merged).
- **CoGA apply fails after Part B migration:** the runtime SAs/grants are now owned by the
  central repo and untouched. Fix forward in the CoGA repo; do **not** revert #326 without
  re-importing the SAs back into CoGA state first (a bare revert re-adds `iam.tf`/`kms.tf`
  and CoGA would try to *create* SAs that already exist → "already exists" errors).
- **Full unwind:** revert #326 in CoGA **and** `terraform state rm` the SAs/grants from the
  central repo (leaving them live), so CoGA can re-import/adopt them. Coordinate — only one
  state may own a given SA at a time.
