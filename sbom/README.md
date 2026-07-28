# Software Bill of Materials (SBOM)

CycloneDX SBOMs for CoGA's two deployable components, generated from the pinned
lockfiles:

| Component | Source of truth | SBOM file | Tool |
| --- | --- | --- | --- |
| Backend (Python) | `backend/requirements.txt` (hash-locked) | `sbom/backend.cdx.json` | `cyclonedx-bom` (`cyclonedx-py`) |
| Frontend (Node) | `frontend/package-lock.json` | `sbom/frontend.cdx.json` | `@cyclonedx/cyclonedx-npm` |

Format: **CycloneDX 1.6 (JSON)**.

## How they are produced & retained

- **On demand:** `./scripts/generate-sbom.sh` (runs the pinned generators in
  `python:3.10` / `node:22` containers and writes both files here).
- **In CI:** the `sbom` job in `.github/workflows/ci.yml` regenerates both on
  every build and uploads them as **retained build artifacts**, so a verifiable
  SBOM exists for each commit/release.

The `*.cdx.json` outputs are **git-ignored on purpose**: each generation embeds a
fresh `serialNumber` and timestamp, so committing them would create noisy,
non-reproducible diffs and risk going stale. The authoritative, point-in-time
SBOM for a clinical release is the **CI artifact archived into the technical
file / release record** per the change-control process (TF-18).

## Why this exists (regulatory)

The SBOM is cybersecurity evidence for the in-house IVD technical file
(**TF-13 Cybersecurity & SBOM**) and the inventory that vulnerability monitoring
of the **SOUP register (TF-08)** runs against. Regenerate whenever the locked
dependencies change (the lock is rebuilt with `./scripts/compile-requirements.sh`).
