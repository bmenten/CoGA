"""End-to-end validation of the monogenic NIPT analysis on the synthetic demo.

Parses the committed demo VCF (demo/nipt_family/nipt_combined.vcf), builds the
father+cfDNA site observations with the real building blocks, runs the analysis
core, and checks it recovers the ground truth encoded in each line's EXP_CAT
INFO field: the fetal fraction, every category, the quality/artifact funnel, and
the recessive-at-risk gene logic. Regenerate the VCF with
``scripts/generate_nipt_demo.py`` if the scenario changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.nipt_analysis import NiptQualityThresholds, run_nipt_analysis
from backend.app.services.nipt_service import (
    NiptClassifiedVariant,
    _recessive_at_risk_variants,
    build_nipt_observations,
)
from backend.app.services.variant_upload_service import (
    _parse_float_list,
    _parse_format,
    _parse_info,
    _parse_int_list,
    _parse_qual,
)

_DEMO_VCF = (
    Path(__file__).resolve().parents[2] / "demo" / "nipt_family" / "nipt_combined.vcf"
)
FATHER_SAMPLE = "FATHER_NIPT"
CFDNA_SAMPLE = "CFDNA_NIPT"


def _parse_demo_vcf() -> tuple[list[SmallVariantRecord], dict[str, str]]:
    """Return (records, variant_id -> EXP_CAT) from the committed demo VCF."""
    records: list[SmallVariantRecord] = []
    expected: dict[str, str] = {}
    sample_names: list[str] = []
    for raw in _DEMO_VCF.read_text().splitlines():
        if raw.startswith("#CHROM"):
            sample_names = raw.split("\t")[9:]
            continue
        if not raw or raw.startswith("#"):
            continue
        fields = raw.split("\t")
        chrom, pos, _id, ref, alt, qual, _filter, info_field, fmt = fields[:9]
        info = _parse_info(info_field)
        variant_id = f"{chrom}-{pos}-{ref}-{alt}"
        expected[variant_id] = info.get("EXP_CAT", "")
        gene = info.get("GENE")
        calls: list[SmallVariantCall] = []
        for sample_name, sample_field in zip(sample_names, fields[9:]):
            values = _parse_format(fmt, sample_field)
            calls.append(
                SmallVariantCall(
                    sample=sample_name,
                    gt=values.get("GT", "./."),
                    gq=None,
                    dp=int(values["DP"]) if values.get("DP") not in (None, "", ".") else None,
                    af=_parse_float_list(values.get("AF")),
                    ad=_parse_int_list(values.get("AD")),
                    ps=None,
                )
            )
        records.append(
            SmallVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=chrom,
                start=int(pos),
                end=int(pos),
                ref=ref,
                alt=alt,
                source="nipt-demo",
                rsid=None,
                filters=[],
                gene_symbols=[gene] if gene else [],
                annotations=[],
                calls=calls,
                qual=_parse_qual(qual),
            )
        )
    return records, expected


def test_demo_vcf_is_present() -> None:
    assert _DEMO_VCF.exists(), "run scripts/generate_nipt_demo.py to create the demo VCF"


def test_nipt_demo_end_to_end() -> None:
    records, expected = _parse_demo_vcf()
    artifact_ids = {vid for vid, cat in expected.items() if cat == "ARTIFACT"}

    sites = build_nipt_observations(
        records, father_sample_id=FATHER_SAMPLE, cfdna_sample_id=CFDNA_SAMPLE
    )
    result = run_nipt_analysis(
        sites, NiptQualityThresholds(), artifact_lookup=artifact_ids.__contains__
    )

    # Fetal fraction is recovered from the 40 category-7 sites.
    assert result.fetal_fraction.n_sites == 40
    assert result.fetal_fraction.ff_computed == pytest.approx(0.12, abs=0.01)
    assert not result.fetal_fraction.low_confidence

    # The filter funnel: one low-depth drop, one artifact drop.
    assert result.filter_counts == {
        "total_in": 49,
        "passed": 47,
        "failed_quality": 1,
        "failed_artifact": 1,
    }

    # Every maternal/fetal category is represented as designed.
    assert result.category_counts == {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 40, 8: 1}

    # Each classified variant matches its ground-truth category.
    for classification in result.classifications:
        assert classification.category is not None
        assert str(classification.category) == expected[classification.variant_id], (
            f"{classification.variant_id}: got {classification.category}, "
            f"expected {expected[classification.variant_id]}"
        )


def test_nipt_demo_recessive_at_risk() -> None:
    records, expected = _parse_demo_vcf()
    artifact_ids = {vid for vid, cat in expected.items() if cat == "ARTIFACT"}
    records_by_id = {record.variant_id: record for record in records}

    sites = build_nipt_observations(
        records, father_sample_id=FATHER_SAMPLE, cfdna_sample_id=CFDNA_SAMPLE
    )
    result = run_nipt_analysis(
        sites, NiptQualityThresholds(), artifact_lookup=artifact_ids.__contains__
    )

    classified = [
        NiptClassifiedVariant(
            record=records_by_id[classification.variant_id], classification=classification
        )
        for classification in result.classifications
    ]
    observations = {site.variant_id: site for site in sites}
    at_risk = _recessive_at_risk_variants(classified, observations)
    at_risk_genes = {
        gene for variant in at_risk for gene in variant.record.gene_symbols
    }

    # Genes where both parents carry: GENE_RECESS (maternal cat 3 + paternal cat 7),
    # GENE_HOMRISK (cat 4 — maternal het + paternal allele the fetus made homozygous),
    # GENE_MATHOM2 (cat 6 — both hom). GENE_CARRIER's lone paternal hit (cat 7, no
    # maternal carrier) and the maternal-only genes (cat 2/5, father hom-ref) are excluded.
    assert at_risk_genes == {"GENE_RECESS", "GENE_HOMRISK", "GENE_MATHOM2"}
    assert len(at_risk) == 4  # 2 compound-pair carriers + 2 homozygous variants
    assert "GENE_CARRIER" not in at_risk_genes
