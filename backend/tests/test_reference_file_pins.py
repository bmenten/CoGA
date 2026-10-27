"""The reference files a start loads are the shipped ones, byte for byte (TRACE-14).

The HPO download and the STRchive file were pinned by SHA-256 (OPS-6, CLIN-31), but two files a
start loads were not checked:

- the segmental-duplication file (``REFERENCE_SEGMENTAL_DUPLICATIONS_PATH``, on Cloud Run the
  reference-data bucket's copy), ClinGen's V2.1 recurrent CNV file for GRCh38 as shipped in
  ``data/ref-data/clinical_cnv_syndromes_hg38_bundle/``;
- an HPO ontology file already in place: only the download was checked.

Both are held to their pin now, by the STRchive rule: the shipped, validated file is the
source, an identical copy passes wherever it lies, and any other file is refused before
anything is written, with one logged error that names the file, both SHA-256 digests and the
fix. Unlike STRchive, a refusal skips only that import and the start goes on: the
segmental duplications are a track shown for orientation that no classification reads, the HPO
download is refused the same way (OPS-6), and both imports only fill an empty table, so a
refusal never changes data a running system shows. The clinical CNV knowledgebase build's pin
of the same ClinGen file is tested in test_clinical_cnv_knowledgebase_sources.py. Nothing here
goes to the network.

The segmental-duplication file holds GRCh38 coordinates, so the start loads it into a GRCh38
assembly only, whichever assembly ``REFERENCE_BOOTSTRAP_ASSEMBLY_NAME`` names (OPS-17).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.app import main as app_main
from backend.app.core import config
from backend.app.core.config import settings
from backend.app.services import hpo_service
from backend.app.services import reference_metadata_service as rms

sys.path.append(str(Path(__file__).resolve().parents[2] / "scripts"))
import clinical_cnv_knowledgebase as kb_script

_REPO = Path(__file__).resolve().parents[2]
_SHIPPED_SEGDUPS = _REPO / "data" / "ref-data" / "clinical_cnv_syndromes_hg38_bundle" / "ClinGen_recurrent_CNV_V2.1-hg38.bed"
_VENDORED_HPO = _REPO / "data" / "ref-data" / "hpo" / "hp.obo"
_TF08 = _REPO / "docs" / "regulatory" / "TF-08-soup-register.md"
_PROVENANCE_NOTE = _SHIPPED_SEGDUPS.with_name("COGA-PROVENANCE.md")
# Another HPO release, as a bucket might hold one.
_LATER_HPO_RELEASE = (
    b"format-version: 1.2\ndata-version: hp/releases/2026-09-01\n\n[Term]\nid: HP:0000001\nname: All\n"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- The pins ---------------------------------------------------------------------------------------


def test_the_segmental_duplication_pin_is_the_shipped_file_and_the_build_pins_the_same_file() -> None:
    assert rms.REPO_SEGMENTAL_DUPLICATIONS_PATH == _SHIPPED_SEGDUPS
    assert config.SEGMENTAL_DUPLICATIONS_PINNED_SHA256 == _sha256(_SHIPPED_SEGDUPS)
    # The knowledgebase build reads ClinGen's own copy of the same GRCh38 file.
    assert kb_script.CLINGEN_RECURRENT_CNV_SHA256["GRCh38"] == config.SEGMENTAL_DUPLICATIONS_PINNED_SHA256
    assert kb_script.CLINGEN_RECURRENT_CNV["GRCh38"].endswith("/" + _SHIPPED_SEGDUPS.name)
    # No setting overrides either pin: they move only with the file, in a reviewed change.
    assert not [name for name in config.Settings.model_fields if "sha256" in name]


def test_tf08_and_the_note_beside_the_file_name_the_pins() -> None:
    for record in (_TF08, _PROVENANCE_NOTE):
        text = record.read_text(encoding="utf-8")
        for name in ("SEGMENTAL_DUPLICATIONS_PINNED_SHA256", "CLINGEN_RECURRENT_CNV_SHA256"):
            assert name in text, f"{record.name} does not name {name}"


def test_the_shipped_files_read_as_themselves() -> None:
    assert rms.read_shipped_segmental_duplications(_SHIPPED_SEGDUPS) == _SHIPPED_SEGDUPS.read_bytes()
    assert hpo_service.read_pinned_hpo_ontology(_VENDORED_HPO) == _VENDORED_HPO.read_bytes()


# ---- The segmental-duplication file -----------------------------------------------------------------


class _RecordingSession:
    """Records every statement; answers nothing (the lookups are patched)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.committed = False

    async def execute(self, statement: Any, params: Any = None) -> None:
        self.calls.append((str(statement), params))

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None

    def params_of(self, insert_into: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for sql, params in self.calls:
            if f"INSERT INTO {insert_into}" in sql:
                rows.extend(params if isinstance(params, list) else [params])
        return rows


def _bootstrap(
    monkeypatch: pytest.MonkeyPatch, *, segdups: Path, repository: Path, assembly_name: str = "GRCh38"
) -> None:
    """An empty assembly named ``assembly_name`` (GRCh38), the one the bootstrap setting names;
    the segmental-duplication setting names ``segdups``, the repository's copy is
    ``repository``."""

    async def named_assembly(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"id": "assembly-1", "assembly_name": assembly_name, "version": "p14"}

    async def nothing_loaded(*_args: Any, **_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(rms, "_get_assembly_by_id", named_assembly)
    monkeypatch.setattr(rms, "_get_assembly_by_name", named_assembly)
    monkeypatch.setattr(rms, "_assembly_dataset_count", nothing_loaded)
    monkeypatch.setattr(settings, "reference_bootstrap_enabled", True)
    monkeypatch.setattr(settings, "reference_bootstrap_assembly_name", assembly_name)
    monkeypatch.setattr(settings, "reference_segmental_duplications_path", str(segdups))
    monkeypatch.setattr(rms, "REPO_SEGMENTAL_DUPLICATIONS_PATH", repository)


def _another_segdup_file(tmp_path: Path) -> Path:
    """The shipped file with one cluster's end moved: what a bucket holds after an edit."""
    text = _SHIPPED_SEGDUPS.read_bytes().decode()
    first_cluster = next(line for line in text.splitlines() if line.startswith("chr") and line.endswith("0,0,0"))
    chrom, start, end, *rest = first_cluster.split("\t")
    moved = "\t".join([chrom, start, str(int(end) + 1000), *rest])
    path = tmp_path / "bucket" / "clinical_cnv_syndromes_hg38_bundle" / _SHIPPED_SEGDUPS.name
    path.parent.mkdir(parents=True)
    path.write_bytes(text.replace(first_cluster, moved, 1).encode())
    return path


def _errors(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_an_identical_copy_in_the_bucket_is_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    copy = tmp_path / "bucket" / _SHIPPED_SEGDUPS.name
    copy.parent.mkdir()
    shutil.copyfile(_SHIPPED_SEGDUPS, copy)
    _bootstrap(monkeypatch, segdups=copy, repository=tmp_path / "no-checkout" / _SHIPPED_SEGDUPS.name)
    session = _RecordingSession()

    asyncio.run(rms.seed_builtin_reference_tracks(session))  # type: ignore[arg-type]

    [record] = session.params_of("reference_dataset_imports")
    assert record["dataset_type"] == "segmental_duplications"
    assert record["source"] == _SHIPPED_SEGDUPS.name
    assert session.params_of("segmental_duplications")
    assert session.committed


def test_another_file_is_refused_logged_and_not_loaded_and_the_start_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    other = _another_segdup_file(tmp_path)
    # The repository's copy is the shipped file, but a configured file that exists is the
    # one read: a refused file is not replaced by another one behind the operator's back.
    _bootstrap(monkeypatch, segdups=other, repository=_SHIPPED_SEGDUPS)
    caplog.set_level(logging.INFO, logger=rms.logger.name)
    session = _RecordingSession()

    asyncio.run(rms.seed_builtin_reference_tracks(session))  # type: ignore[arg-type]

    assert session.params_of("segmental_duplications") == []
    assert session.params_of("reference_dataset_imports") == []
    assert session.committed
    [error] = _errors(caplog)
    message = error.getMessage()
    # The file, both digests and the fix, without a traceback: an expected refusal.
    for name in (
        str(other),
        _sha256(other),
        config.SEGMENTAL_DUPLICATIONS_PINNED_SHA256,
        "gcloud storage cp data/ref-data/clinical_cnv_syndromes_hg38_bundle/ClinGen_recurrent_CNV_V2.1-hg38.bed",
        "REFERENCE_SEGMENTAL_DUPLICATIONS_PATH",
        "SEGMENTAL_DUPLICATIONS_PINNED_SHA256",
    ):
        assert name in message, name
    assert error.exc_info is None


def test_the_startup_loads_no_clinical_cnvs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The startup loaded any file at REFERENCE_CLINICAL_CNVS_PATH as the clinical CNVs, unpinned,
    # and its default was never shipped, so it never ran (OPS-17). The knowledgebase rebuild
    # loads them from its pinned sources, and an administrator can upload a file. The settings
    # define no such path under any name, and a deployment that still sets it starts and keeps
    # nothing of it.
    names = {name.upper() for key, field in config.Settings.model_fields.items() for name in (key, field.alias) if name}
    assert "REFERENCE_CLINICAL_CNVS_PATH" not in names
    clinical_cnvs = tmp_path / "clinical_cnvs.tsv"
    clinical_cnvs.write_text("chr7\t72700000\t74300000\tWilliams-Beuren region\tClinGen\t\t\t\t\n", encoding="utf-8")
    monkeypatch.setenv("REFERENCE_CLINICAL_CNVS_PATH", str(clinical_cnvs))
    loaded = config.Settings(_env_file=None, APP_ENV="test")
    assert str(clinical_cnvs) not in loaded.model_dump().values()
    # A start loads the segmental duplications alone.
    copy = tmp_path / "bucket" / _SHIPPED_SEGDUPS.name
    copy.parent.mkdir()
    shutil.copyfile(_SHIPPED_SEGDUPS, copy)
    _bootstrap(monkeypatch, segdups=copy, repository=tmp_path / "no-checkout" / _SHIPPED_SEGDUPS.name)
    session = _RecordingSession()

    asyncio.run(rms.seed_builtin_reference_tracks(session))  # type: ignore[arg-type]

    imported = [record["dataset_type"] for record in session.params_of("reference_dataset_imports")]
    assert imported == ["segmental_duplications"]
    assert session.params_of("clinical_cnvs") == []


def test_the_repository_copy_is_held_to_the_pin_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    edited = _another_segdup_file(tmp_path)
    _bootstrap(monkeypatch, segdups=tmp_path / "unmounted" / _SHIPPED_SEGDUPS.name, repository=edited)
    caplog.set_level(logging.INFO, logger=rms.logger.name)
    session = _RecordingSession()

    asyncio.run(rms.seed_builtin_reference_tracks(session))  # type: ignore[arg-type]

    assert session.params_of("segmental_duplications") == []
    assert session.params_of("reference_dataset_imports") == []
    [error] = _errors(caplog)
    assert str(edited) in error.getMessage()


def test_a_file_that_cannot_be_read_is_refused_the_same_way(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    unreadable = tmp_path / "bucket" / _SHIPPED_SEGDUPS.name
    unreadable.mkdir(parents=True)  # exists, but reading it fails (IsADirectoryError)
    _bootstrap(monkeypatch, segdups=unreadable, repository=_SHIPPED_SEGDUPS)
    caplog.set_level(logging.INFO, logger=rms.logger.name)
    session = _RecordingSession()

    asyncio.run(rms.seed_builtin_reference_tracks(session))  # type: ignore[arg-type]

    assert session.params_of("segmental_duplications") == []
    [error] = _errors(caplog)
    message = error.getMessage()
    assert str(unreadable) in message and "cannot be read" in message
    assert config.SEGMENTAL_DUPLICATIONS_PINNED_SHA256 in message


# ---- Into a GRCh38 assembly only (OPS-17) -------------------------------------------------------------


@pytest.mark.parametrize("assembly_name", ["T2T CHM13v2.0", "hs1", "GRCh37", "hg19", "GRCm39"])
def test_the_grch38_file_is_loaded_into_no_other_assembly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, assembly_name: str
) -> None:
    # REFERENCE_BOOTSTRAP_ASSEMBLY_NAME names another assembly. The shipped file holds GRCh38's
    # coordinates: the start loaded it into that assembly, each cluster at its GRCh38 place.
    _bootstrap(monkeypatch, segdups=_SHIPPED_SEGDUPS, repository=_SHIPPED_SEGDUPS, assembly_name=assembly_name)
    caplog.set_level(logging.INFO, logger=rms.logger.name)
    session = _RecordingSession()

    asyncio.run(rms.seed_builtin_reference_tracks(session))  # type: ignore[arg-type]

    # Nothing written: no row, no import record, no statement at all.
    assert session.params_of("segmental_duplications") == []
    assert session.params_of("reference_dataset_imports") == []
    assert session.calls == []
    # One line, a warning, naming the assembly, the shipped file's build and the fix.
    [line] = [record for record in caplog.records if record.name == rms.logger.name]
    assert line.levelno == logging.WARNING
    message = line.getMessage()
    for part in (
        f"'{assembly_name}'",
        "GRCh38",
        _SHIPPED_SEGDUPS.name,
        "upload",
        "Species & assemblies",
        "REFERENCE_BOOTSTRAP_ASSEMBLY_NAME",
    ):
        assert part in message, part


@pytest.mark.parametrize("assembly_name", ["GRCh38", "hg38", "GRCh38.p14"])
def test_a_grch38_assembly_is_loaded_under_any_of_its_names(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, assembly_name: str
) -> None:
    # GRCh38 as the PAR lookup reads an assembly's name (sex_chromosomes.grc_build): GRCh38 or
    # UCSC's hg38, in any case, with or without a patch suffix.
    _bootstrap(monkeypatch, segdups=_SHIPPED_SEGDUPS, repository=_SHIPPED_SEGDUPS, assembly_name=assembly_name)
    caplog.set_level(logging.INFO, logger=rms.logger.name)
    session = _RecordingSession()

    asyncio.run(rms.seed_builtin_reference_tracks(session))  # type: ignore[arg-type]

    [record] = session.params_of("reference_dataset_imports")
    assert record["dataset_type"] == "segmental_duplications"
    assert record["source"] == _SHIPPED_SEGDUPS.name
    rows = session.params_of("segmental_duplications")
    assert rows and record["inserted"] == len(rows)
    assert not [line for line in caplog.records if line.levelno >= logging.WARNING]


# ---- An HPO ontology file in place ------------------------------------------------------------------


class _HpoSession:
    async def rollback(self) -> None:
        return None


def _hpo_start(monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict[str, Any]], list[str]]:
    """An empty HPO terminology. The first list collects what is imported, the second every
    download requested (none should be)."""
    imported: list[dict[str, Any]] = []
    requested: list[str] = []

    async def no_terms(_session: Any) -> int:
        return 0

    async def record_import(_session: Any, **kwargs: Any) -> dict[str, int]:
        imported.append(kwargs)
        return {"terms": 1, "synonyms": 0, "edges": 0, "closure_rows": 1}

    def no_download(request: Any, timeout: float = 60) -> io.BytesIO:
        requested.append(request.full_url)
        raise AssertionError("a file was in place: nothing may be downloaded")

    monkeypatch.setattr(hpo_service, "_hpo_term_count", no_terms)
    monkeypatch.setattr(hpo_service, "import_hpo_ontology", record_import)
    monkeypatch.setattr(hpo_service, "urlopen", no_download)
    return imported, requested


@pytest.mark.asyncio
async def test_the_pinned_ontology_in_place_is_imported_from_the_bytes_that_were_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    in_place = tmp_path / "ref-data" / "hpo" / "hp.obo"
    in_place.parent.mkdir(parents=True)
    shutil.copyfile(_VENDORED_HPO, in_place)
    imported, requested = _hpo_start(monkeypatch)

    result = await hpo_service.ensure_hpo_ontology_on_startup(_HpoSession(), ontology_path=in_place)

    assert requested == []
    [call] = imported
    assert call["path"] == in_place
    assert call["release_version"] == config.HPO_PINNED_RELEASE
    # The import parses the checked bytes; it does not read the file again.
    assert call["ontology_text"] == _VENDORED_HPO.read_bytes().decode("utf-8")
    # And the terms keep the SHA-256 those bytes were checked against (TRACE-18).
    assert call["release_sha256"] == config.HPO_PINNED_ONTOLOGY_SHA256
    assert result is not None and result["release_version"] == config.HPO_PINNED_RELEASE


@pytest.mark.asyncio
async def test_another_ontology_in_place_is_refused_logged_and_not_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    in_place = tmp_path / "ref-data" / "hpo" / "hp.obo"
    in_place.parent.mkdir(parents=True)
    in_place.write_bytes(_LATER_HPO_RELEASE)
    imported, requested = _hpo_start(monkeypatch)
    caplog.set_level(logging.INFO, logger=hpo_service.logger.name)

    result = await hpo_service.ensure_hpo_ontology_on_startup(_HpoSession(), ontology_path=in_place)

    assert result is None
    assert imported == []
    # Refused as the download is: nothing is downloaded in its place, and the file stays.
    assert requested == []
    assert in_place.read_bytes() == _LATER_HPO_RELEASE
    [error] = _errors(caplog)
    message = error.getMessage()
    for name in (
        str(in_place),
        hashlib.sha256(_LATER_HPO_RELEASE).hexdigest(),
        config.HPO_PINNED_ONTOLOGY_SHA256,
        config.HPO_PINNED_RELEASE,
        "gcloud storage cp data/ref-data/hpo/hp.obo",
        "HPO_ONTOLOGY_PATH",
        "HPO_PINNED_ONTOLOGY_SHA256",
    ):
        assert name in message, name
    assert error.exc_info is None


@pytest.mark.asyncio
async def test_an_ontology_in_place_that_cannot_be_read_is_not_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    in_place = tmp_path / "ref-data" / "hpo" / "hp.obo"
    in_place.parent.mkdir(parents=True)
    shutil.copyfile(_VENDORED_HPO, in_place)
    imported, requested = _hpo_start(monkeypatch)

    def unreadable(path: Path) -> bytes:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(hpo_service, "read_pinned_hpo_ontology", unreadable)
    caplog.set_level(logging.INFO, logger=hpo_service.logger.name)

    result = await hpo_service.ensure_hpo_ontology_on_startup(_HpoSession(), ontology_path=in_place)

    assert result is None and imported == [] and requested == []
    [error] = _errors(caplog)
    message = error.getMessage()
    assert str(in_place) in message and "PermissionError" in message
    assert config.HPO_PINNED_ONTOLOGY_SHA256 in message


# ---- A refusal skips only its import: the application starts -----------------------------------------


class _App:
    class state:  # FastAPI's app.state, as the lifespan reads it
        skip_startup_tasks = False


# Every step of the lifespan but the HPO and reference-track bootstraps.
_OTHER_STARTUP_STEPS = (
    "wait_for_postgres",
    "init_postgres_schema",
    "init_postgres_admin_user",
    "start_audit_log_worker",
    "start_ui_event_worker",
    "seed_builtin_repeat_catalog",
    "ensure_human_grch38_reference_on_startup",
    "ensure_human_t2t_reference_on_startup",
    "queue_startup_gene_reference_refresh_if_needed",
    "wait_for_clickhouse",
    "init_clickhouse_schema",
    "verify_clickhouse_variant_storage_identity",
    "drop_orphaned_import_backups",
    "start_clickhouse_integrity_monitor",
    "gene_reference_refresh_worker",
    "family_package_import_worker",
    "stop_gene_reference_worker",
    "stop_family_package_import_worker",
    "stop_clickhouse_integrity_monitor",
    "stop_audit_log_worker",
    "stop_ui_event_worker",
    "close_clickhouse_client",
    "close_postgres_engine",
)


@pytest.mark.asyncio
async def test_the_application_starts_when_both_files_are_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    def step(name: str):
        async def _step(*_args: Any, **_kwargs: Any) -> None:
            calls.append(name)

        return _step

    session = _RecordingSession()

    class _SessionFactory:
        async def __aenter__(self) -> _RecordingSession:
            return session

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

    for name in _OTHER_STARTUP_STEPS:
        monkeypatch.setattr(app_main, name, step(name))
    monkeypatch.setattr(app_main, "get_postgres_sessionmaker", lambda: _SessionFactory)
    monkeypatch.setattr(app_main.settings, "family_import_worker_count", 1)
    monkeypatch.setattr(app_main.settings, "hpo_bootstrap_on_startup", True)
    in_place = tmp_path / "ref-data" / "hpo" / "hp.obo"
    in_place.parent.mkdir(parents=True)
    in_place.write_bytes(_LATER_HPO_RELEASE)
    monkeypatch.setattr(app_main.settings, "hpo_ontology_path", str(in_place))
    imported, _requested = _hpo_start(monkeypatch)
    _bootstrap(monkeypatch, segdups=_another_segdup_file(tmp_path), repository=_SHIPPED_SEGDUPS)
    caplog.set_level(logging.INFO)

    async with app_main.lifespan(_App()):
        pass

    # Both refused and logged, nothing of either written, and the start went on past them.
    assert imported == []
    assert session.params_of("segmental_duplications") == []
    assert len(_errors(caplog)) == 2
    assert "queue_startup_gene_reference_refresh_if_needed" in calls
    assert "start_clickhouse_integrity_monitor" in calls
