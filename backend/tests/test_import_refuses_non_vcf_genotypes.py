"""A genotype written as a word is refused at import (CLIN-16, the owner's decision of 2026-10-28).

VCF writes a genotype (GT) in allele notation: allele indices, ``.`` for a missing allele, ``/``
or ``|`` between them (``0/1``, ``1|0``, ``./.``, haploid ``1``). Only a malformed VCF holds a
word such as ``HET`` or ``HETEROZYGOUS``, and the small-variant loaders stored it as written.
Three helpers then read it three ways: ``classify_genotype`` as a no-call (every filter, count
and inheritance check), ``_normalize_gt`` as ``0/1`` on the variant list (``HOM`` as ``1/1``,
``REF`` as ``0/0``), and the compound-het partner check ``HET`` as het, ``HETEROZYGOUS`` not.

Now a record whose GT holds anything VCF's allele notation does not use fails the upload or
the dataset, naming the file, the line and the sample, and the value shown short: the
small-variant loader (the upload, a package's family VCF, a NIPT or mitochondrial sample's
file, imputed genotypes) and a long-read sample's SNV file. The helpers read only VCF
notation: the variant list shows a stored genotype as stored, and the compound-het check
classifies it as every filter does.

The stores are faked at their edge: the loader's lock, count, delete and insert.
"""

from __future__ import annotations

from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.services import genotypes, per_sample_small_variants, variant_upload_service
from backend.app.services.clickhouse_small_variants import SmallVariantFamilyRecord, has_affected_het_call
from backend.app.services.clickhouse_variant_queries import _normalize_gt
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.per_sample_small_variants import PerSampleVcf

_SAMPLES = ("S1", "S2")
_FAMILY = FamilyMetadataContext(
    family_uuid="family-uuid",
    family_id="F1",
    project_ids=["project-1"],
    sample_rows=[{"sample_uuid": f"uuid-{name}", "sample_id": name, "sex": "female", "role": "proband"} for name in _SAMPLES],
    sample_uuid_to_name={f"uuid-{name}": name for name in _SAMPLES},
    sample_name_to_uuid={name: f"uuid-{name}" for name in _SAMPLES},
    affected_sample_names=["S1"],
    assembly_id="assembly-1",
    assembly_name="GRCh38",
)
_SAMPLE_CONTEXTS = {
    name: SampleMetadataContext(
        sample_uuid=f"uuid-{name}",
        sample_id=name,
        family_uuid="family-uuid",
        family_id="F1",
        sex="female",
        project_ids=["project-1"],
        assembly_id="assembly-1",
        assembly_name="GRCh38",
    )
    for name in _SAMPLES
}
_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
_REFUSED = "which is not VCF's allele notation (such as 0/1, 1|0 or ./.)"


def _vcf(*s2_genotypes: str) -> str:
    """Records on chr1, S1 heterozygous in each, S2 with the genotypes given (one per record)."""
    return _HEADER + "".join(
        f"1\t{1000 + index * 10}\t.\tA\tG\t50\tPASS\t.\tGT:DP\t0/1:30\t{gt}:31\n"
        for index, gt in enumerate(s2_genotypes)
    )


class _Session:
    async def commit(self) -> None:
        return None


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The loader's stores: ``stored`` rows of the callset, and what the loader did."""
    state: dict[str, Any] = {"stored": 2, "events": [], "rows": []}

    async def lock(*_args: Any, **_kwargs: Any) -> None:
        state["events"].append("lock")

    async def count(*_args: Any, **_kwargs: Any) -> int:
        return state["stored"]

    async def delete(*_args: Any, **_kwargs: Any) -> None:
        state["events"].append("delete")

    async def insert(_assembly: Any, _family: Any, _projects: Any, records: list[Any], **_kwargs: Any) -> None:
        state["events"].append("insert")
        state["rows"].extend(records)

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    for name, value in {
        "lock_family_variant_writes": lock,
        "count_family_small_variants": count,
        "delete_family_small_variants": delete,
        "insert_small_variant_records": insert,
        "refresh_family_small_variant_summaries": nothing,
    }.items():
        monkeypatch.setattr(variant_upload_service, name, value)
    from backend.app.services import annotation_manifest_service

    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", nothing)
    return state


async def _load(text: str, *, name: str = "family.vcf", check_before_replacing: bool = True) -> dict[str, Any]:
    return await variant_upload_service.upload_family_small_variant_file(
        _Session(),  # type: ignore[arg-type]
        context=_FAMILY,
        sample_contexts=_SAMPLE_CONTEXTS,
        file=UploadFile(file=BytesIO(text.encode()), filename=name),
        overwrite=True,
        format_hint="clair3",
        check_before_replacing=check_before_replacing,
    )


# --- the notation ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gt",
    ["0/1", "1|0", "1/1", "0/0", "./.", ".", "1", "0", "1/2", "10/12", "0/0/1", "./1", "1|.", "", " 0/1 "],
)
def test_vcf_allele_notation_is_a_vcf_genotype(gt: str) -> None:
    assert genotypes.is_vcf_genotype(gt)


@pytest.mark.parametrize(
    "gt", ["HET", "HETEROZYGOUS", "HOM", "HOM_ALT", "HOMOZYGOUS", "ALT", "REF", "WT", "het", "NO_CALL", "A/G", "1/a", "0-1", "N/A"]
)
def test_a_word_is_no_vcf_genotype(gt: str) -> None:
    assert not genotypes.is_vcf_genotype(gt)
    # Read as every filter reads it, as before: a no-call.
    assert genotypes.classify_genotype(gt) == genotypes.NO_CALL


# --- the small-variant loader ----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("gt", ["HET", "HETEROZYGOUS", "HOM", "het", "A/G", "1/a"])
async def test_a_genotype_word_is_refused_before_the_upload_replaces_the_callset(stores: dict[str, Any], gt: str) -> None:
    # The third record (line 5) holds the word, for S2.
    with pytest.raises(HTTPException) as refused:
        await _load(_vcf("0/1", "0/0", gt, "1/1"))

    assert refused.value.status_code == 400
    assert refused.value.detail == f"The VCF family.vcf, line 5: the genotype (GT) of S2 is '{gt}', {_REFUSED}"
    # Read whole before the delete, as every refusal is (SAFE-25): the callset as it was.
    # Before: stored, the word as S2's genotype.
    assert stores["events"] == ["lock"]


@pytest.mark.asyncio
async def test_a_genotype_word_fails_a_package_imports_load_naming_its_file(stores: dict[str, Any]) -> None:
    # A package import reads its file once, after the delete (check_before_replacing=False):
    # the refusal fails the dataset, whose clean-up and the import's snapshot put the family
    # back, as for every record the loader refuses.
    with pytest.raises(HTTPException) as refused:
        await _load(_vcf("0/1", "HET"), name="S2.mutect2.vcf", check_before_replacing=False)

    assert refused.value.status_code == 400
    assert refused.value.detail == (
        f"The VCF S2.mutect2.vcf, line 4: the genotype (GT) of S2 is 'HET', {_REFUSED}"
    )
    # Before: stored, the word as S2's genotype.
    assert "insert" not in stores["events"]


@pytest.mark.asyncio
async def test_the_refusal_shows_an_overlong_value_short(stores: dict[str, Any]) -> None:
    word = "HETEROZYGOUS" * 40 + "\x1b[31m"
    with pytest.raises(HTTPException) as refused:
        await _load(_vcf(word))

    detail = str(refused.value.detail)
    assert word not in detail and "\x1b" not in detail
    assert detail.startswith("The VCF family.vcf, line 3: the genotype (GT) of S2 is 'HETEROZYGOUSHETER...'")
    assert len(detail) < 200


@pytest.mark.asyncio
async def test_vcf_allele_notation_is_stored_as_written(stores: dict[str, Any]) -> None:
    written = ["0/1", "1|0", "./.", ".", "1", "0/0/1", "10/12", "./1"]

    result = await _load(_vcf(*written))

    assert result["inserted"] == len(written)
    assert [call.gt for row in stores["rows"] for call in row.calls if call.sample == "S2"] == written


# --- a long-read sample's SNV file ------------------------------------------------------------


def _per_sample_vcf(path: Path, sample: str, *genotypes_written: str) -> PerSampleVcf:
    path.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000000>\n"
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        + "".join(
            f"chr1\t{100 + index * 10}\t.\tA\tG\t30\tPASS\t.\tGT:GQ\t{gt}:30\n"
            for index, gt in enumerate(genotypes_written)
        )
    )
    return PerSampleVcf(sample_id=sample, path=path)


@pytest.mark.asyncio
async def test_a_long_read_samples_genotype_word_is_refused_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    async def recorded(name: str, *_args: Any, **_kwargs: Any) -> int:
        events.append(name)
        return 0

    for name in (
        "lock_family_variant_writes",
        "count_family_small_variants",
        "delete_family_small_variants",
        "insert_small_variant_records",
        "refresh_family_small_variant_summaries",
    ):
        monkeypatch.setattr(per_sample_small_variants, name, partial(recorded, name))
    from backend.app.services import annotation_manifest_service

    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", partial(recorded, "provenance"))
    files = [
        _per_sample_vcf(tmp_path / "S1.vcf", "S1", "0/1", "1/1"),
        _per_sample_vcf(tmp_path / "S2.vcf", "S2", "HETEROZYGOUS", "0/1"),
    ]

    with pytest.raises(HTTPException) as refused:
        await per_sample_small_variants.upload_family_per_sample_small_variant_files(
            _Session(),  # type: ignore[arg-type]
            context=_FAMILY,
            sample_contexts=_SAMPLE_CONTEXTS,
            files=files,
            overwrite=True,
        )

    assert refused.value.status_code == 400
    assert refused.value.detail == (
        f"The SNV file of S2 (S2.vcf), line 4: the genotype (GT) is 'HETEROZYGOUS', {_REFUSED}"
    )
    # Each file's first record is read before anything is written. Before: stored.
    assert events == []


# --- the helpers read VCF notation only --------------------------------------------------------


def test_the_variant_list_shows_a_stored_genotype_as_stored() -> None:
    # Before: HET shown as 0/1, HOM as 1/1, REF as 0/0, which no filter read so.
    assert [_normalize_gt(gt) for gt in ("HET", "HOM", "REF", "0/1", "1|1", "", None)] == [
        "HET",
        "HOM",
        "REF",
        "0/1",
        "1|1",
        "./.",
        "./.",
    ]


def test_the_compound_het_check_reads_a_genotype_as_every_filter_does() -> None:
    def record(gt: str) -> SmallVariantFamilyRecord:
        return SmallVariantFamilyRecord(
            variant_key=None, variant_id="1-1000-A-G", gene_symbols=["GENE1"], sample_calls={"S1": gt}, annotations=[]
        )

    # Before: HET read as het, HETEROZYGOUS not.
    assert [has_affected_het_call(record(gt), ["S1"]) for gt in ("0/1", "./1", "HET", "HETEROZYGOUS", "1/1")] == [
        True,
        True,
        False,
        False,
        False,
    ]
