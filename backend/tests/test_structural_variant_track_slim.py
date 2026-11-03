"""The genome SV track (track_mode=True) must serve a slim payload: no annotation_extra
or population_frequencies (the track only draws a rectangle from chr/start/end/type and
the per-sample genotype). The SV table (track_mode=False) must keep the full annotation
enrichment. These guard the slimming that took one family's track from 182 MB -> ~8 MB
per member.

The route serves the track's page without its null fields (``exclude_none``: about 46 of a
variant's 59 fields are null for an SV), the table's page with them, as its response model
writes it."""

from datetime import datetime, timezone
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.core.postgres import get_postgres_session
from backend.app.dependencies import get_current_user
from backend.app.main import app
from backend.app.middleware import request_logging
from backend.app.routers import families_structural_variants as sv_router
from backend.app.schemas import VariantPage
from backend.app.services.access_control import CurrentUser
from backend.app.services.clickhouse_variant_queries import (
    _structural_annotation_extra,
    _structural_variant_out,
)
from backend.app.services.clickhouse_variant_records import StructuralVariantCall, StructuralVariantRecord
from backend.tests._sv_fakes import proband_family


def _record() -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=1,
        variant_id="sv1",
        chr="1",
        start=1000,
        end=2000,
        sv_type="DEL",
        source="needlr",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=1000,
        filters=[],
        gene_symbols=["BRCA1"],
        annotations=[{"Allele_Freq_ALL": "0.05"}],
        calls=[StructuralVariantCall(sample="DAD", gt="0/1", qual=30.0, read_support=12, filter="PASS")],
    )


def test_structural_annotation_extra_track_mode_returns_empty():
    # The chokepoint: track mode skips all annotation extraction regardless of payload.
    assert _structural_annotation_extra(_record(), track_mode=True) == {}
    # Not for want of an annotation: the same record has one in table mode.
    assert _structural_annotation_extra(_record(), track_mode=False) != {}


def test_structural_variant_out_track_mode_zeroes_annotations_keeps_track_fields():
    slim = _structural_variant_out(_record(), ["DAD"], cytoband="1p36.33", track_mode=True)
    # Heavy fields dropped...
    assert slim.annotation_extra == {}
    assert slim.population_frequencies == {}
    # ...but everything the track renders is preserved.
    assert slim.chr == "1"
    assert slim.start == 1000
    assert slim.end == 2000
    assert slim.type == "DEL"
    assert slim.source == "needlr"
    assert [g.gt for g in slim.genotypes] == ["0/1"]


def test_structural_variant_out_table_mode_keeps_annotation_enrichment():
    # track_mode=False (the SV table / detail view) is unchanged: cytoband enrichment
    # still lands in annotation_extra, beside the population frequencies the track drops.
    full = _structural_variant_out(_record(), ["DAD"], cytoband="1p36.33", track_mode=False)
    assert full.annotation_extra.get("cytoband") == "1p36.33"
    assert full.annotation_extra.get("population_af") == 0.05
    assert full.population_frequencies == {"Allele_Freq_ALL": 0.05}
    assert full.chr == "1" and full.type == "DEL"


# --- the route --------------------------------------------------------------------------------

_USER = CurrentUser(
    id="u1",
    username="reviewer",
    email="reviewer@example.org",
    role="viewer",
    created_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
)


@pytest.fixture()
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The app as ``_USER``, whose SV search answers with the record's row as the search
    serves it in the mode asked for."""

    async def family_context(_session: Any, **_kwargs: Any):
        return proband_family()

    async def search(_session: Any, *, track_mode: bool = False, **_kwargs: Any) -> VariantPage:
        variant = _structural_variant_out(_record(), ["DAD"], cytoband="1p36.33", track_mode=track_mode)
        return VariantPage(total=1, variants=[variant])

    async def no_session():
        yield None

    async def user() -> CurrentUser:
        return _USER

    async def no_audit_row(_payload: Any) -> None:
        return None

    monkeypatch.setattr(sv_router, "build_family_metadata_context", family_context)
    monkeypatch.setattr(sv_router, "get_family_structural_variants_clickhouse", search)
    monkeypatch.setattr(request_logging, "write_audit_log_event", no_audit_row)
    monkeypatch.setitem(app.dependency_overrides, get_postgres_session, no_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, user)
    monkeypatch.setattr(app.state, "skip_startup_tasks", True, raising=False)
    with TestClient(app) as client:
        yield client


def _nulls(value: Any) -> int:
    """How many null values the JSON holds, at any depth."""
    if value is None:
        return 1
    if isinstance(value, dict):
        return sum(_nulls(item) for item in value.values())
    if isinstance(value, list):
        return sum(_nulls(item) for item in value)
    return 0


def test_the_track_is_served_without_its_null_fields_and_the_table_with_them(served: TestClient) -> None:
    track = served.get("/api/families/FAM001/structural-variants", params={"track_mode": "true"})
    table = served.get("/api/families/FAM001/structural-variants")

    assert track.status_code == table.status_code == 200
    [drawn] = track.json()["variants"]
    [listed] = table.json()["variants"]
    assert _nulls(track.json()) == 0
    # Fewer fields than the table's row: the null ones left out. (The id's key is not compared:
    # the track's page is dumped without the response model's alias, ``_id``.)
    ids = {"id", "_id"}
    assert _nulls(listed) > 0 and set(drawn) - ids < set(listed) - ids
    # What the track draws is all there.
    assert (drawn["chr"], drawn["start"], drawn["end"], drawn["type"], drawn["source"]) == ("1", 1000, 2000, "DEL", "needlr")
    assert [genotype["gt"] for genotype in drawn["genotypes"]] == ["0/1"]
    assert track.json()["total"] == 1
