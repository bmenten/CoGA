"""The family's mtDNA workspace (``mitochondrial_analysis``).

The response lists each member's haplogroup, mtDNA QC verdict (against the lab's configured
limits) and coverage, and each chrM variant with its annotation, its review, each member's
heteroplasmy and its maternal transmission, read along the proband's maternal line from the
parents the pedigree links to the proband (a mother without data, or none linked, is "not
assessed"); a ClinVar record of conflicting classifications reads uncertain, never pathogenic;
the chrM structural variants with each sample's heteroplasmy; and a count-only presence. The
coverage it reads (one track of read depth per sample) is in test_coverage_readers_read_depth.py.
All samples and values are synthetic.
"""

from __future__ import annotations

import pytest

from backend.app.schemas import MitoDNACoverageOut, SmallVariantReviewOut
from backend.app.services import mitochondrial_analysis
from backend.app.services.clickhouse_variant_records import (
    SmallVariantCall,
    SmallVariantRecord,
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext


@pytest.fixture(autouse=True)
def _no_mt_structural_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    """The response also reads the chrM SVs from ClickHouse; these tests have none."""

    async def fake_fetch_mt_structural_variants(_context):
        return []

    monkeypatch.setattr(
        mitochondrial_analysis, "_fetch_mt_structural_variants", fake_fetch_mt_structural_variants
    )


@pytest.fixture(autouse=True)
def _stub_review_map(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """MT variants share the small-variant review store; isolate tests from PG.

    Tests that care about review attachment populate the returned dict.
    """

    reviews: dict[str, object] = {}

    async def fake_review_map(_session, *, family_uuid, variant_ids):
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
        # The pedigree links, as loaded with the family: they, not the roles, name the parents.
        relationship_rows=[
            _parent_link("DAD", "PROBAND", "father"),
            _parent_link("MOM", "PROBAND", "mother"),
        ],
    )


def _parent_link(parent: str, child: str, role: str) -> dict[str, str]:
    return {
        "relationship_type": "parent_child",
        "sample_id_a": parent,
        "sample_id_b": child,
        "role_a": role,
        "role_b": "child",
    }


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

    async def fake_coverage_by_sample(_session, _context):
        return {
            "PROBAND": mitochondrial_analysis.MitoDNACoverageOut(
                mean_depth=418.5,
                min_depth=400,
                max_depth=450,
                breadth=0.98,
                source="coverage",
                regions=4,
            )
        }, []

    monkeypatch.setattr(mitochondrial_analysis, "_fetch_mt_records", fake_fetch_mt_records)
    monkeypatch.setattr(mitochondrial_analysis, "_coverage_by_sample", fake_coverage_by_sample)

    # The mtDNA cut-offs are admin configuration, not constants in this module. Stand in
    # for the profile a lab would have configured.
    async def fake_thresholds(_session, *, family_uuid):
        return {
            "profile_key": "default",
            "profile_label": "Default",
            "thresholds": {
                "mtdna.contamination": {"warn_value": 0.01, "error_value": 0.03},
                "mtdna.mean_depth": {"warn_value": 50.0, "error_value": 10.0},
            },
        }

    monkeypatch.setattr(mitochondrial_analysis, "resolve_family_qc_thresholds", fake_thresholds)

    response = await mitochondrial_analysis.get_family_mitochondrial_analysis_response(
        None,  # type: ignore[arg-type]
        context=_family_context(),
    )

    assert [sample.sample_id for sample in response.samples] == ["PROBAND", "MOM", "DAD"]
    assert response.samples[0].haplogroup == "H1"
    assert response.samples[1].qc.status == "warn"
    assert response.samples[1].qc.contamination == 0.012
    # The note names the configured limit, so a reviewer can see what it was judged against.
    assert any("0.01" in note for note in response.samples[1].qc.notes)
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

    async def fake_coverage_by_sample(_session, _context):
        return {}, []

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

    async def fake_has_mt_coverage(_session, _context):
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

    async def fake_has_mt_coverage(_session, _context):
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


# "Conflicting classifications of pathogenicity" contains "pathogenic": matched as a substring
# it was read as pathogenic, so the mtDNA table said Pathogenic and the mt ACMG evaluator
# suggested PP5. A conflicting record is neither pathogenic nor benign.
@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        ({"clinvar": "Conflicting_classifications_of_pathogenicity"}, "uncertain"),
        ({"ClinVar_CLNSIG": "Conflicting_interpretations_of_pathogenicity"}, "uncertain"),
        ({"clinvar": "Conflicting_classifications_of_pathogenicity", "mitomap_status": "Cfrm"}, "uncertain"),
        ({"clinical_significance": "Pathogenic"}, "pathogenic"),
        ({"clinvar": "Likely_pathogenic"}, "likely_pathogenic"),
        ({"clinvar": "Benign"}, "benign"),
        ({"clinvar": "Uncertain_significance"}, "uncertain"),
        ({"mitomap_status": "Cfrm"}, "reported"),
        ({}, "unknown"),
        # The other statuses the reader tells apart, and a key in other capitals.
        ({"clinvar": "Likely_benign"}, "likely_benign"),
        ({"CLINVAR": "Benign"}, "benign"),
        ({"mitomap_status": "Polymorphism"}, "polymorphism"),
        ({"status": "common"}, "polymorphism"),
        ({"classification": "VUS"}, "uncertain"),
        ({"variant_status": "Confirmed"}, "reported"),
        ({"variant_status": "unclassified"}, "unknown"),
    ],
)
def test_clinical_significance_reads_a_conflicting_record_as_uncertain(
    annotation: dict[str, str], expected: str
) -> None:
    assert mitochondrial_analysis._clinical_significance(annotation) == expected


def _three_generation_context() -> FamilyMetadataContext:
    """A proband, a brother, the parents and the paternal grandparents, with the roles a PED
    import gives them: everyone who has a child is a "father" or a "mother"."""
    rows = [
        ("PROBAND", "proband", "female", True),
        ("SIB", "sibling", "male", False),
        ("MOM", "mother", "female", False),
        ("DAD", "father", "male", False),
        ("PGM", "mother", "female", False),
        ("PGF", "father", "male", False),
    ]
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="three_generations",
        project_ids=["project-uuid"],
        sample_rows=[
            {
                "sample_uuid": f"uuid-{sample_id}",
                "sample_id": sample_id,
                "role": role,
                "affected": affected,
                "sex": sex,
                "sample_metadata": {},
            }
            for sample_id, role, sex, affected in rows
        ],
        sample_uuid_to_name={f"uuid-{sample_id}": sample_id for sample_id, *_ in rows},
        sample_name_to_uuid={sample_id: f"uuid-{sample_id}" for sample_id, *_ in rows},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
        relationship_rows=[
            _parent_link("DAD", "PROBAND", "father"),
            _parent_link("MOM", "PROBAND", "mother"),
            _parent_link("DAD", "SIB", "father"),
            _parent_link("MOM", "SIB", "mother"),
            _parent_link("PGF", "DAD", "father"),
            _parent_link("PGM", "DAD", "mother"),
        ],
    )


async def _response(monkeypatch: pytest.MonkeyPatch, context: FamilyMetadataContext, records):
    """The response over ``records``, with no coverage track and no QC limit configured."""

    async def fake_fetch_mt_records(_context):
        return records

    async def fake_coverage_by_sample(_session, _context):
        return {}, []

    async def fake_thresholds(_session, *, family_uuid):
        return {"profile_key": "default", "profile_label": "Default", "thresholds": {}}

    monkeypatch.setattr(mitochondrial_analysis, "_fetch_mt_records", fake_fetch_mt_records)
    monkeypatch.setattr(mitochondrial_analysis, "_coverage_by_sample", fake_coverage_by_sample)
    monkeypatch.setattr(mitochondrial_analysis, "resolve_family_qc_thresholds", fake_thresholds)
    return await mitochondrial_analysis.get_family_mitochondrial_analysis_response(
        None,  # type: ignore[arg-type]
        context=context,
    )


# The transmission is read along the proband's maternal line, with the parents the pedigree
# links to the proband. By role, the paternal grandmother was a "mother": a variant she and the
# proband carry read as maternally transmitted, and the mt ACMG evaluator could offer PP1.
@pytest.mark.parametrize(
    ("carriers", "expected"),
    [
        ({"PROBAND", "PGM"}, "maternal_not_observed"),
        ({"PROBAND", "DAD", "PGM"}, "maternal_not_observed"),
        # Nor is the paternal grandfather the father.
        ({"PGF"}, "family_private"),
        ({"PROBAND", "MOM"}, "maternal_shared"),
        # A child of the same mother shares her mtDNA.
        ({"SIB", "MOM"}, "maternal_shared"),
        ({"MOM"}, "maternal_only"),
        ({"DAD"}, "father_only"),
    ],
)
@pytest.mark.asyncio
async def test_maternal_transmission_follows_the_mother_the_pedigree_links_to_the_proband(
    monkeypatch: pytest.MonkeyPatch, carriers: set[str], expected: str
) -> None:
    context = _three_generation_context()
    record = _mt_record()
    record.calls = [
        SmallVariantCall(sample=sample_id, gt="0/1", gq=None, dp=400, af=[0.4], ad=[240, 160], ps=None)
        if sample_id in carriers
        else SmallVariantCall(sample=sample_id, gt="0/0", gq=None, dp=400, af=[0.0], ad=[400, 0], ps=None)
        for sample_id in context.sample_name_to_uuid
    ]

    assert await _transmissions(monkeypatch, context, [record]) == [expected]


@pytest.mark.asyncio
async def test_mtdna_response_lists_chrm_structural_variants_with_each_samples_heteroplasmy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chrM SV callset was found but never imported or shown: a large heteroplasmic
    deletion went unseen."""
    context = _three_generation_context()
    deletion = StructuralVariantRecord(
        variant_key=1,
        variant_id="SV-M-8470-13447-DEL",
        chr="M",
        start=8470,
        end=13447,
        sv_type="DEL",
        source="mito_sv",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=-4977,
        filters=[],
        gene_symbols=["MT-ATP8", "MT-ND5"],
        annotations=[
            {"source": "mito_sv", "sample": "MOM", "heteroplasmy": 0.05},
            {"source": "mito_sv", "sample": "PROBAND", "heteroplasmy": 0.35},
        ],
        calls=[
            StructuralVariantCall(sample="MOM", gt="0/1", qual=60.0, read_support=20, filter=None),
            StructuralVariantCall(sample="PROBAND", gt="0/1", qual=60.0, read_support=140, filter=None),
        ],
    )

    async def fake_fetch_mt_structural_variants(_context):
        return [deletion]

    monkeypatch.setattr(
        mitochondrial_analysis, "_fetch_mt_structural_variants", fake_fetch_mt_structural_variants
    )

    response = await _response(monkeypatch, context, [])

    [sv] = response.structural_variants
    assert (sv.sv_type, sv.start, sv.end, sv.length) == ("DEL", 8470, 13447, 4977)
    assert sv.genes == ["MT-ATP8", "MT-ND5"]
    assert {sample: call.heteroplasmy for sample, call in sv.calls.items()} == {"MOM": 0.05, "PROBAND": 0.35}
    assert sv.calls["PROBAND"].role == "proband"
    assert sv.calls["PROBAND"].read_support == 140


async def _transmissions(monkeypatch: pytest.MonkeyPatch, context, records) -> list[str]:
    response = await _response(monkeypatch, context, records)
    return [variant.maternal_transmission for variant in response.variants]


def _call(sample_id: str, gt: str, *, dp: int | None = 400) -> SmallVariantCall:
    if gt == "0/1":
        return SmallVariantCall(sample=sample_id, gt=gt, gq=None, dp=dp, af=[0.4], ad=[240, 160], ps=None)
    if gt == "0/0":
        return SmallVariantCall(sample=sample_id, gt=gt, gq=None, dp=dp, af=[0.0], ad=[400, 0], ps=None)
    return SmallVariantCall(sample=sample_id, gt=gt, gq=None, dp=dp, af=[], ad=[], ps=None)


# "Not seen in mother" read every proband variant when no mother was there to look at.
@pytest.mark.asyncio
async def test_proband_variant_without_a_linked_mother_is_not_assessed(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _three_generation_context()
    # A father-child duo: the pedigree links no mother to the proband.
    context.relationship_rows = [row for row in context.relationship_rows if "MOM" not in str(row)]
    record = _mt_record()
    record.calls = [_call("PROBAND", "0/1"), _call("DAD", "0/0")]

    assert await _transmissions(monkeypatch, context, [record]) == ["mother_not_assessed"]


@pytest.mark.asyncio
async def test_proband_variant_with_an_unsequenced_mother_is_not_assessed(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _three_generation_context()
    record = _mt_record()
    # MOM is linked but has no mtDNA data at all: no calls, no coverage.
    record.calls = [_call("PROBAND", "0/1"), _call("DAD", "0/0")]

    assert await _transmissions(monkeypatch, context, [record]) == ["mother_not_assessed"]


@pytest.mark.asyncio
async def test_mother_no_call_at_the_site_is_not_assessed_but_her_absence_elsewhere_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _three_generation_context()
    other = _mt_record()
    other.variant_id, other.start = "M-100-A-G", 100
    other.calls = [_call("PROBAND", "0/1"), _call("MOM", "0/1")]
    no_call_site = _mt_record()
    no_call_site.calls = [_call("PROBAND", "0/1"), _call("MOM", "./.", dp=None)]
    absent_site = _mt_record()
    absent_site.variant_id, absent_site.start = "M-9000-C-T", 9000
    # Sequenced (she has depth on the other site) but no call here: not seen in her.
    absent_site.calls = [_call("PROBAND", "0/1")]

    transmissions = await _transmissions(monkeypatch, context, [other, no_call_site, absent_site])

    assert sorted(transmissions) == sorted(["maternal_shared", "mother_not_assessed", "maternal_not_observed"])


@pytest.mark.asyncio
async def test_couple_without_a_proband_has_no_maternal_line(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _three_generation_context()
    context.sample_rows = [
        {**row, "role": "sibling"} for row in context.sample_rows if row["sample_id"] in {"MOM", "DAD"}
    ]
    context.relationship_rows = []
    record = _mt_record()
    record.calls = [_call("MOM", "0/1"), _call("DAD", "0/0")]

    assert await _transmissions(monkeypatch, context, [record]) == ["no_proband"]


@pytest.mark.asyncio
async def test_a_variant_nobody_carries_has_no_transmission_to_read(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _three_generation_context()
    record = _mt_record()
    record.calls = [_call(sample_id, "0/0") for sample_id in ("PROBAND", "MOM", "DAD")]

    assert await _transmissions(monkeypatch, context, [record]) == ["no_alt_calls"]


def test_an_sv_without_a_length_is_as_long_as_its_span() -> None:
    record = StructuralVariantRecord(
        variant_key=1,
        variant_id="SV-M-8470-13447-DEL",
        chr="M",
        start=8470,
        end=13447,
        sv_type="DEL",
        source="mito_sv",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=None,
        filters=[],
        gene_symbols=[],
        annotations=[{"sample": "PROBAND", "heteroplasmy": "0.35"}, {"heteroplasmy": 0.9}],
        calls=[StructuralVariantCall(sample="PROBAND", gt="0/1", qual=None, read_support=None, filter=None)],
    )

    sv = mitochondrial_analysis._structural_variant_out(record, member_by_sample={})

    assert sv.length == 4977
    # A heteroplasmy is read for the sample its annotation names, never for none.
    assert {sample: call.heteroplasmy for sample, call in sv.calls.items()} == {"PROBAND": 0.35}


# --- each call's heteroplasmy -------------------------------------------------------------------
# HOMOPLASMY_THRESHOLD is 0.95 and HETEROPLASMY_THRESHOLD 0.02: the class at and either side of
# each, from the call's allele fraction, else from its allele depths, else from its genotype.


@pytest.mark.parametrize(
    ("call", "zygosity", "display"),
    [
        (SmallVariantCall(sample="S", gt="0/1", gq=None, dp=400, af=[0.95], ad=[], ps=None), "homoplasmic", "95.0%"),
        (SmallVariantCall(sample="S", gt="0/1", gq=None, dp=400, af=[0.949], ad=[], ps=None), "heteroplasmic", "94.9%"),
        (SmallVariantCall(sample="S", gt="0/1", gq=None, dp=400, af=[0.02], ad=[], ps=None), "heteroplasmic", "2.0%"),
        (SmallVariantCall(sample="S", gt="0/1", gq=None, dp=400, af=[0.019], ad=[], ps=None), "low_level", "1.9%"),
        (SmallVariantCall(sample="S", gt="0/0", gq=None, dp=400, af=[0.0], ad=[], ps=None), "reference", "0.0%"),
        # The highest fraction of a multi-allelic call.
        (SmallVariantCall(sample="S", gt="1/2", gq=None, dp=400, af=[0.3, 0.6], ad=[], ps=None), "heteroplasmic", "60.0%"),
        # No fraction: the alternate reads over the depth, the depth the reads' sum without DP.
        (SmallVariantCall(sample="S", gt="0/1", gq=None, dp=None, af=[], ad=[90, 10], ps=None), "heteroplasmic", "10.0%"),
        (SmallVariantCall(sample="S", gt="0/1", gq=None, dp=200, af=[], ad=[100, 100], ps=None), "heteroplasmic", "50.0%"),
        # Neither: the genotype.
        (SmallVariantCall(sample="S", gt="1", gq=None, dp=None, af=[], ad=[], ps=None), "homoplasmic", "1"),
        (SmallVariantCall(sample="S", gt="0/1", gq=None, dp=None, af=[], ad=[], ps=None), "heteroplasmic", "0/1"),
        (SmallVariantCall(sample="S", gt="0/0", gq=None, dp=30, af=[], ad=[], ps=None), "reference", "ref"),
        (SmallVariantCall(sample="S", gt="./.", gq=None, dp=None, af=[], ad=[], ps=None), "no_call", "no-call"),
    ],
    ids=[
        "at homoplasmy", "just below homoplasmy", "at heteroplasmy", "just below heteroplasmy", "a fraction of 0",
        "multi-allelic", "from the reads", "from the reads and DP", "haploid genotype", "het genotype",
        "reference genotype", "no call",
    ],
)
def test_a_calls_heteroplasmy_reads_its_fraction_else_its_reads_else_its_genotype(
    call: SmallVariantCall, zygosity: str, display: str
) -> None:
    assert mitochondrial_analysis._zygosity(call) == zygosity
    assert mitochondrial_analysis._call_display(call) == display


# --- the pipeline's own QC verdict, and the lab's limits ------------------------------------------


@pytest.mark.parametrize(
    ("stored", "status"),
    [
        ("PASSED", "pass"),
        ("ok", "pass"),
        ("Failed", "fail"),
        ("error", "fail"),
        ("Review", "warn"),
        ("warning", "warn"),
        ("not run", "skip"),
        ("n/a", "skip"),
        # A verdict this module does not know, or none, claims nothing.
        ("maybe", "skip"),
        ("", "skip"),
    ],
)
def test_the_pipelines_qc_verdict_is_read_as_one_of_the_qc_statuses(stored: str, status: str) -> None:
    qc = mitochondrial_analysis._sample_qc({"mtdna": {"qc_status": stored}}, MitoDNACoverageOut())
    assert qc.status == status


_LIMITS = {
    "mtdna.contamination": {"warn_value": 0.01, "error_value": 0.03},
    "mtdna.mean_depth": {"warn_value": 300.0, "error_value": 250.0},
}


def test_a_failed_pipeline_verdict_is_not_passed_by_the_lab_s_limits() -> None:
    qc = mitochondrial_analysis._sample_qc(
        {"mtdna": {"qc_status": "fail", "contamination": 0.001}}, MitoDNACoverageOut(mean_depth=1_000.0), _LIMITS
    )
    assert (qc.status, qc.notes) == ("fail", [])


def test_a_failed_limit_is_not_lowered_to_a_warning_by_another() -> None:
    # Contamination above its error limit, the depth under its warning limit only.
    qc = mitochondrial_analysis._sample_qc(
        {"mtdna": {"contamination": 0.05}}, MitoDNACoverageOut(mean_depth=280.0), _LIMITS
    )
    assert qc.status == "fail"
    assert qc.notes == [
        "Contamination estimate is at or above the configured limit of 0.03.",
        "Mean mtDNA depth is below the configured limit of 300.0x.",
    ]
    # The pipeline's own pass does not hide a failed limit either.
    qc = mitochondrial_analysis._sample_qc(
        {"mtdna": {"qc_status": "pass"}}, MitoDNACoverageOut(mean_depth=200.0), _LIMITS
    )
    assert qc.status == "fail"


def test_without_a_limit_or_a_verdict_a_sample_is_not_claimed_to_pass() -> None:
    qc = mitochondrial_analysis._sample_qc({}, MitoDNACoverageOut(mean_depth=1_000.0), {})
    assert qc.status == "skip"
    # The depth recorded with the sample is read when no coverage track gives one.
    qc = mitochondrial_analysis._sample_qc({"mtdna": {"mean_depth": "200"}}, MitoDNACoverageOut(), _LIMITS)
    assert (qc.status, qc.mean_depth) == ("fail", 200.0)


# --- the stored values it reads -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "read"),
    [
        ({"mtdna": {"haplogroup": "H1"}}, {"mtdna": {"haplogroup": "H1"}}),
        ('{"mtdna": {"haplogroup": "H1"}}', {"mtdna": {"haplogroup": "H1"}}),
        (b'{"mtdna": {"haplogroup": "H1"}}', {"mtdna": {"haplogroup": "H1"}}),
        ("not json", {}),
        ('["a list"]', {}),
        ("", {}),
        (None, {}),
    ],
    ids=["mapping", "JSON text", "JSON bytes", "not JSON", "not an object", "empty", "none"],
)
def test_a_samples_stored_metadata_is_read_as_a_mapping_or_as_none(stored, read) -> None:
    assert mitochondrial_analysis._metadata_dict(stored) == read


def test_the_coverage_of_rows_without_a_usable_depth_is_empty() -> None:
    assert mitochondrial_analysis._coverage_from_rows([], "web") == MitoDNACoverageOut()
    # Rows without a value, or covering no base of chrM: counted as rows, read as no depth.
    rows = [{"start": 0, "end": 100, "value": None}, {"start": 16_569, "end": 16_600, "value": 50.0}]
    assert mitochondrial_analysis._coverage_from_rows(rows, "web") == MitoDNACoverageOut(regions=2)


@pytest.mark.asyncio
async def test_a_family_without_an_assembly_reads_no_chrm_variants() -> None:
    context = _family_context()
    context.assembly_name = None
    assert await mitochondrial_analysis._fetch_mt_records(context) == []
    assert await mitochondrial_analysis._fetch_mt_structural_variants(context) == []


# --------------------------------------------------------------------------- #
# Overlapping genes and the mtDNA population frequencies
# --------------------------------------------------------------------------- #


def _overlap_record(annotations: list[dict]) -> SmallVariantRecord:
    """A synthetic chrM call at 8530, where MT-ATP8 (8366–8572) and MT-ATP6 (8527–9207) overlap."""
    return SmallVariantRecord(
        variant_key=2,
        variant_id="MT-8530-A-G",
        chr="MT",
        start=8530,
        end=8530,
        ref="A",
        alt="G",
        source="clair3",
        rsid=None,
        filters=[],
        gene_symbols=["MT-ATP6", "MT-ATP8"],
        annotations=annotations,
        calls=[SmallVariantCall(sample="PROBAND", gt="1", gq=None, dp=400, af=[0.99], ad=[4, 396], ps=None)],
    )


def test_an_overlap_variant_names_each_genes_consequence_and_the_primary_ones_locus() -> None:
    # VEP's entries in CSQ order: a neighbour's upstream entry first, then MT-ATP6's synonymous
    # and MT-ATP8's missense. The first entry was read, and the other gene's consequence was
    # not shown; the locus was the first that holds the position.
    record = _overlap_record(
        [
            {"gene": "MT-CO2", "effect": "upstream_gene_variant", "impact": "MODIFIER", "canonical": True},
            {"gene": "MT-ATP6", "effect": "synonymous_variant", "impact": "LOW", "canonical": True},
            {
                "gene": "MT-ATP8",
                "effect": "missense_variant",
                "impact": "MODERATE",
                "canonical": True,
                "population_frequencies": {"gnomad_mt_af_hom": 0.0001, "gnomad_mt_af_het": 0.00002},
                "gnomad_mt_max_hl": 0.92,
            },
        ]
    )
    annotation = mitochondrial_analysis._annotation_for_record(record)
    # The primary entry, picked as every small-variant view picks it: the most severe.
    assert (annotation.gene, annotation.consequence, annotation.region) == ("MT-ATP8", "missense_variant", "MT-ATP8")
    assert [(entry.gene, entry.consequence) for entry in annotation.gene_consequences] == [
        ("MT-ATP8", "missense_variant"),
        ("MT-ATP6", "synonymous_variant"),
    ]
    assert (annotation.gnomad_mt_af_hom, annotation.gnomad_mt_af_het, annotation.gnomad_mt_max_hl) == (
        0.0001,
        0.00002,
        0.92,
    )
    # A position in one gene names no per-gene list.
    assert mitochondrial_analysis._annotation_for_record(_mt_record()).gene_consequences == []


def test_helixmtdb_frequencies_from_a_mutserve_table_are_served_apart() -> None:
    record = _overlap_record([{"gene": "MT-ATP6", "helix_af": "0.0003", "helix_af_het": "0.00001"}])
    annotation = mitochondrial_analysis._annotation_for_record(record)
    assert (annotation.helix_af_hom, annotation.helix_af_het) == (0.0003, 0.00001)
    # The locus is the annotation's own gene's, where the position lies in two.
    assert annotation.region == "MT-ATP6"
