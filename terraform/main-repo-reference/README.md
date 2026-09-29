# Central-infra-repo prerequisites for CoGA

`coga-prerequisites.tf.example` is a ready-to-lift template. Copy it into the central
repo, drop the `.example` suffix, wire the two variables (`project_id`,
`cmek_key_self_link`), and apply it **before** the CoGA repo's `terraform apply`. The
CoGA pipeline holds no project-IAM-admin or service-account-admin rights, so everything
that needs them lives here.

**Operator runbook:** [`rollout-checklist.md`](rollout-checklist.md): the central-repo PR,
the `gcloud` verifications, the grants the template does not make, the first CoGA apply and
the go-live switches.

## What must exist before CoGA applies

1. **Enabled APIs** on the CoGA project (the `google_project_service` block).
2. **Four runtime service accounts** with these exact account ids, so the emails the CoGA
   configuration derives by default line up:
   - `coga-backend-run`
   - `coga-frontend-run`
   - `coga-clickhouse-vm`
   - `coga-db-migrate` (the schema-migration job, used with the restricted database role)

   If you use different names, pass the emails to the CoGA configuration through
   `backend_service_account_email`, `frontend_service_account_email`,
   `clickhouse_vm_service_account_email` and `db_migrate_service_account_email`.
3. **Project-level role grants** to those accounts (least privilege; see the template).
4. **CMEK key grants**: the Cloud SQL, Compute and Cloud Storage service agents each need
   `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the CMEK key. The key itself is managed
   centrally.
5. **Project-wide GCS data-access audit logging** (S-4: byte-level PHI download audit).

Not in the template, but needed as well:

- **The deploy pipeline's account** (`coga-<env>-<short>-gh-actions@<project>`) needs
  `roles/iam.serviceAccountUser` on each of the four runtime accounts, and a Cloud Run role
  that includes `run.jobs.run` (`roles/run.developer` or `roles/run.admin`) for the
  migration job.
- **The ClickHouse VM's account** needs `roles/artifactregistry.reader` on the repository
  holding its mirrored image before the egress lockdown is switched on (a commented example
  is in the template).
- **Cloud Run's service agent** (`service-<project-number>@serverless-robot-prod.iam.gserviceaccount.com`)
  needs `roles/artifactregistry.reader` on the image repository when the images live in
  another project, such as the shared registry project.

## What stays in the CoGA repo (intentionally)

- **Resource-level IAM** on the secrets and buckets the CoGA configuration creates
  (`secretmanager.secretAccessor`, `storage.objectViewer`). Granting IAM on your own secret
  or bucket cannot escalate to project-level roles, and moving it here would deadlock (this
  repo cannot grant access to a secret or bucket the CoGA repo has not created yet). The
  CoGA pipeline therefore needs `setIamPolicy` only on its *own* secrets and buckets, never
  at project scope.

## Ordering / hand-off contract

```
central repo apply  →  APIs on, SAs exist, project+KMS IAM granted, audit config set
        │
        ▼
CoGA repo apply     →  creates VPC, DBs, Cloud Run, LB, secrets/buckets (+ their
                       resource-level IAM), referencing the SAs by email
```
