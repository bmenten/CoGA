"""Pinned GRCh38 deployment, atomic cache and read-only-runtime GCS publishing.

Every HTTP response, genome and bucket here is synthetic. HTSlib independently
checks the generated index and its random-access sequence results.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse

import pysam
import pytest

from app import reference_fasta as ref


@pytest.fixture
def genome(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    fasta = source / ref.FASTA_NAME
    fasta.write_bytes(b">chr1 synthetic\nACGT\nNN\n>chr2\nTGCA\nA\n")
    pysam.faidx(str(fasta))
    artifacts = {path.name: ref.file_identity(path) for path in (fasta, Path(f"{fasta}.fai"))}
    compressed = gzip.compress(fasta.read_bytes(), mtime=0)
    archive = tmp_path / "official.fa.gz"
    archive.write_bytes(compressed)
    monkeypatch.setattr(ref, "SOURCE_SHA256", hashlib.sha256(compressed).hexdigest())
    monkeypatch.setattr(ref, "ARTIFACTS", artifacts)
    monkeypatch.setattr(ref, "AUTOSOME_LENGTHS", (6, 5))
    calls = []

    def open_archive(request):
        calls.append(request)
        assert request.full_url == ref.SOURCE_URL
        assert request.get_header("Authorization") is None
        return io.BytesIO(compressed)

    monkeypatch.setattr(ref, "_open", open_archive)
    return archive, tmp_path / "cache", calls


def test_complete_download_index_and_repeat_deploy_reuse(genome):
    archive, root, calls = genome
    fasta = ref.prepare_local(root)
    expected = archive.parent / "source" / f"{ref.FASTA_NAME}.fai"
    assert Path(f"{fasta}.fai").read_bytes() == expected.read_bytes()
    with pysam.FastaFile(str(fasta)) as indexed:
        assert indexed.fetch("chr1", 2, 6) == "GTNN"
        assert indexed.fetch("chr2", 1, 5) == "GCAA"
    assert json.loads((fasta.parent / ref.MANIFEST_NAME).read_text()) == ref.provenance()
    assert ref.prepare_local(root) == fasta
    assert len(calls) == 1
    assert not list(fasta.parent.parent.glob(".prepare-*"))


def test_offline_pinned_archive_needs_no_network(genome):
    archive, root, calls = genome
    assert ref.prepare_local(root, archive).is_file()
    assert not calls


@pytest.mark.parametrize("changed", ["fasta", "index", "manifest"])
def test_corrupted_existing_cache_is_refused_without_overwrite(genome, changed):
    _, root, calls = genome
    fasta = ref.prepare_local(root)
    target = {"fasta": fasta, "index": Path(f"{fasta}.fai"), "manifest": fasta.parent / ref.MANIFEST_NAME}[changed]
    target.write_text('{}' if changed == "manifest" else 'corrupted')
    with pytest.raises(ref.ReferenceFastaError, match="checksum|provenance"):
        ref.prepare_local(root)
    assert target.read_text() in {'{}', 'corrupted'}
    assert len(calls) == 1


@pytest.mark.parametrize("contents,match", [(b"changed", "pinned SHA"), (b"", "pinned SHA")])
def test_wrong_download_never_publishes_a_directory(genome, monkeypatch, contents, match):
    _, root, _ = genome
    monkeypatch.setattr(ref, "_open", lambda request: io.BytesIO(contents))
    with pytest.raises(ref.ReferenceFastaError, match=match):
        ref.prepare_local(root)
    assert not (root / ref.RELATIVE_DIRECTORY).exists()
    assert not list((root / ref.RELATIVE_DIRECTORY).parent.glob('.prepare-*'))


@pytest.mark.parametrize("limit", ["MAX_COMPRESSED", "MAX_UNCOMPRESSED"])
def test_transfer_and_decompression_caps_never_publish(genome, monkeypatch, limit):
    _, root, _ = genome
    monkeypatch.setattr(ref, limit, 1)
    with pytest.raises(ref.ReferenceFastaError, match="size|pinned"):
        ref.prepare_local(root)
    assert not (root / ref.RELATIVE_DIRECTORY).exists()


def test_download_has_an_overall_deadline(genome, monkeypatch):
    _, root, _ = genome
    times = iter([0, 1801])
    monkeypatch.setattr(ref.time, "monotonic", lambda: next(times))
    with pytest.raises(ref.ReferenceFastaError, match="time limit"):
        ref.prepare_local(root)


@pytest.mark.parametrize("truncated", [True, False])
def test_invalid_or_truncated_gzip_never_publishes(genome, monkeypatch, truncated):
    archive, root, _ = genome
    contents = archive.read_bytes()[:-4] if truncated else b"invalid gzip"
    archive.write_bytes(contents)
    monkeypatch.setattr(ref, "SOURCE_SHA256", hashlib.sha256(contents).hexdigest())
    with pytest.raises((EOFError, gzip.BadGzipFile)):
        ref.prepare_local(root, archive)
    assert not (root / ref.RELATIVE_DIRECTORY).exists()


@pytest.mark.parametrize("contents,reason", [
    (b"", "no complete"), (b"ACGT\n", "invalid sequence line"),
    (b">chr1\n", "no complete"), (b">chr1\n>chr2\nACGT\n", "empty sequence"),
    (b">\nACGT\n", "invalid sequence name"), (b">chr1/invalid\nACGT\n", "invalid sequence name"),
    (b">chr1\nACGT\n>chr1\nACGT\n", "duplicate"),
    (b">chr1\nACGT\nA\nACGT\n", "line widths"), (b">chr1\nA\nACGT\n", "line widths"),
    (b">chr1\nACGT\n\n", "invalid sequence line"), (b">chr1\nX\n", "invalid sequence line"),
    (b">chr1\nACGT\n", "GRCh38 autosomes"),
    (b">chr1\n" + b"A" * ref.CHUNK, "oversized line"),
])
def test_malformed_or_wrong_assembly_is_not_indexed(tmp_path, contents, reason):
    fasta = tmp_path / "wrong.fa"
    fasta.write_bytes(contents)
    with pytest.raises(ref.ReferenceFastaError, match=reason):
        ref.index_fasta(fasta)
    assert not Path(f"{fasta}.fai").exists()


def test_crlf_index_matches_htslib(tmp_path, monkeypatch):
    monkeypatch.setattr(ref, "AUTOSOME_LENGTHS", (6,))
    fasta = tmp_path / "crlf.fa"
    fasta.write_bytes(b">chr1 description\r\nACGT\r\nNN\r\n")
    generated = ref.index_fasta(fasta).read_bytes()
    Path(f"{fasta}.fai").unlink()
    pysam.faidx(str(fasta))
    assert Path(f"{fasta}.fai").read_bytes() == generated


def test_reference_requests_never_redirect():
    with pytest.raises(ref.ReferenceFastaError, match="redirect"):
        ref._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://example.org")


class SyntheticBucket:
    def __init__(self, monkeypatch, archive):
        self.objects = {}
        self.uploads = []
        self.downloads = 0
        self.failure = None
        self.http_failure = None
        self.archive = archive
        monkeypatch.setattr(ref, "_open", self.open)
        monkeypatch.setattr(ref.subprocess, "run", self.run)

    def run(self, command, **kwargs):
        assert kwargs.get("capture_output") is True
        assert command[-1] in {"--project=synthetic-project", "--quiet"}
        if command[1:3] == ["auth", "print-access-token"]:
            return subprocess.CompletedProcess(command, 0, "synthetic-access", "")
        assert command[1:3] == ["storage", "cp"]
        assert "--if-generation-match=0" in command
        source, target = Path(command[3]), command[4]
        name = unquote(target.rsplit('/', 1)[1])
        self.uploads.append(name)
        if name == self.failure or name in self.objects:
            return subprocess.CompletedProcess(command, 1, b"", b"synthetic failure")
        self.objects[name] = source.read_bytes()
        return subprocess.CompletedProcess(command, 0, b"", b"")

    def open(self, request):
        if request.full_url == ref.SOURCE_URL:
            self.downloads += 1
            assert request.get_header("Authorization") is None
            return io.BytesIO(self.archive.read_bytes())
        assert request.get_header("Authorization") == "Bearer synthetic-access"
        assert urlparse(request.full_url).hostname == "storage.googleapis.com"
        if self.http_failure:
            raise HTTPError(request.full_url, self.http_failure, 'synthetic HTTP failure', {}, None)
        name = unquote(urlparse(request.full_url).path.split('/o/', 1)[1]).rsplit('/', 1)[1]
        if name not in self.objects:
            raise HTTPError(request.full_url, 404, 'not found', {}, None)
        value = self.objects[name]
        if urlparse(request.full_url).query:
            return io.BytesIO(value)
        return io.BytesIO(json.dumps({"md5Hash": base64.b64encode(hashlib.md5(value).digest()).decode(), "size": str(len(value))}).encode())


def test_gcs_cold_and_cached_deployment_use_no_runtime_write_permissions(genome, monkeypatch):
    archive, _, _ = genome
    bucket = SyntheticBucket(monkeypatch, archive)
    monkeypatch.setattr(sys, 'argv', ['reference_fasta.py', '--gcs-bucket=synthetic-refdata', '--project=synthetic-project'])
    ref.main()
    assert bucket.uploads == [*ref.ARTIFACTS, ref.MANIFEST_NAME]
    assert bucket.downloads == 1
    assert ref.GcsPublisher('synthetic-refdata', 'synthetic-project').complete()
    ref.main()
    assert bucket.downloads == 1
    assert len(bucket.uploads) == 3


def test_partial_gcs_upload_resumes_without_publishing_a_manifest_early(genome, monkeypatch):
    archive, root, _ = genome
    directory = ref.prepare_local(root, archive).parent
    bucket = SyntheticBucket(monkeypatch, archive)
    bucket.failure = f"{ref.FASTA_NAME}.fai"
    publisher = ref.GcsPublisher('synthetic-refdata', 'synthetic-project')
    with pytest.raises(ref.ReferenceFastaError, match="Publishing"):
        publisher.publish(directory)
    assert ref.MANIFEST_NAME not in bucket.objects
    assert not publisher.complete()
    bucket.failure = None
    publisher.publish(directory)
    assert publisher.complete()
    assert bucket.uploads.count(ref.FASTA_NAME) == 1


@pytest.mark.parametrize('changed', ['fasta', 'manifest'])
def test_gcs_corruption_blocks_the_next_rollout(genome, monkeypatch, changed):
    archive, root, _ = genome
    directory = ref.prepare_local(root, archive).parent
    bucket = SyntheticBucket(monkeypatch, archive)
    publisher = ref.GcsPublisher('synthetic-refdata', 'synthetic-project')
    publisher.publish(directory)
    bucket.objects[ref.FASTA_NAME if changed == 'fasta' else ref.MANIFEST_NAME] = b'{}'
    with pytest.raises(ref.ReferenceFastaError, match="unexpected contents|inconsistent"):
        publisher.complete()
    assert bucket.downloads == 0


def test_gcs_denied_access_is_not_treated_as_missing_data(genome, monkeypatch):
    archive, _, _ = genome
    bucket = SyntheticBucket(monkeypatch, archive)
    bucket.http_failure = 403
    with pytest.raises(ref.ReferenceFastaError, match="deploy account"):
        ref.GcsPublisher('synthetic-refdata', 'synthetic-project').complete()


@pytest.mark.parametrize('bucket,project', [('https://example.org', 'synthetic-project'), ('synthetic-refdata', '')])
def test_gcs_requires_a_bucket_name_and_project(bucket, project):
    with pytest.raises(ref.ReferenceFastaError, match="required"):
        ref.GcsPublisher(bucket, project)


def test_cli_prepares_the_local_reference_and_reports_fixed_failure(genome, monkeypatch, capsys):
    _, root, _ = genome
    monkeypatch.setattr(sys, 'argv', ['reference_fasta.py', '--directory', str(root)])
    ref.main()
    assert 'FASTA and index are ready' in capsys.readouterr().out
    monkeypatch.setattr(ref, 'prepare_local', lambda *_args: (_ for _ in ()).throw(OSError('synthetic private error')))
    with pytest.raises(SystemExit, match='1'):
        ref.main()
    message = capsys.readouterr().err
    assert 'OSError' in message and 'synthetic private error' not in message


def test_deployed_reference_resolves_pgs_ref_and_supports_gt_scoring(genome):
    from app.services.pgs_scoring import parse_weights, score_file, sha256_file

    _, root, _ = genome
    fasta = ref.prepare_local(root)
    weights, provenance = parse_weights(
        '#pgs_id=PGS900011\n#HmPOS_build=GRCh38\n#variants_number=1\n'
        'hm_chr\thm_pos\teffect_allele\tother_allele\teffect_weight\n1\t3\tG\tA\t0.5\n',
        'PGS900011', str(fasta))
    assert weights[0].ref == 'G'
    assert provenance['fasta_sha256'] == ref.ARTIFACTS[ref.FASTA_NAME]['sha256']
    vcf = root / 'synthetic.vcf'
    vcf.write_text('##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\n'
                   '1\t3\t.\tG\tA\t.\tPASS\t.\tGT\t0/1\n')
    result = score_file(vcf, sha256_file(vcf), {'synthetic': weights}, ['PROBAND'], assume_ref=True)[0]
    assert result['raw_score'] == 0.5 and result['qc']['called'] == 1


def test_a_killed_preparers_staging_is_cleaned_without_following_symlinks(genome):
    _, root, _ = genome
    parent = (root / ref.RELATIVE_DIRECTORY).parent
    parent.mkdir(parents=True)
    orphan = parent / '.prepare-orphan'
    orphan.mkdir()
    (orphan / 'partial.fa').write_text('synthetic partial data')
    outside = root / 'independent-reference'
    outside.mkdir()
    (outside / 'keep.fa').write_text('synthetic independent data')
    link = parent / '.prepare-symlink'
    link.symlink_to(outside, target_is_directory=True)
    assert ref.prepare_local(root).is_file()
    assert not orphan.exists() and not link.is_symlink()
    assert (outside / 'keep.fa').read_text() == 'synthetic independent data'
