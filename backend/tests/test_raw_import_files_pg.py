import pytest

from backend.app.core.config import settings
from backend.app.services import raw_import_files_pg as rifp


@pytest.fixture(autouse=True)
def _files_under_an_import_root(monkeypatch, tmp_path) -> None:
    """The files verified here lie in ``tmp_path``, made an import root: Verify reads a
    recorded path only inside the folders CoGA reads source files from (SEC-29)."""
    monkeypatch.setattr(settings, "family_import_roots", [str(tmp_path)])


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
