# HPO ontology — vendored backup (version-pinned)

A start on an empty database imports **HPO's newest release**, downloaded from HPO. This
committed `hp.obo` is the **backup**: the start imports it when HPO cannot be reached, when what
HPO serves is refused, or when the download is switched off (`HPO_DOWNLOAD_LATEST_ON_STARTUP=false`,
for an offline or air-gapped deployment). It is pinned by SHA-256, so the backup is always this
file, byte for byte.

## Pinned version

| Field | Value |
| --- | --- |
| Source | Human Phenotype Ontology (HPO) |
| File | `hp.obo` (OBO format-version 1.2) |
| **Release (`data-version`)** | **`hp/releases/2026-02-16`** |
| Terms | 19,944 |
| Vendored into the repo | 2026-06-05 |
| SHA-256 | `8d6c23798667d4506767ce643fc3c028f0d1c85e7e1d8810e491181a345d53cd` |
| Pinned release URL (the backup download) | <https://purl.obolibrary.org/obo/hp/releases/2026-02-16/hp.obo> |
| Floating URL (not used) | <https://purl.obolibrary.org/obo/hp.obo> |
| License | HPO license — free for use, see <https://hpo.jax.org/app/license> |

The **pinned release URL** is the reproducible fetch: it always returns the exact
`2026-02-16` release, whereas the floating URL moves to the latest release.

## How a start loads HPO

Only a start on a database without HPO terms imports anything; a database that has terms keeps
them (a newer release is applied under Administration → HPO terminology, below).

1. **The newest release.** The backend reads which release HPO published last (HPO's latest
   GitHub release, `HPO_LATEST_RELEASE_API_URL`) and downloads that release from its own
   versioned PURL, `https://purl.obolibrary.org/obo/hp/releases/<date>/hp.obo`, which never
   floats. Every request, each redirect included, goes over HTTPS, and each answer is read within
   a size cap. The file must be an OBO file that states the release asked for (its
   `data-version:` header), or it is refused. It is imported from the bytes that were checked;
   nothing is written to disk. This is the admin sync's download (TRACE-18), without the preview.
2. **The backup**, when step 1 fails (HPO unreachable, GitHub's limit of 60 requests an hour,
   a file that states another release or is not an OBO file) or is switched off. The backend logs
   one warning that names what failed and the backup release, then imports the ontology file it
   finds (`HPO_ONTOLOGY_PATH`, on Cloud Run the reference-data bucket's copy), and only if its
   SHA-256 is the one above (TRACE-14). Another file, such as a later release, is refused before
   anything is written: the backend logs one error that names the file, both digests and the
   fix, imports nothing and downloads nothing in its place, and the start goes on with the HPO
   terminology empty.
3. **The backup download**, when no ontology file is in place and the download is on: the backend
   fetches this release from the pinned release URL and uses the file only if its SHA-256 is the
   one above. Any other file is refused before anything is written, so no file on disk changes.

Whichever file is imported, the backend reads the release it states and stores it on every term
(`hpo_term.release_version` and `release_date`), with the SHA-256 of the bytes imported
(`metadata.release_sha256`). The release and its SHA-256 are shown on the admin HPO page, and the
release is part of the key of the phenotype-prioritised ranking cache, so a new release
invalidates the cached rankings. Every sign-out freezes both, as the `hpo` reference module of
the annotation manifest
([annotation-provenance.md](../../../docs/annotation-provenance.md#the-reference-modules)), so a
signed record names the exact file its phenotype matching used, whichever release a start
imported. The [TF-08 SOUP register](../../../docs/regulatory/TF-08-soup-register.md) lists HPO
as reference data.

Two instances started at different times can therefore begin on different HPO releases. Compare
the release on each admin HPO page, and apply the same release to both if they must match.

## The pin

The backup's pin is kept in one place, `backend/app/core/config.py`: `HPO_PINNED_RELEASE` and
`HPO_PINNED_ONTOLOGY_SHA256`, with the URL built from the release. No setting changes them, so
the backup release changes only through a reviewed code change, together with this file. The
`HPO_ONTOLOGY_URL` and `HPO_ONTOLOGY_SHA256` settings that could replace them were removed; the
backend ignores either variable if a deployment still sets it, as it ignores every variable it
does not define. Nor is the newest release's source a setting: the GitHub releases URL and the
PURL template are code constants.
[`test_hpo_download_pin.py`](../../../backend/tests/test_hpo_download_pin.py) fails until this
file, the pin and the table above agree;
[`test_hpo_startup_latest_release.py`](../../../backend/tests/test_hpo_startup_latest_release.py)
covers the newest-release download and when the backup takes over.

## Applying a newer release

A running system keeps the release it has. Apply a new one under Administration → HPO
Terminology (`/admin/reference/hpo`), which says when HPO has published one newer than the loaded
release: choose *Download an HPO release* with the release's date (it is fetched from its
versioned PURL and must state that release), or a file under `/data/ref-data/hpo/` with the
release as the *Expected release*; preview, then apply. The sync records the release the file
states and its SHA-256 with the terms, and refuses a file that states another release, or none
(TRACE-18). A newer release changes phenotype matching, so on a running system it is an
administrator's step, never a start's.

## Updating the backup (controlled change)

Replacing this file is a **TF-18 controlled change**:

1. Download the new release from its versioned PURL (HTTPS),
   `https://purl.obolibrary.org/obo/hp/releases/<date>/hp.obo`, and keep the file
   byte for byte as served.
2. Replace `hp.obo`, then update the **Release**, **Terms**, **Vendored**,
   **SHA-256** and **Pinned release URL** rows above (recompute with
   `shasum -a 256 hp.obo`).
3. Move the pin to the same release in `backend/app/core/config.py`: set
   `HPO_PINNED_RELEASE` to the new `data-version` and `HPO_PINNED_ONTOLOGY_SHA256` to
   the new digest. Run `backend/tests/test_hpo_download_pin.py`.
4. A deployment that reads its reference data from a bucket needs the new file there too, or a
   start that cannot reach HPO refuses the old file in the bucket.
5. Record it: before the first release candidate the pull request is the record (TF-10 §8
   already lists HPO releases in the validation scope); from the release candidate on, a
   `CHANGELOG.md` entry and a TF-18 §8 change record.
