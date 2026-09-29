from datetime import timedelta
from pathlib import Path

import pytest

from app.core import object_storage as s
from app.core.config import Settings


# --- Scheme-agnostic remote URI helpers -------------------------------------

def test_is_remote_uri():
    assert s.is_remote_uri("s3://bucket/key")
    assert s.is_remote_uri("S3://Bucket/Key")
    assert s.is_remote_uri("gs://bucket/key")
    assert s.is_remote_uri("GS://Bucket/Key")
    assert not s.is_remote_uri("/data/families/F1")
    assert not s.is_remote_uri(None)
    assert not s.is_remote_uri(123)


def test_parse_remote_uri_s3_and_gcs():
    s3 = s.parse_remote_uri("s3://bucket/families/F1")
    assert (s3.scheme, s3.bucket, s3.key) == ("s3", "bucket", "families/F1")
    assert s3.uri == "s3://bucket/families/F1"

    gcs = s.parse_remote_uri("gs://bucket/families/F1")
    assert (gcs.scheme, gcs.bucket, gcs.key) == ("gs", "bucket", "families/F1")
    assert gcs.uri == "gs://bucket/families/F1"


def test_parse_remote_uri_rejects_local():
    with pytest.raises(ValueError):
        s.parse_remote_uri("/data/families/F1")


def test_join_remote_uri_preserves_scheme_and_collapses_slashes():
    assert s.join_remote_uri("s3://bucket/families/F1", "S1.cram") == "s3://bucket/families/F1/S1.cram"
    assert s.join_remote_uri("gs://bucket/families/F1", "S1.cram") == "gs://bucket/families/F1/S1.cram"
    assert (
        s.join_remote_uri("gs://bucket/families/F1/", "sub/", "/x.vcf.gz")
        == "gs://bucket/families/F1/sub/x.vcf.gz"
    )


def test_remote_folder_name_is_the_last_segment_or_the_bucket():
    # What a package without a manifest family_id is named after, as a local folder is.
    assert s.remote_folder_name("gs://bucket/imports/F1") == "F1"
    assert s.remote_folder_name("s3://bucket/imports/F1/") == "F1"
    assert s.remote_folder_name("gs://bucket") == "bucket"


def test_object_key_honours_prefix_per_backend(monkeypatch):
    monkeypatch.setattr(s.settings, "storage_backend", "s3")
    monkeypatch.setattr(s.settings, "s3_prefix", "families")
    assert s.object_key("F1", "S1.cram") == "families/F1/S1.cram"

    monkeypatch.setattr(s.settings, "storage_backend", "gcs")
    monkeypatch.setattr(s.settings, "gcs_prefix", "phi")
    assert s.object_key("F1", "S1.cram") == "phi/F1/S1.cram"

    monkeypatch.setattr(s.settings, "gcs_prefix", "")
    assert s.object_key("F1", "S1.cram") == "F1/S1.cram"


# --- Backend flags ----------------------------------------------------------

def test_backend_flags(monkeypatch):
    monkeypatch.setattr(s.settings, "storage_backend", "local")
    assert not s.storage_is_remote() and not s.storage_is_s3() and not s.storage_is_gcs()

    monkeypatch.setattr(s.settings, "storage_backend", "s3")
    assert s.storage_is_remote() and s.storage_is_s3() and not s.storage_is_gcs()

    monkeypatch.setattr(s.settings, "storage_backend", "gcs")
    assert s.storage_is_remote() and s.storage_is_gcs() and not s.storage_is_s3()


# --- Settings validation ----------------------------------------------------

def test_storage_backend_defaults_to_local():
    assert Settings(_env_file=None).storage_backend == "local"


def test_s3_backend_requires_bucket():
    with pytest.raises(Exception):
        Settings(_env_file=None, APP_ENV="development", STORAGE_BACKEND="s3")


def test_s3_backend_with_bucket_is_valid():
    settings = Settings(
        _env_file=None, APP_ENV="development", STORAGE_BACKEND="s3", S3_BUCKET="my-bucket"
    )
    assert settings.storage_backend == "s3"
    assert settings.s3_bucket == "my-bucket"


def test_gcs_backend_requires_bucket():
    with pytest.raises(Exception):
        Settings(_env_file=None, APP_ENV="development", STORAGE_BACKEND="gcs")


def test_gcs_backend_with_bucket_is_valid():
    settings = Settings(
        _env_file=None, APP_ENV="development", STORAGE_BACKEND="gcs", GCS_BUCKET="my-bucket"
    )
    assert settings.storage_backend == "gcs"
    assert settings.gcs_bucket == "my-bucket"


def test_invalid_storage_backend_rejected():
    with pytest.raises(Exception):
        Settings(_env_file=None, APP_ENV="development", STORAGE_BACKEND="azure")


# --- Backward-compatible S3-named aliases -----------------------------------

def test_deprecated_s3_aliases_still_resolve():
    assert s.is_s3_uri is s.is_remote_uri
    assert s.parse_s3_uri is s.parse_remote_uri
    assert s.join_s3_uri is s.join_remote_uri
    assert s.list_s3_package_candidates is s.list_remote_package_candidates
    assert s.S3Location is s.RemoteLocation


# --- GCS backend (clients mocked: google-cloud-storage not exercised) --------

def _use_gcs(monkeypatch, bucket="phi-bucket"):
    monkeypatch.setattr(s.settings, "storage_backend", "gcs")
    monkeypatch.setattr(s.settings, "gcs_bucket", bucket)


def test_gcs_object_exists(monkeypatch):
    _use_gcs(monkeypatch)

    class _Blob:
        def __init__(self, key):
            self._key = key

        def exists(self):
            return self._key == "F1/S1.cram"

    class _Bucket:
        def blob(self, key):
            return _Blob(key)

    class _Client:
        def bucket(self, name):
            assert name == "phi-bucket"
            return _Bucket()

    monkeypatch.setattr(s, "_gcs_client", lambda: _Client())
    assert s.object_exists("F1/S1.cram") is True
    assert s.object_exists("F1/missing.cram") is False


def test_gcs_signed_url_uses_iam_signing(monkeypatch):
    _use_gcs(monkeypatch)
    captured: dict = {}

    class _Blob:
        def generate_signed_url(self, **kwargs):
            captured.update(kwargs)
            return "https://signed.example/x"

    class _Bucket:
        def blob(self, key):
            captured["key"] = key
            return _Blob()

    class _Client:
        def bucket(self, name):
            captured["bucket"] = name
            return _Bucket()

    monkeypatch.setattr(s, "_gcs_client", lambda: _Client())
    monkeypatch.setattr(s, "_gcs_signing_credentials", lambda: ("sa@proj.iam.gserviceaccount.com", "tok123"))

    url = s.presigned_get_url("F1/S1.cram", filename="S1.cram", expires=120)
    assert url == "https://signed.example/x"
    assert captured["bucket"] == "phi-bucket"
    assert captured["key"] == "F1/S1.cram"
    assert captured["version"] == "v4"
    assert captured["method"] == "GET"
    assert captured["service_account_email"] == "sa@proj.iam.gserviceaccount.com"
    assert captured["access_token"] == "tok123"
    assert captured["expiration"] == timedelta(seconds=120)
    assert 'filename="S1.cram"' in captured["response_disposition"]


def test_gcs_download_prefix(monkeypatch, tmp_path):
    _use_gcs(monkeypatch)

    class _Blob:
        def __init__(self, name):
            self.name = name

        def download_to_filename(self, target):
            Path(target).write_text("data")

    class _Client:
        def list_blobs(self, bucket, prefix=None, delimiter=None):
            assert bucket == "phi-bucket"
            assert prefix == "fam/F1/"
            return [
                _Blob("fam/F1/manifest.yaml"),
                _Blob("fam/F1/sub/x.vcf.gz"),
                _Blob("fam/F1/"),  # the "directory" placeholder is skipped
            ]

    monkeypatch.setattr(s, "_gcs_client", lambda: _Client())

    written = s.download_prefix("gs://phi-bucket/fam/F1", tmp_path)
    assert written == 2
    assert (tmp_path / "manifest.yaml").read_text() == "data"
    assert (tmp_path / "sub" / "x.vcf.gz").exists()


def test_plan_downloads_maps_legitimate_keys(tmp_path):
    downloads = s._plan_downloads(
        "fam/F1",
        ["fam/F1/manifest.yaml", "fam/F1/sub/x.vcf.gz", "fam/F1/"],
        tmp_path,
    )
    relatives = sorted(
        str(Path(target).relative_to(tmp_path.resolve())) for _name, target in downloads
    )
    assert relatives == ["manifest.yaml", str(Path("sub") / "x.vcf.gz")]


def test_plan_downloads_rejects_parent_traversal_key(tmp_path):
    # A crafted object key with ``..`` must not write outside the staging dir.
    with pytest.raises(ValueError, match="outside the staging directory"):
        s._plan_downloads(
            "fam/F1",
            ["fam/F1/../../../etc/cron.d/evil"],
            tmp_path,
        )
    assert not (tmp_path.parent.parent / "etc").exists()


def test_plan_downloads_rejects_absolute_key(tmp_path):
    # An absolute key would otherwise override the join and escape the staging dir.
    with pytest.raises(ValueError, match="outside the staging directory"):
        s._plan_downloads("", ["/etc/passwd"], tmp_path)


def test_plan_downloads_rejects_symlink_escape(tmp_path):
    # A symlinked subdirectory inside the staging dir must not let a key escape: the
    # containment check resolves the target (following the link) and rejects it. This
    # locks in the symlink behavior so a future switch to a non-symlink-following
    # normalization (e.g. os.path.normpath / PurePath) can't silently reintroduce the
    # traversal while still passing the ``..``/absolute cases.
    dest = tmp_path / "stage"
    dest.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (dest / "escape").symlink_to(outside)
    with pytest.raises(ValueError, match="outside the staging directory"):
        s._plan_downloads("fam/F1", ["fam/F1/escape/evil.txt"], dest)
    assert not (outside / "evil.txt").exists()


def test_gcs_download_prefix_rejects_traversal_key(monkeypatch, tmp_path):
    # End-to-end: a malicious key surfaced by the listing must abort staging before
    # any file is written, rather than escaping via download_to_filename.
    _use_gcs(monkeypatch)

    written_targets: list[str] = []

    class _Blob:
        def __init__(self, name):
            self.name = name

        def download_to_filename(self, target):
            written_targets.append(target)
            Path(target).write_text("data")

    class _Client:
        def list_blobs(self, bucket, prefix=None, delimiter=None):
            return [
                _Blob("fam/F1/manifest.yaml"),
                _Blob("fam/F1/../../../tmp/coga-evil"),
            ]

    monkeypatch.setattr(s, "_gcs_client", lambda: _Client())

    with pytest.raises(ValueError, match="outside the staging directory"):
        s.download_prefix("gs://phi-bucket/fam/F1", tmp_path)
    # Planning happens before any transfer, so nothing was written.
    assert written_targets == []


# --- S3 backend (clients mocked: boto3 not exercised) -----------------------

def _use_s3(monkeypatch, bucket="phi-bucket"):
    monkeypatch.setattr(s.settings, "storage_backend", "s3")
    monkeypatch.setattr(s.settings, "s3_bucket", bucket)


class _FakeS3Paginator:
    def __init__(self, keys):
        self._keys = keys

    def paginate(self, Bucket=None, Prefix=None):
        return [{"Contents": [{"Key": key} for key in self._keys]}]


class _FakeS3Client:
    def __init__(self, keys):
        self._keys = keys
        self.downloaded: list[tuple[str, str]] = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _FakeS3Paginator(self._keys)

    def download_file(self, bucket, key, target):
        self.downloaded.append((key, target))
        Path(target).write_text("data")


def test_s3_download_prefix(monkeypatch, tmp_path):
    _use_s3(monkeypatch)
    client = _FakeS3Client(
        ["fam/F1/manifest.yaml", "fam/F1/sub/x.vcf.gz", "fam/F1/"]  # dir placeholder skipped
    )
    monkeypatch.setattr(s, "_s3_client", lambda: client)

    written = s.download_prefix("s3://phi-bucket/fam/F1", tmp_path)
    assert written == 2
    assert (tmp_path / "manifest.yaml").read_text() == "data"
    assert (tmp_path / "sub" / "x.vcf.gz").exists()


def test_s3_download_prefix_rejects_traversal_key(monkeypatch, tmp_path):
    # S3 mirror of the GCS e2e test: a ``..`` key in the listing aborts staging at
    # plan time, before any download_file is called.
    _use_s3(monkeypatch)
    client = _FakeS3Client(["fam/F1/manifest.yaml", "fam/F1/../../../tmp/coga-evil"])
    monkeypatch.setattr(s, "_s3_client", lambda: client)

    with pytest.raises(ValueError, match="outside the staging directory"):
        s.download_prefix("s3://phi-bucket/fam/F1", tmp_path)
    assert client.downloaded == []


def test_gcs_list_package_candidates(monkeypatch):
    _use_gcs(monkeypatch)

    class _Blob:
        def __init__(self, name):
            self.name = name

    class _BlobPage(list):
        def __init__(self, items, prefixes=()):
            super().__init__(items)
            self.prefixes = list(prefixes)

    class _Client:
        def list_blobs(self, bucket, prefix=None, delimiter=None):
            if delimiter == "/":
                return _BlobPage([], prefixes=["fam/F1/", "fam/F2/", "fam/empty/"])
            if prefix == "fam/F1/":
                return _BlobPage([_Blob("fam/F1/manifest.yaml")])
            if prefix == "fam/F2/":
                return _BlobPage([_Blob("fam/F2/trio.ped")])
            return _BlobPage([])  # fam/empty/ has neither manifest nor ped

    monkeypatch.setattr(s, "_gcs_client", lambda: _Client())

    candidates = {c["name"]: c for c in s.list_remote_package_candidates("gs://phi-bucket/fam")}
    assert set(candidates) == {"F1", "F2"}
    assert candidates["F1"]["has_manifest"] and not candidates["F1"]["has_ped"]
    assert candidates["F2"]["has_ped"] and not candidates["F2"]["has_manifest"]
    assert candidates["F1"]["uri"] == "gs://phi-bucket/fam/F1"


# --- S3 package discovery reads every page ------------------------------------


class _PagedS3:
    """ListObjectsV2 as S3 answers it: at most 1000 entries (keys and common prefixes)
    per response, the rest behind a continuation token."""

    page_size = 1000

    def __init__(self, keys):
        self.keys = sorted(keys)
        self.requests: list[str] = []

    def list_objects_v2(self, *, Bucket, Prefix="", Delimiter=None, ContinuationToken=None, **_kwargs):
        self.requests.append(Prefix)
        entries: list[tuple[str, str]] = []
        common: set[str] = set()
        for key in self.keys:
            if not key.startswith(Prefix):
                continue
            rest = key[len(Prefix):]
            if Delimiter and Delimiter in rest:
                folder = Prefix + rest.split(Delimiter, 1)[0] + Delimiter
                if folder not in common:
                    common.add(folder)
                    entries.append(("prefix", folder))
            else:
                entries.append(("key", key))
        start = int(ContinuationToken or 0)
        page = entries[start:start + self.page_size]
        truncated = start + self.page_size < len(entries)
        response = {
            "Contents": [{"Key": value} for kind, value in page if kind == "key"],
            "CommonPrefixes": [{"Prefix": value} for kind, value in page if kind == "prefix"],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = str(start + self.page_size)
        return response

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        client = self

        class _Paginator:
            def paginate(self, **kwargs):
                token = None
                while True:
                    extra = {"ContinuationToken": token} if token else {}
                    page = client.list_objects_v2(**kwargs, **extra)
                    yield page
                    if not page["IsTruncated"]:
                        return
                    token = page["NextContinuationToken"]

        return _Paginator()


def test_s3_discovery_lists_packages_beyond_the_first_page(monkeypatch):
    _use_s3(monkeypatch)
    client = _PagedS3(f"fam/F{index:04d}/manifest.yaml" for index in range(1500))
    monkeypatch.setattr(s, "_s3_client", lambda: client)

    candidates = s.list_remote_package_candidates("s3://phi-bucket/fam")

    assert len(candidates) == 1500
    assert candidates[-1]["name"] == "F1499"


def test_s3_discovery_finds_a_manifest_beyond_the_first_page_of_a_package(monkeypatch):
    # 1200 objects sort before manifest.yaml ("cnv/..." < "manifest.yaml").
    _use_s3(monkeypatch)
    keys = [f"fam/F1/cnv/part{index:04d}.bed" for index in range(1200)] + ["fam/F1/manifest.yaml"]
    client = _PagedS3(keys)
    monkeypatch.setattr(s, "_s3_client", lambda: client)

    (candidate,) = s.list_remote_package_candidates("s3://phi-bucket/fam")

    assert candidate["name"] == "F1"
    assert candidate["has_manifest"] is True and candidate["has_ped"] is False


def test_s3_discovery_stops_reading_a_package_once_it_has_both_markers(monkeypatch):
    # The manifest and the PED are on the first page; 3000 objects follow them.
    _use_s3(monkeypatch)
    keys = ["fam/F1/family.ped", "fam/F1/manifest.yaml"] + [f"fam/F1/snv/chunk{index:04d}.vcf.gz" for index in range(3000)]
    client = _PagedS3(keys)
    monkeypatch.setattr(s, "_s3_client", lambda: client)

    (candidate,) = s.list_remote_package_candidates("s3://phi-bucket/fam")

    assert candidate["has_manifest"] and candidate["has_ped"]
    assert client.requests.count("fam/F1/") == 1
