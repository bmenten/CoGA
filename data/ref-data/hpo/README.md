# HPO ontology — vendored reference (version-pinned)

`hp.obo` is deliberately **vendored** (committed) rather than fetched at build
time, so the platform bootstraps deterministically — including in offline /
air-gapped deployments — as required for an IVDR in-house IVD. The runtime
download (`HPO_DOWNLOAD_IF_MISSING`, in
[`hpo_service.py`](../../../backend/app/services/hpo_service.py)) is a **fallback
only**; this committed copy is the authoritative, pinned reference.

## Pinned version

| Field | Value |
| --- | --- |
| Source | Human Phenotype Ontology (HPO) |
| File | `hp.obo` (OBO format-version 1.2) |
| **Release (`data-version`)** | **`hp/releases/2026-02-16`** |
| Terms | 19,944 |
| Vendored into the repo | 2026-06-05 |
| SHA-256 | `8d6c23798667d4506767ce643fc3c028f0d1c85e7e1d8810e491181a345d53cd` |
| Canonical (floating) URL | <https://purl.obolibrary.org/obo/hp.obo> |
| Pinned (reproducible) URL | <https://purl.obolibrary.org/obo/hp/releases/2026-02-16/hp.obo> |
| License | HPO license — free for use, see <https://hpo.jax.org/app/license> |

The **pinned URL** is the reproducible fetch: it always returns the exact
`2026-02-16` release, whereas the canonical URL floats to the latest release.

## How the version is used

On startup, when no HPO terms are loaded yet, the backend imports this file. It
reads the `data-version:` header and stores the release on every term
(`hpo_term.release_version` and `release_date`). The release is shown on the admin
HPO page, and it is part of the key of the phenotype-prioritised ranking cache, so
a new release invalidates the cached rankings. The signed report does not yet record
the HPO release ([ROADMAP](../../../docs/ROADMAP.md)); the
[TF-08 SOUP register](../../../docs/regulatory/TF-08-soup-register.md) lists HPO
as reference data.

## Updating (controlled change)

Replacing this ontology is a **TF-18 controlled change**:

1. Download the new release from its versioned PURL (HTTPS).
2. Replace `hp.obo`, then update the **Release**, **Terms**, **Vendored**, and
   **SHA-256** rows above (recompute with `shasum -a 256 hp.obo`).
3. Existing databases keep their terms: the startup import only runs when none are
   loaded. Apply the new file under Administration → HPO Terminology
   (`/admin/reference/hpo`): preview, then apply, with the path
   `/data/ref-data/hpo/hp.obo`.
4. Record it: a `CHANGELOG.md` entry and a TF-18 §8 change record with its proposed
   level. If you rely on the download fallback, set `HPO_ONTOLOGY_SHA256` to the new
   file's digest so the download is checked against it.
