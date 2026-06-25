from __future__ import annotations

import pytest

from backend.app.schemas import SmallVariantReviewOut
from backend.app.services import mitochondrial_analysis
from backend.app.services.clickhouse_family_variants import SmallVariantCall, SmallVariantRecord
from backend.app.services.family_metadata_context import FamilyMetadataContext


@pytest.fixture(autouse=True)
def _stub_review_map(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """MT variants share the small-variant review store; isolate tests from PG.

    Tests that care about review attachment populate the returned dict.
    """

    reviews: dict[str, object] = {}

    async def fake_review_map(_session, *, family_uuid, variant_ids):  # noqa: ANN001
        return {vid: reviews[vid] for vid in variant_ids if vid in reviews}

    monkeypatch.setattr(mitochondrial_analysis, "get_small_variant_review_map", fake_review_map)
    return reviews


def _family_context() -> FamilyMetadataContext:
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="demo_family",
        project_ids=["project-uuid"],
        sample_rows=[
            {
                "sample_uuid": "sample-proband",
                "sample_id": "PROBAND",
                "role": "proband",
                "affected": True,
                "sex": "female",
                "sample_metadata": {"mtdna": {"haplogroup": "H1", "qc_status": "pass"}},
            },
            {
                "sample_uuid": "sample-mom",
                "sample_id": "MOM",
                "role": "mother",
                "affected": False,
                "sex": "female",
                "sample_metadata": {
                    "mtdna": {
                        "haplogroup": "H1",
                        "contamination": 0.012,
                    }
                },
            },
            {
                "sample_uuid": "sample-dad",
                "sample_id": "DAD",
                "role": "father",
                "affected": False,
                "sex": "male",
                "sample_metadata": {"mtdna": {"haplogroup": "J1"}},
            },
        ],
        sample_uuid_to_name={
            "sample-proband": "PROBAND",
            "sample-mom": "MOM",
            "sample-dad": "DAD",
        },
        sample_name_to_uuid={
            "PROBAND": "sample-proband",
            "MOM": "sample-mom",
            "DAD": "sample-dad",
        },
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def _mt_record() -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=1,
        variant_id="m.3243A>G",
        chr="MT",
        start=3243,
        end=3243,
        ref="A",
        alt="G",
        source="mutserve",
        rsid="rs199474657",
        filters=[],
        gene_symbols=["MT-TL1"],
        annotations=[
            {
                "gene": "MT-TL1",
                "consequence": "tRNA_variant",
                "mitomap_disease": "MELAS syndrome",
                "clinical_significance": "Pathogenic",
            }
        ],
        calls=[
            SmallVariantCall(sample="PROBAND", gt="0/1", gq=None, dp=420, af=[0.46], ad=[227, 193], ps=None),
            SmallVariantCall(sample="MOM", gt="0/1", gq=None, dp=390, af=[0.88], ad=[47, 343], ps=None),
            SmallVariantCall(sample="DAD", gt="0/0", gq=None, dp=310, af=[0.0], ad=[310, 0], ps=None),
        ],
    )


@pytest.mark.asyncio
async def test_family_mitochondrial_analysis_summarizes_maternal_mt_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_mt_records(_context):
        return [_mt_record()]

    async def fake_coverage_by_sample(_context):
        return {
            "PROBAND": mitochondrial_analysis.MitoDNACoverageOut(
                mean_depth=418.5,
                min_depth=400,
                max_depth=450,
                breadth=0.98,
                source="coverage",
                regions=4,
            )
        }

    monkeypatch.setattr(mitochondrial_analysis, "_fetch_mt_records", fake_fetch_mt_records)
    monkeypatch.setattr(mitochondrial_analysis, "_coverage_by_sample", fake_coverage_by_sample)

    response = await mitochondrial_analysis.get_family_mitochondrial_analysis_response(
        None,  # type: ignore[arg-type]
        context=_family_context(),
    )

    assert [sample.sample_id for sample in response.samples] == ["PROBAND", "MOM", "DAD"]
    assert response.samples[0].haplogroup == "H1"
    assert response.samples[1].qc.status == "warning"
    assert response.samples[1].qc.contamination == 0.012
    assert response.variants[0].label == "m.3243A>G"
    assert response.variants[0].annotation.gene == "MT-TL1"
    assert response.variants[0].annotation.disorders == ["MELAS syndrome"]
    assert response.variants[0].annotation.clinical_significance == "pathogenic"
    assert response.variants[0].annotation.consequence_terms == ["trna_variant"]
    assert response.variants[0].annotation.gnomad_af is None
    assert response.variants[0].review is None
    assert response.variants[0].annotation.mitomap_query == "m.3243A>G"
    assert response.variants[0].annotation.mitomap_url.endswith("/cgi-bin/search_allele?variant=3243A%3EG")
    assert response.variants[0].maternal_transmission == "maternal_shared"
    assert response.variants[0].calls["PROBAND"].zygosity == "heteroplasmic"
    assert response.variants[0].calls["PROBAND"].display == "46.0%"
    assert response.variants[0].calls["MOM"].zygosity == "heteroplasmic"
    assert response.variants[0].calls["DAD"].zygosity == "reference"
    # The full response also carries the presence summary.
    assert response.variant_count == 1
    assert response.has_coverage is True


@pytest.mark.asyncio
async def test_family_mitochondrial_analysis_surfaces_frequency_consequence_and_review(
    monkeypatch: pytest.MonkeyPatch,
    _stub_review_map: dict[str, object],
) -> None:
    common_synonymous = SmallVariantRecord(
        variant_key=2,
        variant_id="m.8860A>G",
        chr="MT",
        start=8860,
        end=8860,
        ref="A",
        alt="G",
        source="mutserve",
        rsid="rs2001031",
        filters=[],
        gene_symbols=["MT-ATP6"],
        annotations=[
            {
                "gene": "MT-ATP6",
                # VEP-style joined effect terms should split into tokens.
                "effect": "synonymous_variant&NMD_transcript_variant",
                "gnomad_af": 0.97,
            }
        ],
        calls=[
            SmallVariantCall(sample="PROBAND", gt="1/1", gq=None, dp=500, af=[0.99], ad=[1, 499], ps=None),
        ],
    )

    async def fake_fetch_mt_records(_context):
        return [common_synonymous]

    async def fake_coverage_by_sample(_context):
        return {}

    monkeypatch.setattr(mitochondrial_analysis, "_fetch_mt_records", fake_fetch_mt_records)
    monkeypatch.setattr(mitochondrial_analysis, "_coverage_by_sample", fake_coverage_by_sample)

    review = SmallVariantReviewOut(
        variant_id="m.8860A>G",
        classification="acmg_class_1",
        tags=["acmg_class_1"],
    )
    _stub_review_map["m.8860A>G"] = review

    response = await mitochondrial_analysis.get_family_mitochondrial_analysis_response(
        None,  # type: ignore[arg-type]
        context=_family_context(),
    )

    variant = response.variants[0]
    assert variant.annotation.gnomad_af == 0.97
    assert variant.annotation.consequence_terms == ["synonymous_variant", "nmd_transcript_variant"]
    assert variant.review is not None
    assert variant.review.classification == "acmg_class_1"


@pytest.mark.asyncio
async def test_family_mitochondrial_analysis_count_only_skips_variant_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_mt_records(_context):
        return [_mt_record()]

    async def fake_has_mt_coverage(_context):
        return False

    monkeypatch.setattr(mitochondrial_analysis, "_fetch_mt_records", fake_fetch_mt_records)
    # No coverage track here: presence is established from variant-call read depth,
    # so the count-only path must not need the full coverage scan at all.
    monkeypatch.setattr(mitochondrial_analysis, "_has_mt_coverage", fake_has_mt_coverage)

    response = await mitochondrial_analysis.get_family_mitochondrial_analysis_response(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        count_only=True,
    )

    assert response.variant_count == 1
    assert response.has_coverage is True
    # The heavy per-variant / per-sample payload is omitted in count_only mode.
    assert response.variants == []
    assert response.samples == []
    assert response.qc_notes == []


@pytest.mark.asyncio
async def test_family_mitochondrial_analysis_count_only_coverage_without_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_mt_records(_context):
        return []  # no chrM variants

    async def fake_has_mt_coverage(_context):
        return True  # a coverage track exists even though no variants were called

    monkeypatch.setattr(mitochondrial_analysis, "_fetch_mt_records", fake_fetch_mt_records)
    monkeypatch.setattr(mitochondrial_analysis, "_has_mt_coverage", fake_has_mt_coverage)

    response = await mitochondrial_analysis.get_family_mitochondrial_analysis_response(
        None,  # type: ignore[arg-type]
        context=_family_context(),
        count_only=True,
    )

    # Coverage-only families still register as present via has_coverage.
    assert response.variant_count == 0
    assert response.has_coverage is True
