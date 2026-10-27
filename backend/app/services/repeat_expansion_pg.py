from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from fastapi import HTTPException, UploadFile
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import scrub_log
from ..core.config import STRCHIVE_PINNED_LOCI_SHA256, settings
from ..core.sql import uuid_list_bindparam, uuid_values
from ..schemas import (
    FamilyRepeatExpansionTableOut,
    RepeatExpansionRowOut,
    RepeatExpansionSampleCallOut,
    RepeatExpansionTrackItemOut,
    RepeatExpansionTrackResponse,
)
from .data_scope import chromosome_aliases, normalize_chromosome
from .family_metadata_context import FamilyMetadataContext, SampleMetadataContext
from .upload_safety import decode_upload_text
from .family_package_common import (
    VcfSampleColumnError,
    known_vcf_sample_ids,
    per_sample_vcf_column,
    resolve_vcf_sample_id,
)
from .repeat_count_units import count_unit_warning, known_count_unit_difference
from .repeat_expansion_catalog import BUILTIN_REPEAT_LOCI, PREMUTATION_MIN_BY_GENE
from .sex_chromosomes import hemizygous_chromosome
from .vcf_header_provenance import extract_header_provenance


logger = logging.getLogger(__name__)

REPO_STRCHIVE_LOCI_PATH = Path(__file__).resolve().parents[3] / "data" / "ref-data" / "STRchive-loci.json"


def _json_payload(value: Any) -> str:
    return json.dumps(value if value is not None else {})


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    text_value = str(value).strip()
    return text_value or None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _text_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _first_text(value: Any) -> str | None:
    values = _text_list(value)
    return values[0] if values else None


def _unique_texts(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        for item in _text_list(value):
            key = item.lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)
    return unique


def _normalize_strchive_repeat_locus(entry: dict[str, Any]) -> dict[str, Any] | None:
    locus_id = _as_text(entry.get("id") or entry.get("locus_id") or entry.get("trid"))
    gene = _as_text(entry.get("gene") or entry.get("gene_symbol"))
    if not locus_id or not gene:
        return None

    pathogenic_motifs = _text_list(
        entry.get("pathogenic_motif_reference_orientation") or entry.get("pathogenic_motifs")
    )
    reference_motifs = _text_list(
        entry.get("reference_motif_reference_orientation") or entry.get("reference_motifs")
    )
    interruption_motifs = _unique_texts(
        [
            entry.get("interruption_reference_orientation"),
            entry.get("interruption_gene_orientation"),
        ]
    )
    motif = _first_text(pathogenic_motifs) or _first_text(reference_motifs) or _as_text(entry.get("motif"))
    inheritance = _as_text(entry.get("inheritance"))
    intermediate_min = _as_int(entry.get("intermediate_min"))
    pathogenic_min = _as_int(entry.get("pathogenic_min"))
    disease_id = _as_text(entry.get("disease_id"))
    aliases = _unique_texts(
        [
            locus_id,
            gene,
            disease_id,
            entry.get("stripy"),
            entry.get("tr_atlas"),
            entry.get("webstr_hg38"),
            entry.get("webstr_hg19"),
            entry.get("locus_tags"),
            entry.get("disease_tags"),
        ]
    )

    return {
        "locus_id": locus_id,
        "gene": gene,
        "display_name": gene,
        "disease": _as_text(entry.get("disease")) or disease_id or locus_id,
        "inheritance": inheritance,
        "motif": motif,
        "motif_index": 0,
        "warning_min": intermediate_min,
        "pathogenic_min": pathogenic_min,
        "x_linked": "X" in (inheritance or "").upper(),
        "aliases": aliases,
        "notes": _as_text(entry.get("details") or entry.get("disease_description")),
        "metadata": {
            "source": "STRchive",
            "reference_motifs": reference_motifs,
            "pathogenic_motifs": pathogenic_motifs,
            "interruption_motifs": interruption_motifs,
            "benign_min": _as_int(entry.get("benign_min")),
            "benign_max": _as_int(entry.get("benign_max")),
            "intermediate_max": _as_int(entry.get("intermediate_max")),
            "pathogenic_max": _as_int(entry.get("pathogenic_max")),
            "motif_len": _as_int(entry.get("motif_len")),
            "chrom": _as_text(entry.get("chrom")),
            "start_hg38": _as_int(entry.get("start_hg38")),
            "stop_hg38": _as_int(entry.get("stop_hg38")),
            "hpo_terms": _text_list(entry.get("hpo_terms")),
            "evidence": _text_list(entry.get("evidence")),
            "references": _text_list(entry.get("references")),
            "raw": entry,
        },
    }


def _strchive_repeat_loci_from_payload(payload: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]]
    if isinstance(payload, list):
        entries = [entry for entry in payload if isinstance(entry, dict)]
    elif isinstance(payload, dict):
        entries = [entry for entry in payload.values() if isinstance(entry, dict)]
    else:
        entries = []
    return [
        normalized
        for entry in entries
        if (normalized := _normalize_strchive_repeat_locus(entry)) is not None
    ]


def load_strchive_repeat_loci(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    return _strchive_repeat_loci_from_payload(json.loads(path.read_bytes()))


def _configured_strchive_loci_paths() -> list[Path]:
    raw_paths = [settings.trgt_strchive_loci_path, REPO_STRCHIVE_LOCI_PATH]
    paths: list[Path] = []
    seen: set[Path] = set()
    for raw_path in raw_paths:
        if not raw_path:
            continue
        path = Path(raw_path)
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


class StrchiveReleaseRefused(RuntimeError):
    """The STRchive file a start would read is not the shipped release, or there is none.

    The built-in loci carry the shipped release's thresholds (CLIN-22), so a call reads the
    same whichever catalogue row its TRID finds only while the STRchive rows are that release
    too. A start that would seed another file is refused before anything is written (CLIN-31).
    """


_STRCHIVE_RELEASE_REMEDY = (
    "Copy the repository's data/ref-data/STRchive-loci.json there unchanged (on Cloud Run: "
    "gcloud storage cp data/ref-data/STRchive-loci.json gs://<reference-data bucket>/), or set "
    "TRGT_STRCHIVE_LOCI_PATH to such a copy. Another STRchive release needs a reviewed change "
    "that moves STRCHIVE_PINNED_LOCI_SHA256 with the file (docs/data-import.md, "
    '"The STRchive release").'
)


def _refuse_strchive_release(message: str) -> StrchiveReleaseRefused:
    # Logged as well as raised: the log writes a failed start's traceback without the
    # exception's message (coga_logging), and the operator needs this one.
    logger.error("%s", message)
    return StrchiveReleaseRefused(message)


def read_shipped_strchive_release(path: Path) -> bytes:
    """The STRchive file's bytes, if it is the shipped release byte for byte (CLIN-31).

    The file states no release of its own, so its SHA-256 names it: the pin
    ``STRCHIVE_PINNED_LOCI_SHA256``. An identical copy passes wherever it lies; any other
    file, and one that cannot be read, raises ``StrchiveReleaseRefused``. The bytes checked
    are the bytes returned, so the catalogue is built from exactly what was checked."""
    shown = scrub_log(path)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise _refuse_strchive_release(
            f"The STRchive file {shown} cannot be read ({type(exc).__name__}), so it cannot be "
            f"checked against the shipped release (SHA-256 {STRCHIVE_PINNED_LOCI_SHA256}). The "
            f"backend does not start. {_STRCHIVE_RELEASE_REMEDY}"
        ) from exc
    digest = hashlib.sha256(payload).hexdigest()
    if digest != STRCHIVE_PINNED_LOCI_SHA256:
        raise _refuse_strchive_release(
            f"The STRchive file {shown} is not the shipped, validated STRchive release: its "
            f"SHA-256 is {digest}, the shipped release's is {STRCHIVE_PINNED_LOCI_SHA256}. The "
            "backend does not start, because the built-in repeat loci carry the shipped "
            "release's thresholds and calls would be read against two releases. "
            f"{_STRCHIVE_RELEASE_REMEDY}"
        )
    return payload


def _load_configured_strchive_repeat_loci() -> list[dict[str, Any]]:
    """The shipped STRchive release's loci, from the first configured file that exists:
    ``TRGT_STRCHIVE_LOCI_PATH``, else the repository's copy.

    That file must be the shipped release (``read_shipped_strchive_release``), and one must
    exist: without one, a start would keep whatever STRchive rows an earlier start left, which
    nothing checks, beside built-in rows that carry the shipped release's thresholds. Either
    way the start is refused (``StrchiveReleaseRefused``) before anything is written."""
    paths = _configured_strchive_loci_paths()
    for path in paths:
        if not path.exists():
            continue
        entries = _strchive_repeat_loci_from_payload(json.loads(read_shipped_strchive_release(path)))
        if not entries:  # the shipped release has 75 loci: reading none is a defect here
            raise _refuse_strchive_release(
                f"No locus was read from the STRchive file {scrub_log(path)}, although it is the "
                "shipped release. The backend does not start."
            )
        return entries
    searched = ", ".join(scrub_log(path) for path in paths)
    raise _refuse_strchive_release(
        f"No STRchive file was found (looked for {searched}), so the repeat catalogue cannot be "
        f"built from the shipped release (SHA-256 {STRCHIVE_PINNED_LOCI_SHA256}). The backend "
        f"does not start. {_STRCHIVE_RELEASE_REMEDY}"
    )


async def _seed_repeat_catalog_entries(
    session: AsyncSession,
    entries: Iterable[dict[str, Any]],
) -> None:
    now = datetime.now(timezone.utc)
    for entry in entries:
        entry_metadata = entry.get("metadata", {})
        metadata = {
            "seeded": True,
            "research_use_only": True,
            **(entry_metadata if isinstance(entry_metadata, dict) else {}),
        }
        premutation_min = PREMUTATION_MIN_BY_GENE.get(str(entry.get("gene") or "").upper())
        if premutation_min is not None:
            metadata["premutation_min"] = premutation_min
        await session.execute(
            text(
                """
                INSERT INTO repeat_loci (
                    locus_id,
                    gene,
                    display_name,
                    disease,
                    inheritance,
                    motif,
                    motif_index,
                    warning_min,
                    pathogenic_min,
                    x_linked,
                    aliases,
                    notes,
                    metadata,
                    created_at,
                    updated_at
                )
                VALUES (
                    :locus_id,
                    :gene,
                    :display_name,
                    :disease,
                    :inheritance,
                    :motif,
                    :motif_index,
                    :warning_min,
                    :pathogenic_min,
                    :x_linked,
                    CAST(:aliases_json AS jsonb),
                    :notes,
                    CAST(:metadata_json AS jsonb),
                    :created_at,
                    :updated_at
                )
                ON CONFLICT (locus_id) DO UPDATE
                SET
                    gene = EXCLUDED.gene,
                    display_name = EXCLUDED.display_name,
                    disease = EXCLUDED.disease,
                    inheritance = EXCLUDED.inheritance,
                    motif = EXCLUDED.motif,
                    motif_index = EXCLUDED.motif_index,
                    warning_min = EXCLUDED.warning_min,
                    pathogenic_min = EXCLUDED.pathogenic_min,
                    x_linked = EXCLUDED.x_linked,
                    aliases = EXCLUDED.aliases,
                    notes = EXCLUDED.notes,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                **entry,
                "motif_index": int(entry.get("motif_index", 0)),
                "x_linked": bool(entry.get("x_linked", False)),
                "aliases_json": _json_payload(entry.get("aliases", [])),
                "metadata_json": _json_payload(metadata),
                "notes": entry.get("notes"),
                "created_at": now,
                "updated_at": now,
            },
        )


# STRchive rows a start did not read from the shipped release: loci of another release an
# earlier start seeded, or of the release before a reviewed change moved the pin.
_DELETE_STRCHIVE_LOCI_OUTSIDE_RELEASE_SQL = text(
    """
    DELETE FROM repeat_loci
    WHERE metadata ->> 'source' = 'STRchive'
      AND NOT (locus_id = ANY(CAST(:locus_ids AS text[])))
    """
)


async def seed_builtin_repeat_catalog(session: AsyncSession) -> None:
    """Write the repeat catalogue every start: the built-in loci and the shipped STRchive
    release, upserted by ``locus_id``, in one transaction.

    The STRchive file is checked first (``_load_configured_strchive_repeat_loci``): a file
    that is not the shipped release, or none, refuses the start before anything is written.
    The STRchive rows then become exactly that release's: a STRchive row it does not list,
    left by an earlier start, is removed, so no TRID can find another release's thresholds
    (CLIN-31)."""
    strchive_loci = _load_configured_strchive_repeat_loci()
    await _seed_repeat_catalog_entries(session, BUILTIN_REPEAT_LOCI)
    await _seed_repeat_catalog_entries(session, strchive_loci)
    await session.execute(
        _DELETE_STRCHIVE_LOCI_OUTSIDE_RELEASE_SQL,
        {"locus_ids": [str(entry["locus_id"]) for entry in strchive_loci]},
    )
    await session.commit()


async def decode_repeat_upload_text(file: UploadFile) -> str:
    return await decode_upload_text(file, kind="TRGT")


def parse_info(info_field: str) -> dict[str, str]:
    info: dict[str, str] = {}
    if info_field and info_field != ".":
        for item in info_field.split(";"):
            if "=" in item:
                key, value = item.split("=", 1)
                info[key] = value
    return info


def parse_format(format_field: str, sample_field: str) -> dict[str, str]:
    keys = format_field.split(":")
    values = sample_field.split(":")
    return {k: v for k, v in zip(keys, values)}


def parse_int_list(value: str | None) -> list[int]:
    if value in (None, "", "."):
        return []
    return [int(item) for item in value.split(",") if item and item != "."]


def parse_float_list(value: str | None) -> list[float | None]:
    if value in (None, "", "."):
        return []
    items: list[float | None] = []
    for item in value.split(","):
        if item in ("", "."):
            items.append(None)
        else:
            items.append(float(item))
    return items


def parse_mc_field(value: str | None) -> list[list[int]]:
    if value in (None, "", "."):
        return []
    parsed: list[list[int]] = []
    for allele in value.split(","):
        if allele in ("", "."):
            parsed.append([])
            continue
        parsed.append([int(item) for item in allele.split("_") if item and item != "."])
    return parsed


def parse_ms_field(value: str | None) -> list[str | None]:
    if value in (None, "", "."):
        return []
    return [item if item not in ("", ".") else None for item in value.split(",")]


def _known_interruption_motifs(locus_document: dict[str, Any]) -> set[str]:
    metadata = locus_document.get("metadata")
    if not isinstance(metadata, dict):
        return set()
    motifs = metadata.get("interruption_motifs")
    return {motif.upper() for motif in _text_list(motifs)}


def _motif_count_summary(motifs: list[str], counts: list[int]) -> list[dict[str, int | str]]:
    summary: list[dict[str, int | str]] = []
    for index, count in enumerate(counts):
        motif = motifs[index] if index < len(motifs) else f"motif_{index + 1}"
        summary.append({"motif": motif, "count": int(count)})
    return summary


def _motif_count_label(motif_counts: list[dict[str, int | str]]) -> str | None:
    nonzero = [
        f"{item['motif']} {item['count']}"
        for item in motif_counts
        if int(item.get("count") or 0) > 0
    ]
    return " + ".join(nonzero) if len(nonzero) > 1 else None


def _has_triplet_interruption(
    *,
    motif_counts: list[dict[str, int | str]],
    canonical_motif: str | None,
    interruption_motifs: set[str],
) -> bool:
    if not canonical_motif or len(canonical_motif) != 3 or not interruption_motifs:
        return False
    return any(
        str(item.get("motif", "")).upper() in interruption_motifs
        and int(item.get("count") or 0) > 0
        for item in motif_counts
    )


def classify_repeat_count(
    repeat_count: int | None,
    warning_min: int | None,
    pathogenic_min: int | None,
    *,
    benign_min: int | None = None,
    benign_max: int | None = None,
    pathogenic_max: int | None = None,
    premutation_min: int | None = None,
) -> str:
    """Status for one allele's repeat count.

    Almost every locus is pathogenic by *expansion*: bigger is worse, and "at or above
    the threshold" is the whole rule. Two catalogued loci are not. VWA1 (normal exactly
    2, pathogenic 1 or 3) and MIR7-2 (normal 4, pathogenic 3) are pathogenic by
    *contraction*, so their normal count sits at or above ``pathogenic_min`` and a bare
    ``>=`` test calls every healthy call pathogenic.

    The two shapes are told apart by the catalog itself — a benign count at or above
    ``pathogenic_min`` means repeat count and risk do not rise together — rather than by
    naming genes. ``pathogenic_max`` cannot be that test: 73 of 75 loci record one, as
    the largest count observed rather than a ceiling on harm, so treating it as a bound
    would downgrade a 300-repeat HTT allele.

    On a non-monotonic locus a count outside every stated range is ``review``, not
    ``normal``: "further from the threshold is safer" is exactly the assumption that
    fails there, and the catalog says nothing about those counts. ``review`` ranks above
    ``normal`` so such an allele can never hide behind a normal one (#535); ``unknown``
    is kept for a no-call.

    On a monotonic locus the thresholds alone decide, as they always have: the benign
    range is not consulted. Where the catalog's benign range shares its upper endpoint
    with the grey zone (RFC1 11, SCA8/ATXN8OS 50), that count therefore reads as
    ``intermediate`` — the conservative reading of an ambiguous boundary.

    A locus with a premutation range (``premutation_min``, FMR1 55) splits the
    intermediate range: below it the grey zone (``intermediate``), from it up to the
    pathogenic threshold a ``premutation``, an allele that can expand to a full mutation
    in a child, which a carrier screen reports.

    A locus without thresholds -- no intermediate, premutation or pathogenic threshold:
    no catalogue row matches the call's TRID, or the row states none (STRchive's MUC1 and
    POLG) -- gives a called allele ``not_assessed``. Read as ``normal``, an expansion there
    could not be seen (CLIN-26). Where such a row states a benign range, a count inside it
    is ``normal``: the catalogue calls that count benign (STRchive's POLG, exactly 10; the
    owner's decision of 2026-10-14, CLIN-36). Any other count there, above the range or
    below it, stays ``not_assessed``: a benign range is not a threshold, and nothing says
    where harm starts. A range is stated by both of its ends.

    A row with a pathogenic threshold and a benign range below it, but no grey zone of its
    own (no intermediate threshold), leaves the counts between the two unclassified: such a
    count is neither benign nor pathogenic, and reads ``intermediate``, the cautious reading
    of a gap (the owner's decision of 2026-10-27, CLIN-14). It read ``normal``. Below the
    benign range, and at a row without one, a count under the threshold stays ``normal``.
    """
    if repeat_count is None:
        return "unknown"
    if warning_min is None and pathogenic_min is None and premutation_min is None:
        if benign_min is not None and benign_max is not None and benign_min <= repeat_count <= benign_max:
            return "normal"
        return "not_assessed"

    has_benign_range = benign_min is not None and benign_max is not None
    non_monotonic = (
        has_benign_range and pathogenic_min is not None and benign_max >= pathogenic_min
    )
    if non_monotonic:
        if benign_min <= repeat_count <= benign_max:
            return "normal"
        upper = pathogenic_max if pathogenic_max is not None else repeat_count
        if pathogenic_min <= repeat_count <= upper:
            return "pathogenic"
        return "review"

    if pathogenic_min is not None and repeat_count >= pathogenic_min:
        return "pathogenic"
    if premutation_min is not None and repeat_count >= premutation_min:
        return "premutation"
    if warning_min is not None and repeat_count >= warning_min:
        return "intermediate"
    if warning_min is None and pathogenic_min is not None and has_benign_range and repeat_count > benign_max:
        return "intermediate"
    return "normal"


# How much attention a status calls for; the row / locus shows its most severe allele.
# "review" (a count the catalog cannot classify) outranks "normal" so it surfaces in the
# aberrant-only view; "unknown" (no call) ranks lowest (#535). "not_assessed" (a count at a
# locus without thresholds, outside any benign range stated there: CLIN-26, CLIN-36) outranks
# "normal" for the same reason, and "review" so that a male's two chrX alleles at such a locus
# keep saying why it was not assessed (the note still asks for review). A premutation ranks
# between the grey zone and a full mutation.
REPEAT_STATUS_RANK = {
    "unknown": 0,
    "normal": 1,
    "review": 2,
    "not_assessed": 3,
    "intermediate": 4,
    "premutation": 5,
    "pathogenic": 6,
}


def summarize_repeat_status(statuses: Iterable[str]) -> str:
    best = "unknown"
    for status in statuses:
        if REPEAT_STATUS_RANK.get(status, 0) > REPEAT_STATUS_RANK[best]:
            best = status
    return best


def _reclassify_repeat_alleles(
    alleles: Any,
    *,
    warning_min: int | None,
    pathogenic_min: int | None,
    benign_min: int | None = None,
    benign_max: int | None = None,
    pathogenic_max: int | None = None,
    premutation_min: int | None = None,
) -> list[dict[str, Any]]:
    """Each called allele's status against the thresholds given, whatever it was stored with.

    Without any threshold an allele reads ``not_assessed``, so a call stored as ``normal`` at
    a locus without thresholds (before CLIN-26) reads not assessed here at once; a count
    inside the benign range the catalogue row states there reads ``normal``, so one stored as
    ``not_assessed`` (from CLIN-26 to CLIN-36) reads normal here at once. A no-call keeps its
    stored status.
    """
    if not isinstance(alleles, list):
        return []
    reclassified: list[dict[str, Any]] = []
    for allele in alleles:
        if not isinstance(allele, dict):
            continue
        next_allele = dict(allele)
        repeat_count = next_allele.get("repeat_count")
        if repeat_count is not None:
            next_allele["status"] = classify_repeat_count(
                int(repeat_count),
                warning_min,
                pathogenic_min,
                benign_min=benign_min,
                benign_max=benign_max,
                pathogenic_max=pathogenic_max,
                premutation_min=premutation_min,
            )
        reclassified.append(next_allele)
    return reclassified


def _with_count_unit_warnings(
    alleles: list[dict[str, Any]],
    *,
    validated: bool,
    warning_min: int | None,
    pathogenic_min: int | None,
    benign_min: int | None = None,
    benign_max: int | None = None,
    pathogenic_max: int | None = None,
    premutation_min: int | None = None,
) -> list[dict[str, Any]]:
    """Each allele with its ``count_unit_warning`` (repeat_count_units): the boundaries within
    the margin of its count, at a locus whose counting units QA has not validated.

    The boundaries are found by the classifier itself, with the thresholds the status was
    read against, so the warning and the status never disagree on where a boundary is. A
    validated locus, and a no-call, carry none. The statuses are left as they are. A locus
    without any threshold or range has nothing a count could cross, so the classifier is not
    asked there: a genome-wide TRGT catalogue holds mostly such loci.
    """
    ranges = (warning_min, pathogenic_min, benign_min, benign_max, pathogenic_max, premutation_min)
    if validated or all(value is None for value in ranges):
        return [{**allele, "count_unit_warning": None} for allele in alleles]

    def status_at(count: int) -> str:
        return classify_repeat_count(
            count,
            warning_min,
            pathogenic_min,
            benign_min=benign_min,
            benign_max=benign_max,
            pathogenic_max=pathogenic_max,
            premutation_min=premutation_min,
        )

    marked: list[dict[str, Any]] = []
    for allele in alleles:
        next_allele = dict(allele)
        repeat_count = next_allele.get("repeat_count")
        next_allele["count_unit_warning"] = (
            None if repeat_count is None else count_unit_warning(int(repeat_count), status_at)
        )
        marked.append(next_allele)
    return marked


MALE_X_DISTINCT_ALLELES_NOTE = (
    "Two different chrX alleles in a male: size mosaicism, TRGT run without "
    "--karyotype XY, a recorded sex that does not match the sample, or 47,XXY. "
    "Both alleles are shown; review."
)


def _is_male_hemizygous_locus(
    *, sex: str | None, assembly_name: str | None, chrom: str, position: int
) -> bool:
    """A male's chrX outside the PARs, where he carries a single allele. Unknown PARs
    (an assembly the PAR table does not list) count as diploid, so nothing is cut."""
    return str(sex or "").lower() == "male" and (
        hemizygous_chromosome(assembly_name, chrom, position) == "X"
    )


def _distinct_allele_sizes(alleles: list[dict[str, Any]]) -> set[tuple[Any, Any]]:
    return {
        (allele.get("repeat_count"), allele.get("bp_length"))
        for allele in alleles
        if allele.get("repeat_count") is not None or allele.get("bp_length") is not None
    }


def _normalize_x_male_alleles(
    alleles: list[dict[str, Any]],
    *,
    sex: str,
    chrom: str,
    position: int,
    assembly_name: str | None,
) -> list[dict[str, Any]]:
    """Collapse a male's chrX call to his single allele, but only when the two are one.

    TRGT genotyped without ``--karyotype XY`` writes a male's allele twice, and that pair
    is one allele. Two *different* sizes are never cut: TRGT orders alleles shortest
    first, so keeping the first dropped the expansion -- a mosaic FMR1 full mutation,
    or a premutation of a woman recorded as male -- and the locus read normal.
    """
    if len(alleles) < 2 or not _is_male_hemizygous_locus(
        sex=sex, assembly_name=assembly_name, chrom=chrom, position=position
    ):
        return alleles
    if len(_distinct_allele_sizes(alleles)) <= 1:
        return alleles[:1]
    return alleles


def _male_x_allele_note(
    alleles: list[dict[str, Any]],
    *,
    sex: str | None,
    chrom: str,
    position: int,
    assembly_name: str | None,
) -> str | None:
    if len(_distinct_allele_sizes(alleles)) < 2:
        return None
    if not _is_male_hemizygous_locus(
        sex=sex, assembly_name=assembly_name, chrom=chrom, position=position
    ):
        return None
    return MALE_X_DISTINCT_ALLELES_NOTE


def _call_status(alleles: list[dict[str, Any]], *, note: str | None) -> str:
    """The worst allele's status; a call carrying a note is at least ``review``."""
    status = summarize_repeat_status(allele.get("status", "unknown") for allele in alleles)
    if note and REPEAT_STATUS_RANK.get(status, 0) < REPEAT_STATUS_RANK["review"]:
        return "review"
    return status


def _review_reason(alleles: list[dict[str, Any]], status: str) -> str | None:
    """Why a call reads ``review``: a count the catalogue cannot classify (an allele's own
    status), else the male chrX note that raised it. The page said "outside catalogued
    ranges" for both, the wrong reason for the note (CLIN-14). None for any other status."""
    if status != "review":
        return None
    if any(allele.get("status") == "review" for allele in alleles):
        return "outside_ranges"
    return "male_x_alleles"


def _classified_call(
    row: Mapping[str, Any], *, sex: str | None, assembly_name: str | None
) -> tuple[list[dict[str, Any]], str, str | None]:
    """A stored call as the family table and the genome track read it (CLIN-14): its alleles
    reclassified against the catalogue row that classifies it (``_CALL_CATALOGUE_COLUMNS``),
    each with its count-unit warning, and the call's status with the male chrX note, which
    it returns too. A call whose alleles say nothing keeps its stored status."""
    warning_min = row.get("warning_min")
    pathogenic_min = row.get("pathogenic_min")
    ranges = {
        "benign_min": row.get("benign_min"),
        "benign_max": row.get("benign_max"),
        "pathogenic_max": row.get("pathogenic_max"),
        "premutation_min": row.get("premutation_min"),
    }
    alleles = _reclassify_repeat_alleles(
        row.get("alleles", []), warning_min=warning_min, pathogenic_min=pathogenic_min, **ranges
    )
    alleles = _with_count_unit_warnings(
        alleles,
        validated=bool(row.get("count_units_validated")),
        warning_min=warning_min,
        pathogenic_min=pathogenic_min,
        **ranges,
    )
    note = _male_x_allele_note(
        alleles,
        sex=sex,
        chrom=str(row.get("chr") or ""),
        position=int(row.get("start", 0)),
        assembly_name=assembly_name,
    )
    status = _call_status(alleles, note=note)
    if status == "unknown":
        status = row.get("status", "unknown")
    return alleles, status, note


_REPEAT_LOCI_LOOKUP_SQL = text(
    """
    SELECT
        locus_id,
        gene,
        display_name,
        disease,
        inheritance,
        motif,
        motif_index,
        warning_min,
        pathogenic_min,
        aliases,
        notes,
        metadata
    FROM repeat_loci
    ORDER BY locus_id
    """
)


def _coerce_locus_aliases(value: Any) -> list[str]:
    """Decode a ``repeat_loci.aliases`` JSONB value (a list, or a JSON string
    depending on the driver) into a list of strings for in-memory matching."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


class RepeatLocusLookup(dict[str, dict[str, Any]]):
    """The repeat catalogue for TRGT ingest: each lowercased locus_id, gene, display name and
    alias maps to the row it names (``_build_repeat_locus_lookup``).

    A name that names several STRchive loci -- the gene of a locus with several repeat tracts
    (ARX, HOXA13), or a tag several loci share -- maps to none of them. ``tracts`` lists them
    instead, and the import picks one by the call's position (``_resolve_repeat_locus``).
    """

    def __init__(self) -> None:
        super().__init__()
        self.tracts: dict[str, list[dict[str, Any]]] = {}


def _row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """A catalogue row's metadata as a dict (a JSON string, depending on the driver)."""
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return {}
    return metadata if isinstance(metadata, dict) else {}


def _is_strchive_row(row: dict[str, Any]) -> bool:
    return _row_metadata(row).get("source") == "STRchive"


async def _build_repeat_locus_lookup(session: AsyncSession) -> RepeatLocusLookup:
    """Load the (small, static) repeat catalog once and index it by lowercased
    locus_id / gene / display_name / alias so TRGT ingest can resolve a locus in
    memory instead of issuing a sequential-scan SELECT per VCF line.

    On a key collision an exact field match wins in the order
    locus_id > gene > display_name > alias; within a field the first locus in
    catalog order keeps the key. This is at least as precise as the previous
    per-row ``OR ... LIMIT 1`` query, which returned an arbitrary matching row.

    Except where the rows a key names in its best field include several STRchive loci: the
    tracts of ARX or HOXA13 under the gene's name, or the loci a tag names. The first of those
    by locus_id is one tract among several, with thresholds of its own, so such a key names
    no row; its loci are in ``tracts``, for the call's position to choose (CLIN-31). A
    built-in row and its STRchive row (``HD``: HTT and HD_HTT) are one locus, with the same
    thresholds, and keep the rule above.
    """
    result = await session.execute(_REPEAT_LOCI_LOOKUP_SQL)
    lookup = RepeatLocusLookup()
    best_priority: dict[str, int] = {}
    named: dict[str, list[dict[str, Any]]] = {}

    def _register(value: Any, priority: int, row: dict[str, Any]) -> None:
        if not isinstance(value, str) or not value:
            return
        key = value.lower()
        if key not in best_priority or priority < best_priority[key]:
            best_priority[key] = priority
            lookup[key] = row
            named[key] = [row]
        elif priority == best_priority[key] and all(other is not row for other in named[key]):
            named[key].append(row)

    for row in result.mappings().all():
        row_dict = dict(row)
        _register(row_dict.get("locus_id"), 0, row_dict)
        _register(row_dict.get("gene"), 1, row_dict)
        _register(row_dict.get("display_name"), 2, row_dict)
        for alias in _coerce_locus_aliases(row_dict.get("aliases")):
            _register(alias, 3, row_dict)
    for key, rows in named.items():
        strchive_loci = [row for row in rows if _is_strchive_row(row)]
        if len(strchive_loci) > 1:
            del lookup[key]
            lookup.tracts[key] = strchive_loci
    return lookup


# STRchive places each locus on three assemblies: GRCh38 (start_hg38/stop_hg38), GRCh37
# (_hg19) and T2T-CHM13 (_t2t). Another assembly has no STRchive coordinates.
_ASSEMBLY_PATCH_SUFFIX = re.compile(r"\.p\d+$")


def _strchive_coordinates_of(assembly_name: str | None) -> str | None:
    """The suffix of STRchive's coordinates on this assembly, or None."""
    name = _ASSEMBLY_PATCH_SUFFIX.sub("", str(assembly_name or "").strip().lower())
    if name in {"grch38", "hg38"}:
        return "hg38"
    if name in {"grch37", "hg19"}:
        return "hg19"
    if "chm13" in name or name == "hs1":
        return "t2t"
    return None


def _strchive_locus_span(row: dict[str, Any], suffix: str) -> tuple[str, int, int] | None:
    """A STRchive locus's chromosome, start and stop on an assembly, or None."""
    metadata = _row_metadata(row)
    raw = metadata.get("raw")
    entry = raw if isinstance(raw, dict) else metadata
    chrom = _as_text(entry.get("chrom"))
    start = _as_int(entry.get(f"start_{suffix}"))
    stop = _as_int(entry.get(f"stop_{suffix}"))
    if not chrom or start is None or stop is None:
        return None
    return normalize_chromosome(chrom).upper(), start, stop


def _resolve_repeat_locus(
    locus_lookup: dict[str, dict[str, Any]],
    trid: str | None,
    *,
    chrom: str,
    start: int,
    end: int,
    assembly_name: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """The catalogue row that classifies a call, or None; and why none, where its TRID names
    several STRchive loci.

    A TRID names one row, or none. The gene of a locus with several repeat tracts (ARX,
    HOXA13) names each tract (``RepeatLocusLookup.tracts``): the call's region picks the one
    it overlaps on the family's assembly (the owner's decision of 2026-10-14, CLIN-31). If it
    overlaps none of them, or several, or the assembly has no STRchive coordinates, no row
    classifies the call: it reads *not assessed*, never by another tract's thresholds, and
    the reason says why.
    """
    if not trid:
        return None, None
    tracts = getattr(locus_lookup, "tracts", {}).get(trid.lower())
    if not tracts:
        return locus_lookup.get(trid.lower()), None

    named = (
        f"TRID {trid} names {len(tracts)} STRchive loci "
        f"({', '.join(str(row.get('locus_id')) for row in tracts)})"
    )
    suffix = _strchive_coordinates_of(assembly_name)
    if suffix is None:
        where = f"on {assembly_name}" if assembly_name else "without the family's assembly"
        return None, f"{named}, and STRchive gives no coordinates {where}, so none of their thresholds applies."
    call_chrom = normalize_chromosome(chrom).upper()
    overlapping = [
        row
        for row in tracts
        if (span := _strchive_locus_span(row, suffix)) is not None
        and span[0] == call_chrom
        and span[1] <= end
        and start <= span[2]
    ]
    if len(overlapping) == 1:
        return overlapping[0], None
    region = f"{chrom}:{start}-{end}"
    if not overlapping:
        return None, (
            f"{named}, and the call's region, {region}, overlaps none of them, so none of their "
            "thresholds applies."
        )
    return None, (
        f"{named}, and the call's region, {region}, overlaps {len(overlapping)} of them "
        f"({', '.join(str(row.get('locus_id')) for row in overlapping)}), so none of their "
        "thresholds applies."
    )


def _fallback_locus_document(trid: str, motifs: list[str]) -> dict[str, Any]:
    motif = motifs[0] if motifs else None
    return {
        "locus_id": trid or "UNKNOWN",
        "gene": trid or "UNKNOWN",
        "display_name": trid or "UNKNOWN",
        "disease": trid or "Uncatalogued repeat locus",
        "inheritance": None,
        "motif": motif,
        "motif_index": 0,
        "warning_min": None,
        "pathogenic_min": None,
        "aliases": [],
        "notes": "No catalog thresholds available",
    }


async def clear_sample_repeat_expansions(
    session: AsyncSession,
    *,
    sample_uuid: str,
) -> None:
    await session.execute(
        text(
            """
            DELETE FROM repeat_expansions
            WHERE sample_id = CAST(:sample_id AS uuid)
              AND source = 'trgt'
            """
        ),
        {"sample_id": sample_uuid},
    )


_INSERT_REPEAT_EXPANSION_SQL = text(
    """
    INSERT INTO repeat_expansions (
        sample_id,
        family_id,
        assembly_id,
        source,
        locus_id,
        gene,
        display_name,
        disease,
        inheritance,
        chr,
        start,
        "end",
        motif,
        motifs,
        motif_index,
        genotype,
        allele_count,
        alleles,
        warning_min,
        pathogenic_min,
        status,
        metadata,
        uploaded_at
    )
    VALUES (
        CAST(:sample_id AS uuid),
        CAST(:family_id AS uuid),
        CAST(:assembly_id AS uuid),
        :source,
        :locus_id,
        :gene,
        :display_name,
        :disease,
        :inheritance,
        :chr,
        :start,
        :end,
        :motif,
        CAST(:motifs_json AS jsonb),
        :motif_index,
        :genotype,
        :allele_count,
        CAST(:alleles_json AS jsonb),
        :warning_min,
        :pathogenic_min,
        :status,
        CAST(:metadata_json AS jsonb),
        :uploaded_at
    )
    """
)

# Bound to keep a single executemany statement reasonable for genome-wide catalogs.
_REPEAT_EXPANSION_INSERT_CHUNK = 500


async def _insert_repeat_expansion_batch(
    session: AsyncSession, records: list[dict[str, Any]]
) -> None:
    """Insert TRGT records with one batched (executemany) statement per chunk,
    instead of one round-trip per VCF line."""
    for start in range(0, len(records), _REPEAT_EXPANSION_INSERT_CHUNK):
        chunk = records[start : start + _REPEAT_EXPANSION_INSERT_CHUNK]
        if chunk:
            await session.execute(_INSERT_REPEAT_EXPANSION_SQL, chunk)


def _build_trgt_insert_params(
    *,
    sample_context: SampleMetadataContext,
    locus_lookup: dict[str, dict[str, Any]],
    chrom: str,
    pos: str,
    ref: str,
    info_field: str,
    format_field: str,
    sample_field: str,
    header_sample: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    info = parse_info(info_field)
    sample_values = parse_format(format_field, sample_field)

    trid = info.get("TRID") or header_sample
    end = int(info.get("END", int(pos) + len(ref) - 1))
    motifs = [item for item in info.get("MOTIFS", "").split(",") if item]
    locus_document, locus_not_resolved = _resolve_repeat_locus(
        locus_lookup,
        trid,
        chrom=chrom,
        start=int(pos),
        end=end,
        assembly_name=sample_context.assembly_name,
    )
    if locus_document is None:
        locus_document = _fallback_locus_document(trid, motifs)

    allele_bp_lengths = parse_int_list(sample_values.get("AL"))
    allele_confidence = (
        []
        if sample_values.get("ALLR") in (None, "", ".")
        else sample_values.get("ALLR", "").split(",")
    )
    allele_support = parse_int_list(sample_values.get("SD"))
    allele_purity = parse_float_list(sample_values.get("AP"))
    allele_methylation = parse_float_list(sample_values.get("AM"))
    motif_copy_counts = parse_mc_field(sample_values.get("MC"))
    motif_spans = parse_ms_field(sample_values.get("MS"))
    motif_index = int(locus_document.get("motif_index") or 0)
    catalog_motif = _as_text(locus_document.get("motif"))
    if catalog_motif and motifs:
        motif_lookup = {motif.upper(): index for index, motif in enumerate(motifs)}
        motif_index = motif_lookup.get(catalog_motif.upper(), motif_index)
    warning_min = locus_document.get("warning_min")
    pathogenic_min = locus_document.get("pathogenic_min")
    locus_metadata = locus_document.get("metadata")
    if not isinstance(locus_metadata, dict):
        locus_metadata = {}
    interruption_motifs = _known_interruption_motifs(locus_document)

    alleles: list[dict[str, Any]] = []
    allele_total = max(
        len(allele_bp_lengths),
        len(allele_confidence),
        len(allele_support),
        len(allele_purity),
        len(allele_methylation),
        len(motif_copy_counts),
    )
    for index in range(allele_total):
        repeat_count = None
        if index < len(motif_copy_counts) and motif_index < len(motif_copy_counts[index]):
            repeat_count = motif_copy_counts[index][motif_index]
        elif index < len(allele_bp_lengths) and locus_document.get("motif"):
            motif = str(locus_document["motif"])
            if motif and set(motif) != {"N"}:
                repeat_count = max(
                    int(round(allele_bp_lengths[index] / max(len(motif), 1))),
                    0,
                )
        status = classify_repeat_count(
            repeat_count,
            warning_min,
            pathogenic_min,
            benign_min=_as_int(locus_metadata.get("benign_min")),
            benign_max=_as_int(locus_metadata.get("benign_max")),
            pathogenic_max=_as_int(locus_metadata.get("pathogenic_max")),
            premutation_min=_as_int(locus_metadata.get("premutation_min")),
        )
        motif_counts = (
            _motif_count_summary(motifs, motif_copy_counts[index])
            if index < len(motif_copy_counts)
            else []
        )
        interrupted = _has_triplet_interruption(
            motif_counts=motif_counts,
            canonical_motif=catalog_motif or (motifs[motif_index] if motif_index < len(motifs) else None),
            interruption_motifs=interruption_motifs,
        )
        interruption_label = _motif_count_label(motif_counts)
        if interrupted and not interruption_label:
            interruption_parts = [
                f"{item['motif']} {item['count']}"
                for item in motif_counts
                if str(item.get("motif", "")).upper() in interruption_motifs
                and int(item.get("count") or 0) > 0
            ]
            interruption_label = " + ".join(interruption_parts) if interruption_parts else None
        alleles.append(
            {
                "repeat_count": repeat_count,
                "bp_length": allele_bp_lengths[index] if index < len(allele_bp_lengths) else None,
                "confidence_interval": allele_confidence[index] if index < len(allele_confidence) else None,
                "support_reads": allele_support[index] if index < len(allele_support) else None,
                "purity": allele_purity[index] if index < len(allele_purity) else None,
                "methylation": allele_methylation[index] if index < len(allele_methylation) else None,
                "motif_counts": motif_counts,
                "motif_spans": motif_spans[index] if index < len(motif_spans) else None,
                "interrupted": interrupted,
                "interruption_label": interruption_label,
                "status": status,
            }
        )

    alleles = _normalize_x_male_alleles(
        alleles,
        sex=sample_context.sex,
        chrom=chrom,
        position=int(pos),
        assembly_name=sample_context.assembly_name,
    )
    if not alleles:
        alleles = [{"status": "unknown"}]

    status = _call_status(
        alleles,
        note=_male_x_allele_note(
            alleles,
            sex=sample_context.sex,
            chrom=chrom,
            position=int(pos),
            assembly_name=sample_context.assembly_name,
        ),
    )
    genotype = sample_values.get("GT", "./.")
    return {
        "sample_id": sample_context.sample_uuid,
        "family_id": sample_context.family_uuid,
        "assembly_id": sample_context.assembly_id,
        "source": "trgt",
        "locus_id": locus_document.get("locus_id") or trid,
        "gene": locus_document.get("gene") or trid,
        "display_name": locus_document.get("display_name") or trid,
        "disease": locus_document.get("disease") or trid,
        "inheritance": locus_document.get("inheritance"),
        "chr": normalize_chromosome(chrom),
        "start": int(pos),
        "end": end,
        "motif": locus_document.get("motif") or (motifs[0] if motifs else None),
        "motifs_json": _json_payload(motifs),
        "motif_index": motif_index,
        "genotype": genotype,
        "allele_count": len(alleles),
        "alleles_json": _json_payload(alleles),
        "warning_min": warning_min,
        "pathogenic_min": pathogenic_min,
        "status": status,
        "metadata_json": _json_payload(
            {
                **metadata,
                "trid": trid,
                "motifs": motifs,
                "vcf_sample": header_sample,
                "raw_format": sample_values,
                # Why the call's TRID, naming several STRchive loci, classified it by none
                # (CLIN-31): the family table shows it with the call.
                **({"locus_not_resolved": locus_not_resolved} if locus_not_resolved else {}),
            }
        ),
        "uploaded_at": datetime.now(timezone.utc),
    }


async def _update_sample_repeat_file(
    session: AsyncSession,
    *,
    sample_uuid: str,
    filename: str,
) -> None:
    await session.execute(
        text(
            """
            UPDATE samples
            SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{repeat_files,trgt}',
                to_jsonb(CAST(:filename AS text)),
                true
            )
            WHERE id = CAST(:sample_id AS uuid)
            """
        ),
        {
            "sample_id": sample_uuid,
            "filename": filename,
        },
    )


# A TRGT VCF holds a record for every locus of the catalogue TRGT genotyped, a no-call
# included, so one without a record for a sample is no result for it: a header alone, a
# file cut short before its records, or nothing at all (SAFE-29).
NO_TRGT_RECORDS = "No valid TRGT records found"


@dataclass(frozen=True)
class TrgtSampleCalls:
    """One sample's TRGT calls as ``read_trgt_sample_calls`` read them from its file, each
    built as it is stored, before anything is written."""

    records: list[dict[str, Any]]
    processed: int
    header_lines: list[str]


async def read_trgt_sample_calls(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    text_value: str,
    metadata: dict[str, Any],
    declared: str | None = None,
) -> TrgtSampleCalls:
    """One sample's TRGT calls, read from that sample's column of ``text_value`` and built
    as they are stored. Nothing is written: the caller deletes the calls they replace only
    after this read, so a file it refuses leaves them as they were.

    The column is chosen by ``per_sample_vcf_column`` (``declared`` is a package entry's
    ``vcf_sample``): a file with no column for the sample, or whose column names another
    sample, is refused (400). So is a file with no record for the sample (SAFE-29): it used
    to replace the sample's calls with none.
    """
    lines = text_value.splitlines()
    header_samples: list[str] = []
    # The meta-information lines before the #CHROM line: the caller's version is in them.
    header_lines: list[str] = []
    in_header = True
    sample_column = 0
    processed = 0
    locus_lookup = await _build_repeat_locus_lookup(session)
    insert_records: list[dict[str, Any]] = []

    for line in lines:
        if line.startswith("#CHROM"):
            in_header = False
            header_samples = line.strip().split("\t")[9:]
            if header_samples:
                # Decided before any row is written: a refused file leaves the stored
                # calls as they were.
                try:
                    sample_column = per_sample_vcf_column(
                        header_samples,
                        target_sample_id=sample_context.sample_id,
                        known_sample_ids=await known_vcf_sample_ids(
                            session,
                            family_uuid=sample_context.family_uuid,
                            header_samples=header_samples,
                        ),
                        declared=declared,
                        label="TRGT VCF",
                        remedy="Upload this sample's own file.",
                    )
                except VcfSampleColumnError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            continue
        if not line or line.startswith("#"):
            if in_header and line.startswith("##"):
                header_lines.append(line)
            continue
        in_header = False

        fields = line.rstrip().split("\t")
        if len(fields) < 10:
            continue
        if not header_samples:
            raise HTTPException(status_code=400, detail="TRGT VCF header is missing a sample column")

        sample_fields = fields[9:]
        if sample_column >= len(sample_fields):
            continue
        processed += 1
        chrom, pos, _vid, ref, _alt, _qual, _filter, info_field, format_field = fields[:9]
        insert_records.append(
            _build_trgt_insert_params(
                sample_context=sample_context,
                locus_lookup=locus_lookup,
                chrom=chrom,
                pos=pos,
                ref=ref,
                info_field=info_field,
                format_field=format_field,
                sample_field=sample_fields[sample_column],
                header_sample=header_samples[sample_column],
                metadata=metadata,
            )
        )

    if not insert_records:
        raise HTTPException(status_code=400, detail=NO_TRGT_RECORDS)
    return TrgtSampleCalls(records=insert_records, processed=processed, header_lines=header_lines)


async def store_trgt_sample_calls(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    calls: TrgtSampleCalls,
    metadata: dict[str, Any],
) -> dict[str, int | str]:
    """Store the calls ``read_trgt_sample_calls`` read, record the file on the sample and
    the caller's version in the family's annotation manifest, and commit."""
    await _insert_repeat_expansion_batch(session, calls.records)
    await _update_sample_repeat_file(
        session,
        sample_uuid=sample_context.sample_uuid,
        filename=metadata.get("filename") or "uploaded.vcf",
    )
    # Capture the TRGT caller version into the family's annotation manifest, as the
    # family-level path does. Long-read packages take this per-sample route (the
    # pipeline writes no joint TRGT VCF), so without it the repeat caller's version
    # would be missing from every long-read family's provenance record.
    from .annotation_manifest_service import merge_vcf_header_provenance

    await merge_vcf_header_provenance(
        session,
        family_uuid=sample_context.family_uuid,
        assembly_id=getattr(sample_context, "assembly_id", None),
        modules=extract_header_provenance(calls.header_lines, modality="repeats").as_modules(),
        modality="repeats",
    )
    await session.commit()
    return {
        "processed": calls.processed,
        "inserted": len(calls.records),
        "source_format": "trgt",
    }


async def ingest_trgt_text(
    session: AsyncSession,
    *,
    sample_context: SampleMetadataContext,
    text_value: str,
    metadata: dict[str, Any],
    declared: str | None = None,
    replace: bool = False,
) -> dict[str, int | str]:
    """One sample's TRGT calls, read from that sample's column of ``text_value``
    (``read_trgt_sample_calls``) and stored. With ``replace`` the sample's stored TRGT calls
    are deleted once the file has been read: a file that is refused leaves them as they
    were."""
    calls = await read_trgt_sample_calls(
        session,
        sample_context=sample_context,
        text_value=text_value,
        metadata=metadata,
        declared=declared,
    )
    if replace:
        await clear_sample_repeat_expansions(session, sample_uuid=sample_context.sample_uuid)
    return await store_trgt_sample_calls(
        session, sample_context=sample_context, calls=calls, metadata=metadata
    )


async def ingest_family_trgt_text(
    session: AsyncSession,
    *,
    sample_contexts: dict[str, SampleMetadataContext],
    text_value: str,
    metadata: dict[str, Any],
) -> dict[str, int | str]:
    """The TRGT calls of every family sample a column of the family VCF ``text_value``
    names, each sample's in place of its stored TRGT calls. The whole file is read before
    any of them is deleted: a file none of whose columns names a family sample, or with no
    record for a sample it names (SAFE-29: it used to replace that sample's calls with none),
    is refused (400) with the stored calls as they were."""
    lines = text_value.splitlines()
    header_samples: list[str] = []
    matched_samples: dict[str, SampleMetadataContext] = {}
    inserted = 0
    processed = 0
    locus_lookup = await _build_repeat_locus_lookup(session)
    insert_records: list[dict[str, Any]] = []

    for line in lines:
        if line.startswith("#CHROM"):
            header_samples = line.strip().split("\t")[9:]
            matched_samples = {}
            for sample_name in header_samples:
                # TRGT names the column after its input alignment ("HG002_sort"), so an
                # exact match is not enough; resolve through the shared alias rules.
                resolved = resolve_vcf_sample_id(sample_name, set(sample_contexts))
                if resolved is not None:
                    matched_samples[sample_name] = sample_contexts[resolved]
            if not matched_samples:
                # Silently inserting zero rows here used to report "imported" while the
                # repeat callset was dropped entirely.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "TRGT VCF sample column(s) "
                        f"{header_samples} match no sample in this family"
                    ),
                )
            continue
        if not line or line.startswith("#"):
            continue

        fields = line.rstrip().split("\t")
        if len(fields) < 10:
            continue
        if not header_samples:
            raise HTTPException(status_code=400, detail="TRGT VCF header is missing sample columns")

        processed += 1
        chrom, pos, _vid, ref, _alt, _qual, _filter, info_field, format_field = fields[:9]
        sample_fields = fields[9:]
        for header_sample, sample_field in zip(header_samples, sample_fields):
            sample_context = matched_samples.get(header_sample)
            if sample_context is None:
                continue
            insert_records.append(
                _build_trgt_insert_params(
                    sample_context=sample_context,
                    locus_lookup=locus_lookup,
                    chrom=chrom,
                    pos=pos,
                    ref=ref,
                    info_field=info_field,
                    format_field=format_field,
                    sample_field=sample_field,
                    header_sample=header_sample,
                    metadata=metadata,
                )
            )
            inserted += 1

    with_records = {str(record["sample_id"]) for record in insert_records}
    without_records = sorted(
        {
            sample_context.sample_id
            for sample_context in matched_samples.values()
            if sample_context.sample_uuid not in with_records
        }
    )
    if not insert_records or without_records:
        raise HTTPException(
            status_code=400,
            detail=NO_TRGT_RECORDS + (f" for {', '.join(without_records)}" if without_records else ""),
        )
    # Only now, the file read whole: a refused file leaves every sample's calls as stored.
    for sample_context in matched_samples.values():
        await clear_sample_repeat_expansions(
            session,
            sample_uuid=sample_context.sample_uuid,
        )
    await _insert_repeat_expansion_batch(session, insert_records)
    filename = metadata.get("filename") or "family.trgt.vcf"
    for sample_context in matched_samples.values():
        await _update_sample_repeat_file(
            session,
            sample_uuid=sample_context.sample_uuid,
            filename=filename,
        )
    # Capture the TRGT caller version (and any tool versions in the header) into
    # the family's annotation manifest (best-effort; joins this transaction).
    provenance_ctx = next(iter(matched_samples.values()), None)
    if provenance_ctx is not None:
        from .annotation_manifest_service import merge_vcf_header_provenance

        await merge_vcf_header_provenance(
            session,
            family_uuid=provenance_ctx.family_uuid,
            assembly_id=getattr(provenance_ctx, "assembly_id", None),
            modules=extract_header_provenance(lines, modality="repeats").as_modules(),
            modality="repeats",
        )
    await session.commit()
    return {
        "processed": processed,
        "inserted": inserted,
        "samples": len(matched_samples),
        "source_format": "trgt_family",
    }


# The catalogue row that classifies a stored call, and what the call reads against it: the
# family table and the genome track read a call alike. The track showed the status and the
# thresholds stored at import, and found the catalogue's ranges by the stored locus ID alone,
# so the two disagreed once the catalogue was reseeded (an FMR1 call of 200 under #799, the
# thresholds #806 moved, a call stored as normal at a locus without thresholds; CLIN-14).
# Fixed SQL, interpolated into the two queries: nothing in it comes from a request.
_CALL_CATALOGUE_COLUMNS = """                COALESCE(catalog.locus_id, repeat_expansions.locus_id) AS locus_id,
                COALESCE(catalog.gene, repeat_expansions.gene) AS gene,
                COALESCE(catalog.display_name, repeat_expansions.display_name) AS display_name,
                COALESCE(catalog.disease, repeat_expansions.disease) AS disease,
                COALESCE(catalog.inheritance, repeat_expansions.inheritance) AS inheritance,
                repeat_expansions.chr,
                repeat_expansions.start,
                repeat_expansions."end",
                COALESCE(catalog.motif, repeat_expansions.motif) AS motif,
                -- Both thresholds from the catalogue row when one matched, a missing one
                -- included, so a stored threshold the row no longer has (the old BEAN1
                -- intermediate 300, CLIN-22) does not come back. The stored pair only
                -- when no row matched.
                CASE WHEN catalog.locus_id IS NULL THEN repeat_expansions.warning_min
                     ELSE catalog.warning_min END AS warning_min,
                CASE WHEN catalog.locus_id IS NULL THEN repeat_expansions.pathogenic_min
                     ELSE catalog.pathogenic_min END AS pathogenic_min,
                catalog.benign_min AS benign_min,
                catalog.benign_max AS benign_max,
                catalog.pathogenic_max AS pathogenic_max,
                catalog.premutation_min AS premutation_min,
                -- QA's record for the catalogue row that classifies the call; a call no
                -- row matches is not validated.
                COALESCE(units.validated, false) AS count_units_validated,
                repeat_expansions.status,
                repeat_expansions.genotype,
                repeat_expansions.allele_count,
                repeat_expansions.alleles,
                -- Why the import classified the call by no catalogue locus, where its TRID
                -- names several (CLIN-31); shown while no row matches it.
                CASE WHEN catalog.locus_id IS NULL
                     THEN repeat_expansions.metadata ->> 'locus_not_resolved' END AS locus_not_resolved"""

_CALL_CATALOGUE_JOINS = """            -- The STRchive loci the call's names reach, as the catalogue join below matches
            -- them: its locus_id, gene, display name and TRID.
            LEFT JOIN LATERAL (
                SELECT count(*) AS loci
                FROM repeat_loci AS named
                WHERE named.metadata ->> 'source' = 'STRchive'
                  AND (
                       lower(named.locus_id) = lower(repeat_expansions.locus_id)
                    OR lower(named.gene) = lower(repeat_expansions.gene)
                    OR lower(named.display_name) = lower(repeat_expansions.display_name)
                    OR EXISTS (
                         SELECT 1
                         FROM jsonb_array_elements_text(named.aliases) AS alias(value)
                         WHERE lower(alias.value) IN (
                             lower(repeat_expansions.locus_id),
                             lower(repeat_expansions.gene),
                             lower(repeat_expansions.display_name),
                             lower(repeat_expansions.metadata ->> 'trid')
                         )
                    )
                  )
            ) AS strchive_named ON TRUE
            LEFT JOIN LATERAL (
                SELECT
                    repeat_loci.locus_id,
                    repeat_loci.gene,
                    repeat_loci.display_name,
                    repeat_loci.disease,
                    repeat_loci.inheritance,
                    repeat_loci.motif,
                    repeat_loci.warning_min,
                    repeat_loci.pathogenic_min,
                    -- Not columns: the ranges ride in the catalog metadata.
                    (repeat_loci.metadata ->> 'benign_min')::int AS benign_min,
                    (repeat_loci.metadata ->> 'benign_max')::int AS benign_max,
                    (repeat_loci.metadata ->> 'pathogenic_max')::int AS pathogenic_max,
                    (repeat_loci.metadata ->> 'premutation_min')::int AS premutation_min
                FROM repeat_loci
                WHERE (
                       lower(repeat_loci.locus_id) = lower(repeat_expansions.locus_id)
                    OR lower(repeat_loci.gene) = lower(repeat_expansions.gene)
                    OR lower(repeat_loci.display_name) = lower(repeat_expansions.display_name)
                    OR EXISTS (
                         SELECT 1
                         FROM jsonb_array_elements_text(repeat_loci.aliases) AS alias(value)
                         WHERE lower(alias.value) IN (
                             lower(repeat_expansions.locus_id),
                             lower(repeat_expansions.gene),
                             lower(repeat_expansions.display_name),
                             lower(repeat_expansions.metadata ->> 'trid')
                         )
                    )
                  )
                  -- Names that reach several STRchive loci (ARX's or HOXA13's tracts, a
                  -- shared tag) choose none of them: only the locus the call was stored
                  -- under classifies it. The import chose that tract by the call's position,
                  -- which a name cannot do (CLIN-31).
                  AND (
                       lower(repeat_loci.locus_id) = lower(repeat_expansions.locus_id)
                    OR strchive_named.loci <= 1
                  )
                ORDER BY
                    CASE
                        WHEN lower(repeat_loci.locus_id) = lower(repeat_expansions.locus_id) THEN 0
                        WHEN lower(repeat_loci.gene) = lower(repeat_expansions.gene) THEN 1
                        ELSE 2
                    END,
                    -- A gene can be catalogued twice (built-in ATXN8OS and STRchive
                    -- SCA8_ATXN8OS). Without a tie-breaker the call's thresholds depended
                    -- on row order (#535): prefer the entry that carries the catalogued
                    -- ranges, then a stable id.
                    ((repeat_loci.metadata ->> 'benign_max') IS NOT NULL) DESC,
                    repeat_loci.locus_id
                LIMIT 1
            ) AS catalog ON TRUE
            LEFT JOIN repeat_locus_count_units AS units ON units.locus_id = catalog.locus_id"""


async def get_family_repeat_expansion_table_response(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    count_only: bool = False,
) -> FamilyRepeatExpansionTableOut:
    sample_ids = list(context.sample_uuid_to_name)
    if not sample_ids:
        return FamilyRepeatExpansionTableOut(samples=[], loci=[])
    if count_only:
        # Presence check only: count distinct loci without the catalog LATERAL
        # join, grouping, or row serialization the full table response does.
        count_result = await session.execute(
            text(
                """
                SELECT count(DISTINCT locus_id) AS loci_count
                FROM repeat_expansions
                WHERE family_id = CAST(:family_id AS uuid)
                  AND sample_id IN :sample_ids
                """
            ).bindparams(uuid_list_bindparam("sample_ids")),
            {"family_id": context.family_uuid, "sample_ids": uuid_values(sample_ids)},
        )
        return FamilyRepeatExpansionTableOut(loci_count=int(count_result.scalar() or 0))
    result = await session.execute(
        text(
            f"""
            SELECT
                repeat_expansions.sample_id::text AS sample_uuid,
{_CALL_CATALOGUE_COLUMNS}
            FROM repeat_expansions
{_CALL_CATALOGUE_JOINS}
            WHERE repeat_expansions.family_id = CAST(:family_id AS uuid)
              AND repeat_expansions.sample_id IN :sample_ids
            ORDER BY repeat_expansions.chr, repeat_expansions.start, gene
            """
        ).bindparams(uuid_list_bindparam("sample_ids")),
        {
            "family_id": context.family_uuid,
            "sample_ids": uuid_values(sample_ids),
        },
    )
    rows = [dict(row) for row in result.mappings().all()]
    member_meta = {
        row["sample_uuid"]: {
            "role": row.get("role"),
            "affected": row.get("affected"),
            "sex": row.get("sex"),
        }
        for row in context.sample_rows
    }
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        warning_min = row.get("warning_min")
        pathogenic_min = row.get("pathogenic_min")
        count_units_validated = bool(row.get("count_units_validated"))
        meta = member_meta.get(row["sample_uuid"], {})
        alleles, row_status, note = _classified_call(
            row, sex=meta.get("sex"), assembly_name=context.assembly_name
        )
        review_reason = _review_reason(alleles, row_status)
        # Why no catalogue locus classifies the call, where its TRID names several (CLIN-31).
        # It explains the status the alleles already have, so it does not raise it.
        call_note = " ".join(part for part in (note, row.get("locus_not_resolved")) if part) or None
        key = str(row.get("locus_id") or f'{row.get("chr")}:{row.get("start")}')
        locus = grouped.setdefault(
            key,
            {
                "locus_id": row.get("locus_id"),
                "gene": row.get("gene"),
                "display_name": row.get("display_name"),
                "disease": row.get("disease"),
                "inheritance": row.get("inheritance"),
                "chr": row.get("chr"),
                "start": int(row.get("start", 0)),
                "end": int(row.get("end", 0)),
                "motif": row.get("motif"),
                "warning_min": warning_min,
                "pathogenic_min": pathogenic_min,
                "benign_min": row.get("benign_min"),
                "benign_max": row.get("benign_max"),
                "pathogenic_max": row.get("pathogenic_max"),
                "premutation_min": row.get("premutation_min"),
                "count_units_validated": count_units_validated,
                "count_units_note": known_count_unit_difference(row.get("gene")),
                "status": row_status,
                "review_reason": review_reason,
                "calls": {},
            },
        )
        locus["status"] = summarize_repeat_status((locus["status"], row_status))
        if locus["status"] == "review" and locus["review_reason"] != "outside_ranges":
            # A count the catalogue cannot classify, in any call, is the locus's reason.
            locus["review_reason"] = review_reason or locus["review_reason"]
        elif locus["status"] != "review":
            locus["review_reason"] = None
        sample_name = context.sample_uuid_to_name.get(row["sample_uuid"], row["sample_uuid"])
        locus["calls"][sample_name] = RepeatExpansionSampleCallOut(
            sample=sample_name,
            role=meta.get("role"),
            affected=meta.get("affected"),
            sex=meta.get("sex"),
            genotype=row.get("genotype", "./."),
            allele_count=int(row.get("allele_count", 0)),
            alleles=alleles,
            status=row_status,
            review_reason=review_reason,
            note=call_note,
        )

    ordered_rows = [RepeatExpansionRowOut(**row) for row in grouped.values()]
    ordered_rows.sort(key=lambda row: (row.chr, row.start, row.gene, row.locus_id))
    samples = [
        {
            "sample_id": row["sample_id"],
            "role": row.get("role", "sibling"),
            "affected": bool(row.get("affected", False)),
            "sex": row.get("sex", "und"),
        }
        for row in context.sample_rows
    ]
    return FamilyRepeatExpansionTableOut(
        samples=samples, loci=ordered_rows, loci_count=len(ordered_rows)
    )


async def get_sample_repeat_expansion_track_response(
    session: AsyncSession,
    *,
    context: FamilyMetadataContext,
    sample_name: str,
    chromosomes: Iterable[str] | None = None,
    start: int | None = None,
    end: int | None = None,
) -> RepeatExpansionTrackResponse:
    sample_uuid = context.sample_name_to_uuid.get(sample_name)
    if sample_uuid is None:
        return RepeatExpansionTrackResponse()

    chrom_values: list[str] = []
    for chromosome in chromosomes or []:
        chrom_values.extend(chromosome_aliases(chromosome))
    params: dict[str, Any] = {"sample_id": sample_uuid}
    where_clauses = ["repeat_expansions.sample_id = CAST(:sample_id AS uuid)"]
    if chrom_values:
        where_clauses.append("repeat_expansions.chr IN :chromosomes")
        params["chromosomes"] = chrom_values
    if start is not None and end is not None and chrom_values:
        where_clauses.append("repeat_expansions.start <= :end")
        where_clauses.append("repeat_expansions.\"end\" >= :start")
        params["start"] = start
        params["end"] = end
    # The catalogue row that classifies each call, and the call read against it, as the
    # family table reads it (CLIN-14).
    query = text(
        f"""
        SELECT
{_CALL_CATALOGUE_COLUMNS}
        FROM repeat_expansions
{_CALL_CATALOGUE_JOINS}
        WHERE {' AND '.join(where_clauses)}
        ORDER BY repeat_expansions.chr, repeat_expansions.start
        """
    )
    if chrom_values:
        query = query.bindparams(bindparam("chromosomes", expanding=True))
    result = await session.execute(query, params)
    sex = next(
        (row.get("sex") for row in context.sample_rows if str(row.get("sample_uuid")) == str(sample_uuid)),
        None,
    )
    items = []
    for row in result.mappings().all():
        alleles, status, _note = _classified_call(row, sex=sex, assembly_name=context.assembly_name)
        items.append(
            RepeatExpansionTrackItemOut(
                sample=sample_name,
                locus_id=row["locus_id"],
                gene=row["gene"],
                display_name=row["display_name"],
                disease=row["disease"],
                chr=row["chr"],
                start=int(row["start"]),
                end=int(row["end"]),
                motif=row.get("motif"),
                warning_min=row.get("warning_min"),
                pathogenic_min=row.get("pathogenic_min"),
                status=status,
                review_reason=_review_reason(alleles, status),
                allele_repeat_counts=[
                    int(allele["repeat_count"])
                    for allele in row.get("alleles", [])
                    if allele.get("repeat_count") is not None
                ],
                allele_bp_lengths=[
                    int(allele["bp_length"])
                    for allele in row.get("alleles", [])
                    if allele.get("bp_length") is not None
                ],
                count_units_validated=bool(row.get("count_units_validated")),
                count_units_note=known_count_unit_difference(row.get("gene")),
                count_unit_warnings=[
                    allele["count_unit_warning"]
                    for allele in alleles
                    if allele.get("count_unit_warning") is not None
                ],
            )
        )
    return RepeatExpansionTrackResponse(items=items)
