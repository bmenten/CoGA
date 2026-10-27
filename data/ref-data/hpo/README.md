# HPO ontology — vendored reference (version-pinned)

`hp.obo` is deliberately **vendored** (committed) rather than fetched at build
time, so the platform bootstraps deterministically — including in offline /
air-gapped deployments — as required for an IVDR in-house IVD. The runtime
download (`HPO_DOWNLOAD_IF_MISSING`, in
[`hpo_service.py`](../../../backend/app/services/hpo_service.py)) is a **fallback
only**, and it is pinned to this file: this committed copy is the authoritative,
pinned reference.

## Pinned version

| Field | Value |
| --- | --- |
| Source | Human Phenotype Ontology (HPO) |
| File | `hp.obo` (OBO format-version 1.2) |
| **Release (`data-version`)** | **`hp/releases/2026-02-16`** |
| Terms | 19,944 |
| Vendored into the repo | 2026-06-05 |
| SHA-256 | `8d6c23798667d4506767ce643fc3c028f0d1c85e7e1d8810e491181a345d53cd` |
| Pinned release URL (the download fallback) | <https://purl.obolibrary.org/obo/hp/releases/2026-02-16/hp.obo> |
| Floating URL (not used) | <https://purl.obolibrary.org/obo/hp.obo> |
| License | HPO license — free for use, see <https://hpo.jax.org/app/license> |

The **pinned release URL** is the reproducible fetch: it always returns the exact
`2026-02-16` release, whereas the floating URL moves to the latest release.

## How the version is used

On startup, when no HPO terms are loaded yet, the backend imports this file, and
only this file: whichever ontology file it finds (`HPO_ONTOLOGY_PATH`, on Cloud Run
the reference-data bucket's copy) is imported only if its SHA-256 is the one above
(TRACE-14). Another file, such as a later release, is refused before anything is
written: the backend logs one error that names the file, both digests and the fix,
imports nothing and downloads nothing in its place, and the start goes on with the
HPO terminology empty until this file is there. It
reads the `data-version:` header and stores the release on every term
(`hpo_term.release_version` and `release_date`). The release is shown on the admin
HPO page, and it is part of the key of the phenotype-prioritised ranking cache, so
a new release invalidates the cached rankings. Every sign-out freezes the release too,
as the `hpo` reference module of the annotation manifest
([annotation-provenance.md](../../../docs/annotation-provenance.md#the-reference-modules)); the
[TF-08 SOUP register](../../../docs/regulatory/TF-08-soup-register.md) lists HPO
as reference data.

## The download fallback

The backend image holds no reference data. When the startup finds no ontology file
(a reference-data bucket without `hpo/hp.obo`, for instance), it downloads this
release from the pinned release URL and uses the file only if its SHA-256 is the
one above. Any other file, such as a later release, is refused before anything is
written, so no file on disk changes: the backend logs an error naming both digests
and imports nothing. The HPO terminology then stays empty until this file is in
place.

The pin is kept in one place, `backend/app/core/config.py`: `HPO_PINNED_RELEASE`
and `HPO_PINNED_ONTOLOGY_SHA256`, with the URL built from the release. No setting
changes them, so the release changes only through a reviewed code change, together with
this file. The `HPO_ONTOLOGY_URL` and `HPO_ONTOLOGY_SHA256` settings that could replace
them were removed; the backend ignores either variable if a deployment still sets it, as
it ignores every variable it does not define, and downloads the pinned release.
[`test_hpo_download_pin.py`](../../../backend/tests/test_hpo_download_pin.py) fails
until this file, the pin and the table above agree.

## Updating (controlled change)

Replacing this ontology is a **TF-18 controlled change**:

1. Download the new release from its versioned PURL (HTTPS),
   `https://purl.obolibrary.org/obo/hp/releases/<date>/hp.obo`, and keep the file
   byte for byte as served.
2. Replace `hp.obo`, then update the **Release**, **Terms**, **Vendored**,
   **SHA-256** and **Pinned release URL** rows above (recompute with
   `shasum -a 256 hp.obo`).
3. Move the pin to the same release in `backend/app/core/config.py`: set
   `HPO_PINNED_RELEASE` to the new `data-version` and `HPO_PINNED_ONTOLOGY_SHA256` to
   the new digest. Run `backend/tests/test_hpo_download_pin.py`.
4. Existing databases keep their terms: the startup import only runs when none are
   loaded. Apply the new file under Administration → HPO Terminology
   (`/admin/reference/hpo`): preview, then apply, with the path
   `/data/ref-data/hpo/hp.obo`, and enter the new release as the *Expected release*. The
   sync records the release the file states and its SHA-256 with the terms, and refuses a
   file that states another release, or none (TRACE-18). A deployment that reads its reference data from a
   bucket needs the new file there too, or a start on an empty database refuses the
   old file in the bucket.
5. Record it: before the first release candidate the pull request is the record (TF-10 §8
   already lists HPO releases in the validation scope); from the release candidate on, a
   `CHANGELOG.md` entry and a TF-18 §8 change record.
