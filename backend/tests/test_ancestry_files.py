"""VCF files a package declares for the ancestry PCA without importing them: what a scan keeps of
a VCF or gVCF at the reference markers, and what a manifest declares. All values are synthetic."""
import gzip
import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.app.services import ancestry_callsets as callsets
from backend.app.services import ancestry_files as files
from backend.app.services.ancestry_projection import AncestryInputError
from backend.tests.test_ancestry_callsets import N, member, reference

HEADER = "##fileformat=VCFv4.2\n##reference=GRCh38\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\n"


def gvcf(tmp_path: Path, body: str, *, header: str = HEADER, compressed: bool = False) -> Path:
    path = tmp_path / ("PROBAND.g.vcf.gz" if compressed else "PROBAND.g.vcf")
    data = (header + body).encode()
    path.write_bytes(gzip.compress(data) if compressed else data)
    return path


BODY = (
    # A reference block over markers 1-50, a block of no-calls over 51-53, another reference
    # block over 54-59, a variant at 60 with the gVCF's <NON_REF>, a block that failed its
    # filter over 61-100, and a record off the markers.
    "chr1\t1\t.\tA\t<NON_REF>\t.\t.\tEND=50\tGT:DP\t0/0:30\n"
    "chr1\t51\t.\tA\t<NON_REF>\t.\t.\tEND=53\tGT\t./.\n"
    "chr1\t54\t.\tA\t<NON_REF>\t.\t.\tEND=59\tGT\t0/0\n"
    "chr1\t60\t.\tA\tG,<NON_REF>\t50\tPASS\t.\tGT\t0/1\n"
    "chr1\t61\t.\tA\t<*>\t.\tLowQual\tEND=100\tGT\t0/0\n"
    "chr2\t5\t.\tC\tT\t50\tPASS\t.\tGT\t0/1\n"
)


@pytest.mark.parametrize("compressed", [False, True])
def test_a_gvcf_keeps_its_calls_at_the_markers_reference_blocks_as_reference_calls(tmp_path, compressed):
    path = gvcf(tmp_path, BODY, compressed=compressed)
    scan = files.scan_vcf_markers(path, reference())
    assert scan["columns"] == ["PROBAND"]
    assert scan["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest() and scan["size"] == path.stat().st_size
    rows = {row[1]: row for row in scan["rows"]}
    assert sorted(rows) == list(range(1, N + 1))
    # A passing block: the marker's own alleles, the block's genotype, read as PASS.
    assert rows[1] == ["1", 1, "A", "G", ["PASS"], ["0/0"], [[]]]
    assert rows[52][5] == ["./."] and rows[55][5] == ["0/0"]
    # A variant record keeps its alleles without <NON_REF>.
    assert rows[60] == ["1", 60, "A", "G", ["PASS"], ["0/1"], [[]]]
    # A block that failed its filter keeps it.
    assert rows[80][4] == ["LowQual"]


def test_a_scanned_gvcf_is_read_as_a_complete_callset(tmp_path):
    model = reference()
    scan = files.scan_vcf_markers(gvcf(tmp_path, BODY), model)
    callset = "file:0f8fad5b-d9cb-469f-a165-70867728950e"
    rows = [callsets.MarkerRow(callset, c, p, r, a, tuple(f), tuple(scan["columns"]), tuple(g), tuple(tuple(x) for x in cf))
            for c, p, r, a, f, g, cf in scan["rows"]]
    reading = callsets.read_callsets(rows, model, ["PROBAND"], labels={callset: "VCF file: gVCF"})["PROBAND"][callset]
    qc = reading.scan.qc
    assert reading.scan.kind == "complete" and reading.scan.label == "VCF file: gVCF"
    # Explicit genotypes at 97 of the 100 markers: no absence to read; the failed block is out.
    assert (qc["called"], qc["no_call"], qc["filtered"], qc["assumed_ref"]) == (57, 3, 40, 0)
    assert np.array_equal(np.flatnonzero(reading.usable), [*range(50), *range(53, 60)])


def test_a_vcfs_own_ft_and_several_columns_are_kept(tmp_path):
    header = HEADER.replace("\tPROBAND\n", "\tPROBAND\tMOTHER\n")
    path = gvcf(tmp_path, "1\t7\t.\tA\tG\t50\tPASS\t.\tGT:FT\t0/1:PASS\t1/1:LowGQ\n", header=header)
    scan = files.scan_vcf_markers(path, reference())
    assert scan["columns"] == ["PROBAND", "MOTHER"]
    assert scan["rows"] == [["1", 7, "A", "G", ["PASS"], ["0/1", "1/1"], [["PASS"], ["LowGQ"]]]]


@pytest.mark.parametrize("header,body,compressed", [
    (HEADER.replace("GRCh38", "GRCh37"), BODY, False),
    ("##fileformat=VCFv4.2\n", BODY, False),
    (HEADER, "1\tnot-a-position\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n", False),
])
def test_a_file_that_is_no_grch38_vcf_is_refused_with_a_fixed_reason(tmp_path, header, body, compressed):
    with pytest.raises(AncestryInputError) as refused:
        files.scan_vcf_markers(gvcf(tmp_path, body, header=header, compressed=compressed), reference())
    assert str(refused.value) == files.SCAN_FAILED


def test_a_gzip_cut_short_or_a_missing_file_is_refused(tmp_path):
    path = gvcf(tmp_path, BODY * 50, compressed=True)
    path.write_bytes(path.read_bytes()[:-30])
    with pytest.raises(AncestryInputError):
        files.scan_vcf_markers(path, reference())
    with pytest.raises(AncestryInputError):
        files.scan_vcf_markers(tmp_path / "absent.g.vcf", reference())


def test_a_manifest_declares_files_by_path_with_an_optional_label():
    bundle = SimpleNamespace(manifest=SimpleNamespace(metadata={"ancestry": {"vcfs": [
        "ancestry/PROBAND.g.vcf.gz",
        {"path": "MOTHER.g.vcf.gz", "label": "Mother\x00 gVCF"},
        {"path": "  "}, {"label": "no path"}, 7,
    ]}}))
    assert files.declared_files(bundle) == [
        {"path": "ancestry/PROBAND.g.vcf.gz", "label": "PROBAND.g.vcf.gz"},
        {"path": "MOTHER.g.vcf.gz", "label": "Mother gVCF"},
    ]
    many = SimpleNamespace(manifest=SimpleNamespace(metadata={"ancestry": {"vcfs": [f"{i}.vcf" for i in range(20)]}}))
    assert len(files.declared_files(many)) == files.MAX_DECLARED
    assert files.declared_files(SimpleNamespace(manifest=SimpleNamespace(metadata={}))) == []


def test_a_declared_file_comes_after_direct_calls_and_before_the_others():
    model = reference()
    file_callset = "file:0f8fad5b-d9cb-469f-a165-70867728950e"
    rows = []
    for callset in ("glimpse2", "nipt", file_callset, "clair3"):
        rows += [callsets.MarkerRow(callset, "1", i + 1, "A", "G", ("PASS",), ("PROBAND",), ("0/0",), ((),))
                 for i in range(N)]
    result = callsets.project_family(rows, model, [member("PROBAND")])[0]
    assert [c.callset for c in result.candidates] == ["clair3", file_callset, "nipt", "glimpse2"]
    without_direct = [r for r in rows if r.callset != "clair3"]
    assert callsets.project_family(without_direct, model, [member("PROBAND")])[0].callset == file_callset
