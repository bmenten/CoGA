"""A download named after a family or sample ID keeps its name in Content-Disposition (SEC-15).

The PED download, the variant CSV exports and the signed URLs of a CRAM, a signal track or a
QC report name the file after a family or sample ID. Such an ID is printable text without
spaces, so it may hold a ``"``, a ``\\``, a ``%`` or a character outside ASCII. Put into the
header as it was, the name was cut at the quote or misread, and an ID beyond Latin-1 failed
the PED download with an encoding error (a 500): Starlette encodes a header as Latin-1. Such
a name now goes percent-encoded as UTF-8 in ``filename*``, with an ASCII stand-in in
``filename``. The IDs are synthetic.
"""

from __future__ import annotations

import asyncio
from email.message import Message
import re
from email.utils import collapse_rfc2231_value
from typing import Any

import pytest

from backend.app.core import object_storage
from backend.app.core.content_disposition import content_disposition
from backend.app.core.csv_export import export_response_headers
from backend.app.routers import admin

# A quote, a backslash, a percent sign, a Latin-1 letter, a letter beyond Latin-1, a DEL.
AWKWARD = ['F"1', "F\\1", "F%41", "Fé1", "家1", "F\x7f1"]


# The header as RFC 6266 writes it: printable ASCII, a quoted ``filename`` holding no quote,
# backslash or percent sign (a client may end the name, escape or decode there), and a
# ``filename*`` percent-encoded as RFC 8187 has it.
WELL_FORMED = re.compile(
    r'(?:attachment|inline); filename="[^"\\%\x00-\x1f\x7f-\U0010ffff]*"'
    r"(?:; filename\*=UTF-8''[A-Za-z0-9%._~-]+)?"
)


def _read(value: str) -> str:
    """The name a client reads from the header: ``filename*`` when there is one (RFC 6266),
    otherwise ``filename``. The header must be well formed: a lenient parser such as this one
    reads `filename="F"1.ped"` as the whole name, a strict client as `F`."""
    assert WELL_FORMED.fullmatch(value), value
    message = Message()
    message["Content-Disposition"] = value
    names = [found for key, found in message.get_params(header="content-disposition") if key == "filename"]
    encoded = [collapse_rfc2231_value(found) for found in names if isinstance(found, tuple)]
    return encoded[0] if encoded else str(names[0])


def _stand_in(value: str) -> str:
    message = Message()
    message["Content-Disposition"] = value
    plain = [found for key, found in message.get_params(header="content-disposition") if key == "filename"]
    return str(plain[0])


@pytest.mark.parametrize("family_id", AWKWARD)
def test_an_awkward_name_reads_back_as_written(family_id: str) -> None:
    value = content_disposition("attachment", f"{family_id}.ped")

    assert value.isascii() and value.isprintable()
    assert _read(value) == f"{family_id}.ped"
    # A client that reads only `filename` gets a plain ASCII name, never the cut one.
    assert _stand_in(value).replace("_", "").replace(".", "").isalnum()


def test_a_plain_name_is_sent_as_it_is() -> None:
    for name in ["F1.ped", "FAM-001_x.ped", "F;1.ped", "F'1 (copy).ped"]:
        assert content_disposition("inline", name) == f'inline; filename="{name}"'


def test_the_ped_download_names_the_file_after_any_family_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async def pedigree(_session: Any, *, family_id: str) -> str:
        return f"{family_id}\tS1\t0\t0\t1\t2\n"

    monkeypatch.setattr(admin, "build_pedigree_text", pedigree)

    for family_id in AWKWARD:
        # Before: a family ID beyond Latin-1 raised UnicodeEncodeError (a 500), and one with
        # a quote ended the name early.
        response = asyncio.run(admin.download_family_pedigree(family_id, session=None, user=None))  # type: ignore[arg-type]
        assert _read(response.headers["content-disposition"]) == f"{family_id}.ped"


def test_an_export_names_the_file_after_any_family_id() -> None:
    for family_id in AWKWARD:
        full = export_response_headers(f"family-{family_id}-small-variants", rows=1, truncated=False, limit=10)
        cut = export_response_headers(f"family-{family_id}-small-variants", rows=10, truncated=True, limit=10)
        assert _read(full["Content-Disposition"]) == f"family-{family_id}-small-variants.csv"
        assert _read(cut["Content-Disposition"]) == f"family-{family_id}-small-variants-TRUNCATED-first-10.csv"


def test_a_signed_url_names_the_file_after_any_sample_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Blob:
        def generate_signed_url(self, **kwargs: Any) -> str:
            captured["gcs"] = kwargs["response_disposition"]
            return "https://signed.example/x"

    class _Client:
        def bucket(self, _name: str) -> Any:
            return type("_Bucket", (), {"blob": lambda self, _key: _Blob()})()

    class _S3:
        def generate_presigned_url(self, _operation: str, *, Params: dict[str, str], ExpiresIn: int) -> str:
            captured["s3"] = Params["ResponseContentDisposition"]
            return "https://signed.example/y"

    monkeypatch.setattr(object_storage, "_gcs_client", lambda: _Client())
    monkeypatch.setattr(object_storage, "_gcs_signing_credentials", lambda: ("sa@example.iam", "t"))
    monkeypatch.setattr(object_storage, "_s3_client", lambda: _S3())
    monkeypatch.setattr(object_storage.settings, "gcs_bucket", "phi-bucket")
    monkeypatch.setattr(object_storage.settings, "s3_bucket", "phi-bucket")

    for sample_id in AWKWARD:
        for backend in ("gcs", "s3"):
            monkeypatch.setattr(object_storage.settings, "storage_backend", backend)
            object_storage.presigned_get_url("F1/S1.cram", filename=f"{sample_id}.cram", expires=60)
            # Before: `inline; filename="<sample ID>.cram"`, the ID as it was.
            assert captured[backend].startswith("inline; ")
            assert _read(captured[backend]) == f"{sample_id}.cram"
