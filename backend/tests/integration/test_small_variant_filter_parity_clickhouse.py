"""Every small-variant filter keeps the same variants in ClickHouse and in Python (ENG-1).

The family search applies its filters (``SmallVariantQueryFilters``) twice. The plain page
asks ClickHouse (``_small_query_filter_parts``, through ``_fetch_small_variant_rows``); the
ranked page, the compound-het, recessive and carrier-screening searches read those rows and
apply the filters again in Python (``_kept_small_candidates`` → ``_small_record_matches`` and
``_annotation_matches_normal``, then the inheritance rules). The two used to be compared on
genotype classes and hemizygous positions only.

Seventeen synthetic variants of one trio (an affected son and his unaffected parents) go in
once, through the import's write path (``insert_small_variant_records``), annotated as the
import parses VEP's CSQ. For each filter field, every case runs:

* the SQL: ``_fetch_small_variant_rows`` with the request's scope (panel, review selections,
  intervals, excluded genes), as the plain page fetches, and its bounded count;
* the Python: ``_kept_small_candidates`` over every variant the search reads (the
  family's variants, the imputed callsets among them only when a source is asked for, as
  the fetch reads them), then the inheritance mode's rules (``_small_inheritance_items``);

and asserts that both keep exactly the variants the documented rule keeps (the user guide,
*Small-variant filtering*). For a search that pairs (compound het, recessive, expanded
carrier screening) the SQL is a prefilter: it must keep every variant the pairing keeps, and
the pairing over its rows must find what the pairing over every variant finds.

The scope is built by the search's own ``_resolve_small_variant_scope``, with its Postgres
lookups (the panel, the excluded genes' loci, the review selections) answered by stubs. A
second test opens the family page itself, plain and ranked: *Phenotype prioritization*
orders a search and keeps what it keeps (CLIN-44). Its cases are the gene filter's, alone
and with each kind of mode, whose gene loci the ranked and paired views used to read from
the gene reference, and the default view. The ranking's Postgres inputs (the cache, the
phenotypes, the hydration) are stubbed there: they order and decorate the rows, and keep
or drop none.

The variants sit on chromosome 11 at 95.1 Mb, chromosome X at 72 Mb and chrM 12,345, where
no other test writes: the annotation tables are keyed by variant, not by project or family.
The family and project are fresh ones.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"
_PROBAND, _MOTHER, _FATHER = "ENG1_PROBAND", "ENG1_MOTHER", "ENG1_FATHER"

_CSQ_FIELDS = (
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Gene", "Feature_type", "Feature", "CANONICAL",
    "MANE_SELECT", "MANE_PLUS_CLINICAL", "HGVSc", "HGVSp", "Existing_variation", "LoF", "SIFT",
    "PolyPhen", "CADD_PHRED", "REVEL", "SpliceAI_pred_DS_AG", "SpliceAI_pred_DS_AL",
    "SpliceAI_pred_DS_DG", "SpliceAI_pred_DS_DL", "gnomADe_AF", "gnomADg_AF", "gnomAD_AF_POPMAX",
    "TOPMed_AF", "gnomAD_AC", "gnomAD_nhomalt", "gnomAD_Hemi", "CLIN_SIG",
)
_NO_SPLICING = {
    "SpliceAI_pred_DS_AG": "0.00",
    "SpliceAI_pred_DS_AL": "0.00",
    "SpliceAI_pred_DS_DG": "0.00",
    "SpliceAI_pred_DS_DL": "0.00",
}


def _csq(consequence: str, impact: str, gene: str, transcript: str, **fields: str) -> dict[str, str]:
    """One VEP CSQ entry, as field -> value: a transcript of ``gene``."""
    return {
        "Consequence": consequence,
        "IMPACT": impact,
        "SYMBOL": gene,
        "Gene": f"ENSG_{gene}",
        "Feature_type": "Transcript",
        "Feature": transcript,
        **fields,
    }


@dataclass(frozen=True)
class _Call:
    gt: str
    gq: float = 40.0
    dp: int = 30
    ad: tuple[int, ...] | None = None
    af: tuple[float, ...] | None = None
    ps: int | None = None


@dataclass(frozen=True)
class _Variant:
    """A variant as the VCF holds it: its CSQ entries (with the variant's own population
    frequencies, which VEP writes on every transcript) and each member's call; a member
    without a call has none at the site."""

    csq: list[dict[str, str]]
    calls: dict[str, _Call]
    frequencies: dict[str, str] = field(default_factory=dict)
    rsid: str | None = None
    source: str = "clair3"


# The trio's genotypes: a de novo call, and calls the mother or the father passed on.
def _trio(proband: str, mother: str | None = "0/0", father: str = "0/0", **proband_call: Any) -> dict[str, _Call]:
    calls = {_PROBAND: _Call(proband, **proband_call), _FATHER: _Call(father)}
    if mother is not None:
        calls[_MOTHER] = _Call(mother)
    return calls


DE_NOVO_MISSENSE = "11-95100100-C-T"
HIGH_OFF_MANE = "11-95100110-G-A"
HOMOZYGOUS = "11-95100120-A-G"
MATERNAL_INSERTION = "11-95100130-C-CA"
PATERNAL_DELETION = "11-95100140-CTT-C"
MATERNAL_MNV = "11-95100150-AT-GC"
PATERNAL_SYNONYMOUS = "11-95100160-G-T"
COMMON_PATHOGENIC = "11-95100170-T-C"
UNANNOTATED = "11-95100180-C-G"
WITH_RSID = "11-95100190-A-T"
IMPUTED = "11-95100200-G-C"
LOW_QUALITY = "11-95100210-C-A"
FRACTION_FROM_DEPTHS = "11-95100220-T-G"
LONG_DELETION = "11-95100248-GACGTA-G"
X_MATERNAL = "X-72000100-C-T"
X_DE_NOVO = "X-72000110-G-A"
MITO = "M-12345-A-G"

_VARIANTS: dict[str, _Variant] = {
    # De novo, read-phased against HIGH_OFF_MANE (one phase set, the alts on opposite
    # haplotypes): a compound-het pair in trans. Its frequencies and scores sit exactly on
    # the thresholds the cases below set, where a 32-bit value can round past them.
    DE_NOVO_MISSENSE: _Variant(
        csq=[
            _csq(
                "missense_variant", "MODERATE", "SYNA", "ENST_SYNA_1",
                CANONICAL="YES", MANE_SELECT="NM_SYNA.1",
                HGVSc="ENST_SYNA_1:c.100C>T", HGVSp="ENSP_SYNA_1:p.Ala34Val",
                SIFT="deleterious(0.01)", PolyPhen="probably_damaging(0.99)",
                CADD_PHRED="25.3", REVEL="0.7", CLIN_SIG="uncertain_significance", **_NO_SPLICING,
            )
        ],
        frequencies={
            "gnomADe_AF": "0.001", "gnomADg_AF": "0.0005", "gnomAD_AF_POPMAX": "0.002",
            "TOPMed_AF": "0.0005", "gnomAD_AC": "12", "gnomAD_nhomalt": "0",
        },
        calls=_trio("0|1", ps=4321),
    ),
    # HIGH on a transcript that is neither MANE nor canonical; an intron variant on the MANE one.
    HIGH_OFF_MANE: _Variant(
        csq=[
            _csq(
                "intron_variant", "MODIFIER", "SYNA", "ENST_SYNA_1",
                CANONICAL="YES", MANE_SELECT="NM_SYNA.1", CADD_PHRED="35", CLIN_SIG="pathogenic",
            ),
            _csq(
                "stop_gained", "HIGH", "SYNA", "ENST_SYNA_2",
                HGVSc="ENST_SYNA_2:c.50G>A", HGVSp="ENSP_SYNA_2:p.Trp17Ter", LoF="HC",
                CADD_PHRED="35", CLIN_SIG="pathogenic",
            ),
        ],
        calls=_trio("1|0", ps=4321),
    ),
    # Homozygous in the son, both parents carriers. Its only transcript is MANE Plus
    # Clinical, not canonical; its SpliceAI score is 0.35, which rounds down in 32 bits.
    HOMOZYGOUS: _Variant(
        csq=[
            _csq(
                "missense_variant&splice_region_variant", "MODERATE", "SYNB", "ENST_SYNB_1",
                MANE_PLUS_CLINICAL="NM_SYNB.2", SIFT="tolerated(0.3)", PolyPhen="benign(0.01)",
                CADD_PHRED="20", REVEL="0.69", CLIN_SIG="likely_pathogenic",
                **{**_NO_SPLICING, "SpliceAI_pred_DS_AG": "0.35"},
            )
        ],
        frequencies={
            "gnomADe_AF": "0.0011", "gnomADg_AF": "0.0011", "gnomAD_AF_POPMAX": "0.003",
            "TOPMed_AF": "0.001", "gnomAD_AC": "30", "gnomAD_nhomalt": "1",
        },
        calls=_trio("1/1", mother="0/1", father="0/1"),
    ),
    # SYNB's compound-het pair: one hit from each parent.
    MATERNAL_INSERTION: _Variant(
        csq=[
            _csq(
                "frameshift_variant", "HIGH", "SYNB", "ENST_SYNB_1",
                MANE_PLUS_CLINICAL="NM_SYNB.2", LoF="LC",
            )
        ],
        frequencies={"gnomADe_AF": "0.00002", "gnomAD_AC": "3", "gnomAD_nhomalt": "0"},
        calls=_trio("0/1", mother="0/1"),
    ),
    PATERNAL_DELETION: _Variant(
        csq=[_csq("inframe_deletion", "MODERATE", "SYNB", "ENST_SYNB_1", MANE_PLUS_CLINICAL="NM_SYNB.2")],
        frequencies={"gnomADg_AF": "0.00003", "gnomAD_AF_POPMAX": "0.0021", "TOPMed_AF": "0.0009"},
        calls=_trio("0/1", father="0/1"),
    ),
    # SYNC: a gene in which each parent carries a variant the son does not: the couple's
    # carrier finding. A common benign MNV from the mother, a likely benign one from the father.
    MATERNAL_MNV: _Variant(
        csq=[_csq("missense_variant", "MODERATE", "SYNC", "ENST_SYNC_1", CANONICAL="YES", CLIN_SIG="benign")],
        frequencies={
            "gnomADe_AF": "0.08", "gnomADg_AF": "0.09", "gnomAD_AF_POPMAX": "0.12",
            "TOPMed_AF": "0.07", "gnomAD_AC": "9000", "gnomAD_nhomalt": "300",
        },
        calls=_trio("0/0", mother="0/1"),
    ),
    PATERNAL_SYNONYMOUS: _Variant(
        csq=[
            _csq("synonymous_variant", "LOW", "SYNC", "ENST_SYNC_1", CANONICAL="YES", CLIN_SIG="likely_benign"),
            _csq("missense_variant", "MODERATE", "SYNC", "ENST_SYNC_2", CLIN_SIG="likely_benign"),
        ],
        frequencies={"gnomADe_AF": "0.004"},
        calls=_trio("0/0", father="0/1"),
    ),
    # Common, and ClinVar's aggregate pathogenic/likely pathogenic: the frequency rescue.
    COMMON_PATHOGENIC: _Variant(
        csq=[
            _csq(
                "missense_variant", "MODERATE", "SYND", "ENST_SYND_1",
                CANONICAL="YES", MANE_SELECT="NM_SYND.1", CLIN_SIG="Pathogenic/Likely_pathogenic",
            )
        ],
        frequencies={
            "gnomADe_AF": "0.08", "gnomADg_AF": "0.07", "gnomAD_AF_POPMAX": "0.1",
            "TOPMed_AF": "0.06", "gnomAD_AC": "8000", "gnomAD_nhomalt": "12",
        },
        calls=_trio("0/1", mother="0/1"),
    ),
    UNANNOTATED: _Variant(csq=[], calls=_trio("0/1", father="0/1")),
    # The VCF names its rsID; VEP lists a COSMIC id with it.
    WITH_RSID: _Variant(
        csq=[
            _csq(
                "intron_variant", "MODIFIER", "SYNE", "ENST_SYNE_1",
                CANONICAL="YES", Existing_variation="rs1001&COSV55",
            )
        ],
        rsid="rs1001",
        calls=_trio("0/1", father="0/1"),
    ),
    # An imputed callset: hidden unless a source is asked for.
    IMPUTED: _Variant(
        csq=[_csq("missense_variant", "MODERATE", "SYNA", "ENST_SYNA_1", CANONICAL="YES", MANE_SELECT="NM_SYNA.1")],
        source="glimpse2",
        calls=_trio("0/1", mother="0/1"),
    ),
    # The son's call is weak (GQ 19, DP 20, 9 alt reads, AF 0.45); the mother has no call.
    LOW_QUALITY: _Variant(
        csq=[_csq("missense_variant", "MODERATE", "SYNF", "ENST_SYNF_1", CANONICAL="YES")],
        calls=_trio("0/1", mother=None, gq=19.0, dp=20, ad=(11, 9), af=(0.45,)),
    ),
    # No AF: the allele fraction is read from the allele depths.
    FRACTION_FROM_DEPTHS: _Variant(
        csq=[_csq("missense_variant", "MODERATE", "SYNF", "ENST_SYNF_1", CANONICAL="YES")],
        calls=_trio("0/1", dp=20, ad=(10, 10), af=()),
    ),
    # Starts at 248 and ends at 253.
    LONG_DELETION: _Variant(
        csq=[_csq("frameshift_variant", "HIGH", "SYNG", "ENST_SYNG_1", CANONICAL="YES")],
        calls=_trio("0/1"),
    ),
    # chrX outside the pseudo-autosomal regions: the son hemizygous, from his mother.
    X_MATERNAL: _Variant(
        csq=[_csq("missense_variant", "MODERATE", "SYNX", "ENST_SYNX_1", CANONICAL="YES")],
        frequencies={"gnomAD_Hemi": "3"},
        calls={_PROBAND: _Call("1"), _MOTHER: _Call("0/1"), _FATHER: _Call("0")},
    ),
    # ...and de novo in him.
    X_DE_NOVO: _Variant(
        csq=[_csq("missense_variant", "MODERATE", "SYNX", "ENST_SYNX_1", CANONICAL="YES")],
        calls={_PROBAND: _Call("1/1"), _MOTHER: _Call("0/0"), _FATHER: _Call("0")},
    ),
    # chrM, stored as "M" (the VCF writes chrM).
    MITO: _Variant(
        csq=[_csq("missense_variant", "MODERATE", "MT-SYN1", "ENST_MTSYN1_1", CANONICAL="YES")],
        calls={_PROBAND: _Call("1"), _MOTHER: _Call("1"), _FATHER: _Call("0")},
    ),
}

# What the search reads without a source: every callset but the imputed one.
ALL = frozenset(_VARIANTS) - {IMPUTED}
CHR11 = frozenset(variant for variant in ALL if variant.startswith("11-"))
X = frozenset({X_MATERNAL, X_DE_NOVO})

# The scope's Postgres lookups, answered here. Panels by id; gene loci by the gene query.
SMALL_PANEL = "e1000000-0000-4000-8000-000000000001"
LARGE_PANEL = "e1000000-0000-4000-8000-000000000002"
MITO_PANEL = "e1000000-0000-4000-8000-000000000003"
_FILLER_REGIONS = [("21", 30_000_000 + 1_000 * index, 30_000_000 + 1_000 * index + 500) for index in range(1_000)]


def _panels() -> dict[str, Any]:
    from backend.app.services.clickhouse_variant_records import PanelFilterConstraints, Region

    return {
        # SYNC's variants by gene, UNANNOTATED by the panel's region (spelled as some gene
        # references spell it).
        SMALL_PANEL: PanelFilterConstraints(
            genes=("SYNC",), regions=(Region("chr11", 95_100_175, 95_100_185),)
        ),
        # More than 1,000 regions, as the Mendeliome has: matched by its genes and its regions
        # too, so UNANNOTATED is reached by position (it was matched on its genes alone).
        LARGE_PANEL: PanelFilterConstraints(
            genes=("SYNC",),
            regions=(
                *(Region(chrom, start, end) for chrom, start, end in _FILLER_REGIONS),
                Region("11", 95_100_175, 95_100_185),
            ),
        ),
        # A region alone, on chrM spelled chrMT.
        MITO_PANEL: PanelFilterConstraints(regions=(Region("chrMT", 12_000, 13_000),)),
    }


def _gene_loci() -> dict[str, list[Any]]:
    """Each gene's loci as the gene reference gives them: SYNA's over DE_NOVO_MISSENSE only
    (HIGH_OFF_MANE lies just outside), SYNB's over HOMOZYGOUS only, SYNC's over MATERNAL_MNV
    only, SYNG's over LONG_DELETION's end, SYNQ (no variant is annotated with it) over
    UNANNOTATED. A gene the reference lacks has none."""
    from backend.app.services.clickhouse_variant_records import Region

    return {
        "SYNA": [Region("11", 95_100_095, 95_100_105)],
        "SYNB": [Region("11", 95_100_115, 95_100_125)],
        "SYNC": [Region("chr11", 95_100_145, 95_100_155)],
        "SYNG": [Region("11", 95_100_250, 95_100_300)],
        "SYNQ": [Region("11", 95_100_175, 95_100_185)],
    }


@dataclass(frozen=True)
class _Case:
    """A request: the page's filters, the review selections the stubs answer, and the
    variants the documented rule keeps."""

    filters: dict[str, Any]
    expected: frozenset[str]
    review_variant_ids: frozenset[str] | None = None
    excluded_review_variant_ids: frozenset[str] = frozenset()


def _case(expected: frozenset[str] | set[str], **filters: Any) -> _Case:
    return _Case(filters=filters, expected=frozenset(expected))


_DEFAULT_VIEW = {
    "impact": ["HIGH", "MODERATE"],
    "max_gnomad_exomes_af": 0.01,
    "max_gnomad_genomes_af": 0.01,
    "max_gnomad_popmax_af": 0.01,
    "max_gnomad_hom_count": 10,
    "max_gnomad_hemi_count": 10,
    "clinvar_overrides_frequency": True,
    "exclude_clinvar": ["Benign", "Likely_benign"],
    "sample_filters": [f"{_PROBAND}:0/1|1/1:20:10:0.2:4"],
}

CASES: dict[str, _Case] = {
    "no filter": _case(ALL),
    # --- the location: chromosome, start and end (a variant is in by its position) ---
    "chromosome": _case(CHR11, chromosome="11"),
    "chromosome with chr": _case(CHR11, chromosome="chr11"),
    "chromosome X": _case(X, chromosome="X"),
    "chromosome MT, stored as M": _case({MITO}, chromosome="MT"),
    "chromosome chrM": _case({MITO}, chromosome="chrM"),
    "start and end": _case(CHR11, chromosome="11", start=95_100_100, end=95_100_250),
    "end": _case({DE_NOVO_MISSENSE, HIGH_OFF_MANE, HOMOZYGOUS}, chromosome="11", end=95_100_120),
    "start and end, overlapping": _case(
        {PATERNAL_DELETION, MATERNAL_MNV, PATERNAL_SYNONYMOUS, COMMON_PATHOGENIC},
        chromosome="11", start=95_100_141, end=95_100_170, overlap=True,
    ),
    "start, overlapping": _case(
        CHR11 - {DE_NOVO_MISSENSE, HIGH_OFF_MANE, HOMOZYGOUS, MATERNAL_INSERTION},
        chromosome="11", start=95_100_141, overlap=True,
    ),
    # --- interval lists ---
    "interval": _case({PATERNAL_DELETION, MATERNAL_MNV}, intervals="11:95100141-95100150"),
    "interval in lower case": _case({X_MATERNAL}, intervals="x:72000000-72000105"),
    "interval on chrMT, stored as M": _case({MITO}, intervals="chrMT:12000-13000"),
    "two intervals": _case({DE_NOVO_MISSENSE, X_DE_NOVO}, intervals="11:95100100\nX:72000110"),
    "excluded interval": _case(
        ALL - (CHR11 - {LOW_QUALITY, FRACTION_FROM_DEPTHS, LONG_DELETION}),
        exclude_intervals="11:95100100-95100199",
    ),
    "excluded interval in lower case": _case(ALL - X, exclude_intervals="x:72000000-72001000"),
    # --- variant type and callset ---
    "SNV": _case(
        ALL - {MATERNAL_INSERTION, PATERNAL_DELETION, MATERNAL_MNV, LONG_DELETION}, variant_type="SNV"
    ),
    "snv in lower case": _case(
        ALL - {MATERNAL_INSERTION, PATERNAL_DELETION, MATERNAL_MNV, LONG_DELETION}, variant_type="snv"
    ),
    "INDEL (an MNV is no SNV)": _case(
        {MATERNAL_INSERTION, PATERNAL_DELETION, MATERNAL_MNV, LONG_DELETION}, variant_type="INDEL"
    ),
    "MNV": _case({MATERNAL_MNV}, variant_type="MNV"),
    "source": _case(ALL, source="clair3"),
    "source, part of it in capitals": _case(ALL, source="CLAIR"),
    "source, an imputed callset": _case({IMPUTED}, source="glimpse2"),
    "source with spaces around it": _case(ALL, source=" clair3 "),
    "source left blank": _case(ALL, source="  "),
    "phase set": _case({DE_NOVO_MISSENSE, HIGH_OFF_MANE}, phase_set=4321),
    # --- the gene, the transcript and HGVS ---
    "gene": _case({DE_NOVO_MISSENSE, HIGH_OFF_MANE}, gene="SYNA"),
    "gene in lower case": _case({DE_NOVO_MISSENSE, HIGH_OFF_MANE}, gene="syna"),
    "gene by its Ensembl id": _case({HOMOZYGOUS, MATERNAL_INSERTION, PATERNAL_DELETION}, gene="ENSG_SYNB"),
    "two genes": _case(
        {DE_NOVO_MISSENSE, HIGH_OFF_MANE, MATERNAL_MNV, PATERNAL_SYNONYMOUS}, gene="SYNA SYNC"
    ),
    "two genes, comma-separated": _case(
        {DE_NOVO_MISSENSE, HIGH_OFF_MANE, MATERNAL_MNV, PATERNAL_SYNONYMOUS}, gene="SYNA,SYNC"
    ),
    "mitochondrial gene": _case({MITO}, gene="MT-SYN1"),
    "transcript": _case({HIGH_OFF_MANE}, transcript="ENST_SYNA_2"),
    "transcript, part of it in lower case": _case(
        {HOMOZYGOUS, MATERNAL_INSERTION, PATERNAL_DELETION}, transcript="enst_synb"
    ),
    "HGVSc": _case({DE_NOVO_MISSENSE}, hgvsc="c.100C>T"),
    "HGVSc with spaces around it": _case({DE_NOVO_MISSENSE}, hgvsc=" c.100C>T "),
    "HGVSp": _case({HIGH_OFF_MANE}, hgvsp="p.Trp17Ter"),
    "rsID": _case({WITH_RSID}, rsid="rs1001"),
    "rsID in capitals": _case({WITH_RSID}, rsid="RS1001"),
    "rsID with spaces around it": _case({WITH_RSID}, rsid=" rs1001 "),
    "rsID left blank": _case(ALL, rsid="  "),
    # The variant's rsID is the VCF's ID (else the first transcript's existing variants).
    "another id VEP lists": _case(set(), rsid="COSV55"),
    # --- impact, consequence and the transcript flags (one transcript must meet them all) ---
    "impact": _case({HIGH_OFF_MANE, MATERNAL_INSERTION, LONG_DELETION}, impact=["HIGH"]),
    "impact in lower case": _case({HIGH_OFF_MANE, MATERNAL_INSERTION, LONG_DELETION}, impact=["high"]),
    "two impacts": _case(ALL - {UNANNOTATED, WITH_RSID}, impact=["HIGH", "MODERATE"]),
    "consequence": _case(
        ALL - {HIGH_OFF_MANE, MATERNAL_INSERTION, PATERNAL_DELETION, UNANNOTATED, WITH_RSID, LONG_DELETION},
        effect=["missense_variant"],
    ),
    "consequence, the second of a transcript's": _case({HOMOZYGOUS}, effect=["splice_region_variant"]),
    "consequence as VEP joins two": _case(
        ALL - {HIGH_OFF_MANE, MATERNAL_INSERTION, PATERNAL_DELETION, UNANNOTATED, WITH_RSID, LONG_DELETION},
        effect=["missense_variant&splice_region_variant"],
    ),
    "canonical only and HIGH": _case({LONG_DELETION}, canonical_only=True, impact=["HIGH"]),
    "MANE only (Select or Plus Clinical)": _case(
        {DE_NOVO_MISSENSE, HIGH_OFF_MANE, HOMOZYGOUS, MATERNAL_INSERTION, PATERNAL_DELETION, COMMON_PATHOGENIC},
        mane_only=True,
    ),
    "MANE only and HIGH": _case({MATERNAL_INSERTION}, mane_only=True, impact=["HIGH"]),
    "LoF only": _case({HIGH_OFF_MANE, MATERNAL_INSERTION}, lof_only=True),
    # --- ClinVar ---
    "ClinVar pathogenic": _case({HIGH_OFF_MANE, COMMON_PATHOGENIC}, clinvar=["Pathogenic"]),
    "ClinVar likely pathogenic": _case({HOMOZYGOUS, COMMON_PATHOGENIC}, clinvar=["Likely_pathogenic"]),
    "ClinVar uncertain": _case({DE_NOVO_MISSENSE}, clinvar=["Uncertain_significance"]),
    "ClinVar benign left out": _case(
        ALL - {MATERNAL_MNV, PATERNAL_SYNONYMOUS}, exclude_clinvar=["Benign", "Likely_benign"]
    ),
    # --- frequencies (a variant without one passes) ---
    "gnomAD AF": _case(ALL - {MATERNAL_MNV, COMMON_PATHOGENIC}, max_gnomad_af=0.01),
    "gnomAD AF at the value": _case(
        ALL - {HOMOZYGOUS, MATERNAL_MNV, PATERNAL_SYNONYMOUS, COMMON_PATHOGENIC}, max_gnomad_af=0.001
    ),
    "gnomAD AF with the ClinVar rescue": _case(
        ALL - {MATERNAL_MNV}, max_gnomad_af=0.01, clinvar_overrides_frequency=True
    ),
    "gnomAD exomes AF at the value": _case(
        ALL - {HOMOZYGOUS, MATERNAL_MNV, PATERNAL_SYNONYMOUS, COMMON_PATHOGENIC}, max_gnomad_exomes_af=0.001
    ),
    "gnomAD genomes AF at the value": _case(
        ALL - {HOMOZYGOUS, MATERNAL_MNV, COMMON_PATHOGENIC}, max_gnomad_genomes_af=0.0005
    ),
    "gnomAD popmax AF at the value": _case(
        ALL - {HOMOZYGOUS, PATERNAL_DELETION, MATERNAL_MNV, COMMON_PATHOGENIC}, max_gnomad_popmax_af=0.002
    ),
    "TOPMed AF": _case(ALL - {MATERNAL_MNV, COMMON_PATHOGENIC}, max_topmed_af=0.01),
    "TOPMed AF at the value": _case(
        ALL - {HOMOZYGOUS, PATERNAL_DELETION, MATERNAL_MNV, COMMON_PATHOGENIC}, max_topmed_af=0.0005
    ),
    "gnomAD AC at the value": _case(ALL - {HOMOZYGOUS, MATERNAL_MNV, COMMON_PATHOGENIC}, max_gnomad_ac=12),
    "gnomAD homozygotes": _case(ALL - {HOMOZYGOUS, MATERNAL_MNV, COMMON_PATHOGENIC}, max_gnomad_hom_count=0),
    "gnomAD hemizygotes": _case(ALL - {X_MATERNAL}, max_gnomad_hemi_count=0),
    # --- in silico (one predictor is enough; SpliceAI also passes impact and consequence) ---
    "CADD at the value": _case({DE_NOVO_MISSENSE, HIGH_OFF_MANE}, min_cadd=25.3),
    "REVEL at the value": _case({DE_NOVO_MISSENSE}, min_revel=0.7),
    "CADD or REVEL": _case({DE_NOVO_MISSENSE, HIGH_OFF_MANE, HOMOZYGOUS}, min_cadd=30, min_revel=0.5),
    "SpliceAI": _case({HOMOZYGOUS}, min_spliceai=0.2),
    "SpliceAI at the value": _case({HOMOZYGOUS}, min_spliceai=0.35),
    "SpliceAI from 0": _case({DE_NOVO_MISSENSE, HOMOZYGOUS}, min_spliceai=0.0),
    "SpliceAI or HIGH": _case({HOMOZYGOUS}, min_spliceai=0.35, impact=["HIGH"]),
    "SIFT": _case({DE_NOVO_MISSENSE}, sift="deleterious"),
    "PolyPhen": _case({HOMOZYGOUS}, polyphen="benign"),
    # --- the panel ---
    "panel": _case({MATERNAL_MNV, PATERNAL_SYNONYMOUS, UNANNOTATED}, panel_id=SMALL_PANEL),
    "panel of more than 1,000 regions": _case(
        {MATERNAL_MNV, PATERNAL_SYNONYMOUS, UNANNOTATED}, panel_id=LARGE_PANEL
    ),
    "panel region on chrMT, stored as M": _case({MITO}, panel_id=MITO_PANEL),
    # --- excluded genes: by name, and by the gene reference's loci ---
    "excluded gene the reference lacks": _case(ALL - {COMMON_PATHOGENIC}, exclude_gene="SYND"),
    "excluded gene, a variant outside its loci": _case(ALL - {DE_NOVO_MISSENSE, HIGH_OFF_MANE}, exclude_gene="SYNA"),
    "excluded gene reaching a variant by its span": _case(ALL - {LONG_DELETION}, exclude_gene="SYNG"),
    "excluded gene reaching a variant by position only": _case(ALL - {UNANNOTATED}, exclude_gene="SYNQ"),
    # --- the second hit (sv_hit_genes resolved from the family's SVs) ---
    "also hit by an SV, with the gene filter": _case(
        {HOMOZYGOUS, MATERNAL_INSERTION, PATERNAL_DELETION},
        gene="SYNB", require_sv_second_hit=True, sv_hit_genes=["SYNB", "SYNX"],
    ),
    "also hit by an SV": _case(
        {HOMOZYGOUS, MATERNAL_INSERTION, PATERNAL_DELETION}, require_sv_second_hit=True, sv_hit_genes=["SYNB"]
    ),
    "also hit by an SV, in lower case": _case(
        {HOMOZYGOUS, MATERNAL_INSERTION, PATERNAL_DELETION}, require_sv_second_hit=True, sv_hit_genes=["synb"]
    ),
    "also hit by an SV, no gene is": _case(set(), require_sv_second_hit=True, sv_hit_genes=[]),
    # --- the per-sample filter: genotype class, GQ, DP, AF, AD alt ---
    "proband het": _case(
        {
            DE_NOVO_MISSENSE, HIGH_OFF_MANE, MATERNAL_INSERTION, PATERNAL_DELETION, COMMON_PATHOGENIC,
            UNANNOTATED, WITH_RSID, LOW_QUALITY, FRACTION_FROM_DEPTHS, LONG_DELETION,
        },
        sample_filters=[f"{_PROBAND}:het"],
    ),
    "proband hom (a haploid 1 too)": _case(
        {HOMOZYGOUS, X_MATERNAL, X_DE_NOVO, MITO}, sample_filters=[f"{_PROBAND}:hom"]
    ),
    "proband 1/1 as a genotype": _case(
        {HOMOZYGOUS, X_MATERNAL, X_DE_NOVO, MITO}, sample_filters=[f"{_PROBAND}:1/1|1"]
    ),
    "mother reference (or no call)": _case(
        {
            DE_NOVO_MISSENSE, HIGH_OFF_MANE, PATERNAL_DELETION, PATERNAL_SYNONYMOUS, UNANNOTATED,
            WITH_RSID, LOW_QUALITY, FRACTION_FROM_DEPTHS, LONG_DELETION, X_DE_NOVO,
        },
        sample_filters=[f"{_MOTHER}:ref"],
    ),
    "mother het": _case(
        {HOMOZYGOUS, MATERNAL_INSERTION, MATERNAL_MNV, COMMON_PATHOGENIC, X_MATERNAL},
        sample_filters=[f"{_MOTHER}:het"],
    ),
    "proband GQ": _case(ALL - {LOW_QUALITY}, sample_filters=[f"{_PROBAND}::20"]),
    "proband DP": _case(ALL - {LOW_QUALITY, FRACTION_FROM_DEPTHS}, sample_filters=[f"{_PROBAND}:::25"]),
    "proband AF at the value": _case(
        ALL - {MATERNAL_MNV, PATERNAL_SYNONYMOUS}, sample_filters=[f"{_PROBAND}::::0.45"]
    ),
    "proband AD alt": _case(
        ALL - {MATERNAL_MNV, PATERNAL_SYNONYMOUS, LOW_QUALITY}, sample_filters=[f"{_PROBAND}:::::10"]
    ),
    "proband het, father reference": _case(
        {
            DE_NOVO_MISSENSE, HIGH_OFF_MANE, MATERNAL_INSERTION, COMMON_PATHOGENIC, LOW_QUALITY,
            FRACTION_FROM_DEPTHS, LONG_DELETION,
        },
        sample_filters=[f"{_PROBAND}:het", f"{_FATHER}:ref"],
    ),
    # --- review selections ---
    "review selection": _Case(
        filters={}, expected=frozenset({DE_NOVO_MISSENSE, HOMOZYGOUS}),
        review_variant_ids=frozenset({DE_NOVO_MISSENSE, HOMOZYGOUS, IMPUTED}),
    ),
    "review tag left out": _Case(
        filters={}, expected=ALL - {DE_NOVO_MISSENSE}, excluded_review_variant_ids=frozenset({DE_NOVO_MISSENSE})
    ),
    # --- inheritance (the modes SQL decides for each variant) ---
    "de novo / dominant": _case(
        {DE_NOVO_MISSENSE, HIGH_OFF_MANE, LOW_QUALITY, FRACTION_FROM_DEPTHS, LONG_DELETION, X_DE_NOVO},
        inheritance="de_novo_dominant",
    ),
    "recessive homozygous": _case({HOMOZYGOUS}, inheritance="recessive_homozygous"),
    "X-linked": _case(X, inheritance="x_linked"),
    # --- the searches that pair (SQL prefilters; Python pairs) ---
    "compound het": _case(
        {DE_NOVO_MISSENSE, HIGH_OFF_MANE, MATERNAL_INSERTION, PATERNAL_DELETION, LOW_QUALITY, FRACTION_FROM_DEPTHS},
        inheritance="compound_het",
    ),
    "recessive": _case(
        {
            DE_NOVO_MISSENSE, HIGH_OFF_MANE, MATERNAL_INSERTION, PATERNAL_DELETION, LOW_QUALITY,
            FRACTION_FROM_DEPTHS, HOMOZYGOUS, X_MATERNAL, X_DE_NOVO,
        },
        inheritance="recessive",
    ),
    "expanded carrier screening": _case(
        {HOMOZYGOUS, MATERNAL_INSERTION, PATERNAL_DELETION, MATERNAL_MNV, PATERNAL_SYNONYMOUS, X_MATERNAL},
        expanded_carrier_screening=True,
    ),
    "carrier screening and compound het": _case(
        {MATERNAL_INSERTION, PATERNAL_DELETION}, expanded_carrier_screening=True, inheritance="compound_het"
    ),
    # --- the default view's preset (without its panel) ---
    "the default view": _case(
        {
            DE_NOVO_MISSENSE, HIGH_OFF_MANE, HOMOZYGOUS, MATERNAL_INSERTION, PATERNAL_DELETION,
            COMMON_PATHOGENIC, FRACTION_FROM_DEPTHS, LONG_DELETION, X_MATERNAL, X_DE_NOVO, MITO,
        },
        **_DEFAULT_VIEW,
    ),
}

# The family page opened plain and ranked. A gene filter keeps the variants annotated with
# the gene in every view, as the plain page keeps them: HIGH_OFF_MANE lies just past SYNA's
# loci in the gene reference (``_gene_loci``), and the ranked and paired views kept the
# variants inside the loci alone (ENG-1).
PAGE_CASES: dict[str, _Case] = {
    "gene": _case({DE_NOVO_MISSENSE, HIGH_OFF_MANE}, gene="SYNA"),
    "gene, de novo / dominant": _case(
        {DE_NOVO_MISSENSE, HIGH_OFF_MANE}, gene="SYNA", inheritance="de_novo_dominant"
    ),
    "gene, compound het": _case({DE_NOVO_MISSENSE, HIGH_OFF_MANE}, gene="SYNA", inheritance="compound_het"),
    "gene, recessive": _case(
        {HOMOZYGOUS, MATERNAL_INSERTION, PATERNAL_DELETION}, gene="SYNB", inheritance="recessive"
    ),
    "gene, expanded carrier screening": _case(
        {MATERNAL_MNV, PATERNAL_SYNONYMOUS}, gene="SYNC", expanded_carrier_screening=True
    ),
    "the default view": _case(CASES["the default view"].expected, **_DEFAULT_VIEW),
}

# The searches whose SQL is a prefilter: what they keep is decided by pairing in Python.
_PAIRING = ("compound_het", "recessive")


def _annotations(variant: _Variant) -> list[dict[str, Any]]:
    from backend.app.services.variant_annotation_parser import (
        AnnotationHeaderState,
        extract_small_variant_annotations,
        update_annotation_header_state,
    )

    if not variant.csq:
        return []
    state = AnnotationHeaderState()
    update_annotation_header_state(
        state,
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl'
        f' VEP. Format: {"|".join(_CSQ_FIELDS)}">',
    )
    entries = [
        "|".join({**entry, **variant.frequencies}.get(name, "") for name in _CSQ_FIELDS)
        for entry in variant.csq
    ]
    return extract_small_variant_annotations({"CSQ": ",".join(entries)}, state)


def _small_call(sample: str, call: _Call) -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall
    from backend.app.services.genotypes import HOM_REF, classify_genotype

    reference = classify_genotype(call.gt) == HOM_REF
    alleles = call.gt.replace("|", "/").split("/")
    if call.ad is not None:
        ad = list(call.ad)
    elif reference:
        ad = [30, 0]
    elif "0" in alleles:
        ad = [15, 15]
    else:
        ad = [0, 30]
    af = list(call.af) if call.af is not None else [round(ad[1] / sum(ad), 2)]
    return SmallVariantCall(sample=sample, gt=call.gt, gq=call.gq, dp=call.dp, af=af, ad=ad, ps=call.ps)


def _records() -> list[Any]:
    from backend.app.services.clickhouse_variant_records import SmallVariantRecord

    records = []
    for variant_id, variant in _VARIANTS.items():
        chrom, pos, ref, alt = variant_id.split("-")
        records.append(
            SmallVariantRecord(
                variant_key=None,
                variant_id=variant_id,
                chr="chrM" if chrom == "M" else chrom,
                start=int(pos),
                end=int(pos) + len(ref) - 1,
                ref=ref,
                alt=alt,
                source=variant.source,
                rsid=variant.rsid,
                filters=["PASS"],
                gene_symbols=[],
                annotations=_annotations(variant),
                calls=[_small_call(sample, call) for sample, call in variant.calls.items()],
                qual=50.0,
            )
        )
    return records


def _context(family_uuid: str, project: str, assembly_id: str) -> Any:
    from backend.app.services.family_metadata_context import FamilyMetadataContext

    members = {
        _PROBAND: ("proband", "male", "affected"),
        _MOTHER: ("mother", "female", "unaffected"),
        _FATHER: ("father", "male", "unaffected"),
    }
    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=f"ENG1-{family_uuid[:8]}",
        project_ids=[project],
        sample_rows=[
            {
                "sample_id": name,
                "role": role,
                "sex": sex,
                "affected": status == "affected",
                "clinical_status": status,
            }
            for name, (role, sex, status) in members.items()
        ],
        sample_uuid_to_name={f"uuid-{name}": name for name in members},
        sample_name_to_uuid={name: f"uuid-{name}" for name in members},
        affected_sample_names=[_PROBAND],
        assembly_id=assembly_id,
        assembly_name=_ASSEMBLY,
        relationship_rows=[
            {
                "relationship_type": "parent_child",
                "sample_id_a": parent,
                "role_a": role,
                "sample_id_b": _PROBAND,
                "role_b": "child",
            }
            for parent, role in ((_MOTHER, "mother"), (_FATHER, "father"))
        ],
    )


def _stub_lookups(monkeypatch: pytest.MonkeyPatch, case: _Case) -> None:
    """Answer the scope's Postgres lookups: the panels, the gene loci, the review selections."""
    import backend.app.services.clickhouse_family_variants as family_variants

    panels = _panels()
    loci = _gene_loci()

    async def _panel(_session, panel_id, *, assembly_id=None):
        return panels[panel_id]

    async def _gene_regions(_session, *, gene_query, assembly_id):
        from backend.app.services.clickhouse_variant_queries import _split_gene_terms

        return [region for term in _split_gene_terms(gene_query) for region in loci.get(term.upper(), [])]

    async def _review_ids(_session, *, family_uuid, classifications=None, tags=None, has_notes=False):
        if tags == ["left out"]:
            return set(case.excluded_review_variant_ids)
        return set(case.review_variant_ids or ())

    monkeypatch.setattr(family_variants, "_fetch_panel_constraints", _panel)
    monkeypatch.setattr(family_variants, "_fetch_gene_regions", _gene_regions)
    monkeypatch.setattr(family_variants, "list_matching_small_variant_review_ids", _review_ids)


def _review_kwargs(case: _Case) -> dict[str, Any]:
    return {
        "review_classifications": None,
        "review_tags": ["report"] if case.review_variant_ids is not None else None,
        "exclude_review_tags": ["left out"] if case.excluded_review_variant_ids else None,
        "has_notes": False,
    }


def _item_ids(items: Any) -> frozenset[str]:
    from backend.app.services.clickhouse_family_variants import _item_records

    return frozenset(record.variant_id for record in _item_records(items))


async def _both_paths(context: Any, case: _Case, read: dict[str, list[Any]]) -> dict[str, Any]:
    """What the SQL keeps and what the Python keeps of one request."""
    import backend.app.services.clickhouse_family_variants as family_variants
    from backend.app.services.clickhouse_variant_queries import _normalize_small_variant_inheritance
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    filters = SmallVariantQueryFilters(page=1, page_size=100, **case.filters)
    filters.inheritance = _normalize_small_variant_inheritance(filters.inheritance)
    scope = await family_variants._resolve_small_variant_scope(None, context, filters, **_review_kwargs(case))
    assert scope is not None
    sql_rows = await family_variants._fetch_small_variant_rows(context, filters, **scope.row_filters())
    count, _estimated = await family_variants._count_small_variant_rows_bounded(
        context, filters, **scope.row_filters()
    )
    # The search reads the imputed callsets only when a source is asked for.
    records = read["every callset"] if (filters.source or "").strip() else read["default"]

    def _kept(rows: list[Any]) -> frozenset[str]:
        kept = family_variants._kept_small_candidates(rows, filters, scope, context)
        if filters.inheritance:
            return _item_ids(family_variants._small_inheritance_items(context, filters.inheritance, kept))
        return frozenset(record.variant_id for record in kept)

    sql_ids = frozenset(record.variant_id for record in sql_rows)
    answer = {"count": count == len(sql_ids), "Python": _kept(records)}
    if filters.inheritance in _PAIRING or filters.expanded_carrier_screening:
        answer["SQL prefilter keeps the pairing's variants"] = answer["Python"] <= sql_ids
        answer["pairing over the SQL rows"] = _kept(sql_rows)
    else:
        answer["SQL"] = sql_ids
    return answer


def _expected_answer(case: _Case) -> dict[str, Any]:
    pairing = case.filters.get("inheritance") in _PAIRING or case.filters.get("expanded_carrier_screening")
    if pairing:
        return {
            "count": True,
            "Python": case.expected,
            "SQL prefilter keeps the pairing's variants": True,
            "pairing over the SQL rows": case.expected,
        }
    return {"count": True, "Python": case.expected, "SQL": case.expected}


async def _close_stores() -> None:
    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import close_postgres_engine

    await close_clickhouse_client()
    await close_postgres_engine()


@pytest.fixture(scope="module")
def stored_family() -> dict[str, str]:
    """Write the variants once, for a fresh family in a fresh project."""
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    family_uuid, project, assembly_id = str(uuid4()), str(uuid4()), str(uuid4())

    async def _write() -> None:
        try:
            await ensure_clickhouse_variant_tables(_ASSEMBLY)
            await insert_small_variant_records(_ASSEMBLY, family_uuid, [project], _records())
        finally:
            await _close_stores()

    asyncio.run(_write())
    return {"family_uuid": family_uuid, "project": project, "assembly_id": assembly_id}


@pytest.fixture(scope="module")
def answers(stored_family: dict[str, str]) -> dict[str, Any]:
    """Every case through both paths, in one event loop."""
    import backend.app.services.clickhouse_family_variants as family_variants
    from backend.app.services.clickhouse_variant_queries import IMPUTED_SMALL_VARIANT_SOURCES
    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    context = _context(stored_family["family_uuid"], stored_family["project"], stored_family["assembly_id"])

    async def _run() -> dict[str, Any]:
        default = await family_variants._fetch_small_variant_rows(
            context, SmallVariantQueryFilters(page=1, page_size=100)
        )
        imputed = [
            record
            for source in IMPUTED_SMALL_VARIANT_SOURCES
            for record in await family_variants._fetch_small_variant_rows(
                context, SmallVariantQueryFilters(page=1, page_size=100, source=source)
            )
        ]
        read = {"default": default, "every callset": [*default, *imputed]}
        out: dict[str, Any] = {"stored": {record.variant_id for record in read["every callset"]}}
        for name, case in CASES.items():
            monkeypatch = pytest.MonkeyPatch()
            try:
                _stub_lookups(monkeypatch, case)
                out[name] = await _both_paths(context, case, read)
            finally:
                monkeypatch.undo()
        return out

    async def _wrapped() -> dict[str, Any]:
        try:
            return await _run()
        finally:
            await _close_stores()

    return asyncio.run(_wrapped())


def test_every_variant_is_stored_and_read_back(answers: dict[str, Any]) -> None:
    assert answers["stored"] == set(_VARIANTS)


def _differences(answer: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Each answer that is not the expected one: for a set of variants, those it keeps that
    the rule leaves out, and those it leaves out that the rule keeps."""
    differences: dict[str, Any] = {}
    for key, wanted in expected.items():
        got = answer.get(key)
        if got == wanted:
            continue
        if isinstance(got, frozenset) and isinstance(wanted, frozenset):
            differences[key] = {"kept too": sorted(got - wanted), "left out": sorted(wanted - got)}
        else:
            differences[key] = got
    return differences


@pytest.mark.parametrize("name", list(CASES))
def test_sql_and_python_keep_what_the_filter_keeps(answers: dict[str, Any], name: str) -> None:
    assert _differences(answers[name], _expected_answer(CASES[name])) == {}


def test_every_filter_field_is_exercised() -> None:
    """A field added to ``SmallVariantQueryFilters`` needs its case here."""
    import dataclasses

    from backend.app.services.family_variant_filters import SmallVariantQueryFilters

    exercised = {name for case in CASES.values() for name in case.filters}
    fields = {item.name for item in dataclasses.fields(SmallVariantQueryFilters)} - {"page", "page_size"}
    assert fields <= exercised, sorted(fields - exercised)


def _page_ids(page: Any) -> frozenset[str]:
    ids = {str(variant.id) for variant in page.variants}
    for group in page.variant_groups or []:
        ids.update(str(variant.id) for variant in group.variants)
    return frozenset(ids)


@pytest.mark.parametrize("name", list(PAGE_CASES))
def test_the_page_keeps_the_same_variants_plain_and_ranked(
    monkeypatch: pytest.MonkeyPatch, stored_family: dict[str, str], name: str
) -> None:
    import backend.app.services.clickhouse_family_variants as family_variants

    case = PAGE_CASES[name]
    context = _context(stored_family["family_uuid"], stored_family["project"], stored_family["assembly_id"])
    _stub_lookups(monkeypatch, case)

    async def _none(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _empty(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def _no_phenotypes(_session: Any, _context: Any) -> tuple[list[str], dict[str, str]]:
        return [], {}

    async def _hashes(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
        return f"inputs-{name}", f"base-{name}"

    async def _no_supersets(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(family_variants, "_hydrate_small_variant_outs", _none)
    monkeypatch.setattr(family_variants, "_affected_present_hpo", _no_phenotypes)
    monkeypatch.setattr(family_variants, "compute_ranking_hashes", _hashes)
    monkeypatch.setattr(family_variants, "get_cached_ranking", _none)
    monkeypatch.setattr(family_variants, "store_ranking", _none)
    monkeypatch.setattr(family_variants, "find_superset_candidates", _no_supersets)
    monkeypatch.setattr(family_variants, "_fetch_gene_constraint_metric_map", _empty)

    async def _run() -> dict[str, frozenset[str]]:
        try:
            pages = {}
            for view, prioritize in (("plain", False), ("ranked", True)):
                page = await family_variants.get_family_small_variants_page(
                    None,  # type: ignore[arg-type]
                    context=context,
                    page=1,
                    page_size=100,
                    prioritize=prioritize,
                    **{"chr" if key == "chromosome" else key: value for key, value in case.filters.items()},
                )
                pages[view] = _page_ids(page)
            return pages
        finally:
            await _close_stores()

    assert _differences(asyncio.run(_run()), {"plain": case.expected, "ranked": case.expected}) == {}
