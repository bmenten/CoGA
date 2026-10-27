"""An uploaded CNV keeps its copy number, and two events on one position stay two SVs, each read
in any order (CLIN-39).

* **Copy number.** The Sniffles and Spectre uploads ignored FORMAT/CN, so a CNV loaded that way
  named no direction: it counted as a possible deletion in the second-hit badge and ranked as a
  loss, whatever it called. The call now keeps its copy number, which gives a CNV its change
  (``sv_copy_change``).
* **HiFiCNV ids.** The HiFiCNV reader took a record's ID as its SV id, where every other reader
  names a call by its coordinates. It is now named by its coordinates, type and source, the ID
  kept in its annotation, and one event in two samples' VCFs is one SV with both calls: two
  records under one id kept only one of them.
* **The discriminator.** NeedlR gave the first of two events on one position, type and mate
  the id and the other its length as a suffix, by line order: a file read in another order, or
  the samples' files in another order, swapped the ids, and a review moved to the other event.
  The order is now the events' (``event_discriminators``). An upload had no discriminator: two
  insertions on one position were one SV with the first one's length and two calls of the
  sample. They are now two SVs, the one this source already stores under the id keeping it.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import UploadFile
import pytest

from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import annotation_manifest_service, family_package_datasets, variant_upload_service
from backend.app.services.clickhouse_variant_ids import event_discriminators
from backend.app.services.clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import (
    FamilyPackageBundle,
    ManifestDataset,
    PackageManifest,
    ParsedPed,
    PedMember,
)
from backend.app.services.family_package_variants import (
    _iter_cnv_structural_records,
    _iter_needlr_per_sample_records,
    _iter_needlr_structural_records,
)
from backend.app.services.structural_variant_ingest import iter_structural_variant_records
from backend.app.services.sv_copy_change import copy_change

_SAMPLES = {"S1": "uuid-s1", "S2": "uuid-s2"}


# --- Copy number ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record_format", "header", "line"),
    [
        pytest.param(
            "sniffles",
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n",
            "chr1\t20000\tCNV1\tN\t<CNV>\t30\tPASS\tSVTYPE=CNV;SVLEN=5000;END=25000\tGT:CN\t0/1:{cn}\n",
            id="sniffles",
        ),
        pytest.param(
            "spectre",
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n",
            "chr1\t20000\tSpectre.DUP.1\tN\t<CNV>\t.\tPASS\tSVTYPE=CNV;END=25000\tGT:CN\t0/1:{cn}\n",
            id="spectre",
        ),
    ],
)
@pytest.mark.parametrize(("copies", "change"), [(3, "gain"), (1, "loss"), (2, None)])
def test_an_uploaded_cnv_keeps_its_copy_number_and_names_its_direction(
    record_format: str, header: str, line: str, copies: int, change: str | None
) -> None:
    (parsed,) = iter_structural_variant_records(
        "##fileformat=VCFv4.2\n" + header + line.format(cn=copies), record_format  # type: ignore[arg-type]
    )
    call = variant_upload_service._structural_record_call("S1", parsed)

    # Before: no copy number, so every CNV read as a possible loss.
    assert (parsed.copy_number, call.copy_number) == (copies, copies)
    assert copy_change("CNV", [(call.sample, call.gt, call.copy_number)]) == change


# --- HiFiCNV ids ----------------------------------------------------------------------------------

_HIFICNV = (
    "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
    "chr1\t1000\tHiFiCNV.DUP.1\tN\t<DUP>\t20\tPASS\tSVTYPE=DUP;END=9000\tGT:CN\t0/1:{cn}\n"
)


def test_a_hificnv_call_is_named_by_its_coordinates_its_record_id_kept() -> None:
    (record,) = _iter_cnv_structural_records(_HIFICNV.format(cn=3), sample_id="S1")

    # Before: HiFiCNV.DUP.1, which the caller numbers per run.
    assert record.variant_id == "1-1000-9000-DUP----hificnv"
    assert record.annotations[0]["id"] == "HiFiCNV.DUP.1"


def _hificnv_job(tmp_path: Path) -> family_package_datasets.DatasetImportJob:
    for sample, copies in (("S1", 3), ("S2", 4)):
        (tmp_path / f"{sample}.vcf").write_text(_HIFICNV.format(cn=copies), encoding="utf-8")
    dataset = ManifestDataset(per_sample={"S1": {"vcf": "S1.vcf"}, "S2": {"vcf": "S2.vcf"}})
    family = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_uuid": uuid, "sample_id": name, "sex": "female"} for name, uuid in _SAMPLES.items()],
        sample_uuid_to_name={uuid: name for name, uuid in _SAMPLES.items()},
        sample_name_to_uuid=dict(_SAMPLES),
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    return family_package_datasets.DatasetImportJob(
        session=None,  # type: ignore[arg-type]
        bundle=FamilyPackageBundle(
            root=tmp_path,
            manifest_path=tmp_path / "manifest.yaml",
            manifest=PackageManifest(ped="family.ped", datasets={"cnv": dataset}),
            ped_path=tmp_path / "family.ped",
            ped=ParsedPed(family_ids=["FAM"], members=[], sample_ids=list(_SAMPLES), text=""),
        ),
        dataset=dataset,
        summary=FamilyImportDatasetSummary(dataset_type="cnv", status="valid"),
        family_context=family,
        sample_contexts={
            name: SampleMetadataContext(
                sample_uuid=uuid,
                sample_id=name,
                family_uuid="family-uuid",
                family_id="FAM",
                sex="female",
                project_ids=["project-uuid"],
                assembly_id="assembly-uuid",
                assembly_name="GRCh38",
            )
            for name, uuid in _SAMPLES.items()
        },
        conflict_mode="overwrite",
    )


@pytest.mark.asyncio
async def test_one_event_in_two_samples_hificnv_vcfs_is_one_sv_with_both_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replaced: list[list[StructuralVariantRecord]] = []

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def known(*_args: Any, **_kwargs: Any) -> set[str]:
        return set(_SAMPLES)

    async def replace(_assembly, _family, _projects, records, *, source=None) -> None:
        replaced.append(list(records))

    monkeypatch.setattr(family_package_datasets, "known_vcf_sample_ids", known)
    monkeypatch.setattr(family_package_datasets, "lock_family_variant_writes", nothing)
    monkeypatch.setattr(family_package_datasets, "_update_sv_file_metadata", nothing)
    monkeypatch.setattr(family_package_datasets, "replace_family_structural_variants", replace)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)

    await family_package_datasets.DATASET_IMPORTERS["cnv"](_hificnv_job(tmp_path))

    # Before: two records under the caller's ID, of which the store kept one.
    [[record]] = replaced
    assert record.variant_id == "1-1000-9000-DUP----hificnv"
    assert [(call.sample, call.copy_number) for call in record.calls] == [("S1", 3), ("S2", 4)]


# --- The discriminator ----------------------------------------------------------------------------

_NEEDLR_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
_QUERY = "Query_ID={sample}_sv_phased;Genotype=0/1;Alt_Reads=8"


def _insertion(sequence: str, *, sample: str = "S1") -> str:
    info = f"SVTYPE=INS;SVLEN={len(sequence)};" + _QUERY.format(sample=sample)
    return f"chr1\t20000\t.\tA\tA{sequence}\t.\t.\t{info}\n"


def _ped() -> ParsedPed:
    members = [
        PedMember(family_id="FAM", iid=name, pid="0", mid="0", sex="2", phen="0", line_no=index, clinical_status="unknown")
        for index, name in enumerate(_SAMPLES, 1)
    ]
    return ParsedPed(family_ids=["FAM"], members=members, sample_ids=list(_SAMPLES), text="")


def _contexts() -> dict[str, SampleMetadataContext]:
    return {
        name: SampleMetadataContext(
            sample_uuid=uuid,
            sample_id=name,
            family_uuid="family-uuid",
            family_id="FAM",
            sex="female",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for name, uuid in _SAMPLES.items()
    }


def _ids_by_alt(records: list[StructuralVariantRecord]) -> dict[str, str]:
    return {record.annotations[0]["alt"]: record.variant_id for record in records}


_SHORT, _LONG, _LONG_OTHER = "CGT", "CGTACGT", "TTTACGT"
_EXPECTED_IDS = {
    "A" + _SHORT: "1-20000-20000-INS---",
    "A" + _LONG: "1-20000-20000-INS----7",
    "A" + _LONG_OTHER: "1-20000-20000-INS----7.2",
}


def test_a_needlr_files_order_does_not_move_an_id_to_another_event() -> None:
    lines = [_insertion(_LONG), _insertion(_SHORT), _insertion(_LONG_OTHER)]

    in_order = _iter_needlr_structural_records(_NEEDLR_HEADER + "".join(lines), ped=_ped(), sample_contexts=_contexts())
    reversed_ = _iter_needlr_structural_records(
        _NEEDLR_HEADER + "".join(reversed(lines)), ped=_ped(), sample_contexts=_contexts()
    )

    # The shortest keeps the id, then each other its length. Before: the first line's kept it,
    # so reading the file the other way round gave the long insertion the plain id.
    assert _ids_by_alt(in_order) == _ids_by_alt(reversed_) == _EXPECTED_IDS


def test_the_samples_files_order_does_not_move_an_id_to_another_event() -> None:
    s1 = _NEEDLR_HEADER + _insertion(_LONG, sample="S1")
    s2 = _NEEDLR_HEADER + _insertion(_SHORT, sample="S2")

    one = _iter_needlr_per_sample_records([("S1", s1), ("S2", s2)], ped=_ped(), sample_contexts=_contexts())
    other = _iter_needlr_per_sample_records([("S2", s2), ("S1", s1)], ped=_ped(), sample_contexts=_contexts())

    # Before: S1's long insertion kept the id when S1's file came first, S2's short one when it did.
    assert _ids_by_alt(one) == _ids_by_alt(other) == {
        "A" + _SHORT: "1-20000-20000-INS---",
        "A" + _LONG: "1-20000-20000-INS----7",
    }


def test_the_sv_already_stored_under_an_id_keeps_it() -> None:
    events = [("id", ("A", "A" + _SHORT, 3)), ("id", ("A", "A" + _LONG, 7))]

    assert event_discriminators(events) == {events[0]: None, events[1]: "7"}
    # The 7 bp insertion is stored under the id: it keeps it, and the 3 bp one takes a suffix.
    assert event_discriminators(events, plain_lengths={"id": 7}) == {events[0]: "3", events[1]: None}
    # Another SV of another length is stored under it: both take a suffix.
    assert event_discriminators(events, plain_lengths={"id": 5}) == {events[0]: "3", events[1]: "7"}


class _Store:
    def __init__(self, rows: list[StoredStructuralVariantRow]) -> None:
        self.rows = rows
        self.written: list[StructuralVariantRecord] = []

    async def fetch(self, *_args: Any, source: str | None = None, **_kwargs: Any) -> list[StoredStructuralVariantRow]:
        return list(self.rows)

    async def rewrite(self, _assembly, _family, rows, *, source=None, stored=()) -> None:
        self.written = [row.record for row in rows]


class _Result:
    def scalar_one_or_none(self) -> None:
        return None


class _Session:
    async def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result()

    async def commit(self) -> None:
        return None


async def _upload(monkeypatch: pytest.MonkeyPatch, store: _Store, sample: str, *lines: str) -> dict[str, Any]:
    async def nothing(*_args: Any, **_kwargs: Any) -> Any:
        return None

    async def known(*_args: Any, **_kwargs: Any) -> set[str]:
        return set(_SAMPLES)

    async def no_genes(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    monkeypatch.setattr(variant_upload_service, "fetch_family_structural_variant_rows", store.fetch)
    monkeypatch.setattr(variant_upload_service, "rewrite_family_structural_variants", store.rewrite)
    monkeypatch.setattr(variant_upload_service, "known_vcf_sample_ids", known)
    monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", no_genes)
    monkeypatch.setattr(variant_upload_service, "lock_family_variant_writes", nothing)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
    header = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
    body = header + "".join(lines)
    return await variant_upload_service.upload_structural_variant_file(
        _Session(),  # type: ignore[arg-type]
        family_context=FamilyMetadataContext(
            family_uuid="family-uuid",
            family_id="FAM",
            project_ids=["project-uuid"],
            sample_rows=[],
            sample_uuid_to_name={uuid: name for name, uuid in _SAMPLES.items()},
            sample_name_to_uuid=dict(_SAMPLES),
            affected_sample_names=[],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        ),
        sample_context=_contexts()[sample],
        file=UploadFile(file=BytesIO(body.encode()), filename="calls.vcf"),
        overwrite=True,
        format_hint="sniffles",
    )


def _uploaded_insertion(sequence: str, gt: str = "0/1") -> str:
    return f"chr1\t20000\tS.{len(sequence)}\tA\tA{sequence}\t40\tPASS\tSVTYPE=INS;SVLEN={len(sequence)}\tGT\t{gt}\n"


@pytest.mark.asyncio
async def test_two_insertions_on_one_position_in_one_upload_are_two_svs(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store([])

    result = await _upload(monkeypatch, store, "S1", _uploaded_insertion(_LONG), _uploaded_insertion(_SHORT, "1/1"))

    # Before: one SV with the first one's length (7) and both calls of S1.
    assert result["created"] == 2
    assert sorted((record.variant_id, record.sv_len, [call.gt for call in record.calls]) for record in store.written) == [
        ("1-20000-20000-INS---", 3, ["1/1"]),
        ("1-20000-20000-INS----7", 7, ["0/1"]),
    ]


@pytest.mark.asyncio
async def test_a_later_samples_upload_attaches_its_call_to_the_sv_of_its_length(monkeypatch: pytest.MonkeyPatch) -> None:
    def stored(variant_id: str, length: int) -> StoredStructuralVariantRow:
        return StoredStructuralVariantRow(
            project_id="project-uuid",
            record=StructuralVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr="1",
                start=20000,
                end=20000,
                sv_type="INS",
                source="sniffles",
                remote_chr=None,
                remote_start=None,
                remote_end=None,
                sv_len=length,
                filters=[],
                gene_symbols=[],
                annotations=[],
                calls=[StructuralVariantCall(sample="S1", gt="0/1", qual=None, read_support=None, filter=None)],
            ),
        )

    store = _Store([stored("1-20000-20000-INS---", 3), stored("1-20000-20000-INS----7", 7)])

    # S2's file holds the 7 bp insertion alone: by its own file's order it would take the id.
    await _upload(monkeypatch, store, "S2", _uploaded_insertion(_LONG))

    calls = {record.variant_id: sorted(call.sample for call in record.calls) for record in store.written}
    assert calls == {"1-20000-20000-INS---": ["S1"], "1-20000-20000-INS----7": ["S1", "S2"]}


@pytest.mark.asyncio
async def test_the_same_event_twice_in_one_file_is_one_call_of_the_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store([])

    await _upload(monkeypatch, store, "S1", _uploaded_insertion(_LONG, "0/1"), _uploaded_insertion(_LONG, "1/1"))

    # Before: two calls of S1 on one SV.
    [record] = store.written
    assert [(call.sample, call.gt) for call in record.calls] == [("S1", "1/1")]
