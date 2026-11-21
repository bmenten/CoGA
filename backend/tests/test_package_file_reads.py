"""A package file a validation read whole is not read again while it is unchanged
(``package_file_reads``, the owner's decision of 2026-11-21).

The validation reads every package VCF to its end (``read_package_vcf_whole``); a delivery is
checked again at each correction, inside the request. What a whole read found is remembered per
file and reader with the file's size and modification and change times: an unchanged file is
not read again, a changed one is, a refused read is never remembered, and without remembered
reads (a validation outside ``validated_package``) every file is read.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from backend.app.services import family_package_common as common
from backend.app.services import package_file_reads as reads_module
from backend.app.services.package_file_reads import RememberedReads, remembering

_VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPROBAND\tMOTHER\n"
    "1\t100\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\t0/0\n"
)


@pytest.fixture()
def counted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """The whole reads made, as (file name, reader)."""
    made: list[tuple[str, str]] = []
    read = common._read_package_vcf_with

    def counting(path: Path, *, label: str, reader: Any) -> Any:
        made.append((path.name, reader))
        return read(path, label=label, reader=reader)

    monkeypatch.setattr(common, "_read_package_vcf_with", counting)
    return made


def _vcf(tmp_path: Path, text: str = _VCF) -> Path:
    path = tmp_path / "family.vcf"
    path.write_text(text)
    return path


def test_an_unchanged_file_is_read_whole_once(tmp_path: Path, counted: list[tuple[str, str]]) -> None:
    path = _vcf(tmp_path)
    with remembering(RememberedReads()) as remembered:
        first = common.read_package_vcf_whole(path, label="The SNV VCF", readers=["loader"])
        again = common.read_package_vcf_whole(path, label="The SNV VCF", readers=["loader"])
    assert counted == [("family.vcf", "loader")]
    assert first == again == common.PackageVcfRead(columns=["PROBAND", "MOTHER"], holds_a_line=True)
    # Kept to store after the validation, under the file's absolute path and the reader.
    assert list(remembered.read) == [(os.path.abspath(path), "loader")]
    # Another reader of the same file reads it.
    with remembering(remembered):
        common.read_package_vcf_whole(path, label="The SNV VCF", readers=["text"])
    assert counted[-1] == ("family.vcf", "text")


def test_a_changed_file_is_read_again(tmp_path: Path, counted: list[tuple[str, str]]) -> None:
    path = _vcf(tmp_path)
    with remembering(RememberedReads()):
        common.read_package_vcf_whole(path, label="The SNV VCF", readers=["loader"])
        path.write_text(_VCF.replace("PROBAND\tMOTHER", "PROBAND\tFATHER1"))
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
        changed = common.read_package_vcf_whole(path, label="The SNV VCF", readers=["loader"])
    assert counted == [("family.vcf", "loader"), ("family.vcf", "loader")]
    assert changed.columns == ["PROBAND", "FATHER1"]


def test_a_refused_read_is_never_remembered(tmp_path: Path, counted: list[tuple[str, str]]) -> None:
    # The last record cut short: refused each time it is read.
    path = _vcf(tmp_path, _VCF.rstrip("\n").rsplit("\t", 3)[0])
    with remembering(RememberedReads()) as remembered:
        for _ in range(2):
            with pytest.raises(common.UnreadableVcf):
                common.read_package_vcf_whole(path, label="The SNV VCF", readers=["loader"])
    assert len(counted) == 2
    assert remembered.read == {}


def test_without_remembered_reads_every_file_is_read(tmp_path: Path, counted: list[tuple[str, str]]) -> None:
    path = _vcf(tmp_path)
    for _ in range(2):
        common.read_package_vcf_whole(path, label="The SNV VCF", readers=["loader"])
    assert len(counted) == 2


def test_a_file_that_changed_while_it_was_read_is_not_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _vcf(tmp_path)
    read = common._read_package_vcf_with

    def changing(path: Path, *, label: str, reader: Any) -> Any:
        found = read(path, label=label, reader=reader)
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
        return found

    monkeypatch.setattr(common, "_read_package_vcf_with", changing)
    with remembering(RememberedReads()) as remembered:
        common.read_package_vcf_whole(path, label="The SNV VCF", readers=["loader"])
    assert remembered.read == {}


def test_a_remembered_read_of_the_file_as_it_is_is_taken(tmp_path: Path, counted: list[tuple[str, str]]) -> None:
    # As loaded from Postgres before the validation: the file's version and what was found.
    path = _vcf(tmp_path)
    version = reads_module.file_version(path)
    assert version is not None
    known = RememberedReads()
    known.known[(os.path.abspath(path), "loader")] = reads_module.RememberedRead(
        version=version, columns=("PROBAND", "MOTHER"), holds_a_line=True
    )
    with remembering(known):
        found = common.read_package_vcf_whole(path, label="The SNV VCF", readers=["loader"])
    assert counted == []
    assert found == common.PackageVcfRead(columns=["PROBAND", "MOTHER"], holds_a_line=True)
    # Of another version, it is not.
    stale = RememberedReads()
    stale.known[(os.path.abspath(path), "loader")] = reads_module.RememberedRead(
        version=(version[0] + 1, version[1], version[2]), columns=("OTHER",), holds_a_line=True
    )
    with remembering(stale):
        found = common.read_package_vcf_whole(path, label="The SNV VCF", readers=["loader"])
    assert counted == [("family.vcf", "loader")]
    assert found.columns == ["PROBAND", "MOTHER"]


def test_the_validation_thread_reads_the_remembered_reads_loaded_before_and_stores_them_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = RememberedReads()
    stored: list[RememberedReads] = []
    seen: list[Any] = []

    async def load(folder: Any) -> RememberedReads:
        assert Path(folder) == tmp_path
        return loaded

    async def store(reads: RememberedReads) -> None:
        stored.append(reads)

    def validate(folder_path: Any, *, manifest_text: str) -> tuple[str, None]:
        seen.append(reads_module._REMEMBERED.get())
        return "validation", None

    from backend.app.services import family_package_validation

    monkeypatch.setattr(reads_module, "load_remembered_reads", load)
    monkeypatch.setattr(reads_module, "store_remembered_reads", store)
    monkeypatch.setattr(family_package_validation, "load_validated_family_package", validate)

    result = asyncio.run(reads_module.validated_package(tmp_path, manifest_text="{}"))

    assert result == ("validation", None)
    assert seen == [loaded] and stored == [loaded]
    assert reads_module._REMEMBERED.get() is None


def test_remembered_reads_that_cannot_be_loaded_or_stored_only_cost_the_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The unit suite's Postgres is a closed port: nothing is loaded, nothing stored, and the
    # warning names the error by its kind.
    loaded = asyncio.run(reads_module.load_remembered_reads(tmp_path))
    assert loaded.known == {}
    unsaved = RememberedReads()
    unsaved.read[("/somewhere/family.vcf", "loader")] = reads_module.RememberedRead(
        version=(1, 2, 3), columns=(), holds_a_line=False
    )
    asyncio.run(reads_module.store_remembered_reads(unsaved))
    warnings = [record.getMessage() for record in caplog.records if record.name == reads_module.logger.name]
    assert len(warnings) == 2
