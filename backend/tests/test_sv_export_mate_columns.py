"""The SV CSV export names a breakend's mate (DATA-5).

The SV list serves a breakend's mate (``remote_chr``, ``remote_start``: its *Remote chr* and
*Remote start* columns), but ``GET /families/{family_id}/structural-variants/export`` wrote
neither, so an exported translocation named no partner, whatever its source. The export now
writes the mate under the list's names, *Remote chr* and *Remote start*, beside the locus
(after *End*), and leaves both empty for an SV without a mate. The ranked export holds the
same columns after its *Priority* and *Rank*.

Each export here runs through the route and the real SV search: unranked (the callset's
native page), filtered (the rows filtered in Python) and ranked (the SV page's default). The
search reads a NeedlR breakend and deletion as the package's NeedlR reader stores them, and a
Sniffles2 breakend and deletion as the SV upload reads and stores them; Postgres holds no
reviews, cytobands or phenotypes. The records and the family are synthetic.
``e2e/test_e2e_sv_length_filter.py`` exports the golden trio's NeedlR breakend and an
uploaded Sniffles2 breakend from the real stores.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.routers import families_structural_variants as sv_router
from backend.app.services.access_control import CurrentUser
from backend.app.services.clickhouse_variant_ids import build_structural_variant_id
from backend.app.services.clickhouse_variant_records import (
    StructuralVariantCall,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_package_common import ParsedPed, PedMember
from backend.app.services.family_package_variants import _iter_needlr_structural_records
from backend.app.services.structural_variant_ingest import iter_structural_variant_records

_EXPORT = "/api/families/FAM001/structural-variants/export"
_M = "backend.app.services.clickhouse_family_variants."
_SAMPLES = ("PROBAND", "FATHER", "MOTHER")
_USER = CurrentUser(
    id="u1",
    username="reviewer",
    email="reviewer@example.org",
    role="viewer",
    created_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
)

# The export's columns in order; the ranked export puts its Priority and Rank first.
_COLUMNS = [
    "Chromosome",
    "Start",
    "End",
    "Remote chr",
    "Remote start",
    "Type",
    "Length",
    "Source",
    "Gene",
    "Cytoband",
    "Inheritance",
    "Control AF",
    "Population AF",
    "pLI",
    "Region flags",
    "Classification",
    "Tags",
    "Genotypes",
]

# A family NeedlR VCF: a deletion, and a breakend whose mate is on chr5:3000000.
_NEEDLR_VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    "chr1\t7000\tSVDEL1\tN\t<DEL>\t60\tPASS\tSVTYPE=DEL;SVLEN=-5000;END=12000;"
    "Query_ID=PROBAND;Genotype=0/1;Alt_Reads=18;Maternal_GT=0/0;Paternal_GT=0/1\n"
    "chr1\t20000\tSVBND1\tN\tN]chr5:3000000]\t30\tPASS\tSVTYPE=BND;"
    "Query_ID=PROBAND;Genotype=0/1;Alt_Reads=7;Maternal_GT=0/0;Paternal_GT=0/0\n"
)
# PROBAND's Sniffles2 calls as Sniffles2 writes them: a deletion, and a breakend whose mate
# is on 7:4000000, another than the NeedlR breakend's.
_SNIFFLES2_VCF = (
    "##fileformat=VCFv4.2\n"
    "##source=Sniffles2_2.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\n"
    "1\t50000\tSniffles2.DEL.3\tN\t<DEL>\t55\tPASS\tPRECISE;SVTYPE=DEL;SVLEN=-5000;END=55000;SUPPORT=11\tGT\t0/1\n"
    "1\t60000\tSniffles2.BND.4\tN\tN[7:4000000[\t50\tPASS\tPRECISE;SVTYPE=BND;SUPPORT=8\tGT\t0/1\n"
)

_SEARCHES: dict[str, dict[str, str]] = {
    "unranked": {},
    "filtered": {"min_length": "50"},  # a translocation passes any minimum (CLIN-25)
    "ranked": {"prioritize": "true"},
}


def _needlr() -> list[StructuralVariantRecord]:
    """The NeedlR calls as the package import stores them (``_iter_needlr_structural_records``)."""

    def member(iid: str, pid: str, mid: str, sex: str, line_no: int) -> PedMember:
        return PedMember(
            family_id="FAM001",
            iid=iid,
            pid=pid,
            mid=mid,
            sex=sex,
            phen="0",
            line_no=line_no,
            clinical_status="unknown",
        )

    ped = ParsedPed(
        family_ids=["FAM001"],
        members=[
            member("PROBAND", "FATHER", "MOTHER", "1", 1),
            member("FATHER", "0", "0", "1", 2),
            member("MOTHER", "0", "0", "2", 3),
        ],
        sample_ids=list(_SAMPLES),
        text="",
    )
    contexts = {
        sample: SampleMetadataContext(
            sample_uuid=f"uuid-{sample}",
            sample_id=sample,
            family_uuid="family-uuid",
            family_id="FAM001",
            sex="und",
            project_ids=["project-uuid"],
            assembly_id="assembly-uuid",
            assembly_name="GRCh38",
        )
        for sample in _SAMPLES
    }
    return _iter_needlr_structural_records(_NEEDLR_VCF, ped=ped, sample_contexts=contexts)


def _uploaded() -> list[StructuralVariantRecord]:
    """PROBAND's Sniffles2 calls as ``POST /structural-variants/upload/{sample_id}`` stores
    them (``variant_upload_service``): read by the upload's reader, a breakend with the mate
    its ALT names, under the id its coordinates, type and mate give it."""
    return [
        StructuralVariantRecord(
            variant_key=None,
            variant_id=build_structural_variant_id(
                parsed.chrom,
                parsed.start,
                parsed.end,
                parsed.svtype,
                remote_chr=parsed.remote_chr,
                remote_start=parsed.remote_start,
                remote_end=parsed.remote_end,
            ),
            chr=parsed.chrom,
            start=parsed.start,
            end=parsed.end,
            sv_type=parsed.svtype,
            source="sniffles",
            remote_chr=parsed.remote_chr,
            remote_start=parsed.remote_start,
            remote_end=parsed.remote_end,
            sv_len=parsed.svlen,
            filters=[parsed.filter] if parsed.filter else [],
            gene_symbols=[],
            annotations=[{"info": parsed.info}],
            calls=[
                StructuralVariantCall(
                    sample="PROBAND", gt=parsed.gt, qual=parsed.qual, read_support=None, filter=parsed.filter
                )
            ],
        )
        for parsed in iter_structural_variant_records(_SNIFFLES2_VCF, "sniffles")
    ]


def _family() -> FamilyMetadataContext:
    roles = {
        "PROBAND": ("proband", True, "male"),
        "FATHER": ("father", False, "male"),
        "MOTHER": ("mother", False, "female"),
    }
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_id": sample, "role": role, "affected": affected, "sex": sex}
            for sample, (role, affected, sex) in roles.items()
        ],
        sample_uuid_to_name={f"uuid-{sample}": sample for sample in _SAMPLES},
        sample_name_to_uuid={sample: f"uuid-{sample}" for sample in _SAMPLES},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


@pytest.fixture()
def export(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[list[str]]]:
    """The export as ``_USER`` with the given query, its CSV as rows, the header first.
    ClickHouse answers with the NeedlR and the uploaded calls; Postgres holds no reviews,
    cytobands or phenotypes."""
    records = [*_needlr(), *_uploaded()]

    async def fetch_rows(_context, _filters, *, limit=None, offset=0, **_kwargs):
        rows = records[offset:]
        return rows if limit is None else rows[:limit]

    async def summary(_context, _filters) -> tuple[int, dict]:
        return len(records), {}

    async def no_review_ids(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def nothing(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def no_phenotypes(*_args: Any, **_kwargs: Any) -> tuple[list[str], dict[str, str]]:
        return [], {}

    async def family_context(_session, *, family_identifier, user, project_id=None):
        assert family_identifier == "FAM001"
        return _family()

    async def no_session():
        yield None

    async def user() -> CurrentUser:
        return _USER

    monkeypatch.setattr(_M + "_fetch_structural_variant_rows", fetch_rows)
    monkeypatch.setattr(_M + "_fetch_structural_variant_summary", summary)
    monkeypatch.setattr(_M + "list_matching_structural_variant_review_ids", no_review_ids)
    monkeypatch.setattr(_M + "get_structural_variant_review_map", nothing)
    monkeypatch.setattr(_M + "_fetch_structural_cytoband_map", nothing)
    monkeypatch.setattr(_M + "_affected_present_hpo", no_phenotypes)
    monkeypatch.setattr(sv_router, "build_family_metadata_context", family_context)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, no_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, user)
    monkeypatch.setattr(app.state, "skip_startup_tasks", True, raising=False)

    def _get(**params: str) -> list[list[str]]:
        with TestClient(app) as client:
            response = client.get(_EXPORT, params=params)
        assert response.status_code == 200, response.text
        return list(csv.reader(io.StringIO(response.text)))

    return _get


def _mates(table: list[list[str]]) -> dict[tuple[str, str, str, str], tuple[str, str]]:
    """Each row's (Remote chr, Remote start), by its source, type, chromosome and start."""
    header, *rows = table
    return {
        (row["Source"], row["Type"], row["Chromosome"], row["Start"]): (row["Remote chr"], row["Remote start"])
        for row in (dict(zip(header, cells, strict=True)) for cells in rows)
    }


@pytest.mark.parametrize("search", sorted(_SEARCHES))
def test_the_mate_stands_beside_the_locus_under_the_sv_lists_names(export, search: str) -> None:
    # Before: Chromosome, Start, End, Type, … with no mate column.
    expected = ["Priority", "Rank", *_COLUMNS] if search == "ranked" else _COLUMNS
    assert export(**_SEARCHES[search])[0] == expected


@pytest.mark.parametrize("search", sorted(_SEARCHES))
def test_a_needlr_and_an_uploaded_breakend_export_their_mate(export, search: str) -> None:
    mates = _mates(export(**_SEARCHES[search]))
    # Each breakend its own mate, its chromosome as the Chromosome column writes one.
    assert mates[("needlr", "BND", "1", "20000")] == ("5", "3000000")
    assert mates[("sniffles", "BND", "1", "60000")] == ("7", "4000000")


@pytest.mark.parametrize("search", sorted(_SEARCHES))
def test_a_deletion_exports_empty_mate_columns(export, search: str) -> None:
    mates = _mates(export(**_SEARCHES[search]))
    assert len(mates) == 4, mates
    assert mates[("needlr", "DEL", "1", "7000")] == ("", "")
    assert mates[("sniffles", "DEL", "1", "50000")] == ("", "")
