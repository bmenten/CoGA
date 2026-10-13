"""The Variant Explorer and the family page keep the same variants for the same filters (E2E).

The family search needs one transcript of a variant to meet every annotation filter
together; the Variant Explorer tested each filter against an index that pools a variant's
transcripts. With *MANE only* and impact HIGH it kept a variant whose HIGH transcript is not
its MANE transcript, which the family page drops, and it required every in-silico minimum
where the family page takes any one (CLIN-29).

A family is created through the PED upload under a new id each run, so the golden trio is
never touched, and a VEP-annotated VCF goes in through the family small-variant upload, so
its CSQ is parsed and stored as an import stores it. Its variants have transcripts that
disagree. Each filter set is then sent, as the one query string the filter form builds for
both pages, to the family page's endpoint (unranked, and ranked as the page opens), and to
the Variant Explorer's search and CSV export, narrowed to the variants' locus because other
runs and tests share the assembly. All answer with exactly the expected variants. Everything
runs over HTTP through an in-process ``httpx.ASGITransport`` client, on one event loop (see
test_e2e_api_contract.py for why).

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e``
job sets it.
"""

from __future__ import annotations

import csv
import io
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

# Chromosome 9 around 136.8 Mb: no other test writes there, and the explorer counts every
# family of the assembly.
_LOCUS = {"chr": "9", "start": 136_800_000, "end": 136_801_000}

_CSQ_FORMAT = (
    "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|MANE_SELECT"
    "|MANE_PLUS_CLINICAL|LoF|REVEL|CADD_PHRED|gnomADe_AF|CLIN_SIG"
)


def _csq(
    consequence: str,
    impact: str,
    gene: str,
    transcript: str,
    *,
    mane: str = "",
    lof: str = "",
    revel: str = "",
    cadd: str = "",
    af: str = "0.00001",
    clinvar: str = "",
) -> str:
    """One VEP CSQ entry. A MANE transcript is also the canonical one."""
    canonical = "YES" if mane else ""
    return "|".join(
        [
            "T", consequence, impact, gene, f"ENSG_{gene}", "Transcript", transcript,
            canonical, mane, "", lof, revel, cadd, af, clinvar,
        ]
    )


# HIGH on a transcript that is neither MANE nor canonical; MODIFIER on the MANE one.
_HIGH_OFF_MANE = "9-136800101-C-T"
# One transcript: MANE, HIGH and LoF.
_MANE_HIGH = "9-136800201-C-T"
# Missense on the MANE transcript, a stop gained (LoF) on another.
_LOF_OFF_MANE = "9-136800301-C-T"
# Synonymous on the MANE transcript, a damaging missense (REVEL 0.9) on another.
_REVEL_OFF_MANE = "9-136800401-C-T"
# A missense with CADD 30 and no REVEL score.
_CADD_ONLY = "9-136800501-C-T"
# A common missense (gnomAD 8%) that ClinVar calls likely pathogenic.
_COMMON_LIKELY_PATHOGENIC = "9-136800601-C-T"

_VARIANTS: dict[str, list[str]] = {
    _HIGH_OFF_MANE: [
        _csq("stop_gained", "HIGH", "SYN29G", "ENST_SYN29G_1", cadd="35"),
        _csq("intron_variant", "MODIFIER", "SYN29G", "ENST_SYN29G_2", mane="NM_SYN29G.1", cadd="35"),
    ],
    _MANE_HIGH: [
        _csq("stop_gained", "HIGH", "SYN29H", "ENST_SYN29H_1", mane="NM_SYN29H.1", lof="HC", cadd="40"),
    ],
    _LOF_OFF_MANE: [
        _csq("missense_variant", "MODERATE", "SYN29I", "ENST_SYN29I_1", mane="NM_SYN29I.1", revel="0.3", cadd="24"),
        _csq("stop_gained", "HIGH", "SYN29I", "ENST_SYN29I_2", lof="HC", cadd="24"),
    ],
    _REVEL_OFF_MANE: [
        _csq("synonymous_variant", "LOW", "SYN29J", "ENST_SYN29J_1", mane="NM_SYN29J.1", cadd="12"),
        _csq("missense_variant", "MODERATE", "SYN29J", "ENST_SYN29J_2", revel="0.9", cadd="12"),
    ],
    _CADD_ONLY: [
        _csq("missense_variant", "MODERATE", "SYN29K", "ENST_SYN29K_1", mane="NM_SYN29K.1", cadd="30"),
    ],
    _COMMON_LIKELY_PATHOGENIC: [
        _csq(
            "missense_variant", "MODERATE", "SYN29L", "ENST_SYN29L_1", mane="NM_SYN29L.1",
            revel="0.7", cadd="28", af="0.08", clinvar="likely_pathogenic",
        ),
        _csq(
            "missense_variant", "MODERATE", "SYN29L", "ENST_SYN29L_2",
            revel="0.6", cadd="28", af="0.08", clinvar="likely_pathogenic",
        ),
    ],
}
_ALL = set(_VARIANTS)

# The filter sets, as query parameters (the two endpoints read the same names), and the
# variants both pages keep.
_CASES: dict[str, tuple[dict[str, Any], set[str]]] = {
    "no filter": ({}, _ALL),
    "MANE only and HIGH": ({"mane_only": "true", "impact": ["HIGH"]}, {_MANE_HIGH}),
    "Canonical only and HIGH": ({"canonical_only": "true", "impact": ["HIGH"]}, {_MANE_HIGH}),
    "LoF only and HIGH": ({"lof_only": "true", "impact": ["HIGH"]}, {_MANE_HIGH, _LOF_OFF_MANE}),
    "LoF only and MODERATE": ({"lof_only": "true", "impact": ["MODERATE"]}, set()),
    "MANE only and REVEL": ({"mane_only": "true", "min_revel": "0.5"}, {_COMMON_LIKELY_PATHOGENIC}),
    "CADD or REVEL": ({"min_cadd": "25", "min_revel": "0.5"}, _ALL - {_LOF_OFF_MANE}),
    "frequency": (
        {"max_gnomad_af": "0.01", "clinvar_overrides_frequency": "false"},
        _ALL - {_COMMON_LIKELY_PATHOGENIC},
    ),
    "frequency with the ClinVar rescue": (
        {"max_gnomad_af": "0.01", "clinvar_overrides_frequency": "true"},
        _ALL,
    ),
    "MANE only and ClinVar": (
        {"mane_only": "true", "clinvar": ["Likely_pathogenic"]},
        {_COMMON_LIKELY_PATHOGENIC},
    ),
}

_VCF_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##DeepVariant_version=1.10.0\n"
    "##reference=GRCh38\n"
    f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {_CSQ_FORMAT}">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
)


def _vcf(sample: str) -> bytes:
    lines = [_VCF_HEADER + f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}"]
    for variant_id, entries in _VARIANTS.items():
        chrom, pos, ref, alt = variant_id.split("-")
        csq = ",".join(entries)
        lines.append(f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\tCSQ={csq}\tGT:GQ:DP:AD\t0/1:40:30:15,15")
    return ("\n".join(lines) + "\n").encode()


async def _exercise(family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)

    proband = f"PROBAND_{family_id}"
    out: dict[str, Any] = {"pages": {}}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"

        ped = await ac.post(
            "/api/ped/upload",
            params={"project_id": project_id},
            files={"file": (f"{family_id}.ped", f"{family_id}\t{proband}\t0\t0\t2\t2\n".encode(), "text/plain")},
        )
        upload = await ac.post(
            f"/api/families/{family_id}/small-variants/upload",
            params={"overwrite": "false"},
            files={"file": (f"{family_id}.vcf", _vcf(proband), "text/plain")},
        )
        out["setup"] = {"ped": (ped.status_code, ped.text), "upload": (upload.status_code, upload.text)}

        explorer_scope = {"assembly_id": assembly_id, **_LOCUS}
        for name, (filters, _expected) in _CASES.items():
            answers: dict[str, Any] = {}
            for label, prioritize in (("family page", "false"), ("family page, ranked", "true")):
                response = await ac.get(
                    f"/api/families/{family_id}/small-variants",
                    params={"page": 1, "page_size": 100, "prioritize": prioritize, **filters},
                )
                answers[label] = (
                    {variant["_id"] for variant in response.json()["variants"]}
                    if response.status_code == 200
                    else (response.status_code, response.text)
                )
            response = await ac.get(
                "/api/variant-explorer/small-variants",
                params={"page_size": 200, **explorer_scope, **filters},
            )
            answers["explorer"] = (
                {row["variant_id"] for row in response.json()["variants"]}
                if response.status_code == 200
                else (response.status_code, response.text)
            )
            response = await ac.get(
                "/api/variant-explorer/small-variants/export",
                params={**explorer_scope, **filters},
            )
            answers["explorer export"] = (
                {row["Variant ID"] for row in csv.DictReader(io.StringIO(response.text))}
                if response.status_code == 200
                else (response.status_code, response.text)
            )
            out["pages"][name] = answers
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("explorer_filters_upload"))
    try:
        return _harness.run_async(lambda: _exercise(f"CL29_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_family_and_its_variants_are_stored(run: dict[str, Any]) -> None:
    assert run["setup"]["ped"][0] == 200, run["setup"]["ped"]
    assert run["setup"]["upload"][0] == 200, run["setup"]["upload"]


@pytest.mark.parametrize("case", list(_CASES))
def test_explorer_keeps_what_the_family_page_keeps(run: dict[str, Any], case: str) -> None:
    _filters, expected = _CASES[case]
    assert run["pages"][case] == {
        "family page": expected,
        "family page, ranked": expected,
        "explorer": expected,
        "explorer export": expected,
    }
