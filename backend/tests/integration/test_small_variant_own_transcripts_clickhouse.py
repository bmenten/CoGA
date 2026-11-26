"""The family search reads its per-transcript filters on each row's own transcripts and keeps
exactly the rows it kept reading them on the cohort's shared annotation table, on real
ClickHouse.

The family search tests the per-transcript filters (impact, consequence, ClinVar, frequencies
with the ClinVar rescue, scores, SIFT and PolyPhen, Canonical, MANE and LoF only), the
excluded ClinVar terms and the salient variants on the transcripts each ``entries`` row holds
(``_small_own_transcripts_condition``), and reads the genes a user typed from the gene index
without the family's keys. Before, it read each of them in a subquery over the shared tables,
narrowed to the family's keys (``_small_family_key_condition``). The code keeps that subquery
for the transcript and HGVS text, so the test asks it for every filter (``reads_text``, and
the family's keys for every gene too) and compares the two answers.

About 600 synthetic variants are written once through the import's write path
(``insert_small_variant_records``), from VEP CSQ entries the import parses, drawn at random
with a fixed seed: up to six transcripts each, or none; a consequence and impact, Canonical,
MANE Select, MANE Plus Clinical, LoF, SIFT, PolyPhen, CADD, REVEL, SpliceAI and ClinVar per
transcript; the population frequencies once per variant, as VEP writes them, or now and then
per transcript; values on the thresholds the cases set. Two families carry them:

* a trio, every variant, annotated as ``vep_tsv``;
* a second trio, half of the variants with the same annotations under another annotation
  version (``vcf_info``), the other half annotated anew (another VEP run: another
  annotation set). A row is read with its own annotation: its key and annotation set, under
  any version.

For each family and case the list (``_fetch_small_variant_rows``) and the bounded count keep
the same variants both ways, the cases covering every filter field the transcripts serve,
alone and together, within a locus, with a gene, a panel and an rsID beside them.

The variants sit on chromosome 19 at 41.5 Mb, where no other test writes: the annotation
tables are keyed by variant, not by project or family. Synthetic data only. Skipped unless
``RUN_INTEGRATION=1`` (see conftest.py); the CI ``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
import random
import re
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_PROBAND, _MOTHER, _FATHER = "OWNTX_PROBAND", "OWNTX_MOTHER", "OWNTX_FATHER"
_FIRST_POS = 41_500_001
_VARIANTS = 600

_CSQ_FIELDS = (
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Gene", "Feature_type", "Feature", "CANONICAL",
    "MANE_SELECT", "MANE_PLUS_CLINICAL", "HGVSc", "HGVSp", "Existing_variation", "LoF", "SIFT",
    "PolyPhen", "CADD_PHRED", "REVEL", "SpliceAI_pred_DS_AG", "SpliceAI_pred_DS_AL",
    "SpliceAI_pred_DS_DG", "SpliceAI_pred_DS_DL", "gnomADe_AF", "gnomADg_AF", "gnomAD_AF_POPMAX",
    "TOPMed_AF", "gnomAD_AC", "gnomAD_nhomalt", "gnomAD_Hemi", "ClinVar_CLNSIG",
)
_CONSEQUENCES = (
    ("missense_variant", "MODERATE"),
    ("missense_variant&splice_region_variant", "MODERATE"),
    ("stop_gained", "HIGH"),
    ("frameshift_variant", "HIGH"),
    ("splice_donor_variant", "HIGH"),
    ("synonymous_variant", "LOW"),
    ("intron_variant", "MODIFIER"),
    ("3_prime_UTR_variant", "MODIFIER"),
    ("intergenic_variant", "MODIFIER"),
)
_CLINVAR = (
    "", "", "", "pathogenic", "likely_pathogenic", "pathogenic/likely_pathogenic", "benign",
    "likely_benign", "uncertain_significance", "conflicting_interpretations_of_pathogenicity",
)
# Values on the cases' thresholds, where a 32-bit value could round past them, among others.
_FREQUENCIES = ("", "", "0.001", "0.0005", "0.002", "0.01", "0.05", "0.3", "1e-05")
_COUNTS = ("", "0", "1", "12", "40")
_SCORES = ("", "25.3", "15", "35", "0.5")
_REVEL = ("", "0.5", "0.7", "0.2")
_SPLICE = ("", "0.00", "0.35", "0.9", "0.2")
_SIFT = ("", "deleterious(0.01)", "deleterious_low_confidence(0.03)", "tolerated(0.3)")
_POLYPHEN = ("", "probably_damaging(0.99)", "possibly_damaging(0.6)", "benign(0.01)")
_GENES = ("OWNTXA", "OWNTXB", "OWNTXC")


def _variant_ids() -> list[str]:
    alleles = ("C-T", "G-A", "A-G", "T-C", "C-CA", "GTT-G")
    return [f"19-{_FIRST_POS + 10 * index}-{alleles[index % len(alleles)]}" for index in range(_VARIANTS)]


def _frequencies(rng: random.Random) -> dict[str, str]:
    return {
        "gnomADe_AF": rng.choice(_FREQUENCIES),
        "gnomADg_AF": rng.choice(_FREQUENCIES),
        "gnomAD_AF_POPMAX": rng.choice(_FREQUENCIES),
        "TOPMed_AF": rng.choice(_FREQUENCIES),
        "gnomAD_AC": rng.choice(_COUNTS),
        "gnomAD_nhomalt": rng.choice(_COUNTS),
        "gnomAD_Hemi": rng.choice(_COUNTS),
    }


def _csq(rng: random.Random, variant_id: str) -> list[dict[str, str]]:
    """A variant's CSQ entries, drawn at random: none, or up to six transcripts."""
    if rng.random() < 0.08:
        return []
    alt = variant_id.rsplit("-", 1)[1]
    gene = rng.choice(_GENES)
    variant_frequencies = _frequencies(rng)
    entries = []
    for index in range(rng.randint(1, 6)):
        consequence, impact = rng.choice(_CONSEQUENCES)
        splice = rng.choice(_SPLICE)
        entries.append(
            {
                "Allele": alt,
                "Consequence": consequence,
                "IMPACT": impact,
                "SYMBOL": gene,
                "Gene": f"ENSG_{gene}",
                "Feature_type": "Transcript",
                "Feature": f"ENST_{gene}_{index}",
                "CANONICAL": "YES" if index == 0 and rng.random() < 0.7 else "",
                "MANE_SELECT": f"NM_{gene}.1" if index == 0 and rng.random() < 0.5 else "",
                "MANE_PLUS_CLINICAL": f"NM_{gene}_PC.1" if index == 1 and rng.random() < 0.3 else "",
                "HGVSc": f"ENST_{gene}_{index}:c.{rng.randint(1, 900)}C>T",
                "HGVSp": f"ENSP_{gene}_{index}:p.Ala{rng.randint(1, 300)}Val" if impact == "MODERATE" else "",
                "Existing_variation": "",
                "LoF": rng.choice(("HC", "LC")) if impact == "HIGH" and rng.random() < 0.6 else "",
                "SIFT": rng.choice(_SIFT),
                "PolyPhen": rng.choice(_POLYPHEN),
                "CADD_PHRED": rng.choice(_SCORES),
                "REVEL": rng.choice(_REVEL),
                "SpliceAI_pred_DS_AG": splice,
                "SpliceAI_pred_DS_AL": splice,
                "SpliceAI_pred_DS_DG": splice,
                "SpliceAI_pred_DS_DL": splice,
                "ClinVar_CLNSIG": rng.choice(_CLINVAR),
                # VEP writes a variant's frequencies on each of its transcripts; now and
                # then a transcript holds others.
                **(_frequencies(rng) if rng.random() < 0.1 else variant_frequencies),
            }
        )
    return entries


def _annotations(entries: list[dict[str, str]]) -> list[dict[str, Any]]:
    from backend.app.services.variant_annotation_parser import (
        AnnotationHeaderState,
        extract_small_variant_annotations,
        update_annotation_header_state,
    )

    if not entries:
        return []
    state = AnnotationHeaderState()
    update_annotation_header_state(
        state,
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl'
        f' VEP. Format: {"|".join(_CSQ_FIELDS)}">',
    )
    csq = ",".join("|".join(entry.get(name, "") for name in _CSQ_FIELDS) for entry in entries)
    return extract_small_variant_annotations({"CSQ": csq}, state)


def _records(annotated: dict[str, list[dict[str, str]]], rng: random.Random) -> list[Any]:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    records = []
    for variant_id, entries in annotated.items():
        chrom, pos, ref, alt = variant_id.split("-")
        gts = rng.choice((("0/1", "0/1", "0/0"), ("1/1", "0/1", "0/1"), ("0/1", "0/0", "0/0")))
        records.append(
            SmallVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr=chrom,
                start=int(pos),
                end=int(pos) + len(ref) - 1,
                ref=ref,
                alt=alt,
                source="clair3",
                rsid=f"rs{9_000_000 + int(pos) % 1000}" if rng.random() < 0.3 else None,
                filters=["PASS"],
                gene_symbols=[],
                annotations=_annotations(entries),
                calls=[
                    SmallVariantCall(sample=sample, gt=gt, gq=40, dp=30, af=[0.5], ad=[15, 15], ps=None)
                    for sample, gt in zip((_PROBAND, _MOTHER, _FATHER), gts)
                ],
                qual=50.0,
            )
        )
    return records


def _context(family_uuid: str, project: str) -> Any:
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    members = {_PROBAND: "proband", _MOTHER: "mother", _FATHER: "father"}
    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=f"OWNTX-{family_uuid[:8]}",
        project_ids=[project],
        sample_rows=[{"sample_id": name, "role": role} for name, role in members.items()],
        sample_uuid_to_name={f"uuid-{name}": name for name in members},
        sample_name_to_uuid={name: f"uuid-{name}" for name in members},
        affected_sample_names=[_PROBAND],
        assembly_id="owntx-assembly",
        assembly_name=_ASSEMBLY,
    )


# Each case: the filters, and a gene panel (its genes) beside them or None.
_CASES: dict[str, tuple[dict[str, Any], tuple[str, ...] | None]] = {
    "rare on a threshold": ({"max_gnomad_af": 0.001}, None),
    "rare, HIGH or MODERATE": ({"max_gnomad_af": 0.01, "impact": ["HIGH", "MODERATE"]}, None),
    "rare, ClinVar P/LP rescued": ({"max_gnomad_af": 0.001, "clinvar_pathogenic_override": True}, None),
    "every frequency": (
        {
            "max_gnomad_exomes_af": 0.001,
            "max_gnomad_genomes_af": 0.002,
            "max_gnomad_popmax_af": 0.002,
            "max_topmed_af": 0.001,
            "max_gnomad_ac": 12,
            "max_gnomad_hom_count": 1,
            "max_gnomad_hemi_count": 0,
        },
        None,
    ),
    "consequence": ({"effect": ["missense_variant", "splice_region_variant"]}, None),
    "impact or consequence": ({"impact": ["HIGH"], "effect": ["synonymous_variant"]}, None),
    "ClinVar": ({"clinvar": ["pathogenic", "likely_pathogenic"]}, None),
    "excluded ClinVar": ({"exclude_clinvar": ["benign", "likely_benign"]}, None),
    "Canonical only": ({"canonical_only": True, "impact": ["MODERATE"]}, None),
    "MANE only": ({"mane_only": True}, None),
    "LoF only": ({"lof_only": True}, None),
    "CADD on a threshold": ({"min_cadd": 25.3}, None),
    "REVEL": ({"min_revel": 0.5}, None),
    "SpliceAI on a threshold": ({"min_spliceai": 0.35}, None),
    "SIFT": ({"sift": "deleterious"}, None),
    "PolyPhen": ({"polyphen": "probably_damaging"}, None),
    "in silico together": ({"min_cadd": 20.0, "min_revel": 0.5, "sift": "deleterious"}, None),
    "salient": ({"salient_only": True}, None),
    "every transcript filter": (
        {
            "max_gnomad_af": 0.01,
            "clinvar_pathogenic_override": True,
            "impact": ["HIGH", "MODERATE"],
            "canonical_only": True,
            "min_cadd": 15.0,
            "exclude_clinvar": ["benign"],
        },
        None,
    ),
    "within a locus": (
        {"chromosome": "chr19", "start": _FIRST_POS + 1_000, "end": _FIRST_POS + 4_000, "overlap": True,
         "max_gnomad_af": 0.01, "exclude_clinvar": ["benign"]},
        None,
    ),
    "a gene": ({"gene": "OWNTXA"}, None),
    "a gene, rare": ({"gene": "OWNTXB, ENSG_OWNTXC", "max_gnomad_af": 0.01}, None),
    "a panel, rare": ({"max_gnomad_af": 0.01, "impact": ["HIGH", "MODERATE"]}, ("OWNTXA", "OWNTXC")),
    "an excluded gene": ({"exclude_gene": "OWNTXB", "impact": ["HIGH"]}, None),
    "an rsID, rare": ({"rsid": "rs9000", "max_gnomad_af": 0.05}, None),
    "an HGVS text": ({"hgvsc": "c.1", "impact": ["MODERATE"]}, None),
}


def _shared_table_formulation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The family search as it read every annotation filter on the shared tables: each
    per-transcript condition in a subquery over ``variants/annotations``, and every subquery,
    the gene index's included, narrowed to the family's keys."""
    from backend.app.services import clickhouse_variant_queries as queries

    row_membership = queries._small_annotation_row_membership_condition
    family_keys = queries._small_family_key_condition

    def on_the_shared_table(*args: Any, **kwargs: Any) -> str:
        return row_membership(*args, **{**kwargs, "reads_text": True})

    def with_the_family_s_keys(*args: Any, **kwargs: Any) -> str | None:
        return family_keys(*args, **{**kwargs, "narrow": False})

    monkeypatch.setattr(queries, "_small_annotation_row_membership_condition", on_the_shared_table)
    monkeypatch.setattr(queries, "_small_family_key_condition", with_the_family_s_keys)


async def _answer(context: Any, filters: dict[str, Any], panel: tuple[str, ...] | None) -> tuple[frozenset[str], int]:
    import backend.app.services.clickhouse_family_variants as family_variants
    from backend.app.services.clickhouse_variant_records import PanelFilterConstraints
    from backend.app.services.clickhouse_variant_queries import _split_gene_terms
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    search = SmallVariantQueryFilters(page=1, page_size=1_000, **filters)
    scope: dict[str, Any] = {
        "panel_constraints": PanelFilterConstraints(genes=panel) if panel else None,
        "exclude_gene_terms": _split_gene_terms(search.exclude_gene),
    }
    rows = await family_variants._fetch_small_variant_rows(context, search, **scope)
    count, _estimated = await family_variants._count_small_variant_rows_bounded(context, search, **scope)
    return frozenset(record.variant_id for record in rows), count


async def _close_stores() -> None:
    from backend.app.core.clickhouse import close_clickhouse_client

    await close_clickhouse_client()


@pytest.fixture(scope="module")
def families() -> dict[str, Any]:
    """The two families' variants, written once, each family in a fresh project."""
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    rng = random.Random(20261118)
    annotated = {variant_id: _csq(rng, variant_id) for variant_id in _variant_ids()}
    halves = list(annotated)
    same = {variant_id: annotated[variant_id] for variant_id in halves[::2]}
    anew = {variant_id: _csq(rng, variant_id) for variant_id in halves[1::2]}
    first = (str(uuid4()), str(uuid4()))
    second = (str(uuid4()), str(uuid4()))

    async def _write() -> None:
        try:
            await ensure_clickhouse_variant_tables(_ASSEMBLY)
            await insert_small_variant_records(
                _ASSEMBLY, first[0], [first[1]], _records(annotated, rng), annotation_version="vep_tsv"
            )
            await insert_small_variant_records(
                _ASSEMBLY, second[0], [second[1]], _records(same, rng), annotation_version="vcf_info"
            )
            await insert_small_variant_records(
                _ASSEMBLY, second[0], [second[1]], _records(anew, rng), annotation_version="vep_tsv"
            )
        finally:
            await _close_stores()

    asyncio.run(_write())
    return {"every variant": _context(*first), "half re-annotated": _context(*second)}


@pytest.fixture(scope="module")
def answers(families: dict[str, Any]) -> dict[tuple[str, str, str], tuple[frozenset[str], int]]:
    """Every case for both families, read on the rows' own transcripts and on the shared table."""

    async def _run() -> dict[tuple[str, str, str], tuple[frozenset[str], int]]:
        out: dict[tuple[str, str, str], tuple[frozenset[str], int]] = {}
        try:
            for family, context in families.items():
                for name, (filters, panel) in _CASES.items():
                    out[(family, name, "own transcripts")] = await _answer(context, filters, panel)
                    monkeypatch = pytest.MonkeyPatch()
                    try:
                        _shared_table_formulation(monkeypatch)
                        out[(family, name, "shared table")] = await _answer(context, filters, panel)
                    finally:
                        monkeypatch.undo()
                out[(family, "unfiltered", "own transcripts")] = await _answer(context, {}, None)
        finally:
            await _close_stores()
        return out

    return asyncio.run(_run())


@pytest.mark.parametrize("family", ["every variant", "half re-annotated"])
@pytest.mark.parametrize("name", list(_CASES))
def test_the_row_s_own_transcripts_keep_what_the_shared_table_kept(
    answers: dict[tuple[str, str, str], tuple[frozenset[str], int]], family: str, name: str
) -> None:
    own_rows, own_count = answers[(family, name, "own transcripts")]
    shared_rows, shared_count = answers[(family, name, "shared table")]
    assert own_rows == shared_rows
    assert own_count == shared_count == len(own_rows)


@pytest.mark.parametrize("family", ["every variant", "half re-annotated"])
def test_the_cases_keep_some_variants_and_leave_out_others(
    answers: dict[tuple[str, str, str], tuple[frozenset[str], int]], family: str
) -> None:
    # A case that kept every variant, or none, would show nothing about the filter.
    stored, _count = answers[(family, "unfiltered", "own transcripts")]
    assert len(stored) == _VARIANTS
    for name in _CASES:
        kept, _count = answers[(family, name, "own transcripts")]
        assert kept and kept < stored, name


def test_the_cases_cover_every_field_the_transcripts_serve() -> None:
    from backend.app.services.clickhouse_variant_queries import (
        _small_annotation_exclude_filter_condition,
        _small_annotation_filter_condition,
        _small_salient_condition,
    )
    from backend.app.services.clickhouse_variant_rows import ENTRY_TRANSCRIPT_FIELDS
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    read: set[str] = set()
    for filters, _panel in _CASES.values():
        search = SmallVariantQueryFilters(page=1, page_size=1, **filters)
        params: dict[str, Any] = {}
        for condition in (
            _small_annotation_filter_condition(search, params=params),
            _small_annotation_exclude_filter_condition(search, params=params),
            _small_salient_condition(search, params=params),
        ):
            read.update(re.findall(r"\ba\.(\w+)", condition or ""))
    assert {name for name, _type in ENTRY_TRANSCRIPT_FIELDS} <= read
