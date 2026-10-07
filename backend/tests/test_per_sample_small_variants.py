"""The long-read pipeline's per-sample callsets read side by side as one callset.

nf-core/lrsvar calls and annotates each sample on its own (DeepVariant, VEP) and writes no
joint VCF, so a couple screened for carriership arrives as one VCF per partner. The
import reads the files in step and stores each site once, with the call of every sample
whose file has a record there (``per_sample_small_variants``). All samples and values
here are synthetic.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

from fastapi import HTTPException
import pytest

from backend.app.services import per_sample_small_variants as per_sample
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.per_sample_small_variants import (
    PerSampleMergeError,
    PerSampleVcf,
    _ContigOrder,
    _PerSampleVcfReader,
    iter_merged_sites,
    merged_site_record,
)

_CSQ = (
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. '
    'Format: Allele|Consequence|IMPACT|SYMBOL|Gene|MAX_AF">\n'
)
_FILTERS = (
    '##FILTER=<ID=PASS,Description="All filters passed">\n'
    '##FILTER=<ID=RefCall,Description="Genotyping model thinks this site is reference.">\n'
    '##FILTER=<ID=NoCall,Description="Site has depth=0 resulting in no call.">\n'
)
_CONTIGS = "##contig=<ID=chr1,length=1000000>\n##contig=<ID=chr2,length=1000000>\n##contig=<ID=chrX,length=1000000>\n"


def _vcf(column: str, records: list[str], *, contigs: str = _CONTIGS) -> str:
    return (
        "##fileformat=VCFv4.2\n##DeepVariant_version=1.10.0\n"
        + _FILTERS
        + contigs
        + _CSQ
        + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        + f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
        + "".join(f"{record}\n" for record in records)
    )


def _record(chrom: str, pos: int, ref: str, alt: str, filt: str, gt: str, *, qual: str = "30", gene: str = "GENE1") -> str:
    csq = f"CSQ={alt}|missense_variant|MODERATE|{gene}|ENSG1|0.0001"
    return f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t{qual}\t{filt}\t{csq}\tGT:GQ:DP:AD:VAF:PS\t{gt}:30:20:10,10:0.5:{pos}"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as handle:
        handle.write(text)
    return path


def _readers(files: list[tuple[str, Path]], *, excluded: set[str] = frozenset({"RefCall", "NoCall"})) -> list[_PerSampleVcfReader]:
    order = _ContigOrder()
    readers = []
    for index, (sample_id, path) in enumerate(files):
        reader = _PerSampleVcfReader(index, PerSampleVcf(sample_id=sample_id, path=path), excluded_filters=set(excluded))
        reader.read_header()
        order.add_header(reader.contigs, label=reader.label)
        readers.append(reader)
    for reader in readers:
        reader.start(order)
    return readers


def _merged(readers: list[_PerSampleVcfReader]) -> list[Any]:
    return [
        merged_site_record(group, readers)
        for group in iter_merged_sites(readers)
        if not all(record.excluded for record in group)
    ]


def _calls(record: Any) -> dict[str, tuple[str, list[str]]]:
    return {call.sample: (call.gt, call.filters) for call in record.calls}


def test_a_site_both_partners_carry_is_one_row_holding_both_calls(tmp_path: Path) -> None:
    mother = _write(tmp_path / "MOTHER1.vcf.gz", _vcf("MOTHER1_run", [
        _record("chr1", 100, "A", "G", "PASS", "0/1"),
        _record("chr1", 200, "C", "T", "PASS", "1/1"),
    ]))
    father = _write(tmp_path / "FATHER1.vcf.gz", _vcf("FATHER1_run", [
        _record("chr1", 100, "A", "G", "PASS", "0|1"),
        _record("chr2", 50, "G", "A", "PASS", "0/1"),
    ]))
    records = _merged(_readers([("MOTHER1", mother), ("FATHER1", father)]))

    assert [(record.chr, record.start, record.ref, record.alt) for record in records] == [
        ("1", 100, "A", "G"),
        ("1", 200, "C", "T"),
        ("2", 50, "G", "A"),
    ]
    assert _calls(records[0]) == {"MOTHER1": ("0/1", ["PASS"]), "FATHER1": ("0|1", ["PASS"])}
    # A private variant holds its own sample's call only: the other file has no record.
    assert _calls(records[1]) == {"MOTHER1": ("1/1", ["PASS"])}
    assert _calls(records[2]) == {"FATHER1": ("0/1", ["PASS"])}
    assert all(record.source == "clair3" for record in records)
    assert records[0].annotations and records[0].annotations[0].get("gene") == "GENE1"


def test_reference_and_no_call_records_are_kept_only_where_the_other_sample_has_a_variant(tmp_path: Path) -> None:
    mother = _write(tmp_path / "MOTHER1.vcf.gz", _vcf("MOTHER1", [
        _record("chr1", 100, "A", "G", "PASS", "0/1", qual="40"),
        _record("chr1", 150, "T", "C", "RefCall", "0/0", qual="1"),
        _record("chr1", 300, "G", "C", "PASS", "0/1"),
    ]))
    father = _write(tmp_path / "FATHER1.vcf.gz", _vcf("FATHER1", [
        _record("chr1", 100, "A", "G", "RefCall", "0/0", qual="2"),
        _record("chr1", 150, "T", "C", "NoCall", "./.", qual="0"),
        _record("chr1", 300, "G", "C", "NoCall", "./.", qual="0"),
    ]))
    records = _merged(_readers([("MOTHER1", mother), ("FATHER1", father)]))

    # chr1:150 is reference in one file and uncalled in the other: no variant, no row.
    assert [record.start for record in records] == [100, 300]
    # The father's RefCall says the caller read him as reference there; his NoCall that
    # it could not call him. Each is kept as his call, with its own FILTER.
    assert _calls(records[0]) == {"MOTHER1": ("0/1", ["PASS"]), "FATHER1": ("0/0", ["RefCall"])}
    assert _calls(records[1]) == {"MOTHER1": ("0/1", ["PASS"]), "FATHER1": ("./.", ["NoCall"])}
    # The row's FILTER is every record's; its QUAL the kept record's, not the RefCall's.
    assert records[0].filters == ["PASS", "RefCall"]
    assert records[0].qual == 40.0
    assert {call.sample: call.metrics.get("QUAL") for call in records[0].calls} == {"MOTHER1": 40.0, "FATHER1": 2.0}


def test_two_records_of_one_position_with_other_alleles_are_two_sites(tmp_path: Path) -> None:
    mother = _write(tmp_path / "MOTHER1.vcf.gz", _vcf("MOTHER1", [_record("chr1", 100, "A", "G,T", "PASS", "1/2")]))
    father = _write(tmp_path / "FATHER1.vcf.gz", _vcf("FATHER1", [_record("chr1", 100, "A", "G", "PASS", "0/1")]))
    records = _merged(_readers([("MOTHER1", mother), ("FATHER1", father)]))

    assert [(record.alt, sorted(_calls(record))) for record in records] == [("G,T", ["MOTHER1"]), ("G", ["FATHER1"])]


def test_records_of_one_position_in_another_order_still_meet(tmp_path: Path) -> None:
    mother = _write(tmp_path / "MOTHER1.vcf.gz", _vcf("MOTHER1", [
        _record("chr1", 100, "A", "G", "PASS", "0/1"),
        _record("chr1", 100, "A", "AT", "PASS", "0/1"),
    ]))
    father = _write(tmp_path / "FATHER1.vcf.gz", _vcf("FATHER1", [
        _record("chr1", 100, "A", "AT", "PASS", "0/1"),
        _record("chr1", 100, "A", "G", "PASS", "1/1"),
    ]))
    records = _merged(_readers([("MOTHER1", mother), ("FATHER1", father)]))

    assert {record.alt: _calls(record) for record in records} == {
        "G": {"MOTHER1": ("0/1", ["PASS"]), "FATHER1": ("1/1", ["PASS"])},
        "AT": {"MOTHER1": ("0/1", ["PASS"]), "FATHER1": ("0/1", ["PASS"])},
    }


def test_the_files_are_read_in_the_order_of_their_contig_lines(tmp_path: Path) -> None:
    # chrX before chr2 in both headers: a string or numeric order would misplace it.
    contigs = "##contig=<ID=chr1>\n##contig=<ID=chrX>\n##contig=<ID=chr2>\n"
    mother = _write(tmp_path / "MOTHER1.vcf.gz", _vcf("MOTHER1", [
        _record("chr1", 5, "A", "G", "PASS", "0/1"),
        _record("chrX", 10, "C", "T", "PASS", "0/1"),
        _record("chr2", 7, "G", "A", "PASS", "0/1"),
    ], contigs=contigs))
    father = _write(tmp_path / "FATHER1.vcf.gz", _vcf("FATHER1", [
        _record("chrX", 10, "C", "T", "PASS", "1/1"),
        _record("chr2", 7, "G", "A", "PASS", "0/1"),
    ], contigs=contigs))
    records = _merged(_readers([("MOTHER1", mother), ("FATHER1", father)]))

    assert [(record.chr, record.start, sorted(_calls(record))) for record in records] == [
        ("1", 5, ["MOTHER1"]),
        ("X", 10, ["FATHER1", "MOTHER1"]),
        ("2", 7, ["FATHER1", "MOTHER1"]),
    ]


def test_a_contig_only_one_header_lists_takes_its_place_in_that_files_order() -> None:
    order = _ContigOrder()
    order.add_header(["1", "2", "X"], label="first")
    order.add_header(["1", "2", "2_KI270715v1_random", "X"], label="second")
    assert sorted(["X", "2_KI270715v1_random", "1", "2"], key=order.rank) == ["1", "2", "2_KI270715v1_random", "X"]


def test_files_whose_headers_order_their_contigs_differently_are_refused() -> None:
    order = _ContigOrder()
    order.add_header(["1", "2", "X"], label="first")
    with pytest.raises(PerSampleMergeError, match="another order"):
        order.add_header(["X", "1", "2"], label="second")


def test_an_unsorted_file_is_refused(tmp_path: Path) -> None:
    mother = _write(tmp_path / "MOTHER1.vcf.gz", _vcf("MOTHER1", [
        _record("chr1", 200, "A", "G", "PASS", "0/1"),
        _record("chr1", 100, "C", "T", "PASS", "0/1"),
    ]))
    father = _write(tmp_path / "FATHER1.vcf.gz", _vcf("FATHER1", [_record("chr1", 150, "A", "G", "PASS", "0/1")]))
    with pytest.raises(PerSampleMergeError, match="not sorted"):
        _merged(_readers([("MOTHER1", mother), ("FATHER1", father)]))


# --------------------------------------------------------------------------- #
# The upload: one callset, replacing the family's primary callset as a whole
# --------------------------------------------------------------------------- #


def _context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="COUPLE1",
        project_ids=["p1"],
        sample_rows=[],
        sample_uuid_to_name={"mother1-uuid": "MOTHER1", "father1-uuid": "FATHER1"},
        sample_name_to_uuid={"MOTHER1": "mother1-uuid", "FATHER1": "father1-uuid"},
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _sample_contexts() -> dict[str, SampleMetadataContext]:
    return {
        name: SampleMetadataContext(
            sample_uuid=f"{name.lower()}-uuid",
            sample_id=name,
            family_uuid="family-uuid",
            family_id="COUPLE1",
            sex="und",
            project_ids=["p1"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name in ("MOTHER1", "FATHER1")
    }


class _FakeSession:
    async def commit(self) -> None:
        return None


@pytest.fixture()
def storage(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"existing": 0, "stored_samples": set(), "inserted": [], "deleted": [], "events": []}

    async def count(_assembly, _family, *, project_ids=None, source=None):
        state["events"].append("count")
        return state["existing"]

    async def stored_samples(_assembly, _family, *, source):
        return set(state["stored_samples"])

    async def insert(_assembly, _family, _projects, records, **_kwargs):
        state["events"].append("insert")
        state["inserted"].extend(records)

    async def delete(_assembly, _family, *, source=None):
        state["events"].append("delete")
        state["deleted"].append(source)

    async def no_op(*_args, **_kwargs):
        return None

    async def provenance(*_args, **_kwargs):
        return None

    for name, fn in {
        "count_family_small_variants": count,
        "family_small_variant_call_samples": stored_samples,
        "insert_small_variant_records": insert,
        "delete_family_small_variants": delete,
        "refresh_family_small_variant_summaries": no_op,
        "lock_family_variant_writes": no_op,
    }.items():
        monkeypatch.setattr(per_sample, name, fn)
    import backend.app.services.annotation_manifest_service as manifest_service

    monkeypatch.setattr(manifest_service, "merge_vcf_header_provenance", provenance)
    return state


async def _upload(files: list[PerSampleVcf], **kwargs: Any) -> dict[str, Any]:
    return await per_sample.upload_family_per_sample_small_variant_files(
        _FakeSession(),  # type: ignore[arg-type]
        context=_context(),
        sample_contexts=_sample_contexts(),
        files=files,
        overwrite=True,
        **kwargs,
    )


def _couple_files(tmp_path: Path) -> list[PerSampleVcf]:
    mother = _write(tmp_path / "MOTHER1.vcf.gz", _vcf("MOTHER1_3500_4000", [
        _record("chr1", 100, "A", "G", "PASS", "0/1"),
        _record("chr1", 120, "T", "C", "RefCall", "0/0"),
        _record("chrX", 500, "G", "A", "PASS", "0/1", gene="XGENE1"),
    ]))
    father = _write(tmp_path / "FATHER1.vcf.gz", _vcf("FATHER1_3500_4000", [
        _record("chr1", 100, "A", "G", "PASS", "0/1"),
        _record("chr1", 120, "T", "C", "NoCall", "./."),
    ]))
    return [PerSampleVcf("MOTHER1", mother), PerSampleVcf("FATHER1", father)]


@pytest.mark.asyncio
async def test_the_upload_stores_the_merged_sites_as_the_primary_callset(storage, tmp_path: Path) -> None:
    result = await _upload(_couple_files(tmp_path), exclude_filters=["RefCall", "NoCall"])

    assert [(record.start, sorted(_calls(record))) for record in storage["inserted"]] == [
        (100, ["FATHER1", "MOTHER1"]),
        (500, ["MOTHER1"]),
    ]
    assert result["inserted"] == 2
    assert result["processed"] == 5
    assert result["skipped_filtered"] == 2
    assert result["source_format"] == "clair3"
    assert result["calls_per_sample"] == {"MOTHER1": 2, "FATHER1": 1}
    # Nothing was stored before: nothing to delete.
    assert storage["deleted"] == []


@pytest.mark.asyncio
async def test_the_upload_replaces_the_stored_callset_of_the_same_samples(storage, tmp_path: Path) -> None:
    storage["existing"] = 10
    storage["stored_samples"] = {"MOTHER1", "father1-uuid"}
    await _upload(_couple_files(tmp_path), exclude_filters=["RefCall", "NoCall"])
    assert storage["deleted"] == ["clair3"]
    assert storage["events"].index("delete") < storage["events"].index("insert")


@pytest.mark.asyncio
async def test_a_stored_sample_the_upload_leaves_out_is_refused_before_anything_is_deleted(
    storage, tmp_path: Path
) -> None:
    # Re-importing one partner's file would drop the other's calls without a word.
    storage["existing"] = 10
    storage["stored_samples"] = {"MOTHER1", "FATHER1"}
    with pytest.raises(HTTPException) as raised:
        await _upload(_couple_files(tmp_path)[:1], exclude_filters=["RefCall", "NoCall"])
    assert raised.value.status_code == 409
    assert "FATHER1" in str(raised.value.detail)
    assert storage["deleted"] == [] and storage["inserted"] == []


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_read_with_the_others_fails_before_any_write(storage, tmp_path: Path) -> None:
    files = _couple_files(tmp_path)
    _write(files[1].path, _vcf("FATHER1", [_record("chr1", 100, "A", "G", "PASS", "0/1")], contigs="##contig=<ID=chrX>\n##contig=<ID=chr1>\n"))
    with pytest.raises(HTTPException) as raised:
        await _upload(files)
    assert raised.value.status_code == 400
    assert storage["events"] == []


@pytest.mark.asyncio
async def test_a_failure_after_the_first_rows_removes_the_callset_again(storage, tmp_path: Path, monkeypatch) -> None:
    async def failing_refresh(*_args, **_kwargs):
        raise RuntimeError("ClickHouse went away")

    monkeypatch.setattr(per_sample, "refresh_family_small_variant_summaries", failing_refresh)
    with pytest.raises(RuntimeError, match="went away"):
        await _upload(_couple_files(tmp_path))
    assert storage["deleted"] == ["clair3"]
