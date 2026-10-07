from __future__ import annotations

import asyncio
import logging
import random
import types

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError
from sqlalchemy.exc import DBAPIError

from backend.app.services import sample_integrity_service
from backend.app.services.nipt_analysis import PaternalTransmissionEvidence
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.report_signout_service import _canonical_sample_qc

SAMPLES = ["FATHER", "MOTHER", "CHILD"]


def _context() -> FamilyMetadataContext:
    sample_rows = [
        {"sample_id": "FATHER", "role": "father", "sex": "male"},
        {"sample_id": "MOTHER", "role": "mother", "sex": "female"},
        {"sample_id": "CHILD", "role": "proband", "sex": "male"},
    ]
    relationship_rows = [
        {"relationship_type": "parent_child", "sample_id_a": "FATHER", "sample_id_b": "CHILD", "role_a": "father"},
        {"relationship_type": "parent_child", "sample_id_a": "MOTHER", "sample_id_b": "CHILD", "role_a": "mother"},
    ]
    return FamilyMetadataContext(
        family_uuid="fam-uuid",
        family_id="FAM1",
        project_ids=[],
        sample_rows=sample_rows,
        sample_uuid_to_name={f"uuid-{s}": s for s in SAMPLES},
        sample_name_to_uuid={s: f"uuid-{s}" for s in SAMPLES},
        affected_sample_names=["CHILD"],
        assembly_id="asm",
        assembly_name="GRCh38",
        relationship_rows=relationship_rows,
    )


def _phased(gt: tuple[int, int]) -> str:
    return f"{gt[0]}|{gt[1]}"


def _autosomal_rows(n: int, seed: int, swap_child: bool):
    rng = random.Random(seed)
    rows = []
    stranger = random.Random(seed + 777)
    for i in range(n):
        f = (int(rng.random() < 0.5), int(rng.random() < 0.5))
        m = (int(rng.random() < 0.5), int(rng.random() < 0.5))
        if swap_child:
            c = (int(stranger.random() < 0.5), int(stranger.random() < 0.5))
        else:
            c = (rng.choice(f), rng.choice(m))
        chrom = str(i % 22 + 1)
        rows.append((chrom, i, "A", "G", SAMPLES, [_phased(f), _phased(m), _phased(c)]))
    return rows


def _x_rows(n: int):
    rng = random.Random(3)
    rows = []
    for i in range(n):
        father = (1, 1) if i % 2 else (0, 0)  # male: hemizygous -> hom
        child = (0, 0) if i % 2 else (1, 1)  # male
        mother = (int(rng.random() < 0.5), int(rng.random() < 0.5))  # female: het
        rows.append(("X", i, "A", "G", SAMPLES, [_phased(father), _phased(mother), _phased(child)]))
    return rows


def _patch(monkeypatch, *, swap_child: bool, metadata: dict | None = None):
    async def _fake_context(session, *, family_identifier, user, project_id=None):
        return _context()

    async def _fake_get_family(session, family_id, user):
        return types.SimpleNamespace(metadata=metadata or {})

    async def _fake_sources(context):
        return ["clair3"]

    async def _fake_sample(context, *, scope, limit, source):
        if scope == "chrX":
            return _x_rows(600)
        # A fixed seed: one taken from builtin hash() (randomized per process) made the
        # relatedness occasionally dip to "warn" in CI.
        return _autosomal_rows(2_400, seed=11, swap_child=swap_child)

    monkeypatch.setattr(sample_integrity_service, "build_family_metadata_context", _fake_context)
    monkeypatch.setattr(sample_integrity_service, "get_family_record", _fake_get_family)
    monkeypatch.setattr(sample_integrity_service, "fetch_family_variant_sources", _fake_sources)
    monkeypatch.setattr(sample_integrity_service, "fetch_genotype_site_sample", _fake_sample)


# A value a failed query was given, which the error's text quotes.
_QUOTED_VALUE = "SYNTHETIC-VALUE-17"


def _frozen(report) -> str:
    """The Sample QC as sign-out freezes it into the signed report snapshot."""
    return str(_canonical_sample_qc(report))


def test_service_clean_trio_passes(monkeypatch) -> None:
    _patch(monkeypatch, swap_child=False)
    report = asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id="FAM1", user=None
        )
    )
    assert report.overall_status == "pass"
    assert {c.sample_id for c in report.sex_checks} == set(SAMPLES)
    assert all(c.status == "pass" for c in report.sex_checks)
    pc = [c for c in report.relatedness_checks if c.expected_relationship == "parent-child"]
    assert len(pc) == 2 and all(c.status == "pass" for c in pc)
    assert all(c.status == "pass" for c in report.mendelian_checks)
    # A trio with parent-child edges resolves to the WGS application on clair3.
    assert report.application == "wgs"
    assert report.genotype_source == "clair3"
    # Every site of the autosomal sample is loaded.
    assert report.autosomal_sites == 2_400


def test_service_swapped_child_fails(monkeypatch) -> None:
    _patch(monkeypatch, swap_child=True)
    report = asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id="FAM1", user=None
        )
    )
    assert report.overall_status == "fail"
    pc = [c for c in report.relatedness_checks if c.expected_relationship == "parent-child"]
    assert any(c.status == "fail" for c in pc)
    assert any(c.status == "fail" for c in report.mendelian_checks)


def test_service_notes_a_failed_genotype_load_without_the_error_text(monkeypatch) -> None:
    # ClickHouse's text for a failed query can quote a value it was given. The note is
    # frozen into the signed report snapshot, so it says what did not load, not why.
    _patch(monkeypatch, swap_child=False)

    async def _unreadable_value(context, *, scope, limit, source):
        raise DatabaseError(
            f"Code: 6. DB::Exception: Cannot parse string '{_QUOTED_VALUE}' as UInt32. "
            "(CANNOT_PARSE_TEXT)"
        )

    monkeypatch.setattr(sample_integrity_service, "fetch_genotype_site_sample", _unreadable_value)
    report = asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id="FAM1", user=None
        )
    )
    assert report.overall_status == "warn"
    assert report.notes[0] == "Genotypes could not be loaded."
    assert _QUOTED_VALUE not in _frozen(report)
    assert "DB::Exception" not in _frozen(report)


def test_service_nipt_runs_paternity_parent_sex_and_category_qc(monkeypatch) -> None:
    # A monogenic-NIPT family runs paternity (cat 7/8) + fetal sex + the category
    # distribution QC + germline parent sex (X zygosity, cfDNA excluded), but no
    # genotype relatedness/Mendelian checks.
    _patch(monkeypatch, swap_child=False, metadata={"analysis_type": "monogenic_nipt"})
    # The cfDNA sample is the mother member's maternal-plasma sample, so it sexes
    # the mother; there is no separate maternal germline sample.
    monkeypatch.setattr(
        sample_integrity_service, "resolve_nipt_trio",
        lambda family: types.SimpleNamespace(
            father_sample_id="FATHER", cfdna_sample_id="MOTHER"
        ),
    )
    import backend.app.services.nipt_service as nipt_service

    async def _fake_nipt(session, *, family_id, user, project_id=None, **kwargs):
        return types.SimpleNamespace(
            category_counts={1: 1, 2: 30, 3: 16, 4: 14, 7: 40, 8: 2},
            paternal_evidence={7: 40, 8: 2},
            paternal_transmission=PaternalTransmissionEvidence(
                hom_alt_transmitted=40, hom_alt_not_transmitted=1, het_transmitted=60, het_not_transmitted=55
            ),
            fetal_sex=types.SimpleNamespace(
                inferred="female", x_transmitted=12, x_not_transmitted=0, informative_sites=12
            ),
        )

    monkeypatch.setattr(nipt_service, "run_family_nipt_analysis", _fake_nipt)

    report = asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id="FAM1", user=None
        )
    )
    assert report.application == "nipt"
    assert report.relatedness_checks == [] and report.mendelian_checks == []
    # The two germline parents are sexed from X zygosity; the cfDNA mixture is not.
    assert {c.sample_id for c in report.sex_checks} == {"FATHER", "MOTHER"}
    assert all(c.status == "pass" for c in report.sex_checks)
    assert report.paternity_check is not None
    assert report.paternity_check.father == "FATHER"
    assert report.paternity_check.status == "pass"
    # Fetal sex from paternal-X transmission rides along with the NIPT path.
    assert report.fetal_sex_check is not None and report.fetal_sex_check.inferred_sex == "female"
    # Category QC: ~50% maternal transmission (30/60), de-novo low.
    assert report.category_qc_check is not None
    assert report.category_qc_check.maternal_inherited == 30
    assert report.category_qc_check.maternal_informative == 60
    assert report.category_qc_check.status == "pass"
    assert report.overall_status == "pass"


def test_service_nipt_degrades_to_warning_when_analysis_fails(monkeypatch) -> None:
    # The cfDNA analysis raises -> the page degrades to a warning with a note instead
    # of 500-ing. The note is frozen into the signed report snapshot, so it is a fixed
    # sentence: the error's text quotes the failed statement and its parameters.
    _patch(monkeypatch, swap_child=False, metadata={"analysis_type": "monogenic_nipt"})
    monkeypatch.setattr(
        sample_integrity_service, "resolve_nipt_trio",
        lambda family: types.SimpleNamespace(father_sample_id="FATHER", cfdna_sample_id="MOTHER"),
    )
    import backend.app.services.nipt_service as nipt_service

    async def _boom(session, *, family_id, user, project_id=None, **kwargs):
        raise DBAPIError(
            "SELECT artifact_id FROM nipt_artifacts WHERE assay_key = $1",
            (_QUOTED_VALUE,),
            Exception("invalid input syntax"),
        )

    monkeypatch.setattr(nipt_service, "run_family_nipt_analysis", _boom)

    report = asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id="FAM1", user=None
        )
    )
    assert report.overall_status == "warn"
    assert report.notes == ["NIPT cfDNA analysis could not run."]
    assert _QUOTED_VALUE not in _frozen(report)
    assert "SELECT" not in _frozen(report)
    assert report.paternity_check is None


async def _failed_query(*args, **kwargs):
    raise RuntimeError("SELECT ... [parameters: ('Jane Doe',)]")


def _warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == sample_integrity_service.logger.name
    ]


def test_service_logs_a_failed_check_by_its_kind_not_its_text(monkeypatch, caplog) -> None:
    # A failed query's text holds its parameters: the warnings name the error instead.
    import backend.app.services.nipt_service as nipt_service

    caplog.set_level(logging.WARNING, logger=sample_integrity_service.logger.name)
    _patch(monkeypatch, swap_child=False, metadata={"analysis_type": "monogenic_nipt"})
    monkeypatch.setattr(
        sample_integrity_service, "resolve_nipt_trio",
        lambda family: types.SimpleNamespace(father_sample_id="FATHER", cfdna_sample_id="MOTHER"),
    )
    monkeypatch.setattr(nipt_service, "run_family_nipt_analysis", _failed_query)
    asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id="FAM1", user=None
        )
    )
    assert _warnings(caplog) == ["NIPT cfDNA QC could not run for family FAM1: RuntimeError"]

    caplog.clear()
    _patch(monkeypatch, swap_child=False)
    monkeypatch.setattr(sample_integrity_service, "fetch_family_variant_sources", _failed_query)
    asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id="FAM1", user=None
        )
    )
    assert _warnings(caplog) == ["Genotypes could not be loaded for family FAM1: RuntimeError"]


def _nipt_site(variant_id: str, *, father_state: str, father_dp: int | None, present: bool):
    from backend.app.services.nipt_analysis import NiptSiteObservation

    # An absent allele at 400x and FF 10% would have shown 20 reads: it cannot be missed.
    return NiptSiteObservation(
        variant_id=variant_id, chrom="1", pos=100, is_autosomal=True,
        cf_present=present, cf_dp=400, cf_alt_reads=20 if present else 0,
        cf_vaf=0.05 if present else 0.0, cf_qual=40.0,
        father_state=father_state, father_dp=father_dp, father_qual=None,
    )


def test_service_nipt_paternity_ignores_sites_without_a_confident_father_call(monkeypatch) -> None:
    # The recorded father has no call at any FF/2 site, and his confident hom-alt alleles are
    # absent from the cfDNA. The no-call sites land in category 7 on the de novo prior alone;
    # counted as paternal transmission, they read "paternity supported".
    from backend.app.services.nipt_analysis import NiptQualityThresholds, run_nipt_analysis

    _patch(monkeypatch, swap_child=False, metadata={"analysis_type": "monogenic_nipt"})
    monkeypatch.setattr(
        sample_integrity_service, "resolve_nipt_trio",
        lambda family: types.SimpleNamespace(father_sample_id="FATHER", cfdna_sample_id="MOTHER"),
    )
    import backend.app.services.nipt_service as nipt_service

    sites = [
        _nipt_site(f"nocall-{i}", father_state="missing", father_dp=None, present=True)
        for i in range(40)
    ] + [
        _nipt_site(f"absent-{i}", father_state="hom_alt", father_dp=50, present=False)
        for i in range(24)
    ]
    # These sites alone give no fetal fraction; impose the 0.10 that informative sites
    # elsewhere in the callset would give, so the no-call sites are classified.
    import dataclasses

    from backend.app.services import nipt_analysis

    estimate = nipt_analysis.estimate_fetal_fraction
    monkeypatch.setattr(
        nipt_analysis,
        "estimate_fetal_fraction",
        lambda sites, qc, **kwargs: dataclasses.replace(
            estimate(sites, qc, **kwargs), ff=0.10, ff_computed=0.10
        ),
    )
    analysis = run_nipt_analysis(sites, NiptQualityThresholds())
    assert analysis.category_counts[7] == 40 and analysis.category_counts[8] == 24

    async def _fake_nipt(session, *, family_id, user, project_id=None, **kwargs):
        return analysis

    monkeypatch.setattr(nipt_service, "run_family_nipt_analysis", _fake_nipt)

    report = asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id="FAM1", user=None
        )
    )

    assert report.paternity_check is not None
    assert (report.paternity_check.cat7_transmitted, report.paternity_check.cat8_absent) == (0, 24)
    assert report.paternity_check.status == "fail"
    assert report.overall_status == "fail"


@pytest.mark.parametrize(
    ("gt", "expected"),
    [
        ("0|1", (0, 1)),
        ("1/1", (1, 1)),
        ("0/0", (0, 0)),
        # Haploid (a male's non-PAR chrX from ploidy-1 callers) reads as the homozygote.
        ("1", (1, 1)),
        ("0", (0, 0)),
        # Missing data stays missing: a no-call, a half call, a non-genotype, a polyploid call.
        ("./.", None),
        (".", None),
        ("", None),
        (None, None),
        ("./1", None),
        ("1/.", None),
        ("HET", None),
        ("0/0/1", None),
    ],
)
def test_parse_genotype(gt, expected) -> None:
    assert sample_integrity_service._parse_genotype(gt) == expected


def test_service_sexes_a_haploid_called_nipt_father(monkeypatch) -> None:
    # A caller that emits ploidy-1 calls writes the father's non-PAR chrX as "0" / "1". Read
    # as missing, his sex check stayed indeterminate, which the sign-out gate treats as an
    # unverified identity for a NIPT father.
    _patch(monkeypatch, swap_child=False, metadata={"analysis_type": "monogenic_nipt"})
    monkeypatch.setattr(
        sample_integrity_service, "resolve_nipt_trio",
        lambda family: types.SimpleNamespace(father_sample_id="FATHER", cfdna_sample_id="MOTHER"),
    )
    import backend.app.services.nipt_service as nipt_service

    async def _fake_nipt(session, *, family_id, user, project_id=None, **kwargs):
        return types.SimpleNamespace(
            category_counts={2: 30, 3: 16, 4: 14, 7: 40, 8: 2},
            paternal_evidence={7: 40, 8: 2},
            paternal_transmission=PaternalTransmissionEvidence(
                hom_alt_transmitted=40, hom_alt_not_transmitted=1, het_transmitted=60, het_not_transmitted=55
            ),
            fetal_sex=types.SimpleNamespace(
                inferred="female", x_transmitted=12, x_not_transmitted=0, informative_sites=12
            ),
        )

    monkeypatch.setattr(nipt_service, "run_family_nipt_analysis", _fake_nipt)
    rng = random.Random(5)

    async def _haploid_x_sample(context, *, scope, limit, source):
        assert scope == "chrX"
        rows = []
        for i in range(600):
            father = "1" if i % 2 else "0"
            mother = _phased((int(rng.random() < 0.5), int(rng.random() < 0.5)))
            rows.append(("X", i, "A", "G", SAMPLES, [father, mother, "."]))
        return rows

    monkeypatch.setattr(sample_integrity_service, "fetch_genotype_site_sample", _haploid_x_sample)

    report = asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id="FAM1", user=None
        )
    )

    father = next(c for c in report.sex_checks if c.sample_id == "FATHER")
    assert (father.inferred_sex, father.x_sites, father.status) == ("male", 600, "pass")


# --- The genotype sample spans the genome --------------------------------------

_PGT_SAMPLES = ["FATHER", "MOTHER", "E1", "E2"]
_PHASED = {(a, b): f"{a}|{b}" for a in (0, 1) for b in (0, 1)}


class _SiblingCallset:
    """A dense synthetic PGT family: the parents and two sibling embryos, E1 and E2.

    Each autosome is laid out as the embryos' haplotype sharing: identical (IBD2) over its
    first quarter, one haplotype shared (IBD1) over the next half and none (IBD0) over the
    last quarter, the genome-wide shares of full siblings. chr1-3 hold 120,000 sites each,
    so their first 30,000 sites (a dense callset's first ~30 Mb) lie in the identical part.
    The parents' alleles are drawn with frequency 0.5, and every genotype is a consistent
    transmission.
    """

    def __init__(self) -> None:
        from backend.app.services.clickhouse_variant_ids import small_variant_key

        rng = random.Random(2026)
        self.sites: list[tuple[int, str, int, list[str]]] = []
        for number in range(1, 23):
            chrom = str(number)
            n = 120_000 if number <= 3 else 4_000
            for i in range(n):
                pos = 1_000 * (i + 1)
                bits = rng.getrandbits(4)
                f = (bits & 1, bits >> 1 & 1)
                m = (bits >> 2 & 1, bits >> 3 & 1)
                e1 = (f[0], m[0])
                if i < n // 4:
                    e2 = e1
                elif i < 3 * n // 4:
                    e2 = (f[0], m[1])
                else:
                    e2 = (f[1], m[1])
                key = small_variant_key("GRCh38", f"{chrom}-{pos}-A-G")
                self.sites.append((key, chrom, pos, [_PHASED[f], _PHASED[m], _PHASED[e1], _PHASED[e2]]))

    @staticmethod
    def _row(site):
        _key, chrom, pos, gts = site
        return (chrom, pos, "A", "G", _PGT_SAMPLES, gts)

    def smallest_keys(self, limit: int):
        """What fetch_genotype_site_sample returns: the sites with the smallest keys."""
        return [self._row(site) for site in sorted(self.sites)[:limit]]

    def first_sites(self, chrom: str, n: int):
        return [self._row(site) for site in self.sites if site[1] == chrom][:n]

    @staticmethod
    def x_rows():
        # FATHER and E2 are male (one X); MOTHER and E1, who has her father's X, are female.
        rng = random.Random(7)
        rows = []
        for i in range(600):
            fx = rng.getrandbits(1)
            m = (rng.getrandbits(1), rng.getrandbits(1))
            gts = [_PHASED[(fx, fx)], _PHASED[m], _PHASED[(fx, m[0])], _PHASED[(m[1], m[1])]]
            rows.append(("X", 3_000_000 + 1_000 * i, "A", "G", _PGT_SAMPLES, gts))
        return rows


def _pgt_context() -> FamilyMetadataContext:
    sample_rows = [
        {"sample_id": "FATHER", "role": "father", "sex": "male"},
        {"sample_id": "MOTHER", "role": "mother", "sex": "female"},
        {"sample_id": "E1", "role": "embryo", "sex": "female"},
        {"sample_id": "E2", "role": "embryo", "sex": "male"},
    ]
    relationship_rows = [
        {"relationship_type": "parent_child", "sample_id_a": parent, "sample_id_b": embryo, "role_a": role}
        for embryo in ("E1", "E2")
        for parent, role in (("FATHER", "father"), ("MOTHER", "mother"))
    ]
    return FamilyMetadataContext(
        family_uuid="fam-pgt",
        family_id="PGT1",
        project_ids=[],
        sample_rows=sample_rows,
        sample_uuid_to_name={f"uuid-{s}": s for s in _PGT_SAMPLES},
        sample_name_to_uuid={s: f"uuid-{s}" for s in _PGT_SAMPLES},
        affected_sample_names=[],
        assembly_id="asm",
        assembly_name="GRCh38",
        relationship_rows=relationship_rows,
    )


def test_service_reads_sibling_embryos_as_siblings_from_sites_across_the_genome(monkeypatch) -> None:
    # Siblings share 0, 1 or 2 haplotypes in blocks tens of Mb long. The QC read the first
    # 30,000 sites of chr1-3, a contiguous ~30 Mb of a dense callset each, and so measured
    # the sharing of three blocks: here, where the siblings are identical, it called them
    # duplicates and failed the family. A sample over every autosome reads them as siblings.
    from backend.app.services.sample_integrity_qc import (
        IBS0_PARENT_CHILD_MAX,
        classify_relatedness,
        king_relatedness,
    )

    callset = _SiblingCallset()
    windows = [row for chrom in ("1", "2", "3") for row in callset.first_sites(chrom, 30_000)]
    e1 = [sample_integrity_service._parse_genotype(row[5][2]) for row in windows]
    e2 = [sample_integrity_service._parse_genotype(row[5][3]) for row in windows]
    assert classify_relatedness(*king_relatedness(e1, e2)) == "duplicate"

    sampled: dict[str, list] = {}

    async def _fake_context(session, *, family_identifier, user, project_id=None):
        return _pgt_context()

    async def _fake_get_family(session, family_id, user):
        return types.SimpleNamespace(metadata={})

    async def _fake_sources(context):
        return ["glimpse2"]

    async def _fake_sample(context, *, scope, limit, source):
        assert source == "glimpse2"
        sampled[scope] = callset.x_rows() if scope == "chrX" else callset.smallest_keys(limit)
        return sampled[scope]

    monkeypatch.setattr(sample_integrity_service, "build_family_metadata_context", _fake_context)
    monkeypatch.setattr(sample_integrity_service, "get_family_record", _fake_get_family)
    monkeypatch.setattr(sample_integrity_service, "fetch_family_variant_sources", _fake_sources)
    monkeypatch.setattr(sample_integrity_service, "fetch_genotype_site_sample", _fake_sample)

    report = asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(
            session=None, family_id="PGT1", user=None
        )
    )

    assert report.application == "pgt"
    assert report.autosomal_sites == sample_integrity_service.QC_AUTOSOMAL_SITES
    assert {row[0] for row in sampled["autosomes"]} == {str(n) for n in range(1, 23)}
    siblings = next(c for c in report.relatedness_checks if {c.sample_a, c.sample_b} == {"E1", "E2"})
    assert siblings.inferred_relationship == "sibling", siblings.message
    assert siblings.status == "pass"
    assert 0.2 < siblings.kinship < 0.3
    assert siblings.ibs0_rate > IBS0_PARENT_CHILD_MAX
    parent_child = [c for c in report.relatedness_checks if c.expected_relationship == "parent-child"]
    assert len(parent_child) == 4 and all(c.status == "pass" for c in parent_child)
    assert all(c.status == "pass" for c in report.mendelian_checks)
    assert all(c.status == "pass" for c in report.sex_checks)
    assert report.overall_status == "pass"


def test_service_sexes_a_per_sample_nipt_callset_from_its_allele_depths(monkeypatch) -> None:
    # A per-sample NIPT callset (Mutect2 tumour-only) writes GT 0/1 whatever the allele
    # fraction, so a hemizygous father read by his GT is "het" on every chrX site: female.
    # His genotypes come from his allele depths instead, and a site where his file has no
    # call is reference.
    from backend.app.services import clickhouse_family_variants

    _patch(monkeypatch, swap_child=False, metadata={"analysis_type": "monogenic_nipt"})
    monkeypatch.setattr(
        sample_integrity_service, "resolve_nipt_trio",
        lambda family: types.SimpleNamespace(father_sample_id="FATHER", cfdna_sample_id="MOTHER"),
    )
    import backend.app.services.nipt_service as nipt_service

    async def _fake_nipt(session, *, family_id, user, project_id=None, **kwargs):
        return types.SimpleNamespace(
            category_counts={2: 30, 3: 16, 4: 14, 7: 40, 8: 2},
            paternal_evidence={7: 40, 8: 2},
            paternal_transmission=PaternalTransmissionEvidence(
                hom_alt_transmitted=40, hom_alt_not_transmitted=1, het_transmitted=60, het_not_transmitted=55
            ),
            fetal_sex=types.SimpleNamespace(
                inferred="female", x_transmitted=12, x_not_transmitted=0, informative_sites=12
            ),
        )

    async def _nipt_source(context):
        return ["nipt"]

    rng = random.Random(11)

    async def _fake_execute(query, params):
        assert "e.calls.ad AS sample_ads" in query
        rows = []
        for i in range(600):
            mother_alt = rng.choice((0, 500, 1000))  # hom-ref, het or hom-alt mother
            if i % 3 == 0:
                # The father's hemizygous allele (GT 0/1 from Mutect2), the mother's call too.
                rows.append((i, "X", 3_000_000 + i, "A", "G", ["FATHER", "MOTHER"], ["0/1", "0/1"],
                             [[1, 299], [1000 - mother_alt, max(mother_alt, 5)]]))
            else:
                # Only the mother has a call: the father's file has none (reference).
                rows.append((i, "X", 3_000_000 + i, "A", "G", ["MOTHER"], ["0/1"],
                             [[1000 - mother_alt, max(mother_alt, 5)]]))
        return rows

    monkeypatch.setattr(nipt_service, "run_family_nipt_analysis", _fake_nipt)
    monkeypatch.setattr(sample_integrity_service, "fetch_family_variant_sources", _nipt_source)
    monkeypatch.setattr(sample_integrity_service, "fetch_genotype_site_sample", clickhouse_family_variants.fetch_genotype_site_sample)
    monkeypatch.setattr(clickhouse_family_variants, "_execute_clickhouse", _fake_execute)

    report = asyncio.run(
        sample_integrity_service.get_family_sample_integrity_qc(session=None, family_id="FAM1", user=None)
    )

    sexes = {check.sample_id: (check.inferred_sex, check.status) for check in report.sex_checks}
    assert sexes["FATHER"] == ("male", "pass")
    assert sexes["MOTHER"] == ("female", "pass")


def test_vaf_genotype() -> None:
    from backend.app.services.clickhouse_variant_queries import vaf_genotype

    assert vaf_genotype([1, 299], "0/1") == "1/1"
    assert vaf_genotype([150, 150], "0/1") == "0/1"
    assert vaf_genotype([280, 20], "0/1") == "0/0"  # 7%: noise, not a genotype
    assert vaf_genotype([3, 2], "0/1") == "./."  # too few reads
    assert vaf_genotype([], "1|1") == "1|1"  # no allele depths: the GT
