import pytest

from backend.app.services import admin_service


@pytest.mark.asyncio
async def test_list_data_inventory_page_batches_counts_per_assembly(monkeypatch) -> None:
    family_rows = [
        {
            "family_uuid": "fam-1",
            "family_id": "F1",
            "project_ids": ["p1"],
            "assembly_names": ["GRCh38", "GRCh37"],
            "sample_count": 2,
            "metadata": {},
        },
        {
            "family_uuid": "fam-2",
            "family_id": "F2",
            "project_ids": ["p2"],
            "assembly_names": ["GRCh38"],
            "sample_count": 1,
            "metadata": {},
        },
        # A project-less family: counted unscoped.
        {
            "family_uuid": "fam-3",
            "family_id": "F3",
            "project_ids": [],
            "assembly_names": ["GRCh38"],
            "sample_count": 1,
            "metadata": {},
        },
    ]

    async def fake_family_rows(session, *, search=None, page=1, page_size=50):
        return (3, family_rows)

    small_by_assembly = {
        "GRCh38": {"fam-1": 10, "fam-2": 7, "fam-3": 9},
        "GRCh37": {"fam-1": 5},
    }
    sv_by_assembly = {
        "GRCh38": {"fam-1": 1, "fam-2": 3},
        "GRCh37": {"fam-1": 2},
    }
    small_calls: list[tuple] = []
    sv_calls: list[tuple] = []

    async def fake_small(assembly_name, *, family_project_pairs, families_without_project=()):
        small_calls.append((assembly_name, tuple(family_project_pairs), tuple(families_without_project)))
        return dict(small_by_assembly.get(assembly_name, {}))

    async def fake_sv(assembly_name, *, family_project_pairs, families_without_project=()):
        sv_calls.append((assembly_name, tuple(family_project_pairs), tuple(families_without_project)))
        return dict(sv_by_assembly.get(assembly_name, {}))

    async def fake_interval(session, uuids):
        return {}

    async def fake_repeat(session, uuids):
        return {}

    monkeypatch.setattr(admin_service, "_family_rows", fake_family_rows)
    monkeypatch.setattr(admin_service, "count_family_small_variants_by_family", fake_small)
    monkeypatch.setattr(admin_service, "count_family_structural_variants_by_family", fake_sv)
    monkeypatch.setattr(admin_service, "_interval_counts_by_family", fake_interval)
    monkeypatch.setattr(admin_service, "_repeat_counts_by_family", fake_repeat)

    page = await admin_service.list_data_inventory_page(None, page=1, page_size=50)

    assert page.total == 3
    by_family = {item.family_id: item for item in page.items}
    # Counts summed across each family's assemblies.
    assert by_family["F1"].track_counts["small_variants"] == 15  # 10 + 5
    assert by_family["F1"].track_counts["structural_variants"] == 3  # 1 + 2
    assert by_family["F2"].track_counts["small_variants"] == 7
    assert by_family["F2"].track_counts["structural_variants"] == 3
    assert by_family["F3"].track_counts["small_variants"] == 9
    assert by_family["F3"].track_counts["structural_variants"] == 0

    # One query per assembly, not per (family, assembly).
    assert len(small_calls) == 2
    assert {call[0] for call in small_calls} == {"GRCh38", "GRCh37"}
    grch38 = next(call for call in small_calls if call[0] == "GRCh38")
    # GRCh38 pairs carry each family's own project; fam-3 (no projects) is unscoped.
    assert ("fam-1", "p1") in grch38[1]
    assert ("fam-2", "p2") in grch38[1]
    assert grch38[2] == ("fam-3",)
