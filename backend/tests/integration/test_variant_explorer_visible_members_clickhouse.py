"""The Variant Explorer counts the samples their family shows, on real ClickHouse and Postgres
(the owner's decision of 2026-11-06).

A family's ``entries`` row holds a call of every sample of the VCF it was imported from. A
member taken out of the family later (a structure edit's ``remove_members``, which leaves
the imported data unless asked to clear it) keeps its calls in those rows, but its family no
longer shows it: the family search reads the calls of the family's active members alone
(``_visible_clickhouse_sample_ids``). The Explorer reads each family's rows by the same rule:
its counts, its export, its carriers and its sample genotype filter, so a taken-out member is
not counted, not listed, and keeps no variant through the genotype filter.

Family A is a quad imported with its father FATHER1 and its sibling SIBLING, who were then
taken out of the family; FATHER1 was added to family C of his own, whose callset holds one
variant. Family B is a proband alone. The test opens family A's page and asks the Explorer's
search, CSV export, carrier drill-down and genotype-filter sample picker.

``resolve_scope`` is monkeypatched to a fresh project, so only these rows are counted; the
families, samples and members are real Postgres rows, the variants real ClickHouse rows
written by the import's write path. The family page's hydration is stubbed: it decorates the
rows it is given and keeps or drops none. The variants sit on chromosome 13 at 61.1 Mb, where
no other test writes.

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI
``smoke`` job sets it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from backend.tests.integration._datastores import (
    admin_account,
    fresh_assembly,
    fresh_species,
    run_closing_stores,
)

pytestmark = pytest.mark.integration

_ASSEMBLY = "GRCh38"

# Carried by family A's proband (het) and its taken-out father (het), and by family B's
# proband (hom).
_PROBAND = "13-61100101-C-T"
# Carried in family A by its taken-out members alone; the proband and mother are 0/0.
_TAKEN_OUT_ONLY = "13-61100201-G-A"
# A row of the taken-out father's own callset in family A: no call of a member it shows.
_FATHER_ROW = "13-61100301-T-C"
# Carried by FATHER1 in family A's rows (taken out) and in family C's (a member).
_MOVED = "13-61100401-A-G"

# Each family's members: (sample, role, sex, active). A sample is created in the family
# that names it first.
_MEMBERS = {
    "A": (
        ("proband", "proband", "female", True),
        ("mother", "mother", "female", True),
        ("father", "father", "male", False),
        ("sibling", "sibling", "male", False),
    ),
    "B": (("proband_b", "proband", "male", True),),
    "C": (("father", "proband", "male", True),),
}


def _record(variant_id: str, calls: dict[str, str], *, source: str = "clair3") -> Any:
    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=int(pos), end=int(pos),
        ref=ref, alt=alt, source=source, rsid=None, filters=["PASS"], gene_symbols=[],
        annotations=[],
        calls=[
            SmallVariantCall(sample=sample, gt=gt, gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)
            for sample, gt in calls.items()
        ],
        qual=100.0,
    )


@pytest.fixture(scope="module")
def stored() -> dict[str, Any]:
    """Write the three families once, in one fresh project."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.clickhouse_variant_storage import (
        ensure_clickhouse_variant_tables,
        insert_small_variant_records,
    )

    tag = uuid4().hex[:8]
    samples = {
        "proband": f"PROBAND-{tag}",
        "mother": f"MOTHER-{tag}",
        "father": f"FATHER1-{tag}",
        "sibling": f"SIBLING-{tag}",
        "proband_b": f"PROBAND-B-{tag}",
    }

    async def _write() -> dict[str, Any]:
        async with get_postgres_sessionmaker()() as session:
            species = await fresh_species(session, f"explorer-visible {tag}")
            assembly_id = await fresh_assembly(session, species, _ASSEMBLY)
            project = (
                await session.execute(
                    text(
                        "INSERT INTO projects (name, species_id, assembly_id) "
                        "VALUES (:n, CAST(:s AS uuid), CAST(:a AS uuid)) RETURNING id::text"
                    ),
                    {"n": f"explorer-visible {tag}", "s": species, "a": assembly_id},
                )
            ).scalar_one()
            families: dict[str, str] = {}
            sample_uuids: dict[str, str] = {}
            for label, members in _MEMBERS.items():
                family = (
                    await session.execute(
                        text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
                        {"f": f"EXPV-{label}-{tag}"},
                    )
                ).scalar_one()
                families[label] = str(family)
                await session.execute(
                    text(
                        "INSERT INTO family_projects (family_id, project_id) "
                        "VALUES (CAST(:f AS uuid), CAST(:p AS uuid))"
                    ),
                    {"f": family, "p": project},
                )
                for key, role, sex, active in members:
                    if key not in sample_uuids:
                        sample_uuids[key] = (
                            await session.execute(
                                text(
                                    "INSERT INTO samples (sample_id, family_id, sex) "
                                    "VALUES (:s, CAST(:f AS uuid), :sex) RETURNING id::text"
                                ),
                                {"s": samples[key], "f": family, "sex": sex},
                            )
                        ).scalar_one()
                        await session.execute(
                            text(
                                "INSERT INTO sample_projects (sample_id, project_id) "
                                "VALUES (CAST(:s AS uuid), CAST(:p AS uuid))"
                            ),
                            {"s": sample_uuids[key], "p": project},
                        )
                    await session.execute(
                        text(
                            "INSERT INTO family_members (family_id, sample_id, role, active) "
                            "VALUES (CAST(:f AS uuid), CAST(:s AS uuid), :r, :a)"
                        ),
                        {"f": family, "s": sample_uuids[key], "r": role, "a": active},
                    )
            admin = await admin_account(session, f"explorer-visible-{tag}")
            await session.commit()

        await ensure_clickhouse_variant_tables(_ASSEMBLY)
        quad = (samples["proband"], samples["mother"], samples["father"], samples["sibling"])
        await insert_small_variant_records(
            _ASSEMBLY,
            families["A"],
            [project],
            [
                _record(_PROBAND, dict(zip(quad, ("0/1", "0/0", "0/1", "0/0")))),
                _record(_TAKEN_OUT_ONLY, dict(zip(quad, ("0/0", "0/0", "1/1", "0/1")))),
                _record(_MOVED, dict(zip(quad, ("0/0", "0/0", "0/1", "0/0")))),
                _record(_FATHER_ROW, {samples["father"]: "0/1"}, source="deepvariant"),
            ],
        )
        await insert_small_variant_records(
            _ASSEMBLY, families["B"], [project], [_record(_PROBAND, {samples["proband_b"]: "1/1"})]
        )
        await insert_small_variant_records(
            _ASSEMBLY, families["C"], [project], [_record(_MOVED, {samples["father"]: "0/1"})]
        )
        return {
            "tag": tag,
            "project": project,
            "assembly_id": assembly_id,
            "samples": samples,
            "admin": admin,
        }

    return run_closing_stores(_write, init_schema=True)


@pytest.fixture
def explorer_scope(monkeypatch: pytest.MonkeyPatch, stored: dict[str, Any]) -> None:
    """Scope the Explorer to the fresh project and stub the family page's hydration."""
    import backend.app.services.clickhouse_family_variants as family_variants
    import backend.app.services.variant_explorer_service as explorer

    scope = explorer.ExplorerScope(
        assembly_id=stored["assembly_id"], assembly_name=_ASSEMBLY, project_ids=[stored["project"]]
    )

    async def _fake_scope(session: Any, user: Any, assembly_id: Any) -> Any:
        return scope

    async def _no_hydration(session: Any, *, context: Any, variants: Any) -> None:
        return None

    async def _brought_up_to_date(session: Any, scope: Any) -> list[str]:
        # As the variant-carrier worker would have: the summary holds these families as
        # they are, so none is pending.
        from backend.app.services.variant_carrier_summary import reconcile_variant_carriers

        status = await reconcile_variant_carriers(scope.assembly_id, scope.assembly_name)
        assert not status.pending
        return []

    monkeypatch.setattr(explorer, "resolve_scope", _fake_scope)
    monkeypatch.setattr(explorer, "_pending_families", _brought_up_to_date)
    monkeypatch.setattr(family_variants, "_hydrate_small_variant_outs", _no_hydration)


def _observe(stored: dict[str, Any], **filters: Any) -> dict[str, Any]:
    """What family A's page lists (each variant with the samples it shows), and what the
    Explorer's search, export and carrier drill-down count, under the Explorer ``filters``.
    A row is (families, carriers, heterozygous, homozygous); a carrier is (family, sample,
    role)."""
    import backend.app.services.clickhouse_family_variants as family_variants
    import backend.app.services.variant_explorer_service as explorer
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.family_metadata_context import build_family_metadata_context

    names = {value: key for key, value in stored["samples"].items()}

    async def _run() -> dict[str, Any]:
        async with get_postgres_sessionmaker()() as session:
            context = await build_family_metadata_context(
                session, family_identifier=f"EXPV-A-{stored['tag']}", user=stored["admin"]
            )
            page = await family_variants.get_family_small_variants_page(
                session, context=context, page=1, page_size=100
            )
            search = await explorer.search_global_small_variants(
                session,
                user=None,
                filters=explorer.GlobalVariantFilters(**filters),
                assembly_id=stored["assembly_id"],
            )
            export = await explorer.export_global_small_variants(
                session,
                user=None,
                filters=explorer.GlobalVariantFilters(**filters),
                assembly_id=stored["assembly_id"],
            )
            carriers: dict[str, Any] = {}
            for row in search.variants:
                drill = await explorer.get_variant_carriers(
                    session, user=None, variant_key=int(row.key), assembly_id=stored["assembly_id"]
                )
                carriers[row.variant_id] = sorted(
                    (group.family_id.split("-")[1], names[sample.sample_id], sample.role)
                    for group in drill.families
                    for sample in group.samples
                )
        return {
            "family A page": {
                variant.id: sorted(names[call.sample] for call in variant.genotypes)
                for variant in page.variants
            },
            "explorer total": search.total,
            "explorer rows": {
                row.variant_id: (row.total_families, row.total_samples, row.het_samples, row.hom_samples)
                for row in search.variants
            },
            "explorer export": {
                row.variant_id: (row.total_families, row.total_samples, row.het_samples, row.hom_samples)
                for row in export.rows
            },
            "carriers": carriers,
        }

    return run_closing_stores(_run)


@pytest.mark.usefixtures("explorer_scope")
def test_the_explorer_counts_the_samples_their_family_shows(stored: dict[str, Any]) -> None:
    # Family A's page shows its proband and mother alone. Family C shows FATHER1: his call
    # of the moved variant counts there, not in family A's rows.
    rows = {_PROBAND: (2, 2, 1, 1), _MOVED: (1, 1, 1, 0)}
    assert _observe(stored) == {
        "family A page": {
            _PROBAND: ["mother", "proband"],
            _TAKEN_OUT_ONLY: ["mother", "proband"],
            _MOVED: ["mother", "proband"],
        },
        "explorer total": 2,
        "explorer rows": rows,
        "explorer export": rows,
        "carriers": {
            _PROBAND: [("A", "proband", "proband"), ("B", "proband_b", "proband")],
            _MOVED: [("C", "father", "proband")],
        },
    }


@pytest.mark.usefixtures("explorer_scope")
def test_the_genotype_filter_reads_a_sample_in_the_families_that_show_it(stored: dict[str, Any]) -> None:
    # FATHER1 is read in family C's rows alone: of the variants he carries in family A's
    # rows, none is kept. SIBLING, whom no family shows, keeps no variant.
    samples = stored["samples"]
    observed = {
        sample: _observe(stored, sample_genotype_filters=[(samples[sample], "het_hom")])
        for sample in ("father", "sibling")
    }
    assert {
        sample: (result["explorer total"], result["explorer rows"], result["explorer export"])
        for sample, result in observed.items()
    } == {
        "father": (1, {_MOVED: (1, 1, 1, 0)}, {_MOVED: (1, 1, 1, 0)}),
        "sibling": (0, {}, {}),
    }


def test_the_genotype_picker_lists_the_samples_their_family_shows(stored: dict[str, Any]) -> None:
    import backend.app.services.variant_explorer_service as explorer
    from backend.app.core.postgres import get_postgres_sessionmaker

    async def _run() -> list[str]:
        async with get_postgres_sessionmaker()() as session:
            return await explorer.list_explorer_samples(
                session, stored["admin"], assembly_id=stored["assembly_id"]
            )

    listed = set(run_closing_stores(_run))
    samples = stored["samples"]
    assert {key for key, sample in samples.items() if sample in listed} == {
        "proband",
        "mother",
        "father",
        "proband_b",
    }
