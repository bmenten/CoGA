"""Stand-ins the structural-variant unit tests share: the SV search's stores and the SV upload's.

* **The SV search** (``clickhouse_family_variants``: the SV list, ranked or not, its CSV export,
  the genome track). ``stored_sv_row`` is a record as ClickHouse returns it to the candidate
  read (``_fetch_structural_variant_rows``), ``serve_stored_sv_rows`` a ClickHouse holding some
  records, ``stub_sv_postgres_lookups`` a Postgres without reviews, cytobands or phenotypes, and
  ``forbid_sv_native_page`` keeps the callset's native page from answering a search whose
  filters its SQL does not apply. ``kept_by_record_filter`` runs the record filter
  (``_structural_record_matches``) alone.
* **The per-sample SV upload** (``variant_upload_service.upload_structural_variant_file``).
  ``SvStore`` is the family's stored SV rows, served by source as the real read serves them,
  and the rewrites made; ``install_sv_upload_fakes`` puts it, the gene table and the manifest's
  provenance write in place; ``UploadSession`` is Postgres as the upload uses it.

Import as ``from backend.tests._sv_fakes import …``. The records and the families are synthetic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

import pytest

from backend.app.services import annotation_manifest_service, variant_upload_service
from backend.app.services.clickhouse_variant_queries import _structural_record_matches
from backend.app.services.clickhouse_variant_records import (
    StoredStructuralVariantRow,
    StructuralVariantRecord,
)
from backend.app.services.family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from backend.app.services.family_variant_filters import StructuralVariantQueryFilters

# The module the SV search's stores are patched on.
SV_SEARCH = "backend.app.services.clickhouse_family_variants."


# --------------------------------------------------------------------------- #
# The SV search
# --------------------------------------------------------------------------- #


def stored_sv_row(record: StructuralVariantRecord, key: int) -> tuple[Any, ...]:
    """``record`` as ClickHouse returns it to the SV candidate read
    (``_fetch_structural_variant_rows``): the entry's position, type and calls, and the SVLEN
    of its details row (``svLen``)."""
    return (
        key,
        record.variant_id,
        record.chr,
        record.start,
        record.end,
        record.sv_type,
        record.source,
        None,
        None,
        record.sv_len,
        list(record.filters),
        "",
        list(record.gene_symbols),
        [call.sample for call in record.calls],
        [call.gt for call in record.calls],
        [call.qual for call in record.calls],
        [call.read_support for call in record.calls],
        [call.filter for call in record.calls],
        [call.copy_number for call in record.calls],
    )


def kept_by_record_filter(records: Iterable[StructuralVariantRecord], **filters: Any) -> list[str]:
    """The ids of ``records`` the SV record filter keeps for ``filters``, in order."""
    query = StructuralVariantQueryFilters(page=1, page_size=100, **filters)
    return [record.variant_id for record in records if _structural_record_matches(record, query, [], [])]


def proband_family() -> FamilyMetadataContext:
    """A family of one affected proband, ``PROBAND``."""
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM001",
        project_ids=["project-uuid"],
        sample_rows=[{"sample_id": "PROBAND", "role": "proband", "affected": True, "sex": "female"}],
        sample_uuid_to_name={"sample-proband": "PROBAND"},
        sample_name_to_uuid={"PROBAND": "sample-proband"},
        affected_sample_names=["PROBAND"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def stub_sv_postgres_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Postgres holds no SV reviews, cytobands or phenotypes for the SV search."""

    async def no_review_ids(*_args: Any, **_kwargs: Any) -> set[str]:
        return set()

    async def nothing(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def no_phenotypes(*_args: Any, **_kwargs: Any) -> tuple[list[str], dict[str, str]]:
        return [], {}

    monkeypatch.setattr(SV_SEARCH + "list_matching_structural_variant_review_ids", no_review_ids)
    monkeypatch.setattr(SV_SEARCH + "get_structural_variant_review_map", nothing)
    monkeypatch.setattr(SV_SEARCH + "_fetch_structural_cytoband_map", nothing)
    monkeypatch.setattr(SV_SEARCH + "_affected_present_hpo", no_phenotypes)


def forbid_sv_native_page(monkeypatch: pytest.MonkeyPatch, why: str) -> None:
    """The callset's native page may not answer: its SQL does not apply the search's filters
    (``why``), so a search that reached it would pass them over."""

    async def native_summary(*_args: Any, **_kwargs: Any):
        raise AssertionError(why)

    monkeypatch.setattr(SV_SEARCH + "_fetch_structural_variant_summary", native_summary)


def serve_stored_sv_rows(
    monkeypatch: pytest.MonkeyPatch, records: Iterable[StructuralVariantRecord]
) -> list[tuple[str, dict[str, Any]]]:
    """ClickHouse holding ``records``: every query gets all their rows, so the record filter
    decides (the SQL applies neither a length nor a type, and every row meets the conditions
    it does send: the family, its samples). Returns the queries sent, with their parameters."""
    sent: list[tuple[str, dict[str, Any]]] = []
    rows = [stored_sv_row(record, key) for key, record in enumerate(records, start=1)]

    async def execute(query: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        sent.append((query, dict(params)))
        return rows

    monkeypatch.setattr(SV_SEARCH + "_execute_clickhouse", execute)
    return sent


# --------------------------------------------------------------------------- #
# The per-sample SV upload
# --------------------------------------------------------------------------- #


def upload_family_context(
    samples: Mapping[str, str], *, project_ids: list[str] | None = None
) -> FamilyMetadataContext:
    """The uploading family ``FAM``: ``samples`` maps each sample the family view shows to its
    uuid (a member left out is one the view hides, such as an inactive member)."""
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM",
        project_ids=project_ids if project_ids is not None else ["project-uuid"],
        sample_rows=[],
        sample_uuid_to_name={uuid: name for name, uuid in samples.items()},
        sample_name_to_uuid=dict(samples),
        affected_sample_names=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def upload_sample_context(samples: Mapping[str, str], sample: str) -> SampleMetadataContext:
    """The uploaded sample of ``upload_family_context(samples)``."""
    return SampleMetadataContext(
        sample_uuid=samples[sample],
        sample_id=sample,
        family_uuid="family-uuid",
        family_id="FAM",
        sex="und",
        project_ids=["project-uuid"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


class _UploadResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "_UploadResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar_one_or_none(self) -> None:
        return None


class UploadSession:
    """Postgres as the upload uses it: the stored-sample lookup of its column check (the
    family's samples are ``samples``, PROBAND alone by default), the sample's metadata read
    and update, and the commit."""

    def __init__(self, samples: Iterable[str] = ("PROBAND",)) -> None:
        self.samples = list(samples)

    async def execute(self, statement: Any, params: Any = None) -> _UploadResult:
        if "FROM samples WHERE family_id" in " ".join(str(statement).split()):
            return _UploadResult([{"sample_id": sample} for sample in self.samples])
        return _UploadResult()

    async def commit(self) -> None:
        return None


class SvStore:
    """The family's stored SV rows, served with the real read's scope: exactly the requested
    source's rows, every source's without one. ``reads`` is the source each read asked for,
    ``rewrites`` each rewrite's source and rows."""

    def __init__(self, rows: Iterable[StoredStructuralVariantRow] = ()) -> None:
        self.rows = list(rows)
        self.reads: list[str | None] = []
        self.rewrites: list[dict[str, Any]] = []

    @classmethod
    def of_records(cls, records: Iterable[StructuralVariantRecord], project: str = "project-uuid") -> "SvStore":
        """A store of ``records``, each a row of ``project``."""
        return cls(StoredStructuralVariantRow(project_id=project, record=record) for record in records)

    async def fetch(self, _assembly: Any, _family: Any, *, source: str | None = None) -> list[StoredStructuralVariantRow]:
        self.reads.append(source)
        return [row for row in self.rows if source is None or row.record.source == source]

    async def rewrite(
        self, _assembly: Any, _family: Any, rows: Iterable[StoredStructuralVariantRow], *, source=None, stored=()
    ) -> None:
        self.rewrites.append({"source": source, "rows": list(rows)})

    def written(self) -> list[StoredStructuralVariantRow]:
        """The rows of the one rewrite made."""
        assert len(self.rewrites) == 1, self.rewrites
        return self.rewrites[0]["rows"]


def install_sv_upload_fakes(
    monkeypatch: pytest.MonkeyPatch,
    store: SvStore,
    *,
    genes: Mapping[str, list[tuple[int, int, str]]] | None = None,
    known_samples: Iterable[str] | None = None,
    provenance: Callable[..., Awaitable[None]] | None = None,
) -> SvStore:
    """The upload's stores: the SV rows in ``store``, the gene table holding ``genes`` (none
    by default), and the manifest's provenance write (``provenance``, or nothing). With
    ``known_samples`` the stored-sample lookup of the column check answers them, without
    reaching the session. The family's write lock is Postgres's
    (test_family_variant_writes_serialized has it)."""

    async def gene_table(*_args: Any, **_kwargs: Any) -> Mapping[str, list[tuple[int, int, str]]]:
        return dict(genes or {})

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(variant_upload_service, "fetch_family_structural_variant_rows", store.fetch)
    monkeypatch.setattr(variant_upload_service, "rewrite_family_structural_variants", store.rewrite)
    monkeypatch.setattr(variant_upload_service, "_fetch_genes_for_chroms", gene_table)
    monkeypatch.setattr(variant_upload_service, "lock_family_variant_writes", nothing)
    monkeypatch.setattr(annotation_manifest_service, "merge_vcf_header_provenance", provenance or nothing)
    if known_samples is not None:
        known = set(known_samples)

        async def known_vcf_sample_ids(*_args: Any, **_kwargs: Any) -> set[str]:
            return set(known)

        monkeypatch.setattr(variant_upload_service, "known_vcf_sample_ids", known_vcf_sample_ids)
    return store
