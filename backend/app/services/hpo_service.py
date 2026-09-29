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

from ..core.config import settings
from ..core.sql import is_missing_postgres_schema_error

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
DEFAULT_HPO_ONTOLOGY_URL = "https://purl.obolibrary.org/obo/hp.obo"
# hp.obo is ~10 MB; the ceiling only stops a runaway response exhausting the disk (#522).
MAX_HPO_ONTOLOGY_BYTES = 256 * 1024 * 1024
_HPO_DOWNLOAD_CHUNK = 1 << 20

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


def parse_hpo_ontology_release_metadata_path(path: str | Path) -> HpoOntologyReleaseMetadata:
    resolved = Path(path)
    text_value = resolved.read_text(encoding="utf-8")
    source_format = "json" if resolved.suffix.lower() == ".json" else "obo"
    return parse_hpo_ontology_release_metadata_text(text_value, source_format=source_format)


def parse_hpo_ontology_path(path: str | Path) -> ParsedHpoOntology:
    resolved = Path(path)
    text_value = resolved.read_text(encoding="utf-8")
    if resolved.suffix.lower() == ".json":
        return parse_hpo_json_text(text_value)
    return parse_hpo_obo_text(text_value)


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

    ``/hpo/import`` and ``/admin/hpo/sync`` accept a raw filesystem path from the request
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


def _download_hpo_ontology_file(
    url: str,
    candidate_paths: list[Path],
    *,
    expected_sha256: str = "",
    max_bytes: int = MAX_HPO_ONTOLOGY_BYTES,
) -> Path:
    """Download the HPO ontology over HTTPS, bounded, checked, and written atomically.

    The bootstrap trusts this file as the phenotype ontology, so it is fetched over TLS
    only, capped in size, must look like an OBO file (an HTML error page would otherwise
    be imported as an empty ontology), is verified against ``expected_sha256`` when one is
    pinned, and never leaves a partial file behind (#522).
    """
    if urlparse(url).scheme.lower() != "https":
        raise RuntimeError(f"Refusing to download the HPO ontology over a non-HTTPS URL: {url}")
    request = Request(url, headers={"User-Agent": "CoGA-HPO-bootstrap/1.0"})
    try:
        with urlopen(request, timeout=60) as response:
            payload = _read_bounded(response, max_bytes)
    except Exception as exc:
        raise RuntimeError(f"Failed to download HPO ontology from {url}: {exc}") from exc
    if not payload.lstrip().startswith(b"format-version:"):
        raise RuntimeError(f"The download from {url} is not an OBO ontology file")
    digest = hashlib.sha256(payload).hexdigest()
    pinned = expected_sha256.strip().lower()
    if pinned and digest != pinned:
        raise RuntimeError(
            f"HPO ontology from {url} has SHA-256 {digest}, expected {pinned} (HPO_ONTOLOGY_SHA256)"
        )
    logger.info("Downloaded the HPO ontology from %s (%d bytes, sha256 %s)", url, len(payload), digest)

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
    ontology_url: str = DEFAULT_HPO_ONTOLOGY_URL,
    download_if_missing: bool = True,
    enabled: bool = True,
) -> dict[str, Any] | None:
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
                ontology_url,
                candidate_paths,
                expected_sha256=settings.hpo_ontology_sha256,
            )
            logger.info("Downloaded HPO ontology to %s", resolved_path)
        except Exception:
            logger.exception("Failed to download HPO ontology from %s", ontology_url)
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

    # Reading and parsing the ontology file is CPU and disk work: keep it off the event loop (#527).
    metadata = await asyncio.to_thread(parse_hpo_ontology_release_metadata_path, resolved_path)
    try:
        imported = await import_hpo_ontology(
            session,
            path=resolved_path,
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
    release_version: str | None = None,
    release_date: date | None = None,
    commit: bool = True,
) -> dict[str, int]:
    ontology = await asyncio.to_thread(parse_hpo_ontology_path, path)
    if not ontology.terms:
        raise ValueError("HPO ontology import found no terms")
    release_metadata = await asyncio.to_thread(parse_hpo_ontology_release_metadata_path, path)
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

    if commit:
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


async def _annotation_by_id(
    session: AsyncSession,
    *,
    family_uuid: str,
    annotation_id: str,
) -> dict[str, Any] | None:
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
            {"family_uuid": family_uuid, "annotation_id": annotation_id},
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
            {"family_uuid": family_uuid, "annotation_id": annotation_id},
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
    await session.execute(
        text(
            """
            UPDATE families
            SET metadata = jsonb_set(
                jsonb_set(
                    COALESCE(metadata, '{}'::jsonb),
                    '{derived_data_status,hpo_annotations}',
                    jsonb_build_object(
                        'state', 'stale',
                        'reason', CAST(:reason AS text),
                        'sample_id', CAST(:sample_id AS text),
                        'updated_at', timezone('utc', now())
                    ),
                    TRUE
                ),
                '{derived_data_status,updated_at}',
                to_jsonb(timezone('utc', now())),
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
                    COUNT(*) FILTER (WHERE is_obsolete = TRUE)::int AS obsolete_terms,
                    MAX(release_date) AS release_date,
                    MAX(updated_at) AS last_sync_date
                FROM hpo_term
                """
            )
        )
        summary = dict(result.mappings().one())
        release_result = await session.execute(
            text(
                """
                SELECT release_version
                FROM hpo_term
                WHERE release_version IS NOT NULL
                ORDER BY updated_at DESC, release_version DESC
                LIMIT 1
                """
            )
        )
    except DBAPIError as exc:
        if is_missing_postgres_schema_error(exc):
            return _empty_hpo_admin_summary()
        raise
    summary["release_version"] = release_result.scalar_one_or_none()
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


async def create_individual_hpo_annotation(
    session: AsyncSession,
    *,
    family_uuid: str,
    sample_id: str,
    payload: Any,
    commit: bool = True,
) -> dict[str, Any]:
    await _ensure_hpo_schema_available(session)
    sample_uuid = await _sample_uuid_for(session, family_uuid=family_uuid, sample_id=sample_id)
    hpo_id = normalize_hpo_id(str(getattr(payload, "hpo_id", None) or ""))
    if not hpo_id:
        raise HTTPException(status_code=400, detail="Invalid HPO identifier")
    if hpo_id not in await _existing_hpo_terms(session, [hpo_id]):
        raise HTTPException(status_code=404, detail="HPO term was not found")
    status = normalize_annotation_status(getattr(payload, "status", None)) or "present"
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
    if commit:
        await session.commit()
    annotation = await _annotation_by_id(session, family_uuid=family_uuid, annotation_id=annotation_id)
    assert annotation is not None
    return annotation


async def update_individual_hpo_annotation(
    session: AsyncSession,
    *,
    family_uuid: str,
    annotation_id: str,
    payload: Any,
    commit: bool = True,
) -> dict[str, Any]:
    await _ensure_hpo_schema_available(session)
    existing = await _annotation_by_id(session, family_uuid=family_uuid, annotation_id=annotation_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="HPO annotation was not found")
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
    if commit:
        await session.commit()
    updated = await _annotation_by_id(session, family_uuid=family_uuid, annotation_id=annotation_id)
    assert updated is not None
    return updated


async def delete_individual_hpo_annotation(
    session: AsyncSession,
    *,
    family_uuid: str,
    annotation_id: str,
    commit: bool = True,
) -> None:
    result = await session.execute(
        text(
            """
            DELETE FROM individual_hpo
            WHERE family_id = CAST(:family_uuid AS uuid)
              AND id = CAST(:annotation_id AS uuid)
            """
        ),
        {"family_uuid": family_uuid, "annotation_id": annotation_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="HPO annotation was not found")
    await mark_family_hpo_annotations_stale(
        session,
        family_uuid=family_uuid,
        sample_id=None,
        reason="hpo_annotation_deleted",
    )
    if commit:
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
    commit: bool = True,
) -> dict[str, Any]:
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
    if commit:
        await session.commit()
    return {
        "imported": imported,
        "skipped": skipped,
        "samples": imported_by_sample,
        "errors": [_issue_dict(issue) for issue in issue_list],
    }
