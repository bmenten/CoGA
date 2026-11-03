"""Stand-ins for the repeat catalogue and the repeat tables, shared by the repeat-expansion tests.

``catalogue_after_a_start`` runs the start's seed (``seed_builtin_repeat_catalog``, over the
built-in loci and the shipped STRchive file) into a recording session and reads back the
``repeat_loci`` rows a start leaves, as the import's catalogue query returns them (by
``locus_id``); ``import_lookup`` builds the import's TRID lookup from them. ``RowsSession``
answers every query, the family table's and the genome track's among them, with the rows given.
``trgt_insert_params`` builds the row the import stores for one TRGT record of one sample.
All samples and values are synthetic.

Import as ``from backend.tests._repeat_fakes import ...``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.app.services import repeat_expansion_pg
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext


class Result:
    """A query's rows, read as the repeat services read them."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, Any]:
        return self._rows[0]


class RecordingSession:
    """Keeps each statement (its SQL on one line) with its parameters, and whether it committed."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.committed = False

    @property
    def params(self) -> list[dict[str, Any]]:
        return [params for _sql, params in self.statements]

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:
        self.statements.append((" ".join(str(statement).split()), params or {}))

    async def commit(self) -> None:
        self.committed = True


class RowsSession:
    """Answers every query with ``rows``; keeps each statement's SQL on one line."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Result:
        self.statements.append(" ".join(str(statement).split()))
        return Result(self.rows)


_CATALOGUE_COLUMNS = (
    "locus_id",
    "gene",
    "display_name",
    "disease",
    "inheritance",
    "motif",
    "motif_index",
    "warning_min",
    "pathogenic_min",
    "notes",
)


async def seed_a_start(monkeypatch: pytest.MonkeyPatch) -> RecordingSession:
    """A start's seed of the catalogue, recorded: the upserts, then the removal of STRchive rows
    of another release (CLIN-31)."""
    # The shipped STRchive file, not one an environment variable may point at.
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", None)
    session = RecordingSession()
    await repeat_expansion_pg.seed_builtin_repeat_catalog(session)  # type: ignore[arg-type]
    return session


def upserts(session: RecordingSession) -> list[tuple[str, dict[str, Any]]]:
    """The seed's upserts, without its last statement (the removal, which binds no locus_id)."""
    return [(sql, params) for sql, params in session.statements if "locus_id" in params]


async def catalogue_after_a_start(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """The ``repeat_loci`` rows a start leaves, by locus_id in the catalogue query's order, as
    that query returns them. The seed upserts by ``locus_id``, so the last write of a locus is
    its row."""
    rows: dict[str, dict[str, Any]] = {}
    for _sql, params in upserts(await seed_a_start(monkeypatch)):
        rows[params["locus_id"]] = {
            **{column: params[column] for column in _CATALOGUE_COLUMNS},
            "aliases": json.loads(params["aliases_json"]),
            "metadata": json.loads(params["metadata_json"]),
        }
    return {locus_id: rows[locus_id] for locus_id in sorted(rows)}


async def import_lookup(monkeypatch: pytest.MonkeyPatch) -> repeat_expansion_pg.RepeatLocusLookup:
    """The import's TRID lookup over the catalogue a start leaves."""
    rows = await catalogue_after_a_start(monkeypatch)
    return await repeat_expansion_pg._build_repeat_locus_lookup(RowsSession(list(rows.values())))  # type: ignore[arg-type]


def sample_context(
    *, sex: str = "female", assembly_name: str | None = "GRCh38", family_id: str = "FAM1"
) -> SampleMetadataContext:
    """The sample a per-sample TRGT import reads, named ``sample`` in its VCF."""
    return SampleMetadataContext(
        sample_uuid="sample-uuid",
        sample_id="sample",
        family_uuid="family-uuid",
        family_id=family_id,
        sex=sex,
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name=assembly_name,
    )


def family_context(*members: tuple[str, str, str, bool, str], family_id: str = "FAM1") -> FamilyMetadataContext:
    """A family of ``members``, each (sample uuid, sample ID, role, affected, sex), on GRCh38."""
    return FamilyMetadataContext(
        family_uuid="00000000-0000-4000-8000-0000000000f1",
        family_id=family_id,
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": uuid, "sample_id": name, "role": role, "affected": affected, "sex": sex}
            for uuid, name, role, affected, sex in members
        ],
        sample_uuid_to_name={uuid: name for uuid, name, *_rest in members},
        sample_name_to_uuid={name: uuid for uuid, name, *_rest in members},
        affected_sample_names=[name for _uuid, name, _role, affected, _sex in members if affected],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def trgt_insert_params(
    lookup: dict[str, dict[str, Any]],
    *,
    info_field: str,
    sample_field: str,
    chrom: str,
    pos: int | str,
    ref: str,
    sex: str = "female",
    assembly_name: str | None = "GRCh38",
    family_id: str = "FAM1",
) -> dict[str, Any]:
    """The row the import stores for one TRGT record (``GT:AL:MC``) of the sample ``sample``."""
    return repeat_expansion_pg._build_trgt_insert_params(
        sample_context=sample_context(sex=sex, assembly_name=assembly_name, family_id=family_id),
        locus_lookup=lookup,
        chrom=chrom,
        pos=str(pos),
        ref=ref,
        info_field=info_field,
        format_field="GT:AL:MC",
        sample_field=sample_field,
        header_sample="sample",
        metadata={},
    )


def allele_statuses(params: dict[str, Any]) -> list[str]:
    """The status of each allele of a stored call."""
    return [allele["status"] for allele in json.loads(params["alleles_json"])]
