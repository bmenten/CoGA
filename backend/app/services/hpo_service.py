from __future__ import annotations

import asyncio
import csv
import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date
import io
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.coga_logging import scrub_log
from ..core.config import (
    HPO_PINNED_ONTOLOGY_SHA256,
    HPO_PINNED_ONTOLOGY_URL,
    HPO_PINNED_RELEASE,
    settings,
)
from ..core.sql import canonical_uuid, is_missing_postgres_schema_error, require_uuid
from .access_control import CurrentUser
from .clinical_audit_service import record_clinical_event
from .family_identifiers import visible as visible_text

HPO_ID_PATTERN = re.compile(r"^HP:[0-9]{7}$", re.IGNORECASE)
HPO_ID_SEARCH_PATTERN = re.compile(r"(?:HP[:_])([0-9]{7})", re.IGNORECASE)
HPO_URI_PATTERN = re.compile(r"(?:^|[/_])HP[_:]([0-9]{7})$", re.IGNORECASE)
HPO_RELEASE_DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")

PRESENT_STATUS_VALUES = {"present", "positive", "observed", "yes", "true", "1"}
ABSENT_STATUS_VALUES = {"absent", "excluded", "negative", "no", "false", "0"}
UNKNOWN_STATUS_VALUES = {"unknown", "uncertain", "na", "n/a", "not_applicable"}
ANNOTATION_STATUSES = {"present", "absent", "unknown"}
HPO_ADMIN_TABLES = ("hpo_term", "hpo_synonym", "hpo_edge")
HPO_IMPORT_TABLES = ("hpo_term", "hpo_synonym", "hpo_edge", "hpo_closure")
# hp.obo is ~10 MB; the ceiling only stops a runaway response exhausting the disk (#522).
MAX_HPO_ONTOLOGY_BYTES = 256 * 1024 * 1024
_HPO_DOWNLOAD_CHUNK = 1 << 20
_SHA256_HEX_PATTERN = re.compile(r"[0-9a-f]{64}")

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ParsedHpoTerm:
    hpo_id: str
    label: str
    definition: str | None = None
    is_obsolete: bool = False
    replaced_by: str | None = None
    synonyms: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedHpoEdge:
    child_id: str
    parent_id: str
    relation: str = "is_a"


@dataclass(slots=True)
class ParsedHpoOntology:
    terms: dict[str, ParsedHpoTerm]
    edges: list[ParsedHpoEdge]


@dataclass(frozen=True, slots=True)
class HpoOntologyReleaseMetadata:
    release_version: str | None = None
    release_date: date | None = None


@dataclass(frozen=True, slots=True)
class HpoAnnotationImportIssue:
    line_no: int | None
    code: str
    message: str
    sample_id: str | None = None
    hpo_id: str | None = None


@dataclass(frozen=True, slots=True)
class HpoAnnotationImportRow:
    family_id: str
    individual_id: str
    hpo_id: str
    label: str | None = None
    status: str = "present"
    onset: str | None = None
    evidence: str | None = None
    source: str | None = None
    note: str | None = None
    line_no: int | None = None


def normalize_hpo_id(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    direct_match = HPO_ID_PATTERN.match(cleaned)
    if direct_match:
        return cleaned.upper()
    uri_match = HPO_URI_PATTERN.search(cleaned)
    if uri_match:
        return f"HP:{uri_match.group(1)}"
    search_match = HPO_ID_SEARCH_PATTERN.search(cleaned)
    if search_match:
        return f"HP:{search_match.group(1)}"
    return None


def _configured_hpo_ontology_path() -> str | None:
    """The ontology file the backend is configured to load (``HPO_ONTOLOGY_PATH``).

    The admin page offers it as the file to synchronise, so the page holds no copy of a
    default that can drift from the backend's.
    """
    return settings.hpo_ontology_path or None


def _empty_hpo_admin_summary() -> dict[str, Any]:
    return {
        "total_terms": 0,
        "active_terms": 0,
        "obsolete_terms": 0,
        "release_version": None,
        "release_date": None,
        "last_sync_date": None,
        "automatic_update_supported": False,
        "ontology_loaded": False,
        "ontology_path": _configured_hpo_ontology_path(),
    }


# Confirmed-available tables are cached so the to_regclass probe runs at most once
# per table per process (schema is static once applied). Only positive results are
# cached; absent tables are re-probed since they may appear after schema init.
_pg_tables_confirmed_available: set[str] = set()


async def _postgres_tables_available(
    session: AsyncSession,
    table_names: tuple[str, ...],
) -> bool:
    if not table_names:
        return True
    to_probe = [name for name in table_names if name not in _pg_tables_confirmed_available]
    if not to_probe:
        return True
    select_sql = ", ".join(
        f"to_regclass('{table_name}') IS NOT NULL AS {table_name}"
        for table_name in to_probe
    )
    result = await session.execute(text(f"SELECT {select_sql}"))
    row = result.mappings().one()
    all_available = True
    for table_name in to_probe:
        if bool(row.get(table_name)):
            _pg_tables_confirmed_available.add(table_name)
        else:
            all_available = False
    return all_available


async def _ensure_hpo_schema_available(session: AsyncSession) -> None:
    if not await _postgres_tables_available(session, ("hpo_term",)):
        raise HTTPException(
            status_code=503,
            detail=(
                "HPO database schema is not available. Restart the backend or apply "
                "the latest Postgres schema so the HPO tables are created, then retry."
            ),
        )


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return value
    return []


def _text_list(value: Any) -> list[str]:
    return [str(item) for item in _json_list(value) if item is not None]


def _relation_list(value: Any) -> list[dict[str, str]]:
    relations: list[dict[str, str]] = []
    for item in _json_list(value):
        if not isinstance(item, Mapping):
            continue
        hpo_id = item.get("hpo_id")
        label = item.get("label")
        if hpo_id is None or label is None:
            continue
        relations.append(
            {
                "hpo_id": str(hpo_id),
                "label": str(label),
                "relation": str(item.get("relation") or "is_a"),
            }
        )
    return relations


def normalize_annotation_status(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in ANNOTATION_STATUSES:
        return normalized
    if normalized in PRESENT_STATUS_VALUES:
        return "present"
    if normalized in ABSENT_STATUS_VALUES:
        return "absent"
    if normalized in UNKNOWN_STATUS_VALUES:
        return "unknown"
    return None


def _dedupe_preserve_order(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def _quoted_value(line: str) -> str | None:
    match = re.search(r'"((?:\\"|[^"])*)"', line)
    if not match:
        return None
    return match.group(1).replace('\\"', '"')


def parse_hpo_obo_text(text_value: str) -> ParsedHpoOntology:
    terms: dict[str, ParsedHpoTerm] = {}
    edges: list[ParsedHpoEdge] = []
    current: dict[str, Any] | None = None

    def flush_current() -> None:
        nonlocal current
        if not current:
            return
        hpo_id = normalize_hpo_id(current.get("id"))
        label = str(current.get("name") or "").strip()
        if not hpo_id or not label:
            current = None
            return
        synonyms = _dedupe_preserve_order(
            [*current.get("synonyms", []), *current.get("alt_ids", [])]
        )
        term = ParsedHpoTerm(
            hpo_id=hpo_id,
            label=label,
            definition=current.get("definition"),
            is_obsolete=bool(current.get("is_obsolete")),
            replaced_by=normalize_hpo_id(current.get("replaced_by")),
            synonyms=synonyms,
            metadata={"alt_ids": current.get("alt_ids", [])},
        )
        terms[hpo_id] = term
        for relation, parent_value in current.get("parents", []):
            parent_id = normalize_hpo_id(parent_value)
            if parent_id:
                edges.append(ParsedHpoEdge(child_id=hpo_id, parent_id=parent_id, relation=relation))
        current = None

    for raw_line in text_value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("!"):
            continue
        if line == "[Term]":
            flush_current()
            current = {"synonyms": [], "parents": [], "alt_ids": []}
            continue
        if line.startswith("["):
            flush_current()
            current = None
            continue
        if current is None:
            continue
        if line.startswith("id:"):
            current["id"] = line.removeprefix("id:").strip()
        elif line.startswith("name:"):
            current["name"] = line.removeprefix("name:").strip()
        elif line.startswith("def:"):
            current["definition"] = _quoted_value(line)
        elif line.startswith("synonym:"):
            synonym = _quoted_value(line)
            if synonym:
                current["synonyms"].append(synonym)
        elif line.startswith("alt_id:"):
            alt_id = normalize_hpo_id(line.removeprefix("alt_id:").strip())
            if alt_id:
                current["alt_ids"].append(alt_id)
        elif line.startswith("is_a:"):
            parent_id = line.removeprefix("is_a:").strip().split()[0]
            current["parents"].append(("is_a", parent_id))
        elif line.startswith("relationship:"):
            parts = line.removeprefix("relationship:").strip().split()
            if len(parts) >= 2 and normalize_hpo_id(parts[1]):
                current["parents"].append((parts[0], parts[1]))
        elif line.startswith("is_obsolete:"):
            current["is_obsolete"] = line.removeprefix("is_obsolete:").strip().lower() == "true"
        elif line.startswith("replaced_by:"):
            current["replaced_by"] = line.removeprefix("replaced_by:").strip()
    flush_current()

    return ParsedHpoOntology(terms=terms, edges=edges)


def _json_graphs(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    graphs = payload.get("graphs")
    if isinstance(graphs, list):
        return [graph for graph in graphs if isinstance(graph, dict)]
    graph = payload.get("graph")
    if isinstance(graph, dict):
        return [graph]
    if isinstance(payload.get("nodes"), list) or isinstance(payload.get("edges"), list):
        return [payload]
    return []


def _json_definition(meta: Mapping[str, Any]) -> str | None:
    definition = meta.get("definition")
    if isinstance(definition, dict):
        value = definition.get("val") or definition.get("value")
        return str(value).strip() if value else None
    if isinstance(definition, str):
        return definition.strip() or None
    return None


def _json_synonyms(meta: Mapping[str, Any]) -> list[str]:
    raw_synonyms = meta.get("synonyms")
    if not isinstance(raw_synonyms, list):
        return []
    values: list[str] = []
    for synonym in raw_synonyms:
        if isinstance(synonym, dict):
            value = synonym.get("val") or synonym.get("value")
            if value:
                values.append(str(value))
        elif isinstance(synonym, str):
            values.append(synonym)
    return _dedupe_preserve_order(values)


def _json_replaced_by(meta: Mapping[str, Any]) -> str | None:
    basic_values = meta.get("basicPropertyValues")
    if not isinstance(basic_values, list):
        return None
    for item in basic_values:
        if not isinstance(item, dict):
            continue
        pred = str(item.get("pred") or "").lower()
        if "replaced_by" not in pred and "term_replaced_by" not in pred:
            continue
        replacement = normalize_hpo_id(str(item.get("val") or item.get("value") or ""))
        if replacement:
            return replacement
    return None


def parse_hpo_json_text(text_value: str) -> ParsedHpoOntology:
    payload = json.loads(text_value)
    terms: dict[str, ParsedHpoTerm] = {}
    edges: list[ParsedHpoEdge] = []
    for graph in _json_graphs(payload):
        for node in graph.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            hpo_id = normalize_hpo_id(str(node.get("id") or ""))
            if not hpo_id:
                continue
            label = str(node.get("lbl") or node.get("label") or node.get("name") or "").strip()
            if not label:
                continue
            meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
            terms[hpo_id] = ParsedHpoTerm(
                hpo_id=hpo_id,
                label=label,
                definition=_json_definition(meta),
                is_obsolete=bool(meta.get("deprecated") or node.get("deprecated")),
                replaced_by=_json_replaced_by(meta),
                synonyms=_json_synonyms(meta),
                metadata={},
            )
        for edge in graph.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            child_id = normalize_hpo_id(str(edge.get("sub") or edge.get("subject") or ""))
            parent_id = normalize_hpo_id(str(edge.get("obj") or edge.get("object") or ""))
            if not child_id or not parent_id:
                continue
            relation = str(edge.get("pred") or edge.get("predicate") or "is_a").split("/")[-1]
            edges.append(ParsedHpoEdge(child_id=child_id, parent_id=parent_id, relation=relation))
    return ParsedHpoOntology(terms=terms, edges=edges)


def _parse_release_date(value: str | None) -> date | None:
    if not value:
        return None
    match = HPO_RELEASE_DATE_PATTERN.search(value)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _parse_hpo_obo_release_metadata(text_value: str) -> HpoOntologyReleaseMetadata:
    data_version: str | None = None
    version_info: str | None = None
    for raw_line in text_value.splitlines():
        line = raw_line.strip()
        if line in {"[Term]", "[Typedef]"}:
            break
        if line.startswith("data-version:"):
            data_version = line.split(":", 1)[1].strip() or None
            continue
        if line.startswith("property_value: owl:versionInfo"):
            quoted = _quoted_value(line)
            version_info = quoted or line.rsplit(" ", 1)[-1].strip() or None
    release_version = data_version or version_info
    release_date = _parse_release_date(data_version) or _parse_release_date(version_info)
    return HpoOntologyReleaseMetadata(
        release_version=release_version,
        release_date=release_date,
    )


def _json_metadata_candidates(payload: Any) -> list[str]:
    candidates: list[str] = []
    if isinstance(payload, dict):
        meta = payload.get("meta")
        if isinstance(meta, dict):
            candidates.extend(str(meta[key]) for key in ("version", "dataVersion") if meta.get(key))
        for key in ("version", "dataVersion"):
            if payload.get(key):
                candidates.append(str(payload[key]))
        for graph in _json_graphs(payload):
            graph_meta = graph.get("meta") if isinstance(graph, dict) else None
            if isinstance(graph_meta, dict):
                candidates.extend(
                    str(graph_meta[key])
                    for key in ("version", "dataVersion")
                    if graph_meta.get(key)
                )
    return candidates


def parse_hpo_ontology_release_metadata_text(
    text_value: str,
    *,
    source_format: str = "obo",
) -> HpoOntologyReleaseMetadata:
    if source_format.lower() == "json":
        try:
            candidates = _json_metadata_candidates(json.loads(text_value))
        except json.JSONDecodeError:
            candidates = []
        release_version = candidates[0] if candidates else None
        release_date = next(
            (parsed for parsed in (_parse_release_date(candidate) for candidate in candidates) if parsed),
            None,
        )
        return HpoOntologyReleaseMetadata(
            release_version=release_version,
            release_date=release_date,
        )
    return _parse_hpo_obo_release_metadata(text_value)


def _hpo_source_format(path: str | Path) -> str:
    return "json" if Path(path).suffix.lower() == ".json" else "obo"


def parse_hpo_ontology_release_metadata_path(path: str | Path) -> HpoOntologyReleaseMetadata:
    resolved = Path(path)
    text_value = resolved.read_text(encoding="utf-8")
    return parse_hpo_ontology_release_metadata_text(text_value, source_format=_hpo_source_format(resolved))


def parse_hpo_ontology_text(text_value: str, *, source_format: str = "obo") -> ParsedHpoOntology:
    if source_format.lower() == "json":
        return parse_hpo_json_text(text_value)
    return parse_hpo_obo_text(text_value)


def parse_hpo_ontology_path(path: str | Path) -> ParsedHpoOntology:
    resolved = Path(path)
    text_value = resolved.read_text(encoding="utf-8")
    return parse_hpo_ontology_text(text_value, source_format=_hpo_source_format(resolved))


def hpo_ontology_candidate_paths(ontology_path: str | Path | None = None) -> list[Path]:
    repo_root = Path(__file__).resolve().parents[3]
    candidates = [
        Path(ontology_path).expanduser() if ontology_path else None,
        Path("/data/ref-data/hpo/hpo.obo"),
        Path("/data/ref-data/hpo/hp.obo"),
        repo_root / "data" / "ref-data" / "hpo" / "hpo.obo",
        repo_root / "data" / "ref-data" / "hpo" / "hp.obo",
        # Last resort for the bootstrap download when the reference-data mount is
        # read-only (Cloud Run, #520): the file is only needed until it is imported.
        Path(tempfile.gettempdir()) / "coga-ref-data" / "hpo" / "hp.obo",
    ]
    resolved: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(candidate)
    return resolved


def resolve_hpo_ontology_path(ontology_path: str | Path | None = None) -> Path | None:
    for candidate in hpo_ontology_candidate_paths(ontology_path):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def ensure_authorized_hpo_ontology_path(path: str | Path) -> Path:
    """Confirm an API-supplied ontology path resolves inside an authorized HPO ontology
    directory before it is opened.

    ``/admin/hpo/sync`` accepts a raw filesystem path from the request
    body; without this guard a request could point ``read_text`` at any host file (e.g.
    ``/etc/passwd``) — an admin-gated arbitrary-file read that violates least privilege
    on a clinical platform. The authorized roots are the directories of the known
    ontology locations (the configured ``HPO_ONTOLOGY_PATH`` plus the default ref-data
    dirs), so routine updates that drop a new ``hp.obo``/``hpo.json`` in place still work.
    """
    authorized_roots = {
        os.path.normpath(os.path.expanduser(str(candidate.parent)))
        for candidate in hpo_ontology_candidate_paths(settings.hpo_ontology_path)
    }
    # Validate with pure string normalization — no Path.resolve()/read_text() touches the
    # raw admin-supplied value before it is checked. os.path.normpath collapses '..' so a
    # traversal cannot escape, and requiring the file's directory to be one of the
    # authorized roots rejects absolute or relative paths pointing anywhere else.
    normalized = os.path.normpath(os.path.expanduser(str(path)))
    if os.path.dirname(normalized) in authorized_roots:
        return Path(normalized)
    raise ValueError("HPO ontology path is outside the authorized ontology directory")


def _read_bounded(response: Any, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(_HPO_DOWNLOAD_CHUNK)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"HPO ontology download exceeds {max_bytes} bytes")
        chunks.append(chunk)


class HpoOntologyChecksumMismatch(RuntimeError):
    """The ontology, downloaded (OPS-6) or found in place (TRACE-14), is not the pinned file,
    so it was refused."""


_HPO_PINNED_FILE_REMEDY = (
    "Copy the repository's data/ref-data/hpo/hp.obo there unchanged (on Cloud Run: gcloud storage "
    "cp data/ref-data/hpo/hp.obo gs://<reference-data bucket>/hpo/), or set HPO_ONTOLOGY_PATH to "
    "such a copy. Another HPO release needs a reviewed change that moves HPO_PINNED_RELEASE and "
    "HPO_PINNED_ONTOLOGY_SHA256 with the vendored file (data/ref-data/hpo/README.md)."
)


def read_pinned_hpo_ontology(path: Path) -> bytes:
    """The ontology file's bytes, if it is the pinned release byte for byte (TRACE-14).

    The startup imports only the vendored release: a file already in place is held to the pin
    (``HPO_PINNED_ONTOLOGY_SHA256``, which no setting overrides) as the download is. Any other
    file raises ``HpoOntologyChecksumMismatch`` before anything is written; one that cannot be
    read raises ``OSError``. The bytes checked are the bytes returned, so the import parses
    exactly what was checked."""
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != HPO_PINNED_ONTOLOGY_SHA256:
        raise HpoOntologyChecksumMismatch(
            f"Refused the HPO ontology file {scrub_log(path)}: it is not the pinned release "
            f"{HPO_PINNED_RELEASE}. Its SHA-256 is {digest}, the pinned file's is "
            f"{HPO_PINNED_ONTOLOGY_SHA256}. Nothing was imported, and no file was downloaded "
            f"in its place: the HPO terminology stays empty until the pinned file is there. "
            f"{_HPO_PINNED_FILE_REMEDY}"
        )
    return payload


def _download_hpo_ontology_file(
    url: str,
    candidate_paths: list[Path],
    *,
    expected_sha256: str = HPO_PINNED_ONTOLOGY_SHA256,
    max_bytes: int = MAX_HPO_ONTOLOGY_BYTES,
) -> Path:
    """Download the pinned HPO ontology over HTTPS, bounded, checked, and written atomically.

    The bootstrap trusts this file as the phenotype ontology, so it is fetched over TLS
    only, capped in size, must look like an OBO file (an HTML error page would otherwise
    be imported as an empty ontology), and never leaves a partial file behind (#522). It
    must be the pinned file: its SHA-256 must be ``expected_sha256``, by default the vendored
    file's (``HPO_PINNED_ONTOLOGY_SHA256``, which no setting overrides), checked before
    anything is written. Another file, such as a later release, is refused and no file on
    disk is touched (OPS-6). There is no unchecked download: without a SHA-256 to check
    against, nothing is requested.
    """
    if urlparse(url).scheme.lower() != "https":
        raise RuntimeError(f"Refusing to download the HPO ontology over a non-HTTPS URL: {url}")
    pinned = expected_sha256.strip().lower()
    if not _SHA256_HEX_PATTERN.fullmatch(pinned):
        raise RuntimeError(
            "Refusing to download the HPO ontology without a pinned SHA-256 to check it against "
            "(the pin must be 64 hexadecimal characters)"
        )
    request = Request(url, headers={"User-Agent": "CoGA-HPO-bootstrap/1.0"})
    try:
        with urlopen(request, timeout=60) as response:
            payload = _read_bounded(response, max_bytes)
    except Exception as exc:
        raise RuntimeError(f"Failed to download HPO ontology from {url}: {exc}") from exc
    if not payload.lstrip().startswith(b"format-version:"):
        raise RuntimeError(f"The download from {url} is not an OBO ontology file")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != pinned:
        raise HpoOntologyChecksumMismatch(
            f"Refused the HPO ontology downloaded from {url}: its SHA-256 is {digest}, not the "
            f"pinned {pinned}. Nothing was written."
        )
    logger.info(
        "Downloaded the HPO ontology from %s (%d bytes, sha256 %s, the pinned digest)",
        url,
        len(payload),
        digest,
    )

    errors: list[str] = []
    for target in candidate_paths:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".partial")
            partial.write_bytes(payload)
            os.replace(partial, target)
            return target
        except OSError as exc:
            errors.append(f"{target}: {exc}")
    detail = "; ".join(errors) if errors else "no candidate download target"
    raise RuntimeError(f"Failed to store the HPO ontology downloaded from {url}: {detail}")


async def _hpo_term_count(session: AsyncSession) -> int:
    if not await _postgres_tables_available(session, HPO_IMPORT_TABLES):
        return 0
    result = await session.execute(text("SELECT COUNT(*)::int FROM hpo_term"))
    return int(result.scalar_one() or 0)


async def ensure_hpo_ontology_on_startup(
    session: AsyncSession,
    *,
    ontology_path: str | Path | None = None,
    download_if_missing: bool = True,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Import the HPO ontology when no terms are loaded yet.

    It reads the ontology file it finds (the vendored ``hp.obo``). Only when it finds none,
    and ``download_if_missing``, does it download one: always the pinned release from its
    PURL, checked against the pinned SHA-256 (``HPO_PINNED_ONTOLOGY_URL`` and
    ``HPO_PINNED_ONTOLOGY_SHA256``). No setting changes either: the release moves only with
    the vendored file, through a reviewed code change (OPS-6).

    The file it finds is held to the same pin (TRACE-14): it is imported, from the bytes that
    were checked, only if its SHA-256 is the pinned one. Another file, or one that cannot be
    read, is refused with one logged error, nothing is imported and nothing is downloaded in
    its place, as for a refused download. The start goes on: the terminology stays empty,
    which the admin HPO page shows, until the pinned file is in place.
    """
    if not enabled:
        return None
    try:
        if await _hpo_term_count(session) > 0:
            return None
    except DBAPIError as exc:
        if is_missing_postgres_schema_error(exc):
            logger.warning("HPO bootstrap skipped because the HPO database schema is not available")
            return None
        raise

    candidate_paths = hpo_ontology_candidate_paths(ontology_path)
    resolved_path = resolve_hpo_ontology_path(ontology_path)
    if resolved_path is None and download_if_missing:
        try:
            resolved_path = await asyncio.to_thread(
                _download_hpo_ontology_file,
                HPO_PINNED_ONTOLOGY_URL,
                candidate_paths,
                expected_sha256=HPO_PINNED_ONTOLOGY_SHA256,
            )
            logger.info("Downloaded HPO ontology to %s", resolved_path)
        except Exception as exc:
            if isinstance(exc, HpoOntologyChecksumMismatch):
                # Not the pinned file (OPS-6): an expected refusal, logged without a traceback.
                logger.error("%s The HPO ontology was not imported.", exc)
            else:
                logger.exception("Failed to download HPO ontology from %s", HPO_PINNED_ONTOLOGY_URL)
            try:
                await session.rollback()
            except Exception:  # pragma: no cover
                logger.exception("Failed to roll back after failed HPO ontology download")
            return None
    if resolved_path is None:
        logger.warning(
            "HPO bootstrap skipped because no ontology file was found. Checked: %s",
            ", ".join(str(path) for path in candidate_paths),
        )
        return None

    # Reading, checking and parsing the ontology file is CPU and disk work: keep it off the
    # event loop (#527). The file is imported only if it is the pinned release (TRACE-14),
    # whether it was found in place or just downloaded.
    try:
        payload = await asyncio.to_thread(read_pinned_hpo_ontology, resolved_path)
    except HpoOntologyChecksumMismatch as exc:
        # An expected refusal, logged once without a traceback: its message is the fix.
        logger.error("%s", exc)
        return None
    except OSError as exc:
        logger.error(
            "Refused the HPO ontology file %s: it cannot be read (%s), so it cannot be checked "
            "against the pinned release %s (SHA-256 %s). Nothing was imported. %s",
            scrub_log(resolved_path),
            type(exc).__name__,
            HPO_PINNED_RELEASE,
            HPO_PINNED_ONTOLOGY_SHA256,
            _HPO_PINNED_FILE_REMEDY,
        )
        return None
    ontology_text = payload.decode("utf-8")
    metadata = parse_hpo_ontology_release_metadata_text(
        ontology_text, source_format=_hpo_source_format(resolved_path)
    )
    try:
        imported = await import_hpo_ontology(
            session,
            path=resolved_path,
            ontology_text=ontology_text,
            release_version=metadata.release_version,
            release_date=metadata.release_date,
        )
    except Exception:
        logger.exception("Failed to bootstrap HPO ontology from %s", resolved_path)
        try:
            await session.rollback()
        except Exception:  # pragma: no cover
            logger.exception("Failed to roll back failed HPO bootstrap")
        return None
    logger.info(
        "Bootstrapped HPO ontology from %s (%s terms, release=%s, release_date=%s)",
        resolved_path,
        imported["terms"],
        metadata.release_version or "unknown",
        metadata.release_date.isoformat() if metadata.release_date else "unknown",
    )
    return {
        **imported,
        "path": str(resolved_path),
        "release_version": metadata.release_version,
        "release_date": metadata.release_date,
    }


def compute_hpo_closure(
    terms: Iterable[str],
    edges: Iterable[ParsedHpoEdge],
) -> list[tuple[str, str, int]]:
    term_ids = {term_id for term_id in terms if normalize_hpo_id(term_id)}
    parents_by_child: dict[str, set[str]] = {}
    for edge in edges:
        child_id = normalize_hpo_id(edge.child_id)
        parent_id = normalize_hpo_id(edge.parent_id)
        if not child_id or not parent_id:
            continue
        term_ids.add(child_id)
        term_ids.add(parent_id)
        parents_by_child.setdefault(child_id, set()).add(parent_id)

    closure: list[tuple[str, str, int]] = []
    for hpo_id in sorted(term_ids):
        visited = {hpo_id: 0}
        queue: list[tuple[str, int]] = [(parent, 1) for parent in sorted(parents_by_child.get(hpo_id, set()))]
        while queue:
            ancestor_id, distance = queue.pop(0)
            previous_distance = visited.get(ancestor_id)
            if previous_distance is not None and previous_distance <= distance:
                continue
            visited[ancestor_id] = distance
            queue.extend((parent, distance + 1) for parent in sorted(parents_by_child.get(ancestor_id, set())))
        closure.extend((hpo_id, ancestor_id, distance) for ancestor_id, distance in visited.items())
    return sorted(closure, key=lambda item: (item[0], item[2], item[1]))


async def import_hpo_ontology(
    session: AsyncSession,
    *,
    path: str | Path,
    ontology_text: str | None = None,
    release_version: str | None = None,
    release_date: date | None = None,
) -> dict[str, int]:
    """Import the ontology at ``path``, or ``ontology_text`` when given: the file's text as the
    caller read (and checked) it, so the file is not read again (TRACE-14)."""
    source_format = _hpo_source_format(path)
    if ontology_text is None:
        ontology = await asyncio.to_thread(parse_hpo_ontology_path, path)
        release_metadata = await asyncio.to_thread(parse_hpo_ontology_release_metadata_path, path)
    else:
        ontology = await asyncio.to_thread(parse_hpo_ontology_text, ontology_text, source_format=source_format)
        release_metadata = parse_hpo_ontology_release_metadata_text(ontology_text, source_format=source_format)
    if not ontology.terms:
        raise ValueError("HPO ontology import found no terms")
    effective_release_version = release_version or release_metadata.release_version
    effective_release_date = release_date or release_metadata.release_date

    term_rows = [
        {
            "hpo_id": term.hpo_id,
            "label": term.label,
            "definition": term.definition,
            "is_obsolete": term.is_obsolete,
            "replaced_by": term.replaced_by,
            "release_version": effective_release_version,
            "release_date": effective_release_date,
            "metadata": json.dumps(term.metadata),
        }
        for term in ontology.terms.values()
    ]
    await session.execute(
        text(
            """
            INSERT INTO hpo_term (
                hpo_id, label, definition, is_obsolete, replaced_by,
                release_version, release_date, metadata, updated_at
            )
            VALUES (
                :hpo_id, :label, :definition, :is_obsolete, :replaced_by,
                :release_version, :release_date, CAST(:metadata AS jsonb), timezone('utc', now())
            )
            ON CONFLICT (hpo_id) DO UPDATE SET
                label = EXCLUDED.label,
                definition = EXCLUDED.definition,
                is_obsolete = EXCLUDED.is_obsolete,
                replaced_by = EXCLUDED.replaced_by,
                release_version = EXCLUDED.release_version,
                release_date = EXCLUDED.release_date,
                metadata = EXCLUDED.metadata,
                updated_at = timezone('utc', now())
            """
        ),
        term_rows,
    )

    await session.execute(text("DELETE FROM hpo_synonym"))
    synonym_rows = [
        {"hpo_id": term.hpo_id, "synonym": synonym}
        for term in ontology.terms.values()
        for synonym in term.synonyms
    ]
    if synonym_rows:
        await session.execute(
            text(
                """
                INSERT INTO hpo_synonym (hpo_id, synonym)
                VALUES (:hpo_id, :synonym)
                ON CONFLICT DO NOTHING
                """
            ),
            synonym_rows,
        )

    await session.execute(text("DELETE FROM hpo_edge"))
    ontology_term_ids = set(ontology.terms)
    edge_rows = [
        {
            "child_id": edge.child_id,
            "parent_id": edge.parent_id,
            "relation": edge.relation or "is_a",
        }
        for edge in ontology.edges
        if edge.child_id in ontology_term_ids and edge.parent_id in ontology_term_ids
    ]
    if edge_rows:
        await session.execute(
            text(
                """
                INSERT INTO hpo_edge (child_id, parent_id, relation)
                VALUES (:child_id, :parent_id, :relation)
                ON CONFLICT DO NOTHING
                """
            ),
            edge_rows,
        )

    await session.execute(text("DELETE FROM hpo_closure"))
    closure_rows = [
        {"hpo_id": hpo_id, "ancestor_id": ancestor_id, "distance": distance}
        for hpo_id, ancestor_id, distance in compute_hpo_closure(ontology.terms, ontology.edges)
        if hpo_id in ontology_term_ids and ancestor_id in ontology_term_ids
    ]
    if closure_rows:
        await session.execute(
            text(
                """
                INSERT INTO hpo_closure (hpo_id, ancestor_id, distance)
                VALUES (:hpo_id, :ancestor_id, :distance)
                ON CONFLICT (hpo_id, ancestor_id) DO UPDATE SET distance = EXCLUDED.distance
                """
            ),
            closure_rows,
        )

    await session.commit()
    return {
        "terms": len(term_rows),
        "synonyms": len(synonym_rows),
        "edges": len(edge_rows),
        "closure_rows": len(closure_rows),
    }


def _row_value(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return str(value).strip() if value is not None else ""


def parse_hpo_tsv_text(
    text_value: str,
    *,
    expected_family_id: str | None = None,
    default_source: str | None = None,
) -> tuple[list[HpoAnnotationImportRow], list[HpoAnnotationImportIssue]]:
    reader = csv.DictReader(io.StringIO(text_value), delimiter="\t")
    required_fields = {"family_id", "individual_id", "hpo_id", "status"}
    fieldnames = {str(field).strip().lower() for field in (reader.fieldnames or []) if field}
    if not required_fields.issubset(fieldnames):
        missing = ", ".join(sorted(required_fields - fieldnames))
        return [], [
            HpoAnnotationImportIssue(
                line_no=1,
                code="phenotype_header_invalid",
                message=f"Phenotype TSV header is missing required column(s): {missing}",
            )
        ]

    rows: list[HpoAnnotationImportRow] = []
    issues: list[HpoAnnotationImportIssue] = []
    for index, raw_row in enumerate(reader, start=2):
        row = {str(key).strip().lower(): str(value or "").strip() for key, value in raw_row.items() if key}
        family_id = _row_value(row, "family_id")
        individual_id = _row_value(row, "individual_id")
        hpo_id = normalize_hpo_id(_row_value(row, "hpo_id"))
        status = normalize_annotation_status(_row_value(row, "status"))
        if expected_family_id and family_id and family_id != expected_family_id:
            issues.append(
                HpoAnnotationImportIssue(
                    line_no=index,
                    code="phenotype_family_mismatch",
                    message=f"Phenotype row family_id '{family_id}' does not match package family_id '{expected_family_id}'",
                    sample_id=individual_id or None,
                    hpo_id=hpo_id,
                )
            )
            continue
        if not individual_id:
            issues.append(
                HpoAnnotationImportIssue(
                    line_no=index,
                    code="phenotype_sample_missing",
                    message="Phenotype row is missing individual_id",
                    hpo_id=hpo_id,
                )
            )
            continue
        if not hpo_id:
            issues.append(
                HpoAnnotationImportIssue(
                    line_no=index,
                    code="phenotype_hpo_invalid",
                    message="Phenotype row has an invalid HPO identifier",
                    sample_id=individual_id,
                )
            )
            continue
        if not status:
            issues.append(
                HpoAnnotationImportIssue(
                    line_no=index,
                    code="phenotype_status_invalid",
                    message="Phenotype row status must be present, absent, or unknown",
                    sample_id=individual_id,
                    hpo_id=hpo_id,
                )
            )
            continue
        rows.append(
            HpoAnnotationImportRow(
                family_id=family_id or expected_family_id or "",
                individual_id=individual_id,
                hpo_id=hpo_id,
                label=_row_value(row, "label") or None,
                status=status,
                onset=_row_value(row, "onset") or None,
                evidence=_row_value(row, "evidence") or None,
                source=_row_value(row, "source") or default_source,
                note=_row_value(row, "note") or None,
                line_no=index,
            )
        )
    return rows, issues


def parse_hpo_tsv_path(
    path: str | Path,
    *,
    expected_family_id: str | None = None,
    default_source: str | None = None,
) -> tuple[list[HpoAnnotationImportRow], list[HpoAnnotationImportIssue]]:
    return parse_hpo_tsv_text(
        Path(path).read_text(encoding="utf-8"),
        expected_family_id=expected_family_id,
        default_source=default_source,
    )


def _inline_term_to_row(
    *,
    family_id: str,
    individual_id: str,
    status: str,
    value: Any,
) -> tuple[HpoAnnotationImportRow | None, HpoAnnotationImportIssue | None]:
    if isinstance(value, str):
        hpo_id = normalize_hpo_id(value)
        label = None
        onset = evidence = note = None
    elif isinstance(value, Mapping):
        hpo_id = normalize_hpo_id(str(value.get("hpo_id") or value.get("id") or value.get("term") or ""))
        label = str(value.get("label") or "").strip() or None
        onset = str(value.get("onset") or "").strip() or None
        evidence = str(value.get("evidence") or "").strip() or None
        note = str(value.get("note") or "").strip() or None
    else:
        hpo_id = None
        label = onset = evidence = note = None
    if not hpo_id:
        return None, HpoAnnotationImportIssue(
            line_no=None,
            code="manifest_hpo_invalid",
            message=f"Inline HPO annotation for {individual_id} has an invalid term",
            sample_id=individual_id,
        )
    return HpoAnnotationImportRow(
        family_id=family_id,
        individual_id=individual_id,
        hpo_id=hpo_id,
        label=label,
        status=status,
        onset=onset,
        evidence=evidence,
        source="manifest_inline",
        note=note,
    ), None


def parse_manifest_inline_hpo(
    individuals: Any,
    *,
    family_id: str,
) -> tuple[list[HpoAnnotationImportRow], list[HpoAnnotationImportIssue]]:
    if individuals in (None, {}):
        return [], []
    if not isinstance(individuals, Mapping):
        return [], [
            HpoAnnotationImportIssue(
                line_no=None,
                code="manifest_individuals_invalid",
                message="Manifest individuals section must be an object keyed by individual ID",
            )
        ]
    rows: list[HpoAnnotationImportRow] = []
    issues: list[HpoAnnotationImportIssue] = []
    for individual_id_raw, individual_payload in individuals.items():
        individual_id = str(individual_id_raw).strip()
        if not individual_id or not isinstance(individual_payload, Mapping):
            continue
        hpo_payload = individual_payload.get("hpo")
        if hpo_payload is None:
            continue
        status_blocks: dict[str, Any]
        if isinstance(hpo_payload, list):
            status_blocks = {"present": hpo_payload}
        elif isinstance(hpo_payload, Mapping):
            status_blocks = {
                status: hpo_payload.get(status)
                for status in ("present", "absent", "unknown")
                if hpo_payload.get(status) is not None
            }
        else:
            issues.append(
                HpoAnnotationImportIssue(
                    line_no=None,
                    code="manifest_hpo_invalid",
                    message=f"Inline HPO block for {individual_id} must be a list or status object",
                    sample_id=individual_id,
                )
            )
            continue
        for status, values in status_blocks.items():
            term_values = values if isinstance(values, list) else [values]
            for value in term_values:
                row, issue = _inline_term_to_row(
                    family_id=family_id,
                    individual_id=individual_id,
                    status=status,
                    value=value,
                )
                if issue:
                    issues.append(issue)
                if row:
                    rows.append(row)
    return rows, issues


def _issue_dict(issue: HpoAnnotationImportIssue) -> dict[str, Any]:
    return {key: value for key, value in asdict(issue).items() if value is not None}


def _payload_has_field(payload: Any, field_name: str) -> bool:
    fields_set = getattr(payload, "model_fields_set", None)
    if isinstance(fields_set, set):
        return field_name in fields_set
    return hasattr(payload, field_name)


def _payload_value(payload: Any, field_name: str, default: Any = None) -> Any:
    if not _payload_has_field(payload, field_name):
        return default
    return getattr(payload, field_name, default)


async def _existing_hpo_terms(session: AsyncSession, hpo_ids: Iterable[str]) -> set[str]:
    ids = sorted({hpo_id for hpo_id in hpo_ids if hpo_id})
    if not ids:
        return set()
    try:
        result = await session.execute(
            text(
                """
                SELECT hpo_id
                FROM hpo_term
                WHERE hpo_id IN :hpo_ids
                """
            ).bindparams(bindparam("hpo_ids", expanding=True)),
            {"hpo_ids": ids},
        )
    except DBAPIError as exc:
        if is_missing_postgres_schema_error(exc):
            return set()
        raise
    return {str(row[0]) for row in result.all()}


async def _sample_uuid_for(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_id: str,
) -> str:
    result = await session.execute(
        text(
            """
            SELECT id::text
            FROM samples
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND sample_id = :sample_id
            """
        ),
        {"family_uuid": family_uuid, "sample_id": sample_id},
    )
    sample_uuid = result.scalar_one_or_none()
    if sample_uuid is None:
        raise HTTPException(status_code=404, detail="Family member was not found")
    return str(sample_uuid)


_ANNOTATION_NOT_FOUND = "HPO annotation was not found"


async def _annotation_by_id(
    session: AsyncSession,
    *,
    family_uuid: str,
    annotation_id: str,
) -> dict[str, Any] | None:
    annotation_uuid = canonical_uuid(annotation_id)
    if annotation_uuid is None:
        return None
    if await _postgres_tables_available(session, ("hpo_term",)):
        result = await session.execute(
            text(
                """
                SELECT
                    ih.id::text AS id,
                    s.sample_id AS sample_id,
                    ih.hpo_id AS hpo_id,
                    COALESCE(t.label, ih.hpo_id) AS label,
                    t.definition AS definition,
                    ih.status AS status,
                    ih.onset AS onset,
                    ih.evidence AS evidence,
                    ih.source AS source,
                    ih.note AS note,
                    ih.created_at AS created_at,
                    ih.updated_at AS updated_at
                FROM individual_hpo ih
                JOIN samples s ON s.id = ih.sample_id
                LEFT JOIN hpo_term t ON t.hpo_id = ih.hpo_id
                WHERE ih.family_id = CAST(:family_uuid AS uuid)
                  AND ih.id = CAST(:annotation_id AS uuid)
                """
            ),
            {"family_uuid": family_uuid, "annotation_id": annotation_uuid},
        )
    else:
        result = await session.execute(
            text(
                """
                SELECT
                    ih.id::text AS id,
                    s.sample_id AS sample_id,
                    ih.hpo_id AS hpo_id,
                    ih.hpo_id AS label,
                    NULL::text AS definition,
                    ih.status AS status,
                    ih.onset AS onset,
                    ih.evidence AS evidence,
                    ih.source AS source,
                    ih.note AS note,
                    ih.created_at AS created_at,
                    ih.updated_at AS updated_at
                FROM individual_hpo ih
                JOIN samples s ON s.id = ih.sample_id
                WHERE ih.family_id = CAST(:family_uuid AS uuid)
                  AND ih.id = CAST(:annotation_id AS uuid)
                """
            ),
            {"family_uuid": family_uuid, "annotation_id": annotation_uuid},
        )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def list_family_hpo_annotations(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_id: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"family_uuid": family_uuid}
    sample_clause = ""
    if sample_id:
        sample_clause = "AND s.sample_id = :sample_id"
        params["sample_id"] = sample_id
    if await _postgres_tables_available(session, ("hpo_term",)):
        query = f"""
            SELECT
                ih.id::text AS id,
                s.sample_id AS sample_id,
                ih.hpo_id AS hpo_id,
                COALESCE(t.label, ih.hpo_id) AS label,
                t.definition AS definition,
                ih.status AS status,
                ih.onset AS onset,
                ih.evidence AS evidence,
                ih.source AS source,
                ih.note AS note,
                ih.created_at AS created_at,
                ih.updated_at AS updated_at
            FROM individual_hpo ih
            JOIN samples s ON s.id = ih.sample_id
            LEFT JOIN hpo_term t ON t.hpo_id = ih.hpo_id
            WHERE ih.family_id = CAST(:family_uuid AS uuid)
              {sample_clause}
            ORDER BY s.sample_id, ih.status DESC, COALESCE(t.label, ih.hpo_id)
            """
    else:
        query = f"""
            SELECT
                ih.id::text AS id,
                s.sample_id AS sample_id,
                ih.hpo_id AS hpo_id,
                ih.hpo_id AS label,
                NULL::text AS definition,
                ih.status AS status,
                ih.onset AS onset,
                ih.evidence AS evidence,
                ih.source AS source,
                ih.note AS note,
                ih.created_at AS created_at,
                ih.updated_at AS updated_at
            FROM individual_hpo ih
            JOIN samples s ON s.id = ih.sample_id
            WHERE ih.family_id = CAST(:family_uuid AS uuid)
              {sample_clause}
            ORDER BY s.sample_id, ih.status DESC, ih.hpo_id
            """
    result = await session.execute(text(query), params)
    return [dict(row) for row in result.mappings().all()]


async def mark_family_hpo_annotations_stale(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_id: str | None,
    reason: str,
) -> None:
    # `derived_data_status` is built whole rather than with a nested jsonb_set: that returns
    # its input unchanged when the parent key is missing, as it is until the first marker.
    await session.execute(
        text(
            """
            UPDATE families
            SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{derived_data_status}',
                CASE
                    WHEN jsonb_typeof(metadata -> 'derived_data_status') = 'object'
                        THEN metadata -> 'derived_data_status'
                    ELSE '{}'::jsonb
                END
                || jsonb_build_object(
                    'hpo_annotations', jsonb_build_object(
                        'state', 'stale',
                        'reason', CAST(:reason AS text),
                        'sample_id', CAST(:sample_id AS text),
                        'updated_at', timezone('utc', now())
                    ),
                    'updated_at', timezone('utc', now())
                ),
                TRUE
            )
            WHERE id = CAST(:family_uuid AS uuid)
            """
        ),
        {
            "family_uuid": family_uuid,
            "sample_id": sample_id,
            "reason": reason,
        },
    )


async def search_hpo_terms(
    session: AsyncSession,
    *,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    cleaned = query.strip()
    bounded_limit = max(1, min(limit, 50))
    if not cleaned:
        result = await session.execute(
            text(
                """
                SELECT
                    t.hpo_id AS hpo_id,
                    t.label AS label,
                    t.definition AS definition,
                    t.is_obsolete AS is_obsolete
                FROM hpo_term t
                ORDER BY t.is_obsolete, t.label
                LIMIT :limit
                """
            ),
            {"limit": bounded_limit},
        )
        return [dict(row) for row in result.mappings().all()]

    like_query = f"%{cleaned.lower()}%"
    prefix_query = f"{cleaned.lower()}%"
    result = await session.execute(
        text(
            """
            SELECT
                t.hpo_id AS hpo_id,
                t.label AS label,
                t.definition AS definition,
                t.is_obsolete AS is_obsolete,
                CASE
                    WHEN lower(t.hpo_id) = lower(:query) THEN 0
                    WHEN lower(t.hpo_id) LIKE :prefix_query THEN 1
                    WHEN lower(t.label) LIKE :prefix_query THEN 2
                    WHEN EXISTS (
                        SELECT 1 FROM hpo_synonym s
                        WHERE s.hpo_id = t.hpo_id
                          AND lower(s.synonym) LIKE :prefix_query
                    ) THEN 3
                    ELSE 4
                END AS match_rank
            FROM hpo_term t
            WHERE t.is_obsolete = FALSE
              AND (
                lower(t.hpo_id) LIKE :like_query
                OR lower(t.label) LIKE :like_query
                OR EXISTS (
                    SELECT 1 FROM hpo_synonym s
                    WHERE s.hpo_id = t.hpo_id
                      AND lower(s.synonym) LIKE :like_query
                )
              )
            ORDER BY match_rank, t.label
            LIMIT :limit
            """
        ),
        {
            "query": cleaned,
            "like_query": like_query,
            "prefix_query": prefix_query,
            "limit": bounded_limit,
        },
    )
    return [dict(row) for row in result.mappings().all()]


async def get_hpo_term_details(
    session: AsyncSession,
    *,
    hpo_id: str,
) -> dict[str, Any]:
    normalized_id = normalize_hpo_id(hpo_id)
    if not normalized_id:
        raise HTTPException(status_code=400, detail="Invalid HPO identifier")
    # Single round-trip via LATERAL/jsonb_agg (same pattern as list_hpo_admin_terms)
    # instead of four sequential queries.
    result = await session.execute(
        text(
            """
            SELECT
                t.hpo_id, t.label, t.definition, t.is_obsolete, t.replaced_by,
                t.release_version, t.release_date,
                COALESCE(s.synonyms, '[]'::jsonb) AS synonyms,
                COALESCE(p.parents, '[]'::jsonb) AS parents,
                COALESCE(c.children, '[]'::jsonb) AS children
            FROM hpo_term t
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(to_jsonb(synonym) ORDER BY synonym) AS synonyms
                FROM hpo_synonym
                WHERE hpo_id = t.hpo_id
            ) s ON TRUE
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object('hpo_id', e.parent_id, 'label', parent.label, 'relation', e.relation)
                    ORDER BY parent.label
                ) AS parents
                FROM hpo_edge e
                JOIN hpo_term parent ON parent.hpo_id = e.parent_id
                WHERE e.child_id = t.hpo_id
            ) p ON TRUE
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object('hpo_id', e.child_id, 'label', child.label, 'relation', e.relation)
                    ORDER BY child.label
                ) AS children
                FROM hpo_edge e
                JOIN hpo_term child ON child.hpo_id = e.child_id
                WHERE e.parent_id = t.hpo_id
            ) c ON TRUE
            WHERE t.hpo_id = :hpo_id
            """
        ),
        {"hpo_id": normalized_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="HPO term was not found")
    term = dict(row)
    term["synonyms"] = _text_list(term.get("synonyms"))
    term["parents"] = _relation_list(term.get("parents"))
    term["children"] = _relation_list(term.get("children"))
    return term


async def get_loaded_hpo_release(session: AsyncSession) -> dict[str, Any] | None:
    """The HPO ontology that is loaded: its release, release date and import time.

    No table holds the release: an import writes it onto every term in the file, and a
    term the new release no longer lists keeps the release it came with. So the loaded
    release is the one on the most recently written term. It is not the highest release
    string (an older release re-imported is what is loaded), nor the latest one recorded
    (an import from a file without a data-version header left the release unknown). The
    signed record (annotation manifest), the admin summary and the ranking-cache key all
    read it here, so they cannot name different releases.

    ``None`` when no ontology is loaded. A failed query raises: each caller decides what
    a failure means for it.
    """
    row = (
        await session.execute(
            text(
                "SELECT release_version, release_date, updated_at AS imported_at "
                "FROM hpo_term ORDER BY updated_at DESC LIMIT 1"
            )
        )
    ).mappings().first()
    return dict(row) if row else None


async def get_hpo_admin_summary(session: AsyncSession) -> dict[str, Any]:
    try:
        if not await _postgres_tables_available(session, ("hpo_term",)):
            return _empty_hpo_admin_summary()
        result = await session.execute(
            text(
                """
                SELECT
                    COUNT(*)::int AS total_terms,
                    COUNT(*) FILTER (WHERE is_obsolete = FALSE)::int AS active_terms,
                    COUNT(*) FILTER (WHERE is_obsolete = TRUE)::int AS obsolete_terms
                FROM hpo_term
                """
            )
        )
        summary = dict(result.mappings().one())
        loaded = await get_loaded_hpo_release(session) or {}
    except DBAPIError as exc:
        if is_missing_postgres_schema_error(exc):
            return _empty_hpo_admin_summary()
        raise
    # All three describe the latest import, as the signed record reads it.
    summary["release_version"] = loaded.get("release_version")
    summary["release_date"] = loaded.get("release_date")
    summary["last_sync_date"] = loaded.get("imported_at")
    summary["automatic_update_supported"] = False
    summary["ontology_loaded"] = int(summary.get("total_terms") or 0) > 0
    summary["ontology_path"] = _configured_hpo_ontology_path()
    return summary


async def list_hpo_admin_terms(
    session: AsyncSession,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    cleaned = (query or "").strip()
    bounded_limit = max(1, min(limit, 200))
    try:
        if not await _postgres_tables_available(session, HPO_ADMIN_TABLES):
            return []

        if cleaned:
            like_query = f"%{cleaned.lower()}%"
            prefix_query = f"{cleaned.lower()}%"
            result = await session.execute(
                text(
                    """
                    SELECT
                        t.hpo_id AS hpo_id,
                        t.label AS label,
                        t.definition AS definition,
                        t.is_obsolete AS is_obsolete,
                        t.replaced_by AS replaced_by,
                        t.release_version AS release_version,
                        t.release_date AS release_date,
                        COALESCE(s.synonyms, '[]'::jsonb) AS synonyms,
                        COALESCE(p.parents, '[]'::jsonb) AS parents,
                        COALESCE(c.children, '[]'::jsonb) AS children,
                        COALESCE(p.parent_count, 0)::int AS parent_count,
                        COALESCE(c.child_count, 0)::int AS child_count,
                        CASE
                            WHEN lower(t.hpo_id) = lower(:query) THEN 0
                            WHEN lower(t.hpo_id) LIKE :prefix_query THEN 1
                            WHEN lower(t.label) LIKE :prefix_query THEN 2
                            ELSE 3
                        END AS match_rank
                    FROM hpo_term t
                    LEFT JOIN LATERAL (
                        SELECT jsonb_agg(to_jsonb(synonym) ORDER BY synonym) AS synonyms
                        FROM hpo_synonym
                        WHERE hpo_id = t.hpo_id
                    ) s ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT
                            COUNT(*) AS parent_count,
                            jsonb_agg(
                                jsonb_build_object(
                                    'hpo_id', e.parent_id,
                                    'label', parent.label,
                                    'relation', e.relation
                                )
                                ORDER BY parent.label
                            ) AS parents
                        FROM hpo_edge e
                        JOIN hpo_term parent ON parent.hpo_id = e.parent_id
                        WHERE e.child_id = t.hpo_id
                    ) p ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT
                            COUNT(*) AS child_count,
                            jsonb_agg(
                                jsonb_build_object(
                                    'hpo_id', e.child_id,
                                    'label', child.label,
                                    'relation', e.relation
                                )
                                ORDER BY child.label
                            ) AS children
                        FROM hpo_edge e
                        JOIN hpo_term child ON child.hpo_id = e.child_id
                        WHERE e.parent_id = t.hpo_id
                    ) c ON TRUE
                    WHERE lower(t.hpo_id) LIKE :like_query
                       OR lower(t.label) LIKE :like_query
                       OR EXISTS (
                           SELECT 1 FROM hpo_synonym s
                           WHERE s.hpo_id = t.hpo_id
                             AND lower(s.synonym) LIKE :like_query
                       )
                    ORDER BY match_rank, t.is_obsolete, t.label
                    LIMIT :limit
                    """
                ),
                {
                    "query": cleaned,
                    "prefix_query": prefix_query,
                    "like_query": like_query,
                    "limit": bounded_limit,
                },
            )
        else:
            result = await session.execute(
                text(
                    """
                    SELECT
                        t.hpo_id AS hpo_id,
                        t.label AS label,
                        t.definition AS definition,
                        t.is_obsolete AS is_obsolete,
                        t.replaced_by AS replaced_by,
                        t.release_version AS release_version,
                        t.release_date AS release_date,
                        COALESCE(s.synonyms, '[]'::jsonb) AS synonyms,
                        COALESCE(p.parents, '[]'::jsonb) AS parents,
                        COALESCE(c.children, '[]'::jsonb) AS children,
                        COALESCE(p.parent_count, 0)::int AS parent_count,
                        COALESCE(c.child_count, 0)::int AS child_count
                    FROM hpo_term t
                    LEFT JOIN LATERAL (
                        SELECT jsonb_agg(to_jsonb(synonym) ORDER BY synonym) AS synonyms
                        FROM hpo_synonym
                        WHERE hpo_id = t.hpo_id
                    ) s ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT
                            COUNT(*) AS parent_count,
                            jsonb_agg(
                                jsonb_build_object(
                                    'hpo_id', e.parent_id,
                                    'label', parent.label,
                                    'relation', e.relation
                                )
                                ORDER BY parent.label
                            ) AS parents
                        FROM hpo_edge e
                        JOIN hpo_term parent ON parent.hpo_id = e.parent_id
                        WHERE e.child_id = t.hpo_id
                    ) p ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT
                            COUNT(*) AS child_count,
                            jsonb_agg(
                                jsonb_build_object(
                                    'hpo_id', e.child_id,
                                    'label', child.label,
                                    'relation', e.relation
                                )
                                ORDER BY child.label
                            ) AS children
                        FROM hpo_edge e
                        JOIN hpo_term child ON child.hpo_id = e.child_id
                        WHERE e.parent_id = t.hpo_id
                    ) c ON TRUE
                    ORDER BY t.is_obsolete, t.label
                    LIMIT :limit
                    """
                ),
                {"limit": bounded_limit},
            )
    except DBAPIError as exc:
        if is_missing_postgres_schema_error(exc):
            return []
        raise
    terms: list[dict[str, Any]] = []
    for row in result.mappings().all():
        item = dict(row)
        item["synonyms"] = _text_list(item.get("synonyms"))
        item["parents"] = _relation_list(item.get("parents"))
        item["children"] = _relation_list(item.get("children"))
        item.pop("match_rank", None)
        terms.append(item)
    return terms


async def preview_hpo_ontology_sync(
    session: AsyncSession,
    *,
    path: str | Path,
) -> dict[str, int]:
    ontology = await asyncio.to_thread(parse_hpo_ontology_path, path)
    if not ontology.terms:
        raise ValueError("HPO ontology import found no terms")
    existing: dict[str, dict[str, Any]] = {}
    try:
        if await _postgres_tables_available(session, ("hpo_term",)):
            existing_result = await session.execute(
                text(
                    """
                    SELECT hpo_id, label, definition, is_obsolete, replaced_by
                    FROM hpo_term
                    """
                )
            )
            existing = {str(row["hpo_id"]): dict(row) for row in existing_result.mappings().all()}
    except DBAPIError as exc:
        if not is_missing_postgres_schema_error(exc):
            raise
        existing = {}
    new_terms = 0
    changed_terms = 0
    unchanged_terms = 0
    for term in ontology.terms.values():
        current = existing.get(term.hpo_id)
        if current is None:
            new_terms += 1
            continue
        if (
            current.get("label") != term.label
            or current.get("definition") != term.definition
            or bool(current.get("is_obsolete")) != term.is_obsolete
            or current.get("replaced_by") != term.replaced_by
        ):
            changed_terms += 1
        else:
            unchanged_terms += 1
    removed_terms = len(set(existing) - set(ontology.terms))
    synonym_count = sum(len(term.synonyms) for term in ontology.terms.values())
    edge_count = len(
        [
            edge
            for edge in ontology.edges
            if edge.child_id in ontology.terms and edge.parent_id in ontology.terms
        ]
    )
    return {
        "terms": len(ontology.terms),
        "active_terms": sum(1 for term in ontology.terms.values() if not term.is_obsolete),
        "obsolete_terms": sum(1 for term in ontology.terms.values() if term.is_obsolete),
        "replaced_terms": sum(1 for term in ontology.terms.values() if term.replaced_by),
        "synonyms": synonym_count,
        "edges": edge_count,
        "closure_rows": len(compute_hpo_closure(ontology.terms, ontology.edges)),
        "new_terms": new_terms,
        "changed_terms": changed_terms,
        "unchanged_terms": unchanged_terms,
        "removed_from_release": removed_terms,
    }


async def sync_hpo_ontology(
    session: AsyncSession,
    *,
    path: str | Path,
    release_version: str | None = None,
    release_date: date | None = None,
    preview_only: bool = True,
) -> dict[str, Any]:
    preview = await preview_hpo_ontology_sync(session, path=path)
    release_metadata = await asyncio.to_thread(parse_hpo_ontology_release_metadata_path, path)
    effective_release_version = release_version or release_metadata.release_version
    effective_release_date = release_date or release_metadata.release_date
    if preview_only:
        current = await get_hpo_admin_summary(session)
        return {
            "preview_only": True,
            "release_version": effective_release_version,
            "release_date": effective_release_date,
            "current": current,
            "preview": preview,
            "imported": None,
        }
    if not await _postgres_tables_available(session, HPO_IMPORT_TABLES):
        raise HTTPException(
            status_code=503,
            detail=(
                "HPO database schema is not available. Restart the backend or apply "
                "the latest Postgres schema so the HPO tables are created, then retry synchronization."
            ),
        )
    imported = await import_hpo_ontology(
        session,
        path=path,
        release_version=effective_release_version,
        release_date=effective_release_date,
    )
    current = await get_hpo_admin_summary(session)
    return {
        "preview_only": False,
        "release_version": effective_release_version,
        "release_date": effective_release_date,
        "current": current,
        "preview": preview,
        "imported": imported,
    }


# What a phenotype's clinical audit event records of the annotation: the member, the term and
# every field an edit can change. The label is the term's name in the loaded HPO release.
_PHENOTYPE_AUDIT_FIELDS = ("sample_id", "hpo_id", "label", "status", "onset", "evidence", "source", "note")
# The free-text fields, which a summary names without quoting them.
_PHENOTYPE_TEXT_FIELDS = ("onset", "evidence", "source", "note")


async def _lock_family_phenotypes(session: AsyncSession, family_uuid: str) -> None:
    """One phenotype change of a family at a time until this transaction ends, so the state an
    audit event records as before is the one the change replaced."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"hpo:{family_uuid}"},
    )


def phenotype_audit_state(annotation: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The audited content of a phenotype annotation, each field as text or None; None is no
    annotation."""
    if annotation is None:
        return None
    state: dict[str, Any] = {}
    for name in _PHENOTYPE_AUDIT_FIELDS:
        value = annotation.get(name)
        text_value = str(value).strip() if value is not None else ""
        state[name] = text_value or None
    return state


def _phenotype_term(state: Mapping[str, Any]) -> str:
    label = state.get("label")
    return f"{state['hpo_id']} {label}" if label and label != state["hpo_id"] else str(state["hpo_id"])


def phenotype_change_summary(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> str:
    """The one-line summary of a phenotype change on the audit trail."""
    if before is None and after is not None:
        return f"Phenotype added for {after['sample_id']}: {_phenotype_term(after)} ({after['status']})"
    if after is None and before is not None:
        return f"Phenotype removed for {before['sample_id']}: {_phenotype_term(before)} ({before['status']})"
    assert before is not None and after is not None
    changes: list[str] = []
    if before["hpo_id"] != after["hpo_id"]:
        changes.append(f"term {_phenotype_term(before)} → {_phenotype_term(after)}")
    if before["status"] != after["status"]:
        changes.append(f"status {before['status']} → {after['status']}")
    for name in _PHENOTYPE_TEXT_FIELDS:
        was, now = before.get(name), after.get(name)
        if was != now:
            changes.append(f"{name} {'added' if not was else 'removed' if not now else 'edited'}")
    return f"Phenotype changed for {after['sample_id']}: {_phenotype_term(after)}: {'; '.join(changes)}"


async def _record_phenotype_change(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    annotation_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    user: CurrentUser,
) -> None:
    """Record a phenotype change on the family's hash-chained clinical audit trail, in the
    transaction of the change (SEC-1): what it was and what it is, and a summary that names
    the fields that changed. A save that changes nothing records nothing."""
    before_state, after_state = phenotype_audit_state(before), phenotype_audit_state(after)
    if before_state == after_state:
        return
    changed = after_state or before_state
    assert changed is not None
    await record_clinical_event(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        variant_id=None,
        actor=_audit_actor(user),
        actor_id=getattr(user, "id", None),
        action="phenotype",
        summary=phenotype_change_summary(before_state, after_state),
        before=before_state,
        after=after_state,
        metadata={"sample_id": changed["sample_id"], "annotation_id": annotation_id},
    )


def _audit_actor(user: Any) -> str:
    """The name the clinical audit trail records for a user, as for every clinical event."""
    return getattr(user, "username", None) or getattr(user, "email", "") or "unknown"


# --- Phenotypes an import changes ------------------------------------------------------------
#
# A package import brings a family's phenotypes in (and a failed one puts them back as they
# were, or removes the family it had created), and a PED upload or the Family Builder that
# replaces a family deletes them with it. Each such change is on the family's clinical audit
# trail like an edit on screen: ``phenotype`` events, in the transaction of the change, under
# the family's phenotype lock. One event per member whose phenotypes changed, so the trail
# reads as the edits do (each of their events concerns one member), while an import of many
# terms does not bury it in one event per term: before is each of the member's phenotypes the
# change altered or removed, as it was, and after each it added or altered, as it is.

# How an import came to change a family's phenotypes (``source`` in the events' metadata).
PACKAGE_IMPORT = "package_import"
PED_UPLOAD = "ped_upload"
FAMILY_BUILDER = "family_builder"

# What it did to them (``change`` in the metadata).
PHENOTYPES_IMPORTED = "imported"  # a package's phenotypes added or changed
PHENOTYPES_PUT_BACK = "put_back"  # a failed import's restore put them back as they were
PHENOTYPES_ROLLED_BACK = "rolled_back"  # deleted with the family a failed import had created
PHENOTYPES_REPLACED = "replaced"  # deleted with the family a PED upload or the Family Builder replaced

# A summary names at most this many changes; the event's before and after hold them all.
_MAX_SUMMARY_PHENOTYPES = 5

# A family's phenotype by member, term and status: what an import writes it by.
PhenotypeKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class PhenotypeImport:
    """An import that changes a family's phenotypes, as their clinical audit events name it:
    who ran it, how (``via``: ``PACKAGE_IMPORT``, ``PED_UPLOAD`` or ``FAMILY_BUILDER``), what
    it read (``source``: the package's folder or bucket URI, or the uploaded PED file's name;
    ``files``: the package's phenotype files) and the import job that ran it, if any."""

    user: CurrentUser
    via: str
    source: str | None = None
    files: tuple[str, ...] = ()
    job_id: str | None = None


async def family_phenotype_states(session: AsyncSession, family_uuid: str) -> dict[PhenotypeKey, dict[str, Any]]:
    """The family's phenotypes as their audit events record them (``phenotype_audit_state``),
    by member, term and status."""
    states: dict[PhenotypeKey, dict[str, Any]] = {}
    for annotation in await list_family_hpo_annotations(session, family_uuid=family_uuid):
        state = phenotype_audit_state(annotation)
        assert state is not None
        states[(str(state["sample_id"]), str(state["hpo_id"]), str(state["status"]))] = state
    return states


async def lock_family_phenotype_states(
    session: AsyncSession, family_uuid: str
) -> dict[PhenotypeKey, dict[str, Any]]:
    """Take the family's phenotype lock, the one each edit takes, until this transaction ends,
    and read the family's phenotypes: what a change made in the transaction replaces."""
    await _lock_family_phenotypes(session, family_uuid)
    return await family_phenotype_states(session, family_uuid)


def _without_label(state: Mapping[str, Any]) -> dict[str, Any]:
    # The label is the term's name in the loaded HPO release, not part of the annotation.
    return {name: value for name, value in state.items() if name != "label"}


def imported_phenotype_changes(
    before: Mapping[PhenotypeKey, Mapping[str, Any]],
    after: Mapping[PhenotypeKey, Mapping[str, Any]],
) -> dict[str, dict[str, list[Any]]]:
    """What changed between two readings of a family's phenotypes, per member whose
    phenotypes changed, in a fixed order: ``before``, each annotation that changed or went, as
    it was; ``after``, each that changed or came, as it is; ``phrases``, one per change."""
    changes: dict[str, dict[str, list[Any]]] = {}
    for key in sorted(before.keys() | after.keys()):
        was, now = before.get(key), after.get(key)
        if was is not None and now is not None and _without_label(was) == _without_label(now):
            continue
        member = changes.setdefault(key[0], {"before": [], "after": [], "phrases": []})
        if was is not None:
            member["before"].append(dict(was))
        if now is not None:
            member["after"].append(dict(now))
        if was is None:
            assert now is not None
            member["phrases"].append(f"added {_phenotype_term(now)} ({now['status']})")
        elif now is None:
            member["phrases"].append(f"removed {_phenotype_term(was)} ({was['status']})")
        else:
            fields = [
                f"{name} {'added' if not was.get(name) else 'removed' if not now.get(name) else 'edited'}"
                for name in _PHENOTYPE_TEXT_FIELDS
                if was.get(name) != now.get(name)
            ]
            member["phrases"].append(f"changed {_phenotype_term(now)} ({now['status']}): {', '.join(fields)}")
    return changes


def _bounded_phrases(phrases: list[str]) -> str:
    if len(phrases) > _MAX_SUMMARY_PHENOTYPES:
        hidden = len(phrases) - _MAX_SUMMARY_PHENOTYPES
        phrases = [*phrases[:_MAX_SUMMARY_PHENOTYPES], f"and {hidden} more"]
    return "; ".join(phrases)


def _package_name(source: str | None) -> str:
    """The package as a summary names it: its folder's name (the whole path, or bucket URI,
    is in the event's metadata)."""
    text_value = visible_text(source or "").rstrip("/")
    return text_value.rsplit("/", 1)[-1] or "unknown package"


def imported_phenotypes_summary(sample_id: str, phrases: list[str], origin: PhenotypeImport, change: str) -> str:
    """The one-line summary of what an import changed of a member's phenotypes."""
    changes = _bounded_phrases(phrases)
    package = f"a package import of {_package_name(origin.source)}"
    if change == PHENOTYPES_IMPORTED:
        files = f" ({', '.join(visible_text(name) for name in origin.files)})" if origin.files else ""
        return f"Phenotypes imported for {sample_id} by {package}{files}: {changes}"
    if change == PHENOTYPES_PUT_BACK:
        return f"Phenotypes put back for {sample_id} after a failed {package.removeprefix('a ')}: {changes}"
    if change == PHENOTYPES_ROLLED_BACK:
        return (
            f"Phenotypes removed for {sample_id} with the family a failed "
            f"{package.removeprefix('a ')} had created: {changes}"
        )
    if origin.via == PED_UPLOAD:
        upload = f"a PED upload ({visible_text(origin.source)})" if origin.source else "a PED upload"
        return f"Phenotypes removed for {sample_id}: the family was replaced by {upload}: {changes}"
    return f"Phenotypes removed for {sample_id}: the family was replaced in the Family Builder: {changes}"


def _imported_phenotypes_metadata(sample_id: str, origin: PhenotypeImport, change: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"sample_id": sample_id, "source": origin.via, "change": change}
    if origin.source:
        metadata["package" if origin.via == PACKAGE_IMPORT else "file"] = visible_text(origin.source)
    if origin.files:
        metadata["files"] = [visible_text(name) for name in origin.files]
    if origin.job_id:
        metadata["job_id"] = origin.job_id
    return metadata


def imported_phenotypes_side(sample_id: str, annotations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One side of an import's ``phenotype`` event, as the trail serves every event's before
    and after (``ClinicalAuditEventOut``: an object, or none): the member and its annotations
    the change touched (``phenotype_audit_state`` each). None when it touched none, as an
    edit's event has no before for a term added and no after for one removed."""
    if not annotations:
        return None
    return {"sample_id": sample_id, "phenotypes": annotations}


async def record_imported_phenotype_changes(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    before: Mapping[PhenotypeKey, Mapping[str, Any]],
    after: Mapping[PhenotypeKey, Mapping[str, Any]],
    origin: PhenotypeImport,
    change: str,
) -> int:
    """Record what an import changed of a family's phenotypes, ``before`` to ``after``, on the
    family's hash-chained clinical audit trail, in the import's transaction (no commit): one
    ``phenotype`` event per member whose phenotypes it changed, as the user who ran it, its
    before and after each the member with the annotations it altered or removed, as they were,
    and those it added or altered, as they are (``imported_phenotypes_side``). Returns how many
    events it wrote: an import that changed no phenotype writes none."""
    changes = imported_phenotype_changes(before, after)
    for sample_id, member in changes.items():
        await record_clinical_event(
            session,
            family_uuid=family_uuid,
            family_identifier=family_identifier,
            variant_id=None,
            actor=_audit_actor(origin.user),
            actor_id=getattr(origin.user, "id", None),
            action="phenotype",
            summary=imported_phenotypes_summary(sample_id, member["phrases"], origin, change),
            before=imported_phenotypes_side(sample_id, member["before"]),
            after=imported_phenotypes_side(sample_id, member["after"]),
            metadata=_imported_phenotypes_metadata(sample_id, origin, change),
        )
    return len(changes)


async def record_phenotypes_deleted_with_family(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    origin: PhenotypeImport,
    change: str,
) -> int:
    """Before a family is deleted (``change``: ``PHENOTYPES_ROLLED_BACK`` or
    ``PHENOTYPES_REPLACED``), record the phenotypes it goes with as removed, under the family's
    phenotype lock, in the transaction of the deletion (no commit). The deletion unlinks the
    events from the family as it does every event of its trail; the chain, on the family's ID,
    keeps them."""
    before = await lock_family_phenotype_states(session, family_uuid)
    return await record_imported_phenotype_changes(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        before=before,
        after={},
        origin=origin,
        change=change,
    )


async def create_individual_hpo_annotation(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    sample_id: str,
    payload: Any,
    user: CurrentUser,
) -> dict[str, Any]:
    """Add a phenotype to a family member, or update the one of that term and status they
    have, and record the change on the family's clinical audit trail."""
    await _ensure_hpo_schema_available(session)
    await _lock_family_phenotypes(session, family_uuid)
    sample_uuid = await _sample_uuid_for(session, family_uuid=family_uuid, sample_id=sample_id)
    hpo_id = normalize_hpo_id(str(getattr(payload, "hpo_id", None) or ""))
    if not hpo_id:
        raise HTTPException(status_code=400, detail="Invalid HPO identifier")
    if hpo_id not in await _existing_hpo_terms(session, [hpo_id]):
        raise HTTPException(status_code=404, detail="HPO term was not found")
    status = normalize_annotation_status(getattr(payload, "status", None)) or "present"
    # The member's annotation of this term and status, which the insert below updates.
    held = await session.execute(
        text(
            """
            SELECT id::text
            FROM individual_hpo
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND sample_id = CAST(:sample_uuid AS uuid)
              AND hpo_id = :hpo_id
              AND status = :status
            """
        ),
        {"family_uuid": family_uuid, "sample_uuid": sample_uuid, "hpo_id": hpo_id, "status": status},
    )
    held_id = held.scalar_one_or_none()
    before = (
        await _annotation_by_id(session, family_uuid=family_uuid, annotation_id=str(held_id))
        if held_id is not None
        else None
    )
    result = await session.execute(
        text(
            """
            INSERT INTO individual_hpo (
                family_id, sample_id, hpo_id, status, onset, evidence, source, note, updated_at
            )
            VALUES (
                CAST(:family_uuid AS uuid), CAST(:sample_uuid AS uuid), :hpo_id, :status,
                :onset, :evidence, :source, :note, timezone('utc', now())
            )
            ON CONFLICT (family_id, sample_id, hpo_id, status) DO UPDATE SET
                onset = EXCLUDED.onset,
                evidence = EXCLUDED.evidence,
                source = EXCLUDED.source,
                note = EXCLUDED.note,
                updated_at = timezone('utc', now())
            RETURNING id::text
            """
        ),
        {
            "family_uuid": family_uuid,
            "sample_uuid": sample_uuid,
            "hpo_id": hpo_id,
            "status": status,
            "onset": getattr(payload, "onset", None),
            "evidence": getattr(payload, "evidence", None),
            "source": getattr(payload, "source", None) or "manual",
            "note": getattr(payload, "note", None),
        },
    )
    annotation_id = str(result.scalar_one())
    await mark_family_hpo_annotations_stale(
        session,
        family_uuid=family_uuid,
        sample_id=sample_id,
        reason="hpo_annotation_created",
    )
    annotation = await _annotation_by_id(session, family_uuid=family_uuid, annotation_id=annotation_id)
    assert annotation is not None
    await _record_phenotype_change(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        annotation_id=annotation_id,
        before=before,
        after=annotation,
        user=user,
    )
    await session.commit()
    return annotation


async def update_individual_hpo_annotation(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    annotation_id: str,
    payload: Any,
    user: CurrentUser,
) -> dict[str, Any]:
    """Change a family member's phenotype and record the change on the family's clinical
    audit trail."""
    await _ensure_hpo_schema_available(session)
    await _lock_family_phenotypes(session, family_uuid)
    existing = await _annotation_by_id(session, family_uuid=family_uuid, annotation_id=annotation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=_ANNOTATION_NOT_FOUND)
    annotation_id = str(existing["id"])
    status_value = _payload_value(payload, "status")
    status = normalize_annotation_status(status_value) if status_value is not None else existing["status"]
    if not status:
        raise HTTPException(status_code=400, detail="Annotation status must be present, absent, or unknown")
    hpo_id_value = _payload_value(payload, "hpo_id")
    hpo_id = normalize_hpo_id(str(hpo_id_value)) if hpo_id_value is not None else existing["hpo_id"]
    if not hpo_id:
        raise HTTPException(status_code=400, detail="Invalid HPO identifier")
    if hpo_id != existing["hpo_id"] and hpo_id not in await _existing_hpo_terms(session, [hpo_id]):
        raise HTTPException(status_code=404, detail="HPO term was not found")

    try:
        await session.execute(
            text(
                """
                UPDATE individual_hpo
                SET hpo_id = :hpo_id,
                    status = :status,
                    onset = :onset,
                    evidence = :evidence,
                    source = :source,
                    note = :note,
                    updated_at = timezone('utc', now())
                WHERE family_id = CAST(:family_uuid AS uuid)
                  AND id = CAST(:annotation_id AS uuid)
                """
            ),
            {
                "family_uuid": family_uuid,
                "annotation_id": annotation_id,
                "hpo_id": hpo_id,
                "status": status,
                "onset": _payload_value(payload, "onset", existing["onset"]),
                "evidence": _payload_value(payload, "evidence", existing["evidence"]),
                "source": _payload_value(payload, "source", existing["source"]) or "manual",
                "note": _payload_value(payload, "note", existing["note"]),
            },
        )
    except DBAPIError as exc:
        await session.rollback()
        orig = getattr(exc, "orig", None)
        text_repr = " ".join(
            str(p)
            for p in (type(exc).__name__, exc, type(orig).__name__ if orig is not None else "", orig or "")
        ).lower()
        if "uniqueviolation" in text_repr or "duplicate key" in text_repr:
            raise HTTPException(
                status_code=409,
                detail="An annotation with this HPO term and status already exists for this sample",
            ) from exc
        raise
    await mark_family_hpo_annotations_stale(
        session,
        family_uuid=family_uuid,
        sample_id=str(existing["sample_id"]),
        reason="hpo_annotation_updated",
    )
    updated = await _annotation_by_id(session, family_uuid=family_uuid, annotation_id=annotation_id)
    assert updated is not None
    await _record_phenotype_change(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        annotation_id=annotation_id,
        before=existing,
        after=updated,
        user=user,
    )
    await session.commit()
    return updated


async def delete_individual_hpo_annotation(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_identifier: str,
    annotation_id: str,
    user: CurrentUser,
) -> None:
    """Remove a family member's phenotype and record the removal on the family's clinical
    audit trail."""
    annotation_uuid = require_uuid(annotation_id, _ANNOTATION_NOT_FOUND, status_code=404)
    await _lock_family_phenotypes(session, family_uuid)
    existing = await _annotation_by_id(session, family_uuid=family_uuid, annotation_id=annotation_uuid)
    if existing is None:
        raise HTTPException(status_code=404, detail=_ANNOTATION_NOT_FOUND)
    result = await session.execute(
        text(
            """
            DELETE FROM individual_hpo
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND id = CAST(:annotation_id AS uuid)
            """
        ),
        {"family_uuid": family_uuid, "annotation_id": annotation_uuid},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=_ANNOTATION_NOT_FOUND)
    await mark_family_hpo_annotations_stale(
        session,
        family_uuid=family_uuid,
        sample_id=None,
        reason="hpo_annotation_deleted",
    )
    await _record_phenotype_change(
        session,
        family_uuid=family_uuid,
        family_identifier=family_identifier,
        annotation_id=annotation_uuid,
        before=existing,
        after=None,
        user=user,
    )
    await session.commit()


async def query_family_hpo_annotations(
    session: AsyncSession,
    *,
    family_uuid: str,
    hpo_id: str,
    include_descendants: bool = True,
) -> dict[str, Any]:
    await _ensure_hpo_schema_available(session)
    normalized_id = normalize_hpo_id(hpo_id)
    if not normalized_id:
        raise HTTPException(status_code=400, detail="Invalid HPO identifier")
    if normalized_id not in await _existing_hpo_terms(session, [normalized_id]):
        raise HTTPException(status_code=404, detail="HPO term was not found")
    if include_descendants:
        term_clause = """
        ih.hpo_id IN (
            SELECT hpo_id
            FROM hpo_closure
            WHERE ancestor_id = :hpo_id
        )
        """
    else:
        term_clause = "ih.hpo_id = :hpo_id"
    result = await session.execute(
        text(
            f"""
            SELECT
                ih.id::text AS id,
                s.sample_id AS sample_id,
                ih.hpo_id AS hpo_id,
                COALESCE(t.label, ih.hpo_id) AS label,
                t.definition AS definition,
                ih.status AS status,
                ih.onset AS onset,
                ih.evidence AS evidence,
                ih.source AS source,
                ih.note AS note,
                ih.created_at AS created_at,
                ih.updated_at AS updated_at
            FROM individual_hpo ih
            JOIN samples s ON s.id = ih.sample_id
            LEFT JOIN hpo_term t ON t.hpo_id = ih.hpo_id
            WHERE ih.family_id = CAST(:family_uuid AS uuid)
              AND ih.status = 'present'
              AND {term_clause}
            ORDER BY s.sample_id, COALESCE(t.label, ih.hpo_id)
            """
        ),
        {"family_uuid": family_uuid, "hpo_id": normalized_id},
    )
    annotations = [dict(row) for row in result.mappings().all()]
    return {
        "hpo_id": normalized_id,
        "include_descendants": include_descendants,
        "sample_ids": sorted({str(row["sample_id"]) for row in annotations}),
        "annotations": annotations,
    }


async def import_family_hpo_annotations(
    session: AsyncSession,
    *,
    family_uuid: str,
    family_id: str,
    sample_uuids_by_id: Mapping[str, str],
    rows: Iterable[HpoAnnotationImportRow],
    issues: Iterable[HpoAnnotationImportIssue] = (),
    origin: PhenotypeImport,
) -> dict[str, Any]:
    """Write a package's phenotype rows to the family (each one added, or the member's
    annotation of its term and status updated), and record what that changed on the family's
    clinical audit trail as made by ``origin``, in the same transaction, under the family's
    phenotype lock. A row naming another family, an unknown member or a term the HPO release
    does not hold is skipped with an issue."""
    row_list = list(rows)
    issue_list = list(issues)
    existing_terms = await _existing_hpo_terms(session, [row.hpo_id for row in row_list])
    imported = 0
    skipped = 0
    imported_by_sample: dict[str, int] = {}
    # Accumulate validated rows and upsert them in a single batched statement
    # instead of one round-trip per row. Keyed by the ON CONFLICT target so a
    # duplicate within one import collapses to the last value (matching the
    # per-row upsert order) and the batch never touches the same row twice.
    insert_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in row_list:
        if row.family_id and row.family_id != family_id:
            skipped += 1
            issue_list.append(
                HpoAnnotationImportIssue(
                    line_no=row.line_no,
                    code="phenotype_family_mismatch",
                    message=f"Phenotype row family_id '{row.family_id}' does not match family '{family_id}'",
                    sample_id=row.individual_id,
                    hpo_id=row.hpo_id,
                )
            )
            continue
        sample_uuid = sample_uuids_by_id.get(row.individual_id)
        if sample_uuid is None:
            skipped += 1
            issue_list.append(
                HpoAnnotationImportIssue(
                    line_no=row.line_no,
                    code="phenotype_sample_unknown",
                    message=f"Phenotype row references unknown individual '{row.individual_id}'",
                    sample_id=row.individual_id,
                    hpo_id=row.hpo_id,
                )
            )
            continue
        if row.hpo_id not in existing_terms:
            skipped += 1
            issue_list.append(
                HpoAnnotationImportIssue(
                    line_no=row.line_no,
                    code="phenotype_hpo_unknown",
                    message=f"HPO term '{row.hpo_id}' is not loaded in the HPO ontology table",
                    sample_id=row.individual_id,
                    hpo_id=row.hpo_id,
                )
            )
            continue
        insert_rows[(sample_uuid, row.hpo_id, row.status)] = {
            "family_uuid": family_uuid,
            "sample_uuid": sample_uuid,
            "hpo_id": row.hpo_id,
            "status": row.status,
            "onset": row.onset,
            "evidence": row.evidence,
            "source": row.source or "family_package",
            "note": row.note,
        }
        imported += 1
        imported_by_sample[row.individual_id] = imported_by_sample.get(row.individual_id, 0) + 1
    if insert_rows:
        # What the rows replace, read under the lock each edit of the family's phenotypes
        # takes, so the audit events say what this import changed and nothing else.
        phenotypes_before = await lock_family_phenotype_states(session, family_uuid)
        await session.execute(
            text(
                """
                INSERT INTO individual_hpo (
                    family_id, sample_id, hpo_id, status, onset, evidence, source, note, updated_at
                )
                VALUES (
                    CAST(:family_uuid AS uuid), CAST(:sample_uuid AS uuid), :hpo_id, :status,
                    :onset, :evidence, :source, :note, timezone('utc', now())
                )
                ON CONFLICT (family_id, sample_id, hpo_id, status) DO UPDATE SET
                    onset = EXCLUDED.onset,
                    evidence = EXCLUDED.evidence,
                    source = EXCLUDED.source,
                    note = EXCLUDED.note,
                    updated_at = timezone('utc', now())
                """
            ),
            list(insert_rows.values()),
        )
        await record_imported_phenotype_changes(
            session,
            family_uuid=family_uuid,
            family_identifier=family_id,
            before=phenotypes_before,
            after=await family_phenotype_states(session, family_uuid),
            origin=origin,
            change=PHENOTYPES_IMPORTED,
        )
    await session.commit()
    return {
        "imported": imported,
        "skipped": skipped,
        "samples": imported_by_sample,
        "errors": [_issue_dict(issue) for issue in issue_list],
    }
