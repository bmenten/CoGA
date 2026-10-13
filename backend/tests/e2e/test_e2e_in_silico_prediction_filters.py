"""A SIFT or PolyPhen filter keeps the same variants in every view of the family page (E2E).

VEP writes SIFT and PolyPhen with their score, ``deleterious(0.01)`` and
``probably_damaging(0.998)``. The family search's SQL found the filter's text anywhere in the
value, so ``deleterious`` also kept ``deleterious_low_confidence(0.03)``; the Python matcher,
which runs after the SQL in the ranked default view and the pair-based inheritance modes,
needed the whole value, so ``deleterious(0.01)`` was not ``deleterious`` and a SIFT- or
PolyPhen-only filter kept nothing there (CLIN-45). Now both keep a transcript whose value
names the prediction, whatever its score.

A family is created through the PED upload under a new id each run: one affected proband,
homozygous for each variant, so the recessive modes keep every variant the other filters
keep. A VEP-annotated VCF goes in through the family small-variant upload, so its CSQ is
parsed and stored as an import stores it. Each filter set is sent, as the query string the
filter form builds, to the family page unranked (the SQL alone), ranked as the page opens
(the Python matcher after it), in the recessive mode (pairs and homozygotes: the Python
matcher) and in the homozygous recessive mode (the SQL), and to the Variant Explorer's search
and CSV export, narrowed to the variants' locus because other tests share the assembly. All
answer with exactly the variants whose SIFT or PolyPhen value names the prediction. Everything
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

# Chromosome 20 near 62.9 Mb: no other test writes there, and the explorer counts every
# family of the assembly.
_LOCUS = {"chr": "20", "start": 62_900_000, "end": 62_901_000}

_CSQ_FORMAT = "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|SIFT|PolyPhen|gnomADe_AF"


def _csq(consequence: str, gene: str, transcript: str, *, sift: str = "", polyphen: str = "", canonical: bool = True) -> str:
    """One VEP CSQ entry, rare in gnomAD."""
    impact = "MODERATE" if consequence == "missense_variant" else "LOW"
    return "|".join(
        [
            "T", consequence, impact, gene, f"ENSG_{gene}", "Transcript", transcript,
            "YES" if canonical else "", sift, polyphen, "0.00001",
        ]
    )


# The values as VEP writes them: with the score, or (run to write no score) without it.
_DELETERIOUS = "20-62900101-C-T"  # deleterious(0.01), probably_damaging(0.998)
_DELETERIOUS_LOW_CONFIDENCE = "20-62900201-C-T"  # deleterious_low_confidence(0.03), possibly_damaging(0.612)
_TOLERATED = "20-62900301-C-T"  # tolerated(0.42), benign(0.004)
_TOLERATED_LOW_CONFIDENCE = "20-62900401-C-T"  # tolerated_low_confidence(0.61), unknown(0)
_WITHOUT_SCORES = "20-62900501-C-T"  # deleterious, probably_damaging
_ON_A_SECOND_TRANSCRIPT = "20-62900601-C-T"  # tolerated/benign on one, deleterious/probably_damaging on another
_WITHOUT_PREDICTIONS = "20-62900701-C-T"  # a synonymous variant: neither

_VARIANTS: dict[str, list[str]] = {
    _DELETERIOUS: [
        _csq("missense_variant", "SYN45A", "ENST_SYN45A_1", sift="deleterious(0.01)", polyphen="probably_damaging(0.998)"),
    ],
    _DELETERIOUS_LOW_CONFIDENCE: [
        _csq(
            "missense_variant", "SYN45B", "ENST_SYN45B_1",
            sift="deleterious_low_confidence(0.03)", polyphen="possibly_damaging(0.612)",
        ),
    ],
    _TOLERATED: [
        _csq("missense_variant", "SYN45C", "ENST_SYN45C_1", sift="tolerated(0.42)", polyphen="benign(0.004)"),
    ],
    _TOLERATED_LOW_CONFIDENCE: [
        _csq("missense_variant", "SYN45D", "ENST_SYN45D_1", sift="tolerated_low_confidence(0.61)", polyphen="unknown(0)"),
    ],
    _WITHOUT_SCORES: [
        _csq("missense_variant", "SYN45E", "ENST_SYN45E_1", sift="deleterious", polyphen="probably_damaging"),
    ],
    _ON_A_SECOND_TRANSCRIPT: [
        _csq("missense_variant", "SYN45F", "ENST_SYN45F_1", sift="tolerated(0.2)", polyphen="benign(0.1)"),
        _csq(
            "missense_variant", "SYN45F", "ENST_SYN45F_2",
            sift="deleterious(0.02)", polyphen="probably_damaging(0.95)", canonical=False,
        ),
    ],
    _WITHOUT_PREDICTIONS: [_csq("synonymous_variant", "SYN45G", "ENST_SYN45G_1")],
}
_ALL = set(_VARIANTS)

# The filter sets, as query parameters (both endpoints read the same names), and the variants
# every view keeps. The form offers the first three SIFT and PolyPhen predictions; the API
# takes the others too.
_CASES: dict[str, tuple[dict[str, Any], set[str]]] = {
    "no filter": ({}, _ALL),
    "SIFT deleterious": (
        {"sift": "deleterious"},
        {_DELETERIOUS, _WITHOUT_SCORES, _ON_A_SECOND_TRANSCRIPT},
    ),
    "SIFT deleterious low confidence": ({"sift": "deleterious_low_confidence"}, {_DELETERIOUS_LOW_CONFIDENCE}),
    "SIFT tolerated": ({"sift": "tolerated"}, {_TOLERATED, _ON_A_SECOND_TRANSCRIPT}),
    "SIFT tolerated low confidence": ({"sift": "tolerated_low_confidence"}, {_TOLERATED_LOW_CONFIDENCE}),
    "PolyPhen probably damaging": (
        {"polyphen": "probably_damaging"},
        {_DELETERIOUS, _WITHOUT_SCORES, _ON_A_SECOND_TRANSCRIPT},
    ),
    "PolyPhen possibly damaging": ({"polyphen": "possibly_damaging"}, {_DELETERIOUS_LOW_CONFIDENCE}),
    "PolyPhen benign": ({"polyphen": "benign"}, {_TOLERATED, _ON_A_SECOND_TRANSCRIPT}),
    "PolyPhen unknown": ({"polyphen": "unknown"}, {_TOLERATED_LOW_CONFIDENCE}),
    "SIFT deleterious or PolyPhen benign": (
        {"sift": "deleterious", "polyphen": "benign"},
        {_DELETERIOUS, _WITHOUT_SCORES, _ON_A_SECOND_TRANSCRIPT, _TOLERATED},
    ),
}

# The views of the family page: the query string's own view parameters.
_FAMILY_VIEWS: dict[str, dict[str, str]] = {
    "unranked": {"prioritize": "false"},
    "ranked": {"prioritize": "true"},
    "recessive": {"prioritize": "false", "inheritance": "recessive"},
    "homozygous recessive": {"prioritize": "false", "inheritance": "recessive_homozygous"},
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
        lines.append(f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\tCSQ={csq}\tGT:GQ:DP:AD\t1/1:40:30:0,30")
    return ("\n".join(lines) + "\n").encode()


def _variant_ids(page: dict[str, Any]) -> set[str]:
    """The variants a family page holds: its single variants and those of its pairs."""
    ids = {variant["_id"] for variant in page["variants"]}
    for group in page.get("variant_groups") or []:
        ids.update(variant["_id"] for variant in group["variants"])
    return ids


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
            for view, view_params in _FAMILY_VIEWS.items():
                response = await ac.get(
                    f"/api/families/{family_id}/small-variants",
                    params={"page": 1, "page_size": 100, **view_params, **filters},
                )
                answers[f"family page, {view}"] = (
                    _variant_ids(response.json())
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
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("in_silico_filters_upload"))
    try:
        return _harness.run_async(lambda: _exercise(f"CL45_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_family_and_its_variants_are_stored(run: dict[str, Any]) -> None:
    assert run["setup"]["ped"][0] == 200, run["setup"]["ped"]
    assert run["setup"]["upload"][0] == 200, run["setup"]["upload"]


@pytest.mark.parametrize("case", list(_CASES))
def test_every_view_keeps_the_variants_whose_value_names_the_prediction(run: dict[str, Any], case: str) -> None:
    _filters, expected = _CASES[case]
    assert run["pages"][case] == {
        "family page, unranked": expected,
        "family page, ranked": expected,
        "family page, recessive": expected,
        "family page, homozygous recessive": expected,
        "explorer": expected,
        "explorer export": expected,
    }
