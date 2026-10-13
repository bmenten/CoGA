"""A variant kept on another transcript than its own names that one, in every view (E2E).

The annotation filters must all hold on one transcript of a variant, while a variant's
fields describe its primary transcript (MANE Select, else canonical, else the most severe).
So SIFT *deleterious* keeps a variant whose canonical transcript SIFT calls tolerated and a
second one deleterious, the ready case of test_e2e_in_silico_prediction_filters.py
(20-62900601), and its card and CSV row read *tolerated* (CLIN-51). The variant now names the
transcript the filters held on (``filter_transcript``) with its values, and keeps its own
fields on the primary transcript, which the ranking reads.

A family is created through the PED upload under a new id each run: one affected proband,
homozygous for each variant, so the recessive modes keep every variant the other filters
keep. A VEP-annotated VCF goes in through the family small-variant upload, so its CSQ is
parsed and stored as an import stores it. Each filter set is sent, as the query string the
filter form builds, to the family page unranked (ClickHouse's page), ranked as the page opens
(computed, then served from the ranking cache), in the recessive mode (the candidate read)
and the homozygous recessive mode, and to the family's CSV export, unranked and ranked. Every
view names the same transcript with the same values, or none. Everything runs over HTTP
through an in-process ``httpx.ASGITransport`` client, on one event loop (see
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

_CSQ_FORMAT = "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|SIFT|PolyPhen|gnomADe_AF"


def _csq(
    consequence: str, impact: str, gene: str, transcript: str, *, sift: str = "", polyphen: str = "", canonical: bool
) -> str:
    """One VEP CSQ entry, rare in gnomAD."""
    return "|".join(
        [
            "T", consequence, impact, gene, f"ENSG_{gene}", "Transcript", transcript,
            "YES" if canonical else "", sift, polyphen, "0.00001",
        ]
    )


# The ready case: SIFT and PolyPhen call the canonical transcript tolerated and benign, a
# second one deleterious and probably damaging.
_READY_CASE = "20-63000101-C-T"
# One transcript, deleterious and probably damaging.
_ONE_TRANSCRIPT = "20-63000201-C-T"
# Synonymous on the canonical transcript, missense (SIFT tolerated) on a second one.
_MISSENSE_ON_A_SECOND_TRANSCRIPT = "20-63000301-C-T"

_VARIANTS: dict[str, list[str]] = {
    _READY_CASE: [
        _csq("missense_variant", "MODERATE", "SYN51A", "ENST_SYN51A_1",
             sift="tolerated(0.2)", polyphen="benign(0.1)", canonical=True),
        _csq("missense_variant", "MODERATE", "SYN51A", "ENST_SYN51A_2",
             sift="deleterious(0.02)", polyphen="probably_damaging(0.95)", canonical=False),
    ],
    _ONE_TRANSCRIPT: [
        _csq("missense_variant", "MODERATE", "SYN51B", "ENST_SYN51B_1",
             sift="deleterious(0.01)", polyphen="probably_damaging(0.998)", canonical=True),
    ],
    _MISSENSE_ON_A_SECOND_TRANSCRIPT: [
        _csq("synonymous_variant", "LOW", "SYN51C", "ENST_SYN51C_1", canonical=True),
        _csq("missense_variant", "MODERATE", "SYN51C", "ENST_SYN51C_2",
             sift="tolerated(0.5)", polyphen="benign(0.02)", canonical=False),
    ],
}

# What each variant shows of itself: its transcript, SIFT and PolyPhen (the primary
# transcript's, whatever the filters).
_OWN = {
    _READY_CASE: ("ENST_SYN51A_1", "tolerated(0.2)", "benign(0.1)"),
    _ONE_TRANSCRIPT: ("ENST_SYN51B_1", "deleterious(0.01)", "probably_damaging(0.998)"),
    _MISSENSE_ON_A_SECOND_TRANSCRIPT: ("ENST_SYN51C_1", None, None),
}
_READY_CASE_SECOND = ("ENST_SYN51A_2", "missense_variant", "deleterious(0.02)", "probably_damaging(0.95)")
_MISSENSE_SECOND = ("ENST_SYN51C_2", "missense_variant", "tolerated(0.5)", "benign(0.02)")

# The filter sets, as query parameters, and for each variant kept the transcript it names
# (its id, consequence, SIFT and PolyPhen) or None.
_CASES: dict[str, tuple[dict[str, Any], dict[str, tuple[str, str, str, str] | None]]] = {
    "no filter": (
        {},
        {_READY_CASE: None, _ONE_TRANSCRIPT: None, _MISSENSE_ON_A_SECOND_TRANSCRIPT: None},
    ),
    "SIFT deleterious": (
        {"sift": "deleterious"},
        {_READY_CASE: _READY_CASE_SECOND, _ONE_TRANSCRIPT: None},
    ),
    "PolyPhen probably damaging": (
        {"polyphen": "probably_damaging"},
        {_READY_CASE: _READY_CASE_SECOND, _ONE_TRANSCRIPT: None},
    ),
    # The primary transcript of the ready case is the one SIFT calls tolerated.
    "SIFT tolerated": (
        {"sift": "tolerated"},
        {_READY_CASE: None, _MISSENSE_ON_A_SECOND_TRANSCRIPT: _MISSENSE_SECOND},
    ),
    # The default view's impact filter: a synonymous variant kept as a missense elsewhere.
    "impact HIGH or MODERATE": (
        {"impact": ["HIGH", "MODERATE"]},
        {_READY_CASE: None, _ONE_TRANSCRIPT: None, _MISSENSE_ON_A_SECOND_TRANSCRIPT: _MISSENSE_SECOND},
    ),
}

# The views of the family page: the query string's own view parameters. The ranked view is
# opened twice: computed, then served from the ranking cache.
_FAMILY_VIEWS: dict[str, dict[str, str]] = {
    "unranked": {"prioritize": "false"},
    "ranked": {"prioritize": "true"},
    "ranked, from the cache": {"prioritize": "true"},
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


def _shown(page: dict[str, Any]) -> dict[str, Any]:
    """Each variant of a family page: its own transcript, SIFT and PolyPhen, the transcript
    it names, and its priority score when ranked."""
    variants = list(page["variants"])
    for group in page.get("variant_groups") or []:
        variants.extend(group["variants"])
    return {
        variant["_id"]: {
            "own": (variant["transcript_id"], variant["sift"], variant["polyphen"]),
            "named": (
                (
                    variant["filter_transcript"]["transcript_id"],
                    variant["filter_transcript"]["effect"],
                    variant["filter_transcript"]["sift"],
                    variant["filter_transcript"]["polyphen"],
                )
                if variant.get("filter_transcript")
                else None
            ),
            "score": (variant.get("priority") or {}).get("combined_score"),
        }
        for variant in variants
    }


def _exported(text: str) -> dict[str, Any]:
    """Each row of a family CSV export: its own transcript, SIFT and PolyPhen, and the
    transcript it names (None in a file without the columns)."""
    rows = {}
    for row in csv.DictReader(io.StringIO(text)):
        variant_id = "-".join([row["Chromosome"].removeprefix("chr"), row["Start"], row["Ref"], row["Alt"]])
        rows[variant_id] = {
            "own": (row["Transcript"], row["SIFT"] or None, row["PolyPhen"] or None),
            "named": (
                (
                    row["Filter-matched transcript"],
                    row["Filter-matched effect"],
                    row["Filter-matched SIFT"],
                    row["Filter-matched PolyPhen"],
                )
                if row.get("Filter-matched transcript")
                else None
            ),
        }
    return rows


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
        _admin, project_id, _assembly_id = await _harness.ensure_e2e_project(session)

    proband = f"PROBAND_{family_id}"
    out: dict[str, Any] = {"pages": {}, "exports": {}, "cached": {}}
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

        for name, (filters, _expected) in _CASES.items():
            pages: dict[str, Any] = {}
            for view, view_params in _FAMILY_VIEWS.items():
                response = await ac.get(
                    f"/api/families/{family_id}/small-variants",
                    params={"page": 1, "page_size": 100, **view_params, **filters},
                )
                if response.status_code != 200:
                    pages[view] = (response.status_code, response.text)
                    continue
                pages[view] = _shown(response.json())
                out["cached"][(name, view)] = response.json().get("ranking_cached")
            out["pages"][name] = pages

            exports: dict[str, Any] = {}
            for view, prioritize in (("unranked", "false"), ("ranked", "true")):
                response = await ac.get(
                    f"/api/families/{family_id}/small-variants/export",
                    params={"prioritize": prioritize, **filters},
                )
                exports[view] = (
                    _exported(response.text)
                    if response.status_code == 200
                    else (response.status_code, response.text)
                )
            out["exports"][name] = exports
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("filter_transcript_upload"))
    try:
        return _harness.run_async(lambda: _exercise(f"CL51_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_family_and_its_variants_are_stored(run: dict[str, Any]) -> None:
    assert run["setup"]["ped"][0] == 200, run["setup"]["ped"]
    assert run["setup"]["upload"][0] == 200, run["setup"]["upload"]


@pytest.mark.parametrize("case", list(_CASES))
def test_every_view_names_the_transcript_the_filters_held_on(run: dict[str, Any], case: str) -> None:
    _filters, expected = _CASES[case]
    shown = {
        variant_id: {"own": _OWN[variant_id], "named": named} for variant_id, named in expected.items()
    }
    pages = run["pages"][case]
    for view in _FAMILY_VIEWS:
        page = pages[view]
        assert isinstance(page, dict), (view, page)
        assert {variant_id: {"own": v["own"], "named": v["named"]} for variant_id, v in page.items()} == shown, view
    assert run["exports"][case] == {"unranked": shown, "ranked": shown}


@pytest.mark.parametrize("case", list(_CASES))
def test_the_ranked_view_is_served_from_its_cache_the_second_time(run: dict[str, Any], case: str) -> None:
    assert run["cached"][(case, "ranked")] is False
    assert run["cached"][(case, "ranked, from the cache")] is True


def test_the_ranking_scores_the_primary_transcript_whatever_the_filters(run: dict[str, Any]) -> None:
    # The SIFT filter that keeps the ready case on its second transcript leaves its score
    # as it is without a filter: the score reads its primary transcript.
    unfiltered = run["pages"]["no filter"]["ranked"][_READY_CASE]["score"]
    assert unfiltered is not None
    for case in ("SIFT deleterious", "PolyPhen probably damaging", "SIFT tolerated"):
        assert run["pages"][case]["ranked"][_READY_CASE]["score"] == unfiltered, case
