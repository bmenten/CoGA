"""Object-storage abstraction: local filesystem (dev) or a cloud object store
(production) — AWS S3 or Google Cloud Storage.

Controlled by ``STORAGE_BACKEND`` (``local`` by default, ``s3``, or ``gcs``). In a
remote mode the raw family data lives in a bucket and is read two ways:

- IGV alignments (CRAM/BAM + indexes) are served as short-lived **presigned/signed
  URLs** so the browser fetches bytes directly from the store (native HTTP range
  support).
- Family-package sources are **staged** to a temp directory for an import job, so
  the existing path-based import logic runs unchanged, then cleaned up. Staging can
  leave objects in the store (``download_prefix``'s ``skip``); package import leaves
  the aligned reads there, since it only records where they lie.

Object keys mirror the local layout (``<family_id>/<file>``) under the optional
storage prefix (``S3_PREFIX`` or ``GCS_PREFIX``). Remote URIs use the store's
native scheme: ``s3://`` or ``gs://``. An imported package's alignments are instead
addressed by the URI the import recorded (``configured_object_key``).

Credentials:

- **S3** — the standard AWS chain (env vars / instance role).
- **GCS** — Application Default Credentials (Workload Identity on Cloud Run). Signed
  URLs are produced with IAM ``SignBlob`` so **no private key** is needed; the
  runtime service account must have ``roles/iam.serviceAccountTokenCreator`` on
  itself and the IAM Credentials API (``iamcredentials.googleapis.com``) enabled.

The cloud SDKs (``boto3`` / ``google-cloud-storage``) are imported lazily so a
deployment need not install whichever backend it does not use.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from .config import settings

# Worker threads used to download objects under a prefix concurrently. Package
# staging is dominated by per-object latency, so a small pool gives a large speedup
# without overwhelming the client connection pool.
_DOWNLOAD_PREFIX_MAX_WORKERS = 12

# Remote URI schemes understood by the package-import path.
_REMOTE_SCHEMES = ("s3", "gs")

# Manifest filenames that mark a folder as an importable family package.
_MANIFEST_NAMES = ("manifest.yaml", "manifest.yml", "manifest.json")


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def storage_is_remote() -> bool:
    """True when a cloud object store (S3 or GCS) backs raw family data."""
    return settings.storage_backend in ("s3", "gcs")


def storage_is_gcs() -> bool:
    return settings.storage_backend == "gcs"


# ---------------------------------------------------------------------------
# Scheme-agnostic remote URI helpers (s3:// or gs://)
# ---------------------------------------------------------------------------

def is_remote_uri(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return any(lowered.startswith(f"{scheme}://") for scheme in _REMOTE_SCHEMES)


@dataclass(frozen=True)
class RemoteLocation:
    scheme: str
    bucket: str
    key: str

    @property
    def uri(self) -> str:
        return f"{self.scheme}://{self.bucket}/{self.key}" if self.key else f"{self.scheme}://{self.bucket}"


def parse_remote_uri(uri: str) -> RemoteLocation:
    parsed = urlparse(str(uri))
    if parsed.scheme not in _REMOTE_SCHEMES or not parsed.netloc:
        raise ValueError(f"Not a remote object-store URI (s3:// or gs://): {uri!r}")
    return RemoteLocation(scheme=parsed.scheme, bucket=parsed.netloc, key=parsed.path.lstrip("/"))


def join_remote_uri(base: str, *parts: str) -> str:
    """Append path segments to a remote URI (collapsing slashes), preserving scheme."""
    location = parse_remote_uri(base)
    extra = [str(part).strip("/") for part in parts if str(part).strip("/")]
    key = "/".join(segment for segment in [location.key.strip("/"), *extra] if segment)
    return RemoteLocation(location.scheme, location.bucket, key).uri


def remote_folder_name(uri: str) -> str:
    """A remote folder's name, as a local folder's name: its last path segment
    (``gs://b/imports/F1/`` -> ``F1``), or the bucket's name for a bucket root."""
    location = parse_remote_uri(uri)
    return PurePosixPath(location.key).name or location.bucket


def remote_uri_within(uri: str, root: str) -> bool:
    """Whether ``uri`` names an object below the remote folder ``root``: same scheme and
    bucket, and a key under the root's key on a segment boundary (``imports/`` does not
    contain ``imports-old/``)."""
    try:
        location, base = parse_remote_uri(uri), parse_remote_uri(root)
    except ValueError:
        return False
    if (location.scheme, location.bucket) != (base.scheme, base.bucket) or not location.key:
        return False
    prefix = base.key.strip("/")
    return not prefix or location.key.startswith(f"{prefix}/")


# ---------------------------------------------------------------------------
# Bucket / key resolution for the configured backend
# ---------------------------------------------------------------------------

def _configured_bucket() -> str:
    if storage_is_gcs():
        if not settings.gcs_bucket:
            raise RuntimeError("STORAGE_BACKEND=gcs but GCS_BUCKET is not configured")
        return settings.gcs_bucket
    if not settings.s3_bucket:
        raise RuntimeError("STORAGE_BACKEND=s3 but S3_BUCKET is not configured")
    return settings.s3_bucket


def _configured_prefix() -> str:
    return (settings.gcs_prefix if storage_is_gcs() else settings.s3_prefix) or ""


def object_key(*parts: str) -> str:
    """Key within the configured bucket, under the optional storage prefix."""
    prefix = _configured_prefix().strip("/")
    segments = [prefix, *[str(part).strip("/") for part in parts if str(part).strip("/")]]
    return "/".join(segment for segment in segments if segment)


def configured_object_uri(key: str) -> str:
    """The ``gs://`` or ``s3://`` URI of a key in the configured bucket."""
    return RemoteLocation("gs" if storage_is_gcs() else "s3", _configured_bucket(), key).uri


def configured_object_key(uri: object) -> str | None:
    """The key of ``uri`` when it names an object in the configured bucket, else ``None``.

    For locations read back from the database, which are data rather than trusted input.
    The URI must use the configured backend's scheme (``gs`` for gcs, ``s3`` for s3) and
    bucket, rebuild to exactly the same string -- so no query, fragment, port or user
    info rides along to be dropped or misread -- and have a key with no empty, ``.`` or
    ``..`` segment. The optional storage prefix does not apply: the URI is absolute.
    """
    if not storage_is_remote() or not isinstance(uri, str):
        return None
    try:
        location = parse_remote_uri(uri)
    except ValueError:
        return None
    expected_scheme = "gs" if storage_is_gcs() else "s3"
    if location.scheme != expected_scheme or location.bucket != _configured_bucket():
        return None
    if location.uri != uri:
        return None
    if not location.key or any(segment in {"", ".", ".."} for segment in location.key.split("/")):
        return None
    return location.key


# ---------------------------------------------------------------------------
# Clients (lazy)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _s3_client():
    import boto3  # lazy: only needed in s3 mode
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=settings.s3_region or None,
        endpoint_url=settings.s3_endpoint_url or None,
        config=Config(
            signature_version="s3v4",
            connect_timeout=settings.s3_connect_timeout_seconds,
            read_timeout=settings.s3_read_timeout_seconds,
            # Adaptive mode adds client-side throttling-aware backoff on top of retries,
            # so a slow/throttling object store fails fast and bounded instead of hanging.
            retries={"mode": "adaptive", "max_attempts": settings.s3_max_attempts},
        ),
    )


@lru_cache(maxsize=1)
def _gcs_client():
    from google.cloud import storage  # lazy: only needed in gcs mode

    if settings.gcs_endpoint_url:
        # Emulator / GCS-compatible endpoint (e.g. fake-gcs-server): anonymous creds
        # + explicit endpoint. The project is arbitrary for an emulator.
        from google.auth.credentials import AnonymousCredentials

        return storage.Client(
            project=settings.gcs_project or "coga",
            credentials=AnonymousCredentials(),
            client_options={"api_endpoint": settings.gcs_endpoint_url},
        )
    return storage.Client(project=settings.gcs_project or None)


# ---------------------------------------------------------------------------
# Object existence
# ---------------------------------------------------------------------------

def object_exists(key: str) -> bool:
    """Whether ``key`` exists in the configured bucket."""
    return _exists("gs" if storage_is_gcs() else "s3", _configured_bucket(), key)


def remote_object_exists(uri: str) -> bool:
    """Whether the object a ``gs://``/``s3://`` URI names exists, in the bucket the URI
    names -- which, as for package staging, need not be the configured one: a package
    may come from any bucket FAMILY_IMPORT_ROOTS allows. Blocking -- call from a worker
    thread in async contexts."""
    location = parse_remote_uri(uri)
    return _exists(location.scheme, location.bucket, location.key)


def _exists(scheme: str, bucket: str, key: str) -> bool:
    if scheme == "gs":
        return _gcs_client().bucket(bucket).blob(key).exists()

    from botocore.exceptions import ClientError

    try:
        _s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


# S3's additional checksums, as HEAD Object names them -> the algorithm.
_S3_CHECKSUM_FIELDS = (
    ("ChecksumCRC32", "crc32"),
    ("ChecksumCRC32C", "crc32c"),
    ("ChecksumCRC64NVME", "crc64nvme"),
    ("ChecksumSHA1", "sha1"),
    ("ChecksumSHA256", "sha256"),
)


def remote_object_identity(uri: str) -> dict[str, Any] | None:
    """The store's own record of an object, for provenance; ``None`` if it is absent.

    Its size, its generation (GCS) or version id (S3; ``None`` in an unversioned
    bucket), its ETag, and the checksums the store keeps. Every checksum is labelled
    with its algorithm and encoding, and for S3 with whether it covers the whole object
    or is a checksum of the parts of a multipart upload: none of them is a SHA-256 the
    application computed over the bytes, so none belongs where such a hash is expected.
    An S3 ETag is an opaque identifier (the MD5 only of a plain single-part upload).
    Blocking -- call from a worker thread in async contexts.
    """
    location = parse_remote_uri(uri)
    if location.scheme == "gs":
        blob = _gcs_client().bucket(location.bucket).get_blob(location.key)
        if blob is None:
            return None
        return {
            "store": "gcs",
            "uri": uri,
            "size": blob.size,
            "generation": str(blob.generation) if blob.generation is not None else None,
            "etag": blob.etag,
            # A composite object has no MD5; its CRC32C still covers the whole object.
            "checksums": [
                {"algorithm": algorithm, "encoding": "base64", "value": value}
                for algorithm, value in (("md5", blob.md5_hash), ("crc32c", blob.crc32c))
                if value
            ],
        }

    from botocore.exceptions import ClientError

    try:
        head = _s3_client().head_object(
            Bucket=location.bucket, Key=location.key, ChecksumMode="ENABLED"
        )
    except ClientError:
        return None
    checksum_type = head.get("ChecksumType")  # FULL_OBJECT or COMPOSITE
    return {
        "store": "s3",
        "uri": uri,
        "size": head.get("ContentLength"),
        "version_id": head.get("VersionId"),
        "etag": head.get("ETag"),
        "checksums": [
            {
                "algorithm": algorithm,
                "encoding": "base64",
                "value": head[field],
                **({"type": checksum_type} if checksum_type else {}),
            }
            for field, algorithm in _S3_CHECKSUM_FIELDS
            if head.get(field)
        ],
    }


# ---------------------------------------------------------------------------
# Presigned / signed download URLs
# ---------------------------------------------------------------------------

def presigned_get_url(key: str, *, filename: str | None = None, expires: int | None = None) -> str:
    expires_in = expires or settings.s3_presign_expiry_seconds
    if storage_is_gcs():
        return _gcs_signed_get_url(key, filename=filename, expires=expires_in)

    params: dict[str, str] = {"Bucket": _configured_bucket(), "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'inline; filename="{filename}"'
    return _s3_client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires_in,
    )


def _gcs_signing_credentials() -> tuple[str, str]:
    """Return ``(service_account_email, access_token)`` for IAM-based V4 signing.

    Under Workload Identity there is no private key on disk, so signed URLs are
    produced via the IAM ``signBlob`` API. The signer is the active ADC service
    account, which must hold ``roles/iam.serviceAccountTokenCreator`` on itself.
    """
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    email = getattr(credentials, "service_account_email", None) or getattr(
        credentials, "signer_email", None
    )
    if not email or not getattr(credentials, "token", None):
        raise RuntimeError(
            "Cannot determine a signing service account for GCS signed URLs. The "
            "runtime needs an attached service account with "
            "roles/iam.serviceAccountTokenCreator on itself and the IAM Credentials "
            "API enabled."
        )
    return email, credentials.token


def _gcs_signed_get_url(key: str, *, filename: str | None, expires: int) -> str:
    blob = _gcs_client().bucket(_configured_bucket()).blob(key)
    disposition = f'inline; filename="{filename}"' if filename else None
    signer_email, access_token = _gcs_signing_credentials()
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=expires),
        method="GET",
        response_disposition=disposition,
        service_account_email=signer_email,
        access_token=access_token,
    )


# ---------------------------------------------------------------------------
# Prefix download (package staging)
# ---------------------------------------------------------------------------

def download_prefix(
    uri: str, dest_dir: Path, *, skip: Callable[[str], bool] | None = None
) -> int:
    """Download the objects under a remote prefix into ``dest_dir`` preserving the
    relative key layout. Returns the number of files written. Blocking — call from a
    worker thread in async contexts.

    ``skip`` is asked about each object's path relative to the prefix (normalised,
    ``/``-separated) and leaves the object in the store when it answers True. It is
    called once per object, single-threaded, before any transfer starts."""
    location = parse_remote_uri(uri)
    if location.scheme == "gs":
        return _gcs_download_prefix(location, dest_dir, skip)
    return _s3_download_prefix(location, dest_dir, skip)


def _plan_downloads(
    base_key: str,
    names: list[str],
    dest_dir: Path,
    skip: Callable[[str], bool] | None = None,
) -> list[tuple[str, str]]:
    """Map remote object names under ``base_key`` to local targets, creating parent
    dirs single-threaded (avoids mkdir races) before the concurrent transfer.

    Object keys are untrusted strings. Each target is confined to ``dest_dir`` the same
    way manifest asset paths are (see ``_resolve_package_path``): resolving the join
    collapses any ``..`` segment or symlink and lets an absolute value override the
    join, so the containment check rejects a crafted key like
    ``pkg/../../../etc/cron.d/evil`` (or an absolute key) before any directory is
    created or byte is written, instead of escaping the staging root and writing an
    arbitrary host file. The check runs before ``skip`` is consulted, so a crafted key
    aborts staging whether or not it would have been downloaded.
    """
    base = base_key.rstrip("/")
    base_prefix = f"{base}/" if base else ""
    dest_root = dest_dir.resolve()
    downloads: list[tuple[str, str]] = []
    for name in names:
        if name.endswith("/"):
            continue
        relative = name[len(base_prefix):] if base_prefix and name.startswith(base_prefix) else name
        if not relative:
            continue
        target = (dest_root / relative).resolve()
        try:
            staged_path = target.relative_to(dest_root)
        except ValueError as exc:
            raise ValueError(
                f"Refusing to stage object key outside the staging directory: {name!r}"
            ) from exc
        if skip is not None and skip(staged_path.as_posix()):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        downloads.append((name, str(target)))
    return downloads


def _s3_download_prefix(
    location: RemoteLocation, dest_dir: Path, skip: Callable[[str], bool] | None = None
) -> int:
    client = _s3_client()
    base = location.key.rstrip("/")
    base_prefix = f"{base}/" if base else ""
    paginator = client.get_paginator("list_objects_v2")

    names: list[str] = []
    for page in paginator.paginate(Bucket=location.bucket, Prefix=base_prefix):
        for obj in page.get("Contents", []):
            names.append(obj["Key"])

    downloads = _plan_downloads(location.key, names, dest_dir, skip)
    if not downloads:
        return 0

    def _download(item: tuple[str, str]) -> None:
        key, target = item
        client.download_file(location.bucket, key, target)

    max_workers = min(_DOWNLOAD_PREFIX_MAX_WORKERS, len(downloads))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # Consume the iterator so any download error propagates (as it did serially).
        for _ in pool.map(_download, downloads):
            pass
    return len(downloads)


def _gcs_download_prefix(
    location: RemoteLocation, dest_dir: Path, skip: Callable[[str], bool] | None = None
) -> int:
    client = _gcs_client()
    base = location.key.rstrip("/")
    base_prefix = f"{base}/" if base else ""
    blobs = list(client.list_blobs(location.bucket, prefix=base_prefix))
    by_name = {blob.name: blob for blob in blobs}

    downloads = _plan_downloads(location.key, list(by_name), dest_dir, skip)
    if not downloads:
        return 0

    def _download(item: tuple[str, str]) -> None:
        name, target = item
        by_name[name].download_to_filename(target)

    max_workers = min(_DOWNLOAD_PREFIX_MAX_WORKERS, len(downloads))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for _ in pool.map(_download, downloads):
            pass
    return len(downloads)


# ---------------------------------------------------------------------------
# Package discovery under a remote root
# ---------------------------------------------------------------------------

def _package_markers(object_names: Iterable[str]) -> tuple[bool, bool]:
    """Whether a folder's objects include a manifest and a PED. Reading stops once both
    are seen, so a lazily paged listing is fetched only as far as it needs to be."""
    has_manifest = has_ped = False
    for name in object_names:
        leaf = name.rsplit("/", 1)[-1]
        has_manifest = has_manifest or leaf in _MANIFEST_NAMES
        has_ped = has_ped or leaf.endswith(".ped")
        if has_manifest and has_ped:
            break
    return has_manifest, has_ped


def list_remote_package_candidates(root_uri: str) -> list[dict[str, object]]:
    """List immediate sub-prefixes under a remote root that look like family
    packages (contain a manifest or a ``*.ped``).

    Returns dicts with ``name``, ``uri`` (the remote folder), ``has_manifest`` and
    ``has_ped``. Blocking — call from a worker thread in async contexts."""
    location = parse_remote_uri(root_uri)
    if location.scheme == "gs":
        return _gcs_list_package_candidates(root_uri, location)
    return _s3_list_package_candidates(root_uri, location)


def _s3_list_package_candidates(root_uri: str, location: RemoteLocation) -> list[dict[str, object]]:
    base = location.key.rstrip("/")
    base_prefix = f"{base}/" if base else ""
    # ListObjectsV2 answers at most 1000 entries at a time: page through every listing,
    # or a root with more packages, or a package with more objects, is cut short.
    paginator = _s3_client().get_paginator("list_objects_v2")
    child_prefixes = [
        str(entry.get("Prefix", ""))
        for page in paginator.paginate(Bucket=location.bucket, Prefix=base_prefix, Delimiter="/")
        for entry in page.get("CommonPrefixes", [])
    ]
    candidates: list[dict[str, object]] = []
    for child_prefix in child_prefixes:
        name = child_prefix[len(base_prefix):].strip("/")
        if not name:
            continue
        has_manifest, has_ped = _package_markers(
            str(obj.get("Key", ""))
            for page in paginator.paginate(Bucket=location.bucket, Prefix=child_prefix)
            for obj in page.get("Contents", [])
        )
        if not has_manifest and not has_ped:
            continue
        candidates.append(
            {
                "name": name,
                "uri": join_remote_uri(root_uri, name),
                "has_manifest": has_manifest,
                "has_ped": has_ped,
            }
        )
    return candidates


def _gcs_list_package_candidates(root_uri: str, location: RemoteLocation) -> list[dict[str, object]]:
    client = _gcs_client()
    base = location.key.rstrip("/")
    base_prefix = f"{base}/" if base else ""
    iterator = client.list_blobs(location.bucket, prefix=base_prefix, delimiter="/")
    # Consuming the page iterator populates ``prefixes`` (the common sub-folders).
    for _ in iterator:
        pass
    candidates: list[dict[str, object]] = []
    for child_prefix in sorted(iterator.prefixes):
        name = child_prefix[len(base_prefix):].strip("/")
        if not name:
            continue
        has_manifest, has_ped = _package_markers(
            blob.name for blob in client.list_blobs(location.bucket, prefix=child_prefix)
        )
        if not has_manifest and not has_ped:
            continue
        candidates.append(
            {
                "name": name,
                "uri": join_remote_uri(root_uri, name),
                "has_manifest": has_manifest,
                "has_ped": has_ped,
            }
        )
    return candidates
