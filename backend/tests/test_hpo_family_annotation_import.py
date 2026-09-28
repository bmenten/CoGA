import pytest

from backend.app.services import hpo_service as hs
from backend.app.services.hpo_service import HpoAnnotationImportRow


class _RecordingSession:
    """Minimal AsyncSession stand-in capturing execute()/commit() calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return None

    async def commit(self) -> None:
        self.committed = True


def _patch_existing_terms(monkeypatch, terms):
    async def fake_existing(session, hpo_ids):
        return set(terms)

    monkeypatch.setattr(hs, "_existing_hpo_terms", fake_existing)


@pytest.mark.asyncio
async def test_import_family_hpo_annotations_batches_inserts(monkeypatch) -> None:
    _patch_existing_terms(monkeypatch, {"HP:0001", "HP:0002"})
    session = _RecordingSession()
    rows = [
        HpoAnnotationImportRow(family_id="FAM1", individual_id="IND1", hpo_id="HP:0001", line_no=1),
        HpoAnnotationImportRow(family_id="FAM1", individual_id="IND2", hpo_id="HP:0002", line_no=2),
    ]

    result = await hs.import_family_hpo_annotations(
        session,
        family_uuid="fam-uuid",
        family_id="FAM1",
        sample_uuids_by_id={"IND1": "s1-uuid", "IND2": "s2-uuid"},
        rows=rows,
    )

    # A single batched execute carries both rows (a list of param dicts), not one per row.
    assert len(session.calls) == 1
    _stmt, params = session.calls[0]
    assert isinstance(params, list)
    assert len(params) == 2
    assert {p["sample_uuid"] for p in params} == {"s1-uuid", "s2-uuid"}
    assert session.committed is True
    assert result["imported"] == 2
    assert result["skipped"] == 0
    assert result["samples"] == {"IND1": 1, "IND2": 1}


@pytest.mark.asyncio
async def test_import_family_hpo_annotations_collapses_duplicate_conflict_keys(monkeypatch) -> None:
    _patch_existing_terms(monkeypatch, {"HP:0001"})
    session = _RecordingSession()
    rows = [
        HpoAnnotationImportRow(
            family_id="FAM1", individual_id="IND1", hpo_id="HP:0001", note="first", line_no=1
        ),
        HpoAnnotationImportRow(
            family_id="FAM1", individual_id="IND1", hpo_id="HP:0001", note="second", line_no=2
        ),
    ]

    result = await hs.import_family_hpo_annotations(
        session,
        family_uuid="fam-uuid",
        family_id="FAM1",
        sample_uuids_by_id={"IND1": "s1-uuid"},
        rows=rows,
    )

    # imported still counts every accepted row (unchanged behaviour) ...
    assert result["imported"] == 2
    assert result["samples"] == {"IND1": 2}
    # ... but the batch upserts a single row so ON CONFLICT never touches it twice,
    # and the last value wins (matching the original per-row upsert order).
    _stmt, params = session.calls[0]
    assert len(params) == 1
    assert params[0]["note"] == "second"


@pytest.mark.asyncio
async def test_import_family_hpo_annotations_skips_unknown_and_issues_no_insert(monkeypatch) -> None:
    # No terms loaded -> every row is skipped as an unknown HPO id.
    _patch_existing_terms(monkeypatch, set())
    session = _RecordingSession()
    rows = [
        HpoAnnotationImportRow(family_id="FAM1", individual_id="IND1", hpo_id="HP:0009", line_no=1),
    ]

    result = await hs.import_family_hpo_annotations(
        session,
        family_uuid="fam-uuid",
        family_id="FAM1",
        sample_uuids_by_id={"IND1": "s1-uuid"},
        rows=rows,
    )

    assert result["imported"] == 0
    assert result["skipped"] == 1
    # Nothing validated -> no INSERT round-trip, but the transaction still commits.
    assert session.calls == []
    assert session.committed is True
    assert result["errors"][0]["code"] == "phenotype_hpo_unknown"
