"""A family's primary small-variant callset from one VCF per sample.

The long-read pipeline (nf-core/lrsvar) calls and annotates each sample on its own
(DeepVariant, phased by longphase, annotated by VEP) and writes no joint callset, so a
couple screened for carriership arrives as one annotated VCF per partner. CoGA reads such
files as the family's primary callset (source ``clair3``, the label of the primary,
directly called nuclear callset whatever produced it). The files are read side by side:
every file is sorted by position in the order of its ``##contig`` lines, and the records
of one site -- one chromosome, position, REF and ALT -- become one row holding the call
of each sample whose file has a record there. Nothing is held in memory but the records
of the position being read.

Records whose FILTER values are all excluded (DeepVariant's ``RefCall`` reference sites
and ``NoCall`` zero-depth sites) carry no variant. A site where every record is excluded
is left out. A site where one sample has a variant keeps the other samples' excluded
records too, as their calls there: a ``RefCall`` says the caller read that sample as
reference (0/0), a ``NoCall`` that it could not call it (./.), which says more than no
record.

A sample whose file has no record at a site had no read evidence of the variant that its
caller reported. The genotype readers take it as reference there, as a joint VCF would
have called it, unless the sample has no call at all in the callset (it was not
sequenced): see ``sample_integrity_service``. Two records of one position whose alleles
differ (``A>G`` in one file, ``A>G,T`` in the other) are two sites, as in a
``bcftools merge -m none``; the multi-allelic one is not split.

Each file holds one sample, so a record's QUAL, FILTER and caller metrics are that call's
own and are kept with the call (``calls.filters``, ``calls.metrics``). The row's FILTER is
every FILTER value of its records, its QUAL the best of its kept records' QUAL, and its
annotation that of the first file with a kept record there (one annotation run annotates
every file the same way).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import io
import logging
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Iterator, Sequence

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .annotation_table_parser import _coerce_int
from .clickhouse_variant_ids import build_small_variant_id
from .clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from .clickhouse_variant_storage import (
    count_family_small_variants,
    delete_family_small_variants,
    family_small_variant_call_samples,
    insert_small_variant_records,
    refresh_family_small_variant_summaries,
)
from .data_scope import normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from .family_variant_write_lock import SMALL_VARIANTS, lock_family_variant_writes
from .import_progress import bytes_read_on_disk, file_size, read_stats
from .variant_annotation_parser import (
    AnnotationHeaderState,
    extract_small_variant_annotations,
    update_annotation_header_state,
)
from .variant_upload_service import (
    SMALL_VARIANT_PROGRESS_INTERVAL,
    SMALL_VARIANT_UPLOAD_BATCH_SIZE,
    _PROVENANCE_HEADER_CAP,
    _PROVENANCE_SKIP_PREFIXES,
    _first_present_float,
    _first_present_int,
    _iter_bounded_lines,
    _parse_float_list,
    _parse_format,
    _parse_info,
    _parse_int_list,
    _parse_qual,
)
from .vcf_call_metrics import record_filter_values, single_sample_call_metrics
from .vcf_header_provenance import extract_header_provenance, merge_module_maps

logger = logging.getLogger(__name__)

# The callset the files are stored as: the family's primary, directly called callset.
PER_SAMPLE_CALLSET_SOURCE = "clair3"

_CONTIG_ID = re.compile(r"##contig=<(?:[^>]*,)?ID=([^,>]+)")


class PerSampleMergeError(ValueError):
    """The per-sample VCFs cannot be read side by side (unsorted, or sorted differently)."""


@dataclass(frozen=True, slots=True)
class PerSampleVcf:
    """One sample's VCF: the family sample it holds and the index of that sample's column
    among the file's sample columns (``per_sample_vcf_column`` chose it)."""

    sample_id: str
    path: Path
    column: int = 0


@dataclass(slots=True)
class _SiteRecord:
    reader: int  # the index of the file's reader
    chrom: str
    pos: int
    ref: str
    alt: str
    qual: str
    filt: str
    info: str
    fmt: str
    sample_field: str
    excluded: bool


class _ContigOrder:
    """The order the files are sorted in: the ``##contig`` order of the first file, with
    the contigs only a later file declares placed after the contig that file lists before
    them. A contig no header declares is placed last when a record first names it."""

    def __init__(self) -> None:
        self._order: list[str] = []
        self._ranks: dict[str, int] = {}

    def add_header(self, contigs: Sequence[str], *, label: str) -> None:
        shared = [contig for contig in dict.fromkeys(contigs) if contig in self._ranks]
        if any(self._ranks[left] > self._ranks[right] for left, right in zip(shared, shared[1:])):
            raise PerSampleMergeError(
                f"{label} lists its contigs in another order than the other files: "
                "they were not made against the same reference"
            )
        position = -1
        for contig in dict.fromkeys(contigs):
            if contig in self._ranks:
                position = self._order.index(contig)
                continue
            position += 1
            self._order.insert(position, contig)
        self._ranks = {contig: index for index, contig in enumerate(self._order)}

    def rank(self, contig: str) -> int:
        rank = self._ranks.get(contig)
        if rank is None:
            rank = len(self._order)
            self._order.append(contig)
            self._ranks[contig] = rank
        return rank


class _PerSampleVcfReader:
    """One per-sample VCF read line by line: its header, then its records in file order."""

    def __init__(self, index: int, source: PerSampleVcf, *, excluded_filters: set[str]) -> None:
        self.index = index
        self.source = source
        self.label = f"The SNV file of {source.sample_id} ({source.path.name})"
        self._excluded_filters = excluded_filters
        self._raw = source.path.open("rb")
        magic = self._raw.read(2)
        self._raw.seek(0)
        self._handle: io.TextIOBase
        if magic == b"\x1f\x8b" or source.path.name.endswith(".gz"):
            self._handle = gzip.open(self._raw, mode="rt", encoding="utf-8", errors="replace")
        else:
            self._handle = io.TextIOWrapper(self._raw, encoding="utf-8", errors="replace")
        self._lines = _iter_bounded_lines(self._handle, kind="VCF")
        self.annotation_state = AnnotationHeaderState()
        self.provenance_lines: list[str] = []
        self.contigs: list[str] = []
        self.columns: list[str] = []
        self.records_read = 0
        self.skipped_malformed = 0
        self._order: _ContigOrder | None = None
        self._next: _SiteRecord | None = None
        self._next_key: tuple[int, int] | None = None
        self._last_key: tuple[int, int] | None = None

    def read_header(self) -> None:
        for line in self._lines:
            if line.startswith("##"):
                if line.startswith("##INFO"):
                    update_annotation_header_state(self.annotation_state, line.strip())
                elif line.startswith("##contig"):
                    match = _CONTIG_ID.match(line.strip())
                    if match:
                        self.contigs.append(normalize_chromosome(match.group(1)))
                elif (
                    not line.startswith(_PROVENANCE_SKIP_PREFIXES)
                    and len(self.provenance_lines) < _PROVENANCE_HEADER_CAP
                ):
                    self.provenance_lines.append(line.rstrip())
                continue
            if line.startswith("#CHROM"):
                self.columns = line.rstrip("\r\n").split("\t")[9:]
                return
            break
        raise PerSampleMergeError(f"{self.label} has no #CHROM line naming its sample")

    def start(self, order: _ContigOrder) -> None:
        self._order = order
        self._advance()

    def bytes_read(self) -> int:
        return bytes_read_on_disk(self._handle) or 0

    def close(self) -> None:
        try:
            self._handle.close()
        finally:
            self._raw.close()

    @property
    def next_key(self) -> tuple[int, int] | None:
        return self._next_key

    def _advance(self) -> None:
        assert self._order is not None
        sample_column = 9 + self.source.column
        for line in self._lines:
            if not line.strip() or line.startswith("#"):
                continue
            self.records_read += 1
            fields = line.rstrip("\r\n").split("\t")
            position = _coerce_int(fields[1]) if len(fields) > 1 else None
            if len(fields) <= sample_column or position is None:
                self.skipped_malformed += 1
                continue
            chrom, _pos, _id, ref, alt, qual, filt, info, fmt = fields[:9]
            key = (self._order.rank(normalize_chromosome(chrom)), position)
            if self._last_key is not None and key < self._last_key:
                raise PerSampleMergeError(
                    f"{self.label} is not sorted as the other files are: {chrom}:{position} "
                    "comes after a later position"
                )
            self._last_key = key
            record_filters = [value for value in filt.split(";") if value] if filt not in {"", "."} else []
            self._next = _SiteRecord(
                reader=self.index,
                chrom=chrom,
                pos=position,
                ref=ref,
                alt=alt,
                qual=qual,
                filt=filt,
                info=info,
                fmt=fmt,
                sample_field=fields[sample_column],
                excluded=bool(record_filters)
                and all(value in self._excluded_filters for value in record_filters),
            )
            self._next_key = key
            return
        self._next = None
        self._next_key = None

    def take_position(self, key: tuple[int, int]) -> list[_SiteRecord]:
        records: list[_SiteRecord] = []
        while self._next is not None and self._next_key == key:
            records.append(self._next)
            self._advance()
        return records


def iter_merged_sites(readers: Sequence[_PerSampleVcfReader]) -> Iterator[list[_SiteRecord]]:
    """The records of each site of the files, site by site in position order: one list per
    chromosome, position, REF and ALT, holding the first record of each file there."""
    while True:
        keys = [reader.next_key for reader in readers if reader.next_key is not None]
        if not keys:
            return
        key = min(keys)
        groups: dict[tuple[str, str], list[_SiteRecord]] = {}
        for reader in readers:
            if reader.next_key != key:
                continue
            for record in reader.take_position(key):
                group = groups.setdefault((record.ref, record.alt), [])
                # A second record of one file at one site is a malformed file; its first
                # record stands.
                if all(existing.reader != record.reader for existing in group):
                    group.append(record)
        yield from groups.values()


def _site_call(sample_id: str, record: _SiteRecord) -> SmallVariantCall:
    fmt_vals = _parse_format(record.fmt, record.sample_field)
    return SmallVariantCall(
        sample=sample_id,
        gt=fmt_vals.get("GT", "./."),
        gq=_first_present_float(fmt_vals, "GQ"),
        dp=_first_present_int(fmt_vals, "DP", "MED_DP", "MIN_DP"),
        # DeepVariant writes the per-allele alt fraction as VAF, not AF.
        af=_parse_float_list(fmt_vals.get("AF") or fmt_vals.get("VAF")),
        ad=_parse_int_list(fmt_vals.get("AD")),
        ps=_first_present_int(fmt_vals, "PS"),
        filters=record_filter_values(record.filt),
        metrics=single_sample_call_metrics(record.info, record.qual),
    )


def merged_site_record(
    group: Sequence[_SiteRecord],
    readers: Sequence[_PerSampleVcfReader],
    *,
    source: str = PER_SAMPLE_CALLSET_SOURCE,
) -> SmallVariantRecord:
    """One row of the callset from the records of one site (at least one of them kept)."""
    lead = next((record for record in group if not record.excluded), group[0])
    info = _parse_info(lead.info)
    chrom = normalize_chromosome(lead.chrom)
    calls = sorted(
        (_site_call(readers[record.reader].source.sample_id, record) for record in group),
        key=lambda call: call.sample,
    )
    quals = [
        value
        for value in (_parse_qual(record.qual) for record in group if not record.excluded)
        if value is not None
    ]
    return SmallVariantRecord(
        variant_key=None,
        variant_id=build_small_variant_id(chrom, lead.pos, lead.ref, lead.alt),
        chr=chrom,
        start=lead.pos,
        end=lead.pos + len(lead.ref) - 1,
        ref=lead.ref,
        alt=lead.alt,
        source=source,
        rsid=info.get("RS") or info.get("dbSNP") or None,
        filters=list(dict.fromkeys(record.filt for record in group if record.filt not in {"", "."})),
        gene_symbols=[],
        annotations=extract_small_variant_annotations(info, readers[lead.reader].annotation_state),
        calls=calls,
        qual=max(quals) if quals else None,
    )


@dataclass(slots=True)
class _MergeCounts:
    inserted: int = 0
    skipped_filtered: int = 0
    excluded_calls_kept: int = 0
    per_sample_calls: dict[str, int] = field(default_factory=dict)


def _sample_call_ids(samples: Sequence[SampleMetadataContext]) -> set[str]:
    return {value for sample in samples for value in (sample.sample_id, sample.sample_uuid) if value}


async def upload_family_per_sample_small_variant_files(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    sample_contexts: dict[str, SampleMetadataContext],
    files: Sequence[PerSampleVcf],
    overwrite: bool,
    exclude_filters: Sequence[str] | None = None,
    progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Store ``files``, one VCF per sample, as the family's primary small-variant callset.

    The callset replaces the family's stored primary callset (``overwrite``; else 409 when
    there is one). It must bring the file of every sample with calls in the stored one: a
    sample left out would lose its calls without a word, so that is refused (409) before
    anything is deleted. ``exclude_filters`` names the FILTER values of records that carry
    no variant (see the module docstring). Each file must hold one sample, checked by the
    caller. ``progress`` is told, every ``SMALL_VARIANT_PROGRESS_INTERVAL`` records read,
    the records read and stored and the bytes of the files read.
    """
    if not context.assembly_name:
        raise HTTPException(status_code=400, detail="Could not resolve a single assembly for this family")
    if not files:
        raise HTTPException(status_code=400, detail="No per-sample SNV file to import")
    assembly_name = context.assembly_name
    source = PER_SAMPLE_CALLSET_SOURCE
    excluded_filters = {str(value).strip() for value in (exclude_filters or []) if str(value).strip()}
    file_samples = [file.sample_id for file in files]
    if len(set(file_samples)) != len(file_samples):
        raise HTTPException(status_code=400, detail="Two per-sample SNV files name one sample")
    for sample_id in file_samples:
        if sample_id not in sample_contexts:
            raise HTTPException(status_code=400, detail=f"Sample '{sample_id}' not found in family")
    readers: list[_PerSampleVcfReader] = []
    mutation_started = False
    try:
        # Every file's header is read before anything is written: a file that cannot be
        # read side by side with the others fails the import with the family as it was.
        order = _ContigOrder()
        for index, file in enumerate(files):
            reader = _PerSampleVcfReader(index, file, excluded_filters=excluded_filters)
            readers.append(reader)
            reader.read_header()
            if not 0 <= file.column < len(reader.columns):
                raise PerSampleMergeError(f"{reader.label} has no sample column {file.column}")
            order.add_header(reader.contigs, label=reader.label)
        # One write of the family's small variants at a time, from the read below until the
        # commit at the end.
        await lock_family_variant_writes(
            session,
            context.family_uuid,
            [SMALL_VARIANTS],
            samples=[sample.sample_uuid for sample in sample_contexts.values()],
        )
        existing = await count_family_small_variants(
            assembly_name, context.family_uuid, project_ids=context.project_ids, source=source
        )
        if existing:
            if not overwrite:
                raise HTTPException(status_code=409, detail="Small variants already exist for this family")
            replaced = _sample_call_ids([sample_contexts[sample_id] for sample_id in file_samples])
            stored = await family_small_variant_call_samples(assembly_name, context.family_uuid, source=source)
            uuid_to_id = {sample.sample_uuid: sample.sample_id for sample in sample_contexts.values()}
            kept = sorted({uuid_to_id.get(sample, sample) for sample in stored if sample not in replaced})
            if kept:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"The family's callset also holds the calls of {', '.join(kept)}: import "
                        "every sample's SNV file together to replace it"
                    ),
                )
            await delete_family_small_variants(assembly_name, context.family_uuid, source=source)
        mutation_started = True
        for reader in readers:
            reader.start(order)

        counts = _MergeCounts(per_sample_calls={sample_id: 0 for sample_id in file_samples})
        batch: list[SmallVariantRecord] = []
        bytes_total = sum(file_size(file.path) for file in files)
        last_reported = 0

        def records_read() -> int:
            return sum(reader.records_read for reader in readers)

        async def report() -> None:
            nonlocal last_reported
            last_reported = records_read()
            if progress is None:
                return
            await progress(
                {
                    "processed": last_reported,
                    "inserted": counts.inserted - len(batch),
                    **read_stats(sum(reader.bytes_read() for reader in readers), bytes_total),
                }
            )

        async def flush() -> None:
            if not batch:
                return
            await insert_small_variant_records(
                assembly_name,
                context.family_uuid,
                context.project_ids,
                batch,
                annotation_version="vcf_info",
            )
            batch.clear()

        for group in iter_merged_sites(readers):
            if all(record.excluded for record in group):
                counts.skipped_filtered += len(group)
            else:
                counts.excluded_calls_kept += sum(1 for record in group if record.excluded)
                record = merged_site_record(group, readers, source=source)
                for call in record.calls:
                    counts.per_sample_calls[call.sample] = counts.per_sample_calls.get(call.sample, 0) + 1
                batch.append(record)
                counts.inserted += 1
                if len(batch) >= SMALL_VARIANT_UPLOAD_BATCH_SIZE:
                    await flush()
            if progress is not None and records_read() - last_reported >= SMALL_VARIANT_PROGRESS_INTERVAL:
                await report()

        if counts.inserted == 0:
            detail = "No valid small-variant records found"
            if counts.skipped_filtered:
                detail = (
                    f"No small-variant records remained after excluding FILTER "
                    f"{sorted(excluded_filters)} ({counts.skipped_filtered} record(s) dropped)"
                )
            raise HTTPException(status_code=400, detail=detail)
        await flush()
        await refresh_family_small_variant_summaries(assembly_name, context.family_uuid)

        from .annotation_manifest_service import merge_vcf_header_provenance

        annotation_provenance: dict[str, dict[str, Any]] = {}
        for reader in readers:
            annotation_provenance = merge_module_maps(
                annotation_provenance,
                extract_header_provenance(reader.provenance_lines, modality="snv").as_modules(),
            )
        await merge_vcf_header_provenance(
            session,
            family_uuid=context.family_uuid,
            assembly_id=getattr(context, "assembly_id", None),
            modules=annotation_provenance,
            modality="snv",
        )
        await session.commit()
        skipped_malformed = sum(reader.skipped_malformed for reader in readers)
        if skipped_malformed:
            logger.warning(
                "Skipped %d malformed small-variant record(s) of the per-sample files",
                skipped_malformed,
            )
        result = {
            "inserted": counts.inserted,
            "processed": records_read(),
            "skipped_malformed": skipped_malformed,
            "skipped_filtered": counts.skipped_filtered,
            "excluded_filters": sorted(excluded_filters),
            # Excluded records kept as a sample's call where another sample has a variant.
            "excluded_calls_kept": counts.excluded_calls_kept,
            "calls_per_sample": counts.per_sample_calls,
            "source_format": source,
            "annotation_version": "vcf_info",
            "annotation_provenance": annotation_provenance,
            "insert_batch_size": SMALL_VARIANT_UPLOAD_BATCH_SIZE,
        }
        if progress is not None:
            await progress({**result, **read_stats(bytes_total, bytes_total)})
        return result
    except PerSampleMergeError as exc:
        if mutation_started:
            await _clean_up(assembly_name, context.family_uuid, source)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        # Rows may have been flushed: the callset must not stay half written.
        if mutation_started:
            await _clean_up(assembly_name, context.family_uuid, source)
        raise
    finally:
        for reader in readers:
            try:
                reader.close()
            except Exception:  # closing must not mask the outcome
                logger.debug("Could not close a per-sample VCF", exc_info=True)


async def _clean_up(assembly_name: str, family_uuid: str, source: str) -> None:
    try:
        await delete_family_small_variants(assembly_name, family_uuid, source=source)
    except Exception:  # cleanup must not mask the original error
        logger.warning(
            "Failed to clean up partial small-variant rows after a per-sample import error",
            exc_info=True,
        )
