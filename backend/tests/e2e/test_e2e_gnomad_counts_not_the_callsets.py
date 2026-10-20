"""A gnomAD count filter reads gnomAD's counts, never the callset's own (E2E, CLIN-59).

A VCF's INFO/AC is the allele count over its own samples, a reserved key that joint callers
write. The VEP parser read it as gnomAD's allele count wherever the CSQ had none, and the
unprefixed INFO keys ``nhomalt`` and ``HEMI`` as gnomAD's homozygote and hemizygote counts,
so a maximum gnomAD AC filter filtered on the family's own count. Now a population count is
read only from a field that names its source (``gnomAD_AC``, ``gnomAD_nhomalt``,
``gnomAD_Hemi``, …): a variant without one has none, and a maximum keeps it, as absent from
gnomAD (``ifNull(count, 0) <= maximum`` in the SQL, a missing count passing in Python).

A family is created through the PED upload under a new id each run: one affected proband,
homozygous for each variant, so the callset's own AC is 2 at each and the recessive modes
keep every variant the other filters keep. A VEP-annotated VCF goes in through the family
small-variant upload, so its INFO and CSQ are parsed and stored as an import stores them.
Each count filter is sent, as the query string the filter form builds, to the family page
unranked (the SQL alone), ranked as the page opens (the Python matcher after it), in the
recessive mode (the Python matcher) and in the homozygous recessive mode (the SQL), and to
the Variant Explorer's search and CSV export, narrowed to the variants' locus because other
tests share the assembly. All answer with exactly the variants whose gnomAD count is within
the maximum or who have none. The family page serves each variant's gnomAD counts as its CSQ
gives them, and none where the CSQ gives none. Everything runs over HTTP through an
in-process ``httpx.ASGITransport`` client, on one event loop (see test_e2e_api_contract.py
for why).

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

# Chromosome 20 at 63.1 Mb: no other test writes there, and the explorer counts every
# family of the assembly.
_LOCUS = {"chr": "20", "start": 63_100_000, "end": 63_101_000}

_CSQ_FORMAT = (
    "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|gnomADe_AF"
    "|gnomAD_AC|gnomAD_nhomalt|gnomAD_Hemi"
)


def _csq(gene: str, *, ac: str = "", hom: str = "", hemi: str = "") -> str:
    """One VEP CSQ entry, a missense variant rare in gnomAD's exomes, with gnomAD's allele,
    homozygote and hemizygote counts where given."""
    return "|".join(
        [
            "T", "missense_variant", "MODERATE", gene, f"ENSG_{gene}", "Transcript",
            f"ENST_{gene}_1", "YES", "0.00001", ac, hom, hemi,
        ]
    )


# The callset's own allele count (AC, AN) on every record, as a joint caller writes it.
_COUNTS_IN_INFO_ONLY = "20-63100101-C-T"  # INFO nhomalt=3 and HEMI=1 too; the CSQ has no count
_GNOMAD_COUNTS_WITHIN = "20-63100201-C-T"  # gnomAD AC 1, 0 homozygotes, 0 hemizygotes
_GNOMAD_COUNTS_ABOVE = "20-63100301-C-T"  # gnomAD AC 5, 3 homozygotes, 1 hemizygote
_WITHOUT_CSQ = "20-63100401-C-T"  # no annotation at all

_VARIANTS: dict[str, str] = {
    _COUNTS_IN_INFO_ONLY: f"AC=2;AN=2;nhomalt=3;HEMI=1;CSQ={_csq('SYN59A')}",
    _GNOMAD_COUNTS_WITHIN: f"AC=2;AN=2;CSQ={_csq('SYN59B', ac='1', hom='0', hemi='0')}",
    _GNOMAD_COUNTS_ABOVE: f"AC=2;AN=2;CSQ={_csq('SYN59C', ac='5', hom='3', hemi='1')}",
    _WITHOUT_CSQ: "AC=2;AN=2",
}
_ALL = set(_VARIANTS)
_WITHIN = {_COUNTS_IN_INFO_ONLY, _GNOMAD_COUNTS_WITHIN, _WITHOUT_CSQ}

# The filter sets, as query parameters (both endpoints read the same names), and the variants
# every view keeps. Before the fix, _COUNTS_IN_INFO_ONLY failed each maximum on the callset's
# own keys, and _WITHOUT_CSQ the AC one.
_CASES: dict[str, tuple[dict[str, Any], set[str]]] = {
    "no filter": ({}, _ALL),
    "gnomAD AC at most 1": ({"max_gnomad_ac": 1}, _WITHIN),
    "gnomAD homozygotes at most 0": ({"max_gnomad_hom_count": 0}, _WITHIN),
    "gnomAD hemizygotes at most 0": ({"max_gnomad_hemi_count": 0}, _WITHIN),
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
    '##INFO=<ID=AC,Number=A,Type=Integer,Description="Allele count in genotypes">\n'
    '##INFO=<ID=AN,Number=1,Type=Integer,Description="Total number of alleles in called genotypes">\n'
    '##INFO=<ID=nhomalt,Number=A,Type=Integer,Description="Count of homozygous individuals">\n'
    '##INFO=<ID=HEMI,Number=A,Type=Integer,Description="Count of hemizygous individuals">\n'
    f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {_CSQ_FORMAT}">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
)


def _vcf(sample: str) -> bytes:
    lines = [_VCF_HEADER + f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}"]
    for variant_id, info in _VARIANTS.items():
        chrom, pos, ref, alt = variant_id.split("-")
        lines.append(f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t{info}\tGT:GQ:DP:AD\t1/1:40:30:0,30")
    return ("\n".join(lines) + "\n").encode()


def _variant_ids(page: dict[str, Any]) -> set[str]:
    """The variants a family page holds: its single variants and those of its pairs."""
    ids = {variant["_id"] for variant in page["variants"]}
    for group in page.get("variant_groups") or []:
        ids.update(variant["_id"] for variant in group["variants"])
    return ids


def _served_counts(page: dict[str, Any]) -> dict[str, tuple[Any, Any, Any]]:
    """Each variant's gnomAD allele, homozygote and hemizygote counts, as the page serves them."""
    return {
        variant["_id"]: (
            (variant.get("annotation_extra") or {}).get("gnomad_ac"),
            variant.get("gnomad_hom_count"),
            (variant.get("annotation_extra") or {}).get("gnomad_hemi_count"),
        )
        for variant in page["variants"]
    }


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

        response = await ac.get(
            f"/api/families/{family_id}/small-variants",
            params={"page": 1, "page_size": 100, "prioritize": "false"},
        )
        out["served"] = (
            _served_counts(response.json())
            if response.status_code == 200
            else (response.status_code, response.text)
        )

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
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("gnomad_counts_upload"))
    try:
        return _harness.run_async(lambda: _exercise(f"CL59_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def test_family_and_its_variants_are_stored(run: dict[str, Any]) -> None:
    assert run["setup"]["ped"][0] == 200, run["setup"]["ped"]
    assert run["setup"]["upload"][0] == 200, run["setup"]["upload"]


def test_the_page_serves_gnomads_counts_and_none_where_the_csq_has_none(run: dict[str, Any]) -> None:
    # Before the fix, _COUNTS_IN_INFO_ONLY was served (2, 3, 1) and _WITHOUT_CSQ (2, None, None):
    # the callset's own keys, shown as gnomAD's.
    assert run["served"] == {
        _COUNTS_IN_INFO_ONLY: (None, None, None),
        _GNOMAD_COUNTS_WITHIN: (1, 0, 0),
        _GNOMAD_COUNTS_ABOVE: (5, 3, 1),
        _WITHOUT_CSQ: (None, None, None),
    }


@pytest.mark.parametrize("case", list(_CASES))
def test_every_view_keeps_the_variants_within_gnomads_counts(run: dict[str, Any], case: str) -> None:
    _filters, expected = _CASES[case]
    assert run["pages"][case] == {
        "family page, unranked": expected,
        "family page, ranked": expected,
        "family page, recessive": expected,
        "family page, homozygous recessive": expected,
        "explorer": expected,
        "explorer export": expected,
    }
