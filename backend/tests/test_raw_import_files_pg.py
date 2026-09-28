import pytest

from backend.app.services import raw_import_files_pg as rifp


@pytest.mark.asyncio
async def test_verify_raw_import_file_reports_too_large(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(rifp, "_VERIFY_MAX_BYTES", 4)
    path = tmp_path / "big.bin"
    path.write_bytes(b"0123456789")  # 10 bytes > the 4-byte cap

    result = await rifp.verify_raw_import_file(
        {"id": "f1", "storage_path": str(path), "sha256": "deadbeef"}
    )

    assert result["status"] == "too_large"
    assert result["computed_sha256"] is None  # the file was never hashed


@pytest.mark.asyncio
async def test_verify_raw_import_file_under_cap_still_verifies(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(rifp, "_VERIFY_MAX_BYTES", 1024)
    path = tmp_path / "small.bin"
    path.write_bytes(b"hello world")
    expected, _ = rifp._hash_and_size(path)

    result = await rifp.verify_raw_import_file(
        {"id": "f2", "storage_path": str(path), "sha256": expected}
    )

    assert result["status"] == "verified"
    assert result["computed_sha256"] == expected
