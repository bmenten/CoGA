"""The cohort's carriers of each small variant, kept as the families' variants are written.

The Variant Explorer counts, for each variant, the samples and families that carry it in the
projects a user may read. Counting them from ``entries`` on each page grouped every carrier of
every family at once: from about 20 M rows (three or four whole-genome families) a page passed
the 110 s query limit (docs/clickhouse-scale-benchmark.md). This summary holds those counts per
variant, so a page reads one row per variant and project set instead of one per family.

**What a family adds.** A family's *contribution* is read from its ``entries`` rows under one of
its projects (every write of its variants writes under each project it is linked to, so each
copy holds the same rows): for each variant, the members it shows (its active members, as the
family page and the explorer read them) that carry it, each row of the variant split by the
*class* of the row: its annotation set, its rsID, its gene symbols and whether its callset is
imputed. The explorer's filters read these columns on each row, so a filter keeps or drops a
class whole.

* A variant whose rows in the family share one class (nearly every variant) adds one
  *aggregate row*: one family, its carriers, heterozygous and homozygous carriers, deduplicated
  over the family's rows (two callsets of the same calls count each sample once). Aggregate
  rows of the same variant, project set and class are summed over the families.
* A variant whose rows in the family fall in several classes (two callsets annotated apart, a
  called and an imputed callset), or that a sample carries whom another family shows too (a
  member of two families), adds one *carrier row* per class and carrier, named by family and
  sample. A page counts each sample once over the classes and families its filters keep, as
  the explorer counts a sample by its ID.

**One table.** ``<assembly>/SNV_INDEL/carriers`` (a SummingMergeTree, sorted by ``xpos, key``)
holds both kinds, and a *marker* row per family (``xpos = key = 0``) whose ``applied`` column
says which generation of the family's contribution the table holds. A contribution and its
marker are written to a staging table, merged into one part and attached in one step
(``ATTACH PARTITION … FROM``), so the table holds a family's contribution and its marker
together or neither. Taking a contribution out writes the same rows negated, with the marker
at -1: the rows cancel, and merges drop the rows that sum to zero. ``carrier_generations``
holds what each generation was computed from: the family's projects, the samples it showed,
the project whose copy was read and the family's data version.

**Who keeps it.** Every writer of a family's ``entries`` rows takes the family's contribution
out before its first write (:func:`detach_family_variant_carriers`), under the family's write
lock, recomputing it from the rows it was computed from: the data version proves they have not
changed. The reconciler (:func:`reconcile_variant_carriers`) adds the contribution of every
family whose rows, members or projects differ from what the table holds, under the family's
write lock; the variant-carrier worker runs it every few seconds where the imports run. A
family that a writer holds (an import in progress) waits for it, and the explorer names the
families its page leaves out or counts as before (``pending``). A contribution that cannot be
taken out exactly (rows changed while it was in) empties the whole table, after which the
reconciler adds every family again.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Collection, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
import re
import secrets
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.clickhouse import execute_clickhouse
from ..core.coga_logging import describe_error
from ..core.config import settings
from ..core.postgres import get_postgres_sessionmaker
from ..core.sql import uuid_list_bindparam, uuid_values
from .clickhouse_variant_ids import _require_clickhouse_identifier, _small_table_name
from .family_variant_write_lock import SMALL_VARIANTS, family_variant_lock_key
from .genotypes import ALT_CLASSES, HOM_ALT, clickhouse_genotype_condition
from .worker_tasks import stop_worker

logger = logging.getLogger(__name__)

CARRIERS_SUFFIX = "carriers"
GENERATIONS_SUFFIX = "carrier_generations"
# A staging table's name: ``<dataset>/SNV_INDEL/carriers_stage/<unix seconds>-<hex>``.
_STAGE_SUFFIX = "carriers_stage"
_STAGE_NAME = re.compile(r"^(?P<dataset>[A-Za-z0-9._-]+)/SNV_INDEL/carriers_stage/(?P<created>\d+)-[0-9a-f]+$")
# A staging table older than this belongs to a write that stopped: dropped by the worker.
_STAGE_MAX_AGE_SECONDS = 3600
# The callset names of imputed genotypes (``clickhouse_variant_queries``); read lazily.
_DATA_VERSION_NEVER_WRITTEN = "0:0"
# The marker rows' place in the table: no variant has key 0 (keys are 64-bit hashes).
_MARKER = "c.xpos = 0 AND c.key = 0"

# How a contribution is read and written: in small blocks (the arrays of calls make a block of
# a whole-genome family's rows large), aggregated in the order ``entries`` is sorted in.
_CONTRIBUTION_SETTINGS = (
    "optimize_aggregation_in_order = 1, max_block_size = 8192, "
    "min_insert_block_size_rows = 262144, min_insert_block_size_bytes = 268435456"
)

# The columns of a row of the table, in order.
CARRIER_COLUMNS: tuple[str, ...] = (
    "xpos",
    "key",
    "family_guid",
    "sample_id",
    "projects",
    "annotationSetHash",
    "rsid",
    "gene_symbols",
    "imputed",
    "generation",
    "variantId",
    "chrom",
    "pos",
    "ref",
    "alt",
    "families",
    "samples",
    "het",
    "hom",
    "applied",
)


def carriers_table_ddl(database: str, dataset: str) -> str:
    """The ``carriers`` table of one assembly (``ensure_clickhouse_variant_tables``).

    The sort key is the row's identity: the variant (``xpos, key``), the family and sample of
    a carrier row (empty in an aggregate row), the family's projects, the row's class, and the
    generation of a marker row (0 in a variant row). Rows of one identity are summed; the
    variant columns after it are the same in every row of a variant."""
    return f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/{CARRIERS_SUFFIX}`
        (
            `xpos` UInt64,
            `key` UInt64,
            `family_guid` String,
            `sample_id` String,
            `projects` Array(LowCardinality(String)),
            `annotationSetHash` UInt64,
            `rsid` String,
            `gene_symbols` Array(String),
            `imputed` Bool,
            `generation` UInt64,
            `variantId` String,
            `chrom` LowCardinality(String),
            `pos` UInt32,
            `ref` String,
            `alt` String,
            `families` Int32,
            `samples` Int32,
            `het` Int32,
            `hom` Int32,
            `applied` Int32,
            INDEX idx_carriers_key key TYPE bloom_filter(0.01) GRANULARITY 4,
            INDEX idx_carriers_variant variantId TYPE bloom_filter(0.01) GRANULARITY 4
        )
        ENGINE = SummingMergeTree((families, samples, het, hom, applied))
        ORDER BY (xpos, key, family_guid, sample_id, projects, annotationSetHash, rsid,
                  gene_symbols, imputed, generation)
        """


def carrier_generations_table_ddl(database: str, dataset: str) -> str:
    """The ``carrier_generations`` table: what each generation of a family's contribution was
    computed from. A row of an empty ``family_guid`` asks for the summary to be emptied
    (:func:`_request_rebuild`)."""
    return f"""
        CREATE TABLE IF NOT EXISTS {database}.`{dataset}/SNV_INDEL/{GENERATIONS_SUFFIX}`
        (
            `family_guid` String,
            `generation` UInt64,
            `projects` Array(String),
            `shown_samples` Array(String),
            `shared_samples` Array(String),
            `source_project` String,
            `data_version` String,
            `created_at` DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = ReplacingMergeTree
        ORDER BY (family_guid, generation)
        """


def _carriers(assembly_name: str) -> str:
    return _small_table_name(assembly_name, CARRIERS_SUFFIX)


def _generations(assembly_name: str) -> str:
    return _small_table_name(assembly_name, GENERATIONS_SUFFIX)


# --------------------------------------------------------------------------- #
# What a family's contribution is computed from                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FamilyCarrierState:
    """A family as its contribution reads it: the projects it is linked to on the assembly
    (sorted), the sample IDs of the members it shows (sorted), those of them another family
    of the assembly shows too (sorted), and its data version
    (``get_family_small_variant_data_version``)."""

    projects: tuple[str, ...]
    shown_samples: tuple[str, ...]
    shared_samples: tuple[str, ...]
    data_version: str


@dataclass(frozen=True, slots=True)
class _Generation:
    """A generation of a family's contribution: its id, the state it was computed from, and
    the project whose copy of the rows it read ('' when the family had none)."""

    generation: int
    state: FamilyCarrierState
    source_project: str


@dataclass(slots=True)
class CarrierSummaryStatus:
    """The outcome of a reconciliation: the families whose contribution it brought up to date,
    and those it left as they were, held by a writer or past its time."""

    refreshed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    emptied: bool = False


class CarrierSummaryInconsistent(RuntimeError):
    """A family's contribution cannot be taken out exactly: the table holds more than one
    generation of it, or its rows changed while it was in."""


def _sorted_unique(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value or "").strip()}))


async def _desired_states(
    session: AsyncSession, assembly_id: str, family_uuids: Collection[str] | None = None
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]]:
    """Per family linked to a project of the assembly: its projects on the assembly, the
    sample IDs of its active members (the samples the family page shows,
    ``build_family_metadata_context``), and those of them another family of the assembly shows
    too. With ``family_uuids``, those families alone."""
    clauses = ["p.assembly_id = CAST(:assembly_id AS uuid)"]
    params: dict[str, Any] = {"assembly_id": assembly_id}
    binds = []
    if family_uuids is not None:
        if not family_uuids:
            return {}
        clauses.append("fp.family_id IN :family_uuids")
        params["family_uuids"] = uuid_values(list(family_uuids))
        binds.append(uuid_list_bindparam("family_uuids"))
    projects = await session.execute(
        text(
            f"""
            SELECT fp.family_id::text AS family_uuid, fp.project_id::text AS project_id
            FROM family_projects fp
            JOIN projects p ON p.id = fp.project_id
            WHERE {' AND '.join(clauses)}
            """
        ).bindparams(*binds),
        params,
    )
    linked: dict[str, set[str]] = {}
    for row in projects.mappings().all():
        linked.setdefault(str(row["family_uuid"]), set()).add(str(row["project_id"]))
    if not linked:
        return {}
    members = await session.execute(
        text(
            """
            SELECT fm.family_id::text AS family_uuid, s.sample_id
            FROM family_members fm
            JOIN samples s ON s.id = fm.sample_id
            WHERE fm.active AND fm.family_id IN :family_uuids
            """
        ).bindparams(uuid_list_bindparam("family_uuids")),
        {"family_uuids": uuid_values(sorted(linked))},
    )
    shown: dict[str, set[str]] = {}
    for row in members.mappings().all():
        shown.setdefault(str(row["family_uuid"]), set()).add(str(row["sample_id"]))
    # The samples shown by more than one family of the assembly. With ``family_uuids``, those
    # families' samples are looked up in every family that shows them.
    shared = await _samples_shown_twice(session, assembly_id, set().union(*shown.values()) if shown else set())
    return {
        family: (
            _sorted_unique(projects_of),
            _sorted_unique(shown.get(family, ())),
            _sorted_unique(shown.get(family, set()) & shared),
        )
        for family, projects_of in linked.items()
    }


async def _samples_shown_twice(session: AsyncSession, assembly_id: str, sample_ids: Collection[str]) -> set[str]:
    """Of ``sample_ids``, those an active membership of more than one family linked to a
    project of the assembly names."""
    if not sample_ids:
        return set()
    result = await session.execute(
        text(
            """
            SELECT s.sample_id
            FROM family_members fm
            JOIN samples s ON s.id = fm.sample_id
            WHERE fm.active
              AND s.sample_id = ANY(CAST(:sample_ids AS text[]))
              AND EXISTS (
                  SELECT 1 FROM family_projects fp JOIN projects p ON p.id = fp.project_id
                  WHERE fp.family_id = fm.family_id AND p.assembly_id = CAST(:assembly_id AS uuid)
              )
            GROUP BY s.sample_id
            HAVING count(DISTINCT fm.family_id) > 1
            """
        ),
        # One array parameter: asyncpg takes at most 32,767 parameters a statement.
        {"sample_ids": sorted(sample_ids), "assembly_id": assembly_id},
    )
    return {str(row[0]) for row in result.all()}


async def _data_versions(assembly_name: str, family_uuids: Collection[str] | None = None) -> dict[str, str]:
    """Each written family's data version, as ``get_family_small_variant_data_version``
    reads it (``count:sum`` of its tokens); a family never written is not listed."""
    table = _small_table_name(assembly_name, "family_data_version")
    where = ""
    params: dict[str, Any] = {}
    if family_uuids is not None:
        if not family_uuids:
            return {}
        where = "WHERE family_guid IN %(families)s"
        params["families"] = tuple(family_uuids)
    rows = await execute_clickhouse(
        f"SELECT family_guid, count(), sum(token) FROM {table} {where} GROUP BY family_guid",
        params,
    )
    return {str(family): f"{int(count or 0)}:{int(total or 0)}" for family, count, total in rows or []}


async def _applied_generations(
    assembly_name: str, family_uuids: Collection[str] | None = None
) -> dict[str, dict[int, int]]:
    """Per family, the generations its marker rows sum to other than zero, with their sums:
    ``{generation: 1}`` for a family whose contribution the table holds."""
    where = [_MARKER]
    params: dict[str, Any] = {}
    if family_uuids is not None:
        if not family_uuids:
            return {}
        where.append("c.family_guid IN %(families)s")
        params["families"] = tuple(family_uuids)
    rows = await execute_clickhouse(
        f"""
        SELECT c.family_guid, c.generation, sum(c.applied) AS n
        FROM {_carriers(assembly_name)} AS c
        WHERE {' AND '.join(where)}
        GROUP BY c.family_guid, c.generation
        HAVING n != 0
        """,
        params,
    )
    applied: dict[str, dict[int, int]] = {}
    for family, generation, net in rows or []:
        applied.setdefault(str(family), {})[int(generation)] = int(net)
    return applied


async def _generation_records(
    assembly_name: str, wanted: Collection[tuple[str, int]]
) -> dict[tuple[str, int], _Generation]:
    if not wanted:
        return {}
    rows = await execute_clickhouse(
        f"""
        SELECT family_guid, generation, projects, shown_samples, shared_samples, source_project, data_version
        FROM {_generations(assembly_name)} FINAL
        WHERE (family_guid, generation) IN %(wanted)s
        """,
        {"wanted": tuple(wanted)},
    )
    records: dict[tuple[str, int], _Generation] = {}
    for family, generation, projects, shown, shared, source_project, data_version in rows or []:
        records[(str(family), int(generation))] = _Generation(
            generation=int(generation),
            state=FamilyCarrierState(
                projects=tuple(str(value) for value in projects or ()),
                shown_samples=tuple(str(value) for value in shown or ()),
                shared_samples=tuple(str(value) for value in shared or ()),
                data_version=str(data_version),
            ),
            source_project=str(source_project),
        )
    return records


async def _applied(assembly_name: str, family_uuids: Collection[str] | None = None) -> dict[str, _Generation]:
    """The generation of each family's contribution the table holds, with what it was
    computed from. Raises :class:`CarrierSummaryInconsistent` when the markers of a family sum
    to anything but one generation once, or a generation's record is missing."""
    applied = await _applied_generations(assembly_name, family_uuids)
    for family, generations in applied.items():
        if len(generations) != 1 or next(iter(generations.values())) != 1:
            raise CarrierSummaryInconsistent(f"family {family}: markers {sorted(generations.items())}")
    wanted = [(family, next(iter(generations))) for family, generations in applied.items()]
    records = await _generation_records(assembly_name, wanted)
    missing = [pair for pair in wanted if pair not in records]
    if missing:
        raise CarrierSummaryInconsistent(f"generation records missing: {len(missing)}")
    return {family: records[(family, generation)] for family, generation in wanted}


# --------------------------------------------------------------------------- #
# A family's contribution                                                     #
# --------------------------------------------------------------------------- #


def _contribution_insert(
    assembly_name: str, stage: str, generation: _Generation, *, family_uuid: str, sign: int
) -> tuple[str, dict[str, Any]]:
    """The ``INSERT … SELECT`` writing the family's contribution of ``generation``, times
    ``sign``, with its marker, into the staging table ``stage``.

    Its rows are read from the family's rows under ``generation.source_project`` (``FINAL``:
    the rows a merge leaves, whatever has merged yet), each call of a member the family shows
    (``shown_samples``) with an ALT allele a carrier, a ``1/1`` or haploid ``1`` homozygous
    and every other carrier heterozygous, by the one genotype classification
    (``genotypes``). A variant's rows are grouped in the order ``entries`` holds them, so the
    query keeps one variant at a time."""
    from .clickhouse_variant_queries import IMPUTED_SMALL_VARIANT_SOURCES

    params: dict[str, Any] = {
        "family": family_uuid,
        "source_project": generation.source_project,
        "shown": list(generation.state.shown_samples),
        "shared": list(generation.state.shared_samples),
        "projects": list(generation.state.projects),
        "sign": int(sign),
        "generation": int(generation.generation),
        "imputed_sources": tuple(IMPUTED_SMALL_VARIANT_SOURCES),
    }
    carrier = clickhouse_genotype_condition("g", ALT_CLASSES, param="carrier_alt", params=params)
    hom = clickhouse_genotype_condition("g", {HOM_ALT}, param="carrier_hom", params=params)
    shown = "has(%(shown)s, s)"
    calls = "e.`calls.sampleId`, e.`calls.gt`"
    columns = ", ".join(f"`{column}`" for column in CARRIER_COLUMNS)
    entries = _small_table_name(assembly_name, "entries")
    # One output tuple per row written: (family, sample, annotation set, rsID, gene symbols,
    # imputed, families, carriers, het, hom). An aggregate row for a variant of one class that
    # no sample another family shows carries; a carrier row per class and carrier otherwise.
    aggregate = (
        "[tuple('', '', classes[1].1, classes[1].2, classes[1].3, classes[1].4, %(sign)s, "
        "%(sign)s * length(arrayDistinct(arrayFlatten(row_carriers))), "
        "%(sign)s * length(arrayDistinct(arrayFlatten(row_hets))), "
        "%(sign)s * length(arrayDistinct(arrayFlatten(row_homs))))]"
    )

    def of_class(rows: str) -> str:
        return f"arrayFlatten(arrayFilter((x, k) -> k = cl, {rows}, row_classes))"

    per_carrier = (
        "arrayFlatten(arrayMap(cl -> arrayMap(s -> tuple(%(family)s, s, cl.1, cl.2, cl.3, cl.4, "
        f"0, %(sign)s, %(sign)s * has({of_class('row_hets')}, s), %(sign)s * has({of_class('row_homs')}, s)), "
        f"arraySort(arrayDistinct({of_class('row_carriers')}))), classes))"
    )
    query = f"""
        INSERT INTO {stage} ({columns})
        SELECT xpos, key, out.1, out.2, %(projects)s, out.3, out.4, out.5, out.6, toUInt64(0),
               variantId, chrom, pos, ref, alt,
               toInt32(out.7), toInt32(out.8), toInt32(out.9), toInt32(out.10), toInt32(0)
        FROM (
            SELECT
                xpos,
                key,
                min(variantId) AS variantId,
                min(chrom) AS chrom,
                min(pos) AS pos,
                min(ref) AS ref,
                min(alt) AS alt,
                groupArray(tuple(annotationSetHash, rsid, gene_symbols, imputed)) AS row_classes,
                groupArray(carriers) AS row_carriers,
                groupArray(hets) AS row_hets,
                groupArray(homs) AS row_homs,
                arraySort(arrayDistinct(row_classes)) AS classes,
                if(length(classes) = 1 AND NOT hasAny(arrayFlatten(row_carriers), %(shared)s),
                   {aggregate}, {per_carrier}) AS outs
            FROM (
                SELECT
                    e.xpos AS xpos,
                    e.key AS key,
                    e.annotationSetHash AS annotationSetHash,
                    ifNull(e.rsid, '') AS rsid,
                    arraySort(e.gene_symbols) AS gene_symbols,
                    lowerUTF8(e.source) IN %(imputed_sources)s AS imputed,
                    e.variantId AS variantId,
                    e.chrom AS chrom,
                    e.pos AS pos,
                    e.ref AS ref,
                    e.alt AS alt,
                    arrayFilter((s, g) -> {carrier} AND {shown}, {calls}) AS carriers,
                    arrayFilter((s, g) -> {carrier} AND NOT {hom} AND {shown}, {calls}) AS hets,
                    arrayFilter((s, g) -> {hom} AND {shown}, {calls}) AS homs
                FROM {entries} AS e FINAL
                WHERE e.project_guid = %(source_project)s
                  AND e.family_guid = %(family)s
                  AND e.sign = 1
            )
            WHERE notEmpty(carriers)
            GROUP BY xpos, key
        )
        ARRAY JOIN outs AS out
        UNION ALL
        SELECT toUInt64(0), toUInt64(0), %(family)s, '', CAST([] AS Array(String)), toUInt64(0), '',
               CAST([] AS Array(String)), false, toUInt64(%(generation)s), '', '', toUInt32(0), '', '',
               toInt32(0), toInt32(0), toInt32(0), toInt32(0), toInt32(%(sign)s)
        SETTINGS {_CONTRIBUTION_SETTINGS}
    """
    if not generation.source_project:
        # A family without rows under any of its projects contributes its marker alone.
        query = f"""
            INSERT INTO {stage} ({columns})
            SELECT toUInt64(0), toUInt64(0), %(family)s, '', CAST([] AS Array(String)), toUInt64(0), '',
                   CAST([] AS Array(String)), false, toUInt64(%(generation)s), '', '', toUInt32(0), '', '',
                   toInt32(0), toInt32(0), toInt32(0), toInt32(0), toInt32(%(sign)s)
        """
    return query, params


def _stage_name(assembly_name: str) -> str:
    dataset = _require_clickhouse_identifier(assembly_name)
    return f"{settings.clickhouse_database}.`{dataset}/SNV_INDEL/{_STAGE_SUFFIX}/{int(time.time())}-{secrets.token_hex(6)}`"


async def _write_contribution(
    assembly_name: str, family_uuid: str, generation: _Generation, *, sign: int
) -> None:
    """Add (``sign`` 1) or take out (``sign`` -1) the family's contribution of ``generation``,
    with its marker, in one step, or raise having changed nothing.

    The rows go to a staging table of the summary's structure, which is merged into one part,
    checked to hold the marker once, and attached to the summary (``ATTACH PARTITION … FROM``,
    one part, so all of it or nothing). No statement is retried: a retried insert could write
    its rows twice. When the attach itself fails, the markers tell whether it took effect."""
    carriers = _carriers(assembly_name)
    stage = _stage_name(assembly_name)
    await execute_clickhouse(f"CREATE TABLE {stage} AS {carriers}", retry=False)
    try:
        query, params = _contribution_insert(assembly_name, stage, generation, family_uuid=family_uuid, sign=sign)
        await execute_clickhouse(query, params, retry=False)
        await execute_clickhouse(f"OPTIMIZE TABLE {stage} FINAL", retry=False)
        checks = await execute_clickhouse(
            f"""
            SELECT
                (SELECT count() FROM system.parts
                 WHERE database = %(database)s AND table = %(table)s AND active) AS parts,
                (SELECT countIf(c.key = 0) FROM {stage} AS c) AS markers,
                (SELECT sumIf(c.applied, c.key = 0) FROM {stage} AS c) AS applied
            """,
            {"database": settings.clickhouse_database, "table": stage.split("`")[1]},
        )
        parts, markers, applied = (int(value) for value in checks[0])
        if parts != 1 or markers != 1 or applied != sign:
            raise CarrierSummaryInconsistent(
                f"staging of family {family_uuid}: {parts} parts, {markers} markers summing to {applied}"
            )
        expected = 1 if sign > 0 else 0
        try:
            await execute_clickhouse(f"ALTER TABLE {carriers} ATTACH PARTITION tuple() FROM {stage}", retry=False)
        except Exception:
            # Whether it took effect is in the markers: the part holds the marker.
            net = (await _applied_generations(assembly_name, [family_uuid])).get(family_uuid, {})
            if net.get(generation.generation, 0) != expected:
                raise
    finally:
        try:
            await execute_clickhouse(f"DROP TABLE IF EXISTS {stage}", retry=False)
        except Exception as exc:  # noqa: BLE001 - a staging table left over is dropped later
            logger.warning("Could not drop a carrier staging table: %s", describe_error(exc))


async def _source_project(assembly_name: str, family_uuid: str, projects: Sequence[str]) -> str:
    """The first of ``projects`` (in sorted order) under which the family has rows, or ''."""
    if not projects:
        return ""
    rows = await execute_clickhouse(
        f"""
        SELECT min(project_guid)
        FROM (
            SELECT project_guid
            FROM {_small_table_name(assembly_name, 'entries')}
            WHERE project_guid IN %(projects)s AND family_guid = %(family)s AND sign = 1
            LIMIT 1 BY project_guid
        )
        HAVING count() > 0
        """,
        {"projects": tuple(projects), "family": family_uuid},
    )
    return str(rows[0][0]) if rows else ""


async def _attach(
    assembly_name: str, family_uuid: str, state: FamilyCarrierState
) -> _Generation:
    """Add the family's contribution of ``state`` (the table holds none of it)."""
    generation = _Generation(
        generation=secrets.randbits(63) or 1,
        state=state,
        source_project=await _source_project(assembly_name, family_uuid, state.projects),
    )
    await execute_clickhouse(
        f"INSERT INTO {_generations(assembly_name)} "
        "(family_guid, generation, projects, shown_samples, shared_samples, source_project, data_version) VALUES",
        [
            (
                family_uuid,
                generation.generation,
                list(state.projects),
                list(state.shown_samples),
                list(state.shared_samples),
                generation.source_project,
                state.data_version,
            )
        ],
    )
    await _write_contribution(assembly_name, family_uuid, generation, sign=1)
    # The records of the generations the table no longer holds.
    await execute_clickhouse(
        f"ALTER TABLE {_generations(assembly_name)} DELETE "
        "WHERE family_guid = %(family)s AND generation != %(generation)s SETTINGS mutations_sync = 1",
        {"family": family_uuid, "generation": generation.generation},
    )
    return generation


async def _detach(assembly_name: str, family_uuid: str, applied: _Generation, data_version: str) -> None:
    """Take the family's contribution ``applied`` out, recomputed from the rows it was
    computed from: ``data_version`` is the family's now, which must be the one it was computed
    at, or the rows changed while it was in (:class:`CarrierSummaryInconsistent`)."""
    if data_version != applied.state.data_version:
        raise CarrierSummaryInconsistent(
            f"family {family_uuid}: rows changed while its contribution was in "
            f"({applied.state.data_version} then, {data_version} now)"
        )
    await _write_contribution(assembly_name, family_uuid, applied, sign=-1)


# --------------------------------------------------------------------------- #
# Locks                                                                       #
# --------------------------------------------------------------------------- #


def _summary_lock_key(assembly_name: str) -> str:
    return f"variant-carriers:{_require_clickhouse_identifier(assembly_name)}"


@asynccontextmanager
async def _summary_session(
    assembly_name: str, *, exclusive: bool = False, wait: bool = True
) -> AsyncIterator[tuple[AsyncSession, bool]]:
    """A Postgres transaction holding the summary's lock: shared by every change of one
    family's contribution, exclusive while the summary is emptied. Yields the session and
    whether the lock was granted (always, when ``wait``)."""
    sessionmaker = get_postgres_sessionmaker()
    async with sessionmaker() as session:
        async with session.begin():
            key = _summary_lock_key(assembly_name)
            if exclusive:
                function = "pg_advisory_xact_lock" if wait else "pg_try_advisory_xact_lock"
            else:
                function = "pg_advisory_xact_lock_shared" if wait else "pg_try_advisory_xact_lock_shared"
            result = await session.execute(text(f"SELECT {function}(hashtext(:k))"), {"k": key})
            granted = True if wait else bool(result.scalar_one())
            yield session, granted


async def _try_lock_family(session: AsyncSession, family_uuid: str) -> bool:
    """The family's small-variant write lock for the session's transaction, if no writer
    holds it (``family_variant_write_lock``)."""
    result = await session.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
        {"k": family_variant_lock_key(family_uuid, SMALL_VARIANTS)},
    )
    return bool(result.scalar_one())


# --------------------------------------------------------------------------- #
# Emptying the summary                                                        #
# --------------------------------------------------------------------------- #


async def _request_rebuild(assembly_name: str, reason: str) -> None:
    """Ask for the summary to be emptied, so the reconciler adds every family again: a
    contribution that cannot be taken out exactly leaves its rows in the sums."""
    logger.error("The variant carrier summary of %s is emptied and rebuilt: %s", assembly_name, reason)
    await execute_clickhouse(
        f"INSERT INTO {_generations(assembly_name)} "
        "(family_guid, generation, projects, shown_samples, shared_samples, source_project, data_version) VALUES",
        [("", secrets.randbits(63), [], [], [], "", "")],
    )


async def _rebuild_requested(assembly_name: str) -> bool:
    rows = await execute_clickhouse(
        f"SELECT count() FROM {_generations(assembly_name)} WHERE family_guid = ''", {}
    )
    return bool(rows and int(rows[0][0]))


async def _empty_summary(assembly_name: str) -> bool:
    """Empty the summary and its records, under the summary's exclusive lock; False when a
    change of a family's contribution holds the lock shared (tried again next round)."""
    async with _summary_session(assembly_name, exclusive=True, wait=False) as (_session, granted):
        if not granted:
            return False
        await execute_clickhouse(f"TRUNCATE TABLE {_carriers(assembly_name)}")
        await execute_clickhouse(f"TRUNCATE TABLE {_generations(assembly_name)}")
    return True


# --------------------------------------------------------------------------- #
# The writers' hook                                                           #
# --------------------------------------------------------------------------- #


async def detach_family_variant_carriers(assembly_name: str, family_uuid: str) -> None:
    """Take the family's contribution out of the summary before its ``entries`` rows change.

    Every writer of a family's ``entries`` rows calls it before its first write, holding the
    family's small-variant write lock (``lock_family_variant_writes``), so the rows the
    contribution was computed from are still there. Calling it again is cheap: a family whose
    contribution is out reads one marker query. The reconciler adds the contribution again
    once the writer has let go of the family.

    It never stops the write: when the contribution cannot be taken out exactly, the summary
    is emptied and rebuilt (:func:`_request_rebuild`), and a failure is logged."""
    from .clickhouse_variant_storage import ensure_clickhouse_variant_tables, get_family_small_variant_data_version

    try:
        await ensure_clickhouse_variant_tables(assembly_name)
        if not await _applied_generations(assembly_name, [family_uuid]):
            return
        async with _summary_session(assembly_name):
            applied = (await _applied(assembly_name, [family_uuid])).get(family_uuid)
            if applied is None:
                return
            version = await get_family_small_variant_data_version(assembly_name, family_uuid)
            await _detach(assembly_name, family_uuid, applied, version)
    except Exception as exc:  # noqa: BLE001 - the write goes on; the summary is rebuilt
        try:
            await _request_rebuild(assembly_name, f"family {family_uuid}: {describe_error(exc)}")
        except Exception as request_exc:  # noqa: BLE001 - logged; the write goes on
            logger.error(
                "Could not ask for the variant carrier summary to be rebuilt: %s", describe_error(request_exc)
            )


# --------------------------------------------------------------------------- #
# Reconciliation                                                              #
# --------------------------------------------------------------------------- #


def _stale(desired: FamilyCarrierState | None, applied: _Generation | None) -> bool:
    """Whether the table's contribution of a family differs from what it should hold: none
    for a family not on the assembly or never written, the family's present state else."""
    if desired is None:
        return applied is not None
    if applied is None:
        return desired.data_version != _DATA_VERSION_NEVER_WRITTEN
    return applied.state != desired


async def _family_states(
    session: AsyncSession, assembly_id: str, assembly_name: str, family_uuids: Collection[str] | None
) -> dict[str, FamilyCarrierState]:
    desired = await _desired_states(session, assembly_id, family_uuids)
    versions = await _data_versions(assembly_name, list(desired) if family_uuids is not None else None)
    return {
        family: FamilyCarrierState(
            projects=projects,
            shown_samples=shown,
            shared_samples=shared,
            data_version=versions.get(family, _DATA_VERSION_NEVER_WRITTEN),
        )
        for family, (projects, shown, shared) in desired.items()
    }


async def _current_states(
    assembly_id: str, assembly_name: str, family_uuids: Collection[str] | None
) -> dict[str, FamilyCarrierState]:
    """:func:`_family_states`, read in a session of its own."""
    async with get_postgres_sessionmaker()() as session:
        return await _family_states(session, assembly_id, assembly_name, family_uuids)


async def pending_variant_carrier_families(
    session: AsyncSession, assembly_id: str, assembly_name: str, project_ids: Collection[str]
) -> list[str]:
    """The families of ``project_ids`` whose contribution the summary does not hold as they
    are now: being written, or changed since the reconciler last ran; also a family whose
    contribution still counts it under one of those projects. All of them while the summary
    is being emptied."""
    from .clickhouse_variant_storage import ensure_clickhouse_variant_tables

    await ensure_clickhouse_variant_tables(assembly_name)
    scope = set(project_ids)
    desired = await _family_states(session, assembly_id, assembly_name, None)
    in_scope = {family: state for family, state in desired.items() if scope.intersection(state.projects)}
    if await _rebuild_requested(assembly_name):
        return sorted(
            family for family, state in in_scope.items() if state.data_version != _DATA_VERSION_NEVER_WRITTEN
        )
    try:
        applied = await _applied(assembly_name)
    except CarrierSummaryInconsistent:
        return sorted(in_scope)
    counted = {family for family, generation in applied.items() if scope.intersection(generation.state.projects)}
    return sorted(
        family
        for family in set(in_scope) | counted
        if _stale(desired.get(family), applied.get(family))
    )


async def _refresh_family(
    assembly_id: str, assembly_name: str, family_uuid: str
) -> bool:
    """Bring the family's contribution up to date, holding its write lock; False when a writer
    holds it."""
    from .clickhouse_variant_storage import get_family_small_variant_data_version

    async with _summary_session(assembly_name) as (session, _granted):
        if not await _try_lock_family(session, family_uuid):
            return False
        desired = (await _family_states(session, assembly_id, assembly_name, [family_uuid])).get(family_uuid)
        applied = (await _applied(assembly_name, [family_uuid])).get(family_uuid)
        if not _stale(desired, applied):
            return True
        if applied is not None:
            version = await get_family_small_variant_data_version(assembly_name, family_uuid)
            await _detach(assembly_name, family_uuid, applied, version)
        if desired is not None and desired.data_version != _DATA_VERSION_NEVER_WRITTEN:
            await _attach(assembly_name, family_uuid, desired)
    return True


async def reconcile_variant_carriers(
    assembly_id: str,
    assembly_name: str,
    *,
    family_uuids: Collection[str] | None = None,
    time_budget_seconds: float | None = None,
) -> CarrierSummaryStatus:
    """Bring the contribution of every family of the assembly (or of ``family_uuids``) up to
    date: add a family's when it has rows and the summary holds none, recompute one whose rows,
    shown members or projects changed, take out one no longer linked to the assembly.

    A family whose write lock a writer holds is left for the next round, as are those past
    ``time_budget_seconds``. A summary asked to be emptied is emptied first."""
    from .clickhouse_variant_storage import ensure_clickhouse_variant_tables

    await ensure_clickhouse_variant_tables(assembly_name)
    status = CarrierSummaryStatus()
    if await _rebuild_requested(assembly_name):
        if not await _empty_summary(assembly_name):
            return status
        status.emptied = True
    started = time.monotonic()
    desired = await _current_states(assembly_id, assembly_name, family_uuids)
    try:
        applied = await _applied(assembly_name, family_uuids)
    except CarrierSummaryInconsistent as exc:
        await _request_rebuild(assembly_name, str(exc))
        return status
    candidates = sorted(
        family for family in set(desired) | set(applied) if _stale(desired.get(family), applied.get(family))
    )
    for family in candidates:
        if time_budget_seconds is not None and time.monotonic() - started > time_budget_seconds:
            status.pending.append(family)
            continue
        try:
            if await _refresh_family(assembly_id, assembly_name, family):
                status.refreshed.append(family)
            else:
                status.pending.append(family)
        except CarrierSummaryInconsistent as exc:
            await _request_rebuild(assembly_name, str(exc))
            status.pending.extend(family_ for family_ in candidates if family_ not in status.refreshed)
            break
    return status


# --------------------------------------------------------------------------- #
# The worker                                                                  #
# --------------------------------------------------------------------------- #


async def _assemblies_with_projects() -> list[tuple[str, str]]:
    async with get_postgres_sessionmaker()() as session:
        rows = await session.execute(
            text(
                """
                SELECT DISTINCT a.id::text AS assembly_id, a.assembly_name
                FROM assemblies a
                JOIN projects p ON p.assembly_id = a.id
                ORDER BY 2
                """
            )
        )
        return [(str(row["assembly_id"]), str(row["assembly_name"])) for row in rows.mappings().all()]


async def drop_stale_carrier_stages(now: float | None = None) -> int:
    """Drop the staging tables a write left when its process stopped: those older than an
    hour. Returns how many."""
    rows = await execute_clickhouse(
        "SELECT name FROM system.tables WHERE database = %(database)s AND name LIKE %(pattern)s",
        {"database": settings.clickhouse_database, "pattern": f"%/SNV_INDEL/{_STAGE_SUFFIX}/%"},
    )
    cutoff = (now if now is not None else time.time()) - _STAGE_MAX_AGE_SECONDS
    dropped = 0
    for (name,) in rows or []:
        match = _STAGE_NAME.match(str(name))
        if match is None or int(match.group("created")) > cutoff:
            continue
        await execute_clickhouse(f"DROP TABLE IF EXISTS {settings.clickhouse_database}.`{name}`")
        dropped += 1
    return dropped


async def reconcile_all_variant_carriers(*, time_budget_seconds: float | None = None) -> None:
    """One round of the worker: every assembly with a project."""
    for assembly_id, assembly_name in await _assemblies_with_projects():
        status = await reconcile_variant_carriers(
            assembly_id, assembly_name, time_budget_seconds=time_budget_seconds
        )
        if status.refreshed or status.emptied:
            logger.info(
                "Variant carrier summary of %s: %d families brought up to date, %d left%s",
                assembly_name,
                len(status.refreshed),
                len(status.pending),
                ", emptied first" if status.emptied else "",
            )


VARIANT_CARRIER_ROUND_SECONDS = 5.0


async def variant_carrier_worker(stop: asyncio.Event) -> None:
    """Reconcile the summary every few seconds until ``stop`` is set.

    Runs where the package-import workers run, as the ancestry worker does: in an import-worker
    process (``app.import_worker``), or in the API when it runs import workers itself. Several
    may run: a family is refreshed under its write lock, which one of them gets."""
    try:
        await drop_stale_carrier_stages()
    except Exception as exc:  # noqa: BLE001 - logged; the rounds go on
        logger.warning("Could not drop old carrier staging tables: %s", describe_error(exc))
    while not stop.is_set():
        try:
            await reconcile_all_variant_carriers(time_budget_seconds=60.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - log only sanitized error details
            logger.warning("Variant carrier worker failed: %s", describe_error(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=VARIANT_CARRIER_ROUND_SECONDS)
        except asyncio.TimeoutError:
            pass


stop_variant_carrier_worker = stop_worker
