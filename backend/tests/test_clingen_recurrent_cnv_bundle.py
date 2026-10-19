"""The bundled ClinGen recurrent-CNV file is ClinGen's V2.1 GRCh38 file, unchanged (TRACE-3).

data/ref-data/clinical_cnv_syndromes_hg38_bundle/ClinGen_recurrent_CNV_V2.1-hg38.bed says V2.1
in its name, but its track line names `ClinGen_recurrent_CNV_V2.0-hg38`. On 2026-10-14 the
file was compared with ClinGen's V2.1 GRCh38 download (ftp.clinicalgenome.org): byte-identical.
The V2.0 track name is in ClinGen's own V2.1 file, and the release is V2.1, as the file name
says. TF-08 and the note beside the file record that check; this test pins the file to its
SHA-256, so a changed file fails until it is checked against ClinGen again and recorded. The
startup loads the file as the segmental duplications track: it records the file name as the
import's source and never stores the track line. Nothing here goes to the network.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest

from backend.app.core.config import settings
from backend.app.services import reference_metadata_service as rms

_REPO = Path(__file__).resolve().parents[2]
# The file the startup falls back to when the configured path holds none.
_BUNDLED = rms.REPO_SEGMENTAL_DUPLICATIONS_PATH
_NOTE = _BUNDLED.with_name("COGA-PROVENANCE.md")
_TF08 = _REPO / "docs" / "regulatory" / "TF-08-soup-register.md"
# ClinGen's V2.1 GRCh38 recurrent-CNV file, as compared with ClinGen's download on 2026-10-14.
CLINGEN_V2_1_HG38_SHA256 = "e6a7428f58b055e51a956941da85cdc984d9519ea7839c365c9b557ccc76a763"
_TRACK_NAME = "ClinGen_recurrent_CNV_V2.0-hg38"


def test_the_bundled_file_is_clingens_v2_1_file_as_checked() -> None:
    assert _BUNDLED.name == "ClinGen_recurrent_CNV_V2.1-hg38.bed"
    digest = hashlib.sha256(_BUNDLED.read_bytes()).hexdigest()
    assert digest == CLINGEN_V2_1_HG38_SHA256, (
        f"{_BUNDLED.relative_to(_REPO)} has changed: its SHA-256 is {digest}, not the "
        f"{CLINGEN_V2_1_HG38_SHA256} of ClinGen's V2.1 GRCh38 file checked on 2026-10-14. "
        "Re-check the file against ClinGen's own download (ftp.clinicalgenome.org) by SHA-256, "
        "then record its release, SHA-256, the date and how it was checked in TF-08 "
        "(docs/regulatory/TF-08-soup-register.md, the bundled ClinGen recurrent-CNV file) and in "
        "COGA-PROVENANCE.md beside the file, and move this pin."
    )


def test_tf08_and_the_note_beside_the_file_record_that_check() -> None:
    for record in (_TF08, _NOTE):
        text = record.read_text(encoding="utf-8")
        assert CLINGEN_V2_1_HG38_SHA256 in text, f"{record.name} does not record the checked SHA-256"
        assert "V2.1" in text and "2026-10-14" in text, f"{record.name} does not record the release or the date"
        # The track line inside ClinGen's V2.1 file says V2.0; the records say so.
        assert _TRACK_NAME in text, f"{record.name} does not explain the V2.0 track name"


class _RecordingSession:
    """Records every statement; answers nothing (the lookups are patched)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> None:
        self.calls.append((str(statement), params))

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def params_of(self, insert_into: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for sql, params in self.calls:
            if f"INSERT INTO {insert_into}" in sql:
                rows.extend(params if isinstance(params, list) else [params])
        return rows


def test_the_startup_loads_it_as_v2_1_and_never_stores_the_v2_0_track_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The V2.0 name is ClinGen's own track line, the file's first line.
    first_line = _BUNDLED.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith(f'track name="{_TRACK_NAME}"')

    async def grch38(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"id": "assembly-1", "assembly_name": "GRCh38", "version": "p14"}

    async def nothing_loaded(*_args: Any, **_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(rms, "_get_assembly_by_id", grch38)
    monkeypatch.setattr(rms, "_get_assembly_by_name", grch38)
    monkeypatch.setattr(rms, "_assembly_dataset_count", nothing_loaded)
    monkeypatch.setattr(settings, "reference_bootstrap_enabled", True)
    monkeypatch.setattr(settings, "reference_bootstrap_assembly_name", "GRCh38")
    # No configured file, so the startup loads the bundled one; no clinical-CNV file at all.
    monkeypatch.setattr(settings, "reference_segmental_duplications_path", str(tmp_path / "absent.bed"))
    monkeypatch.setattr(settings, "reference_clinical_cnvs_path", str(tmp_path / "absent.tsv"))
    monkeypatch.setattr(rms, "REPO_CLINICAL_CNVS_PATH", tmp_path / "absent-too.tsv")
    session = _RecordingSession()

    asyncio.run(rms.seed_builtin_reference_tracks(session))  # type: ignore[arg-type]

    [record] = session.params_of("reference_dataset_imports")
    assert record["dataset_type"] == "segmental_duplications"
    # What the reference catalogue shows as the import's source: the file name, V2.1.
    assert record["source"] == "ClinGen_recurrent_CNV_V2.1-hg38.bed"
    rows = session.params_of("segmental_duplications")
    assert rows, "the file's segmental-duplication clusters were not loaded"
    assert record["inserted"] == len(rows)
    assert not [row for row in rows if any("V2.0" in str(value) for value in row.values())]
