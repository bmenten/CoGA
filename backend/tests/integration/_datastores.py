"""Helpers the integration tests share: running a test's body against the real datastores,
and the synthetic rows several of them start from.

Not a test module (pytest collects ``test_*.py`` only). Every value it makes is synthetic.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import uuid4

from sqlalchemy import text

_T = TypeVar("_T")


def run_closing_stores(make_coro: Callable[[], Awaitable[_T]], *, init_schema: bool = False) -> _T:
    """Run ``make_coro()`` on an event loop of its own and return its result.

    The module-global ClickHouse client and Postgres engine bind to the loop that opened them,
    so both are closed afterwards, also when the body failed: the next ``asyncio.run`` would
    otherwise reach a connection of a closed loop ("Event loop is closed"). With
    ``init_schema`` the Postgres schema is made first, so the test passes run alone.
    """

    from backend.app.core.clickhouse import close_clickhouse_client
    from backend.app.core.postgres import close_postgres_engine, init_postgres_schema

    async def _wrapped() -> _T:
        try:
            if init_schema:
                await init_postgres_schema()
            return await make_coro()
        finally:
            await close_clickhouse_client()
            await close_postgres_engine()

    return asyncio.run(_wrapped())


def vep_annotations(csq_fields: str, entries: list[str]) -> list[dict[str, Any]]:
    """What the import parses from VEP CSQ ``entries`` (one per transcript, ``|``-separated)
    under a CSQ header whose ``Format:`` lists ``csq_fields`` (``Allele|Consequence|...``)."""

    from backend.app.services.variant_annotation_parser import (
        AnnotationHeaderState,
        extract_small_variant_annotations,
        update_annotation_header_state,
    )

    state = AnnotationHeaderState()
    update_annotation_header_state(
        state,
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl'
        f' VEP. Format: {csq_fields}">',
    )
    return extract_small_variant_annotations({"CSQ": ",".join(entries)}, state)


def small_variant(
    variant_id: str, annotations: list[dict[str, Any]], *, sample: str = "PROBAND", source: str = "clair3"
) -> Any:
    """A PASS small variant ``chrom-pos-ref-alt`` with its annotations, called heterozygous
    (GQ 99, 15/15 reads) in ``sample``, as the import's write path takes it."""

    from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord

    chrom, pos, ref, alt = variant_id.split("-")
    return SmallVariantRecord(
        variant_key=None, variant_id=variant_id, chr=chrom, start=int(pos), end=int(pos),
        ref=ref, alt=alt, source=source, rsid=None, filters=["PASS"], gene_symbols=[],
        annotations=annotations,
        calls=[SmallVariantCall(sample=sample, gt="0/1", gq=99.0, dp=30, af=[0.5], ad=[15, 15], ps=None)],
        qual=100.0,
    )


def proband_context(
    family_uuid: str,
    family_id: str,
    project: str,
    *,
    sample: str = "PROBAND",
    assembly_id: str | None = None,
    assembly_name: str = "GRCh38",
) -> Any:
    """The context a family endpoint builds for a family of one affected female proband in
    one project; ``assembly_id`` is the assembly its gene references are read from."""

    from backend.app.services.family_metadata_context import FamilyMetadataContext

    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=family_id,
        project_ids=[project],
        sample_rows=[{"sample_id": sample, "role": "proband", "sex": "female", "affected": True}],
        sample_uuid_to_name={sample: sample},
        sample_name_to_uuid={sample: sample},
        affected_sample_names=[sample],
        assembly_id=assembly_id,
        assembly_name=assembly_name,
    )


def explorer_shows(monkeypatch: Any, members: dict[str, list[str]]) -> None:
    """Have the Variant Explorer read ``members`` (family UUID -> sample IDs) as the samples
    their families show (``_shown_members``), for a test whose families have no members in
    Postgres. Each sample's UUID is its name.

    The carrier summary reads them too, each family linked to the projects its rows are
    stored under, and each search brings the summary up to date for these families first
    (``reconcile_variant_carriers``), as the variant-carrier worker would have."""

    import backend.app.services.variant_carrier_summary as summary
    import backend.app.services.variant_explorer_service as explorer
    from backend.app.core.clickhouse import execute_clickhouse

    shown = [
        explorer._ShownMember(family_uuid=family_uuid, sample_id=sample, sample_uuid=sample)
        for family_uuid, samples in members.items()
        for sample in samples
    ]

    async def _shown_members(session: Any, project_ids: Any) -> list[Any]:
        return list(shown)

    async def _desired_states(session: Any, assembly_id: Any, family_uuids: Any = None) -> dict[str, Any]:
        wanted = [family for family in members if family_uuids is None or family in family_uuids]
        if not wanted:
            return {}
        rows = await execute_clickhouse(
            "SELECT family_guid, groupUniqArray(project_guid) "
            f"FROM {explorer._small_table_name('GRCh38', 'entries')} "
            "WHERE family_guid IN %(families)s AND sign = 1 GROUP BY family_guid",
            {"families": tuple(wanted)},
        )
        projects = {str(family): tuple(sorted(str(value) for value in linked)) for family, linked in rows}
        return {
            family: (projects.get(family, ()), tuple(sorted(set(members[family]))), ())
            for family in wanted
        }

    async def _brought_up_to_date(session: Any, scope: Any) -> list[str]:
        status = await summary.reconcile_variant_carriers(
            scope.assembly_id, scope.assembly_name, family_uuids=list(members)
        )
        assert not status.pending
        return []

    monkeypatch.setattr(explorer, "_shown_members", _shown_members)
    monkeypatch.setattr(summary, "_desired_states", _desired_states)
    monkeypatch.setattr(explorer, "_pending_families", _brought_up_to_date)


async def fresh_family(session: Any, label: str) -> str:
    """A family of its own (only ``family_id`` is required), not committed; its UUID."""

    return (
        await session.execute(
            text("INSERT INTO families (family_id) VALUES (:f) RETURNING id::text"),
            {"f": label},
        )
    ).scalar_one()


# The header of a TRGT VCF of one sample; ``{column}`` is its sample column's name.
TRGT_VCF_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##trgtVersion=5.0.0\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{column}\n"
)


def trgt_sample_context(sample_uuid: str, sample_id: str, family_uuid: str, family_id: str, sex: str) -> Any:
    """The context an import of the sample's TRGT VCF runs under: in no project, on GRCh38."""

    from backend.app.services.family_metadata_context import SampleMetadataContext

    return SampleMetadataContext(
        sample_uuid=sample_uuid,
        sample_id=sample_id,
        family_uuid=family_uuid,
        family_id=family_id,
        sex=sex,
        project_ids=[],
        assembly_id=None,
        assembly_name="GRCh38",
    )


async def trgt_family(session: Any, label: str, members: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """A family ``label`` with a sample ``<label>-<NAME>`` of each member's sex (``{"WOMAN":
    "female"}``), committed: the family's UUID and each member's ``trgt_sample_context``."""

    family_uuid = str(await fresh_family(session, label))
    contexts: dict[str, Any] = {}
    for name, sex in members.items():
        sample_id = f"{label}-{name}"
        sample_uuid = (
            await session.execute(
                text(
                    "INSERT INTO samples (sample_id, family_id, sex) "
                    "VALUES (:s, CAST(:f AS uuid), :sex) RETURNING id::text"
                ),
                {"s": sample_id, "f": family_uuid, "sex": sex},
            )
        ).scalar_one()
        contexts[name] = trgt_sample_context(str(sample_uuid), sample_id, family_uuid, label, sex)
    await session.commit()
    return family_uuid, contexts


def repeat_family_context(family_uuid: str, family_id: str, members: list[tuple[Any, str, bool]]) -> Any:
    """The context the family's repeat table and tracks are read under: each member as its
    ``trgt_sample_context``, its role and whether it is affected; in no project, on GRCh38."""

    from backend.app.services.family_metadata_context import FamilyMetadataContext

    return FamilyMetadataContext(
        family_uuid=family_uuid,
        family_id=family_id,
        project_ids=[],
        sample_rows=[
            {
                "sample_uuid": sample.sample_uuid,
                "sample_id": sample.sample_id,
                "role": role,
                "affected": affected,
                "sex": sample.sex,
            }
            for sample, role, affected in members
        ],
        sample_uuid_to_name={sample.sample_uuid: sample.sample_id for sample, _role, _affected in members},
        sample_name_to_uuid={sample.sample_id: sample.sample_uuid for sample, _role, _affected in members},
        affected_sample_names=[sample.sample_id for sample, _role, affected in members if affected],
        assembly_id=None,
        assembly_name="GRCh38",
    )


async def fresh_species(session: Any, name: str) -> str:
    """A species of its own under ``name`` (any free tax id), not committed; its UUID."""

    return (
        await session.execute(
            text("INSERT INTO species (name, common_name, tax_id) VALUES (:n, 'test', :t) RETURNING id::text"),
            {"n": name, "t": uuid4().int % 2_000_000_000},
        )
    ).scalar_one()


async def fresh_assembly(session: Any, species_id: str, name: str) -> str:
    """An assembly of the species under ``name``, not committed; its UUID."""

    return (
        await session.execute(
            text(
                "INSERT INTO assemblies (species_id, assembly_name, version, release_date) "
                "VALUES (CAST(:s AS uuid), :a, 'v1', '2020-01-01') RETURNING id::text"
            ),
            {"s": species_id, "a": name},
        )
    ).scalar_one()


async def named_assembly(session: Any, name: str, species_label: str) -> tuple[str, str]:
    """The assembly of ``name`` and its species, not committed: (species UUID, assembly UUID).
    An assembly is its name (``assemblies.assembly_name`` is unique), so a test on a real name
    such as GRCh38 shares the one the database holds, else makes it under a fresh species."""

    held = (
        await session.execute(
            text("SELECT species_id::text, id::text FROM assemblies WHERE assembly_name = :a"),
            {"a": name},
        )
    ).first()
    if held is not None:
        return str(held[0]), str(held[1])
    species = await fresh_species(session, species_label)
    return species, await fresh_assembly(session, species, name)


async def gene_locus(session: Any, assembly_id: str, symbol: str, chrom: str, start: int, end: int) -> None:
    """A protein-coding gene of the assembly at chrom:start-end (a gene id of its own), not
    committed."""

    await session.execute(
        text(
            'INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end", '
            "strand, biotype, description, source) VALUES "
            "(CAST(:a AS uuid), :gid, :sym, :chr, :start, :end, 1, 'protein_coding', 't', 't')"
        ),
        {"a": assembly_id, "gid": f"ENSG-{symbol}-{uuid4()}", "sym": symbol, "chr": chrom, "start": start, "end": end},
    )


async def admin_account(session: Any, username: str) -> Any:
    """An administrator's account under ``username`` (e-mail ``<username>@example.org``), not
    committed, and the ``CurrentUser`` a request of theirs carries."""

    from backend.app.services.access_control import CurrentUser

    email = f"{username}@example.org"
    user_id = (
        await session.execute(
            text(
                "INSERT INTO users (username, hashed_password, role, email) "
                "VALUES (:u, 'unusable', 'admin', :e) RETURNING id::text"
            ),
            {"u": username, "e": email},
        )
    ).scalar_one()
    return CurrentUser(
        id=user_id, username=username, email=email, role="admin", created_at=datetime.now(timezone.utc)
    )


async def stored_signout_snapshot(session: Any, family_identifier: str, version: int) -> dict[str, Any]:
    """The snapshot of the family ID's sign-out ``version``, as ``report_signouts`` stores it."""

    stored = (
        await session.execute(
            text("SELECT snapshot FROM report_signouts WHERE family_identifier = :f AND version = :v"),
            {"f": family_identifier, "v": version},
        )
    ).scalar_one()
    return json.loads(stored) if isinstance(stored, str) else stored


async def append_only_tables(session: Any) -> set[str]:
    """Every table with an append-only guard, read from the catalogue: each follows the
    ``<table>_block_mutation()`` trigger-function naming in 04_traceability.sql."""

    rows = await session.execute(
        text(
            "SELECT DISTINCT c.relname FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_proc p ON p.oid = t.tgfoid "
            "WHERE NOT t.tgisinternal AND n.nspname = 'public' AND p.proname ~ '_block_mutation$'"
        )
    )
    return {str(row[0]) for row in rows.all()}


async def tamper_behind_the_trigger(session: Any, table: str, sql: str, params: dict[str, Any]) -> None:
    """Run ``sql`` on an append-only ``table`` with its triggers disabled, and commit: the
    privileged tampering (a careless owner, or any role that could) that does NOT recompute the
    chain hashes, which the verifiers must catch."""

    assert re.fullmatch(r"[a-z_]+", table)  # a table name of the caller's, safe in the DDL
    await session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
    await session.execute(text(sql), params)
    await session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
    await session.commit()


def anchor_signing_seed() -> str:
    """A fresh Ed25519 seed for the integrity anchor, base64, made when the test runs (no key
    literal in the repository: the secret scan reads every commit)."""

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    raw = Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return base64.b64encode(raw).decode()


def database_engine(database: str, **kwargs: Any) -> Any:
    """An engine on ``database`` of the configured Postgres, with the configured credentials."""

    from sqlalchemy.ext.asyncio import create_async_engine

    from backend.app.core.config import settings

    return create_async_engine(
        settings.postgres_dsn.set(database=database), connect_args=settings.postgres_connect_args, **kwargs
    )


async def create_database(admin_database: str, prefix: str = "coga_migration_test") -> str:
    """A new, empty database on the configured Postgres, made from ``admin_database``
    (``CREATE DATABASE`` needs the superuser the CI service container provides); its name."""

    name = f"{prefix}_{uuid4().hex[:12]}"
    assert re.fullmatch(r"[a-z0-9_]+", name)  # an identifier, safe to write into the DDL
    admin = database_engine(admin_database, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally:
        await admin.dispose()
    return name


async def drop_database(admin_database: str, name: str) -> None:
    """Drop the database ``name`` made by ``create_database``, its connections closed."""

    assert re.fullmatch(r"[a-z0-9_]+", name)
    admin = database_engine(admin_database, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.dispose()
