"""The repeat catalogue is built from the shipped STRchive release, or the backend does not start (CLIN-31).

Every start seeds the repeat catalogue from the built-in loci and a STRchive file: the one
``TRGT_STRCHIVE_LOCI_PATH`` names (on Cloud Run the reference-data bucket), else the
repository's ``data/ref-data/STRchive-loci.json``. The built-in loci carry the shipped
release's thresholds (CLIN-22), and test_repeat_thresholds_follow_strchive.py checks them
against the shipped file only, so another release in the bucket made the built-in rows and the
STRchive rows disagree again: a call read differently depending on its TRID. The owner decided
on 2026-10-14 that the shipped, validated release is the source. The file states no release of
its own, so its SHA-256 is its identity, pinned in ``core/config.py``
(``STRCHIVE_PINNED_LOCI_SHA256``), which no setting overrides.

- The pin is the shipped file's SHA-256, and the docs that describe the pin name it.
- An identical copy elsewhere is read; another release, the same loci written out again, and
  no file at all refuse the start before anything is written, with a logged error that names
  the file, both digests and the fix. The repository's copy is held to the pin too.
- The application's start stops there: nothing after the catalogue runs.
- A start removes STRchive rows the shipped release does not list (another release's, seeded
  earlier), in the same transaction as its upserts.
(integration/test_repeat_multi_locus_storage.py runs the removal on Postgres.)
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest

from backend.app import main as app_main
from backend.app.core import config
from backend.app.services import repeat_expansion_pg

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED = _REPO_ROOT / "data" / "ref-data" / "STRchive-loci.json"


def _shipped_digest() -> str:
    return hashlib.sha256(_SHIPPED.read_bytes()).hexdigest()


def _another_release(tmp_path: Path) -> Path:
    """A STRchive file of another release: Huntington's pathogenic range starts at 40, not 36."""
    entries = json.loads(_SHIPPED.read_bytes())
    for entry in entries:
        if entry["id"] == "HD_HTT":
            assert entry["pathogenic_min"] == 36
            entry["pathogenic_min"] = 40
    path = tmp_path / "bucket" / "STRchive-loci.json"
    path.parent.mkdir()
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return path


def _rewritten_copy(tmp_path: Path) -> Path:
    """The shipped loci, every value the same, written out again (other whitespace)."""
    path = tmp_path / "rewritten" / "STRchive-loci.json"
    path.parent.mkdir()
    path.write_text(json.dumps(json.loads(_SHIPPED.read_bytes()), indent=2) + "\n", encoding="utf-8")
    return path


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []
        self.committed = False

    async def execute(self, statement: Any, params: dict | None = None) -> None:
        self.statements.append((" ".join(str(statement).split()), params or {}))

    async def commit(self) -> None:
        self.committed = True


def _configure(monkeypatch: pytest.MonkeyPatch, *, setting: Path | None, repository: Path) -> None:
    monkeypatch.setattr(repeat_expansion_pg.settings, "trgt_strchive_loci_path", str(setting) if setting else None)
    monkeypatch.setattr(repeat_expansion_pg, "REPO_STRCHIVE_LOCI_PATH", repository)


def _strchive_upserts(session: _RecordingSession) -> list[str]:
    return [
        params["locus_id"]
        for sql, params in session.statements
        if "INSERT INTO repeat_loci" in sql and json.loads(params["metadata_json"]).get("source") == "STRchive"
    ]


# ---- The pin -------------------------------------------------------------------------------------


def test_the_pin_is_the_shipped_files_sha256() -> None:
    assert config.STRCHIVE_PINNED_LOCI_SHA256 == _shipped_digest()
    assert repeat_expansion_pg.REPO_STRCHIVE_LOCI_PATH == _SHIPPED
    # No setting overrides it: the identity moves only with the file, in a reviewed change.
    assert not [name for name in config.Settings.model_fields if "sha256" in name and "strchive" in name]


def test_the_docs_that_describe_the_pin_name_the_shipped_release() -> None:
    """The docs give the digest shortened, as TF-08 gives image digests; the constant holds it whole."""
    shortened = _shipped_digest()[:12]
    data_import = (_REPO_ROOT / "docs" / "data-import.md").read_text(encoding="utf-8")
    assert "### The STRchive release" in data_import
    section = data_import.split("### The STRchive release", 1)[1].split("\n## ", 1)[0]
    assert shortened in section
    assert "STRCHIVE_PINNED_LOCI_SHA256" in section
    soup_register = (_REPO_ROOT / "docs" / "regulatory" / "TF-08-soup-register.md").read_text(encoding="utf-8")
    [strchive_row] = [line for line in soup_register.splitlines() if line.startswith("| STRchive")]
    assert shortened in strchive_row
    assert "STRCHIVE_PINNED_LOCI_SHA256" in strchive_row


def test_the_shipped_file_reads_as_itself() -> None:
    assert repeat_expansion_pg.read_shipped_strchive_release(_SHIPPED) == _SHIPPED.read_bytes()


# ---- A start reads the shipped release, wherever its copy lies ------------------------------------


@pytest.mark.asyncio
async def test_an_identical_copy_in_the_bucket_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    copy = tmp_path / "bucket" / "STRchive-loci.json"
    copy.parent.mkdir()
    shutil.copyfile(_SHIPPED, copy)
    # The copy is the only file a start can read: the repository's is not there.
    _configure(monkeypatch, setting=copy, repository=tmp_path / "no-checkout" / "STRchive-loci.json")
    session = _RecordingSession()

    await repeat_expansion_pg.seed_builtin_repeat_catalog(session)

    shipped_ids = [entry["id"] for entry in json.loads(_SHIPPED.read_bytes())]
    assert sorted(_strchive_upserts(session)) == sorted(shipped_ids)
    assert len(shipped_ids) == 75
    assert session.committed


@pytest.mark.asyncio
async def test_the_repository_copy_is_read_when_the_setting_names_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, setting=tmp_path / "unmounted" / "STRchive-loci.json", repository=_SHIPPED)
    session = _RecordingSession()

    await repeat_expansion_pg.seed_builtin_repeat_catalog(session)

    assert len(_strchive_upserts(session)) == 75
    assert session.committed


# ---- Any other file refuses the start, before anything is written ---------------------------------


async def _refused_start(session: _RecordingSession) -> BaseException:
    """The error a start raises; a start that seeds the catalogue fails the test."""
    with pytest.raises(Exception) as excinfo:
        await repeat_expansion_pg.seed_builtin_repeat_catalog(session)
    assert isinstance(excinfo.value, repeat_expansion_pg.StrchiveReleaseRefused)
    return excinfo.value


def _assert_refused_and_logged(
    error: BaseException,
    caplog: pytest.LogCaptureFixture,
    *,
    names: list[str],
) -> None:
    message = str(error)
    for name in names:
        assert name in message, name
    # How to fix it: the shipped file, the setting, and the pin that moves only with review.
    for remedy in ("data/ref-data/STRchive-loci.json", "TRGT_STRCHIVE_LOCI_PATH", "STRCHIVE_PINNED_LOCI_SHA256"):
        assert remedy in message, remedy
    # A failed start's traceback is logged without the exception's message, so the message
    # is logged on its own.
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert [record.getMessage() for record in errors] == [message]


@pytest.mark.parametrize("make_file", [_another_release, _rewritten_copy], ids=["another release", "the same loci written out again"])
@pytest.mark.asyncio
async def test_a_start_on_any_other_file_is_refused_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    make_file,
) -> None:
    other = make_file(tmp_path)
    _configure(monkeypatch, setting=other, repository=_SHIPPED)
    caplog.set_level(logging.INFO, logger=repeat_expansion_pg.__name__)
    session = _RecordingSession()

    error = await _refused_start(session)

    # Both digests: the file's and the shipped release's.
    _assert_refused_and_logged(
        error,
        caplog,
        names=[str(other), hashlib.sha256(other.read_bytes()).hexdigest(), _shipped_digest()],
    )
    assert session.statements == []
    assert not session.committed


@pytest.mark.asyncio
async def test_the_repository_copy_is_held_to_the_pin_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    edited = _another_release(tmp_path)
    _configure(monkeypatch, setting=None, repository=edited)
    caplog.set_level(logging.INFO, logger=repeat_expansion_pg.__name__)
    session = _RecordingSession()

    error = await _refused_start(session)

    _assert_refused_and_logged(error, caplog, names=[str(edited), _shipped_digest()])
    assert session.statements == []


@pytest.mark.asyncio
async def test_a_start_without_any_strchive_file_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Without a file a start would keep whatever STRchive rows an earlier start left,
    unchecked, beside built-in rows that carry the shipped release's thresholds."""
    setting = tmp_path / "unmounted" / "STRchive-loci.json"
    repository = tmp_path / "no-checkout" / "STRchive-loci.json"
    _configure(monkeypatch, setting=setting, repository=repository)
    caplog.set_level(logging.INFO, logger=repeat_expansion_pg.__name__)
    session = _RecordingSession()

    error = await _refused_start(session)

    _assert_refused_and_logged(error, caplog, names=[str(setting), str(repository), _shipped_digest()])
    assert session.statements == []


# ---- The application's start stops at the catalogue ------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def step(self, name: str):
        async def _step(*_args: Any, **_kwargs: Any) -> None:
            self.calls.append(name)

        return _step


class _App:
    class state:  # mirrors FastAPI's app.state attribute
        skip_startup_tasks = False


_STEPS_AFTER_THE_CATALOGUE = (
    "ensure_human_grch38_reference_on_startup",
    "ensure_human_t2t_reference_on_startup",
    "ensure_hpo_ontology_on_startup",
    "seed_builtin_reference_tracks",
    "queue_startup_gene_reference_refresh_if_needed",
    "wait_for_clickhouse",
    "init_clickhouse_schema",
    "verify_clickhouse_variant_storage_identity",
    "drop_orphaned_import_backups",
    "start_clickhouse_integrity_monitor",
    "gene_reference_refresh_worker",
    "family_package_import_worker",
)


def _patch_every_step_but_the_catalogue(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> _RecordingSession:
    session = _RecordingSession()

    class _SessionFactory:
        async def __aenter__(self) -> _RecordingSession:
            return session

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

    for name in (
        "wait_for_postgres",
        "init_postgres_schema",
        "init_postgres_admin_user",
        "start_audit_log_worker",
        "start_ui_event_worker",
        *_STEPS_AFTER_THE_CATALOGUE,
        "stop_gene_reference_worker",
        "stop_family_package_import_worker",
        "stop_clickhouse_integrity_monitor",
        "stop_audit_log_worker",
        "stop_ui_event_worker",
        "close_clickhouse_client",
        "close_postgres_engine",
    ):
        monkeypatch.setattr(app_main, name, recorder.step(name))
    monkeypatch.setattr(app_main, "get_postgres_sessionmaker", lambda: _SessionFactory)
    monkeypatch.setattr(app_main.settings, "family_import_worker_count", 1)
    return session


@pytest.mark.asyncio
async def test_the_application_does_not_start_on_another_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorder = _Recorder()
    session = _patch_every_step_but_the_catalogue(monkeypatch, recorder)
    _configure(monkeypatch, setting=_another_release(tmp_path), repository=_SHIPPED)

    with pytest.raises(Exception) as excinfo:
        async with app_main.lifespan(_App()):
            pytest.fail("the app must not serve")  # pragma: no cover

    assert isinstance(excinfo.value, repeat_expansion_pg.StrchiveReleaseRefused)

    assert session.statements == []
    assert not set(_STEPS_AFTER_THE_CATALOGUE) & set(recorder.calls)


@pytest.mark.asyncio
async def test_the_application_starts_on_an_identical_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorder = _Recorder()
    session = _patch_every_step_but_the_catalogue(monkeypatch, recorder)
    copy = tmp_path / "bucket" / "STRchive-loci.json"
    copy.parent.mkdir()
    shutil.copyfile(_SHIPPED, copy)
    _configure(monkeypatch, setting=copy, repository=tmp_path / "no-checkout" / "STRchive-loci.json")

    async with app_main.lifespan(_App()):
        started = list(recorder.calls)

    assert len(_strchive_upserts(session)) == 75
    assert "wait_for_clickhouse" in started


# ---- The STRchive rows a start leaves are the shipped release's ----------------------------------


@pytest.mark.asyncio
async def test_a_start_removes_strchive_rows_the_shipped_release_does_not_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """A database seeded with another release (before the pin, or before a reviewed change moved
    it) keeps loci the shipped release lacks, with that release's thresholds: a TRID naming one
    would read against them. The start's own transaction removes them, after its upserts."""
    _configure(monkeypatch, setting=None, repository=_SHIPPED)
    session = _RecordingSession()

    await repeat_expansion_pg.seed_builtin_repeat_catalog(session)

    *upserts, (delete_sql, delete_params) = session.statements
    assert all("INSERT INTO repeat_loci" in sql for sql, _params in upserts)
    assert delete_sql.startswith("DELETE FROM repeat_loci")
    # Only STRchive rows, never a built-in one, and every row the release lists stays.
    assert "metadata ->> 'source' = 'STRchive'" in delete_sql
    assert "NOT (locus_id = ANY(CAST(:locus_ids AS text[])))" in delete_sql
    assert sorted(delete_params["locus_ids"]) == sorted(_strchive_upserts(session))
    assert session.committed
