"""A raw-file provenance record that could not be written (#686). Recording stays
best-effort, so the import stands, but the loss is logged with the family, dataset and
file. Before, the error was swallowed and the missing record left no trace."""

from __future__ import annotations

import asyncio
import logging

from backend.app.services import raw_import_files_pg as rif


class _Session:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


def test_a_failed_provenance_write_is_logged_and_does_not_raise(monkeypatch, caplog) -> None:
    async def failing_store(family_id: str, file_name: str, content: bytes):
        raise OSError("disk full")

    monkeypatch.setattr(rif, "store_managed_file", failing_store)
    session = _Session()

    with caplog.at_level(logging.WARNING, logger=rif.__name__):
        asyncio.run(
            rif.record_uploaded_file(
                session,  # type: ignore[arg-type]
                family_uuid="fam-uuid",
                family_id="FAM1",
                sample_uuid=None,
                scope="family",
                dataset="small_variants",
                source="web",
                file_name="trio.vcf.gz",
                content=b"payload",
            )
        )

    assert session.rolled_back
    [record] = [r for r in caplog.records if r.name == rif.__name__]
    assert record.getMessage() == (
        "Raw-file provenance for family FAM1 (small_variants, trio.vcf.gz) was not recorded"
    )
    assert record.exc_info is not None and "disk full" in str(record.exc_info[1])


class _UnreadableUpload:
    filename = "trio.vcf.gz"

    async def seek(self, offset: int) -> None:
        raise ValueError("I/O operation on closed file")


def test_an_upload_that_cannot_be_re_read_is_logged(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=rif.__name__):
        asyncio.run(
            rif.record_upload_file_obj(
                _Session(),  # type: ignore[arg-type]
                file=_UnreadableUpload(),  # type: ignore[arg-type]
                family_uuid="fam-uuid",
                family_id="FAM1",
                sample_uuid=None,
                scope="family",
                dataset="small_variants",
            )
        )

    [record] = [r for r in caplog.records if r.name == rif.__name__]
    assert record.getMessage() == (
        "Raw-file provenance for family FAM1 (small_variants, trio.vcf.gz) was not recorded: "
        "the upload could not be re-read"
    )
