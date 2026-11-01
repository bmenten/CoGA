"""Bounded public downloads: exact PGS/build, complete gzip, metadata and URL isolation."""
import gzip
import hashlib

import httpx
import pytest
from fastapi import HTTPException

from backend.app.services import pgs_catalog_service as catalog
from backend.app.services.pgs_scoring import PgsInputError

PGS = "PGS900001"
WEIGHTS = "#pgs_id=PGS900001\n#genome_build=GRCh37\n#HmPOS_build=GRCh38\n#variants_number=2\nhm_chr\thm_pos\teffect_allele\tother_allele\tref_allele\teffect_weight\n1\t10\tG\tA\tA\t0.5\n1\t20\tC\tT\tC\t-1\n"


def metadata(pgs=PGS):
    return {"id": pgs, "name": "Synthetic catalog model", "trait_reported": "Synthetic trait",
            "variants_number": 2, "variants_genomebuild": "GRCh37", "license": "Synthetic licence",
            "publication": {"id": "PGP900001", "title": "Synthetic catalog study", "doi": "10.0000/synthetic"},
            "ancestry_distribution": {"dev": {"dist": {"EUR": 100}}},
            "ftp_harmonized_scoring_files": {"GRCh38": {"positions": f"{catalog.FILES}/pub/databases/spot/pgs/scores/{pgs}/ScoringFiles/Harmonized/{pgs}_hmPOS_GRCh38.txt.gz"}}}


def install_transport(monkeypatch, data=None, content=None, status=200):
    requests = []
    compressed = gzip.compress(WEIGHTS.encode()) if content is None else content
    def serve(request):
        requests.append(request)
        if request.url.host == "www.pgscatalog.org":
            return httpx.Response(status, json=metadata() if data is None else data)
        assert request.url.host == "ftp.ebi.ac.uk"
        return httpx.Response(200, content=compressed)
    monkeypatch.setattr(catalog, "catalog_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(serve), follow_redirects=False))
    return requests, compressed


@pytest.mark.asyncio
async def test_complete_catalog_metadata_and_original_weights_are_preserved(monkeypatch):
    requests, compressed = install_transport(monkeypatch)
    weights, snapshot = await catalog.fetch_catalog(PGS)
    assert weights == WEIGHTS
    assert snapshot["record"] == metadata()
    assert snapshot["download_sha256"] == hashlib.sha256(compressed).hexdigest()
    assert snapshot["scoring_file"]["header"]["genome_build"] == "GRCh37"
    assert snapshot["scoring_file"]["header"]["HmPOS_build"] == "GRCh38"
    assert snapshot["genome_build"] == "GRCh38"
    assert len(requests) == 2
    assert all(not r.url.query and "authorization" not in r.headers for r in requests)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/private", "https://ftp.ebi.ac.uk.evil.invalid/file",
    "https://ftp.ebi.ac.uk:443/file", "https://user@ftp.ebi.ac.uk/file",
    metadata()["ftp_harmonized_scoring_files"]["GRCh38"]["positions"] + "?redirect=private",
    metadata()["ftp_harmonized_scoring_files"]["GRCh38"]["positions"].replace("GRCh38", "GRCh37"),
    metadata()["ftp_harmonized_scoring_files"]["GRCh38"]["positions"].replace(PGS, "PGS900002"),
])
@pytest.mark.asyncio
async def test_unexpected_download_locations_are_refused_before_request(monkeypatch, url):
    record = metadata()
    record["ftp_harmonized_scoring_files"]["GRCh38"]["positions"] = url
    requests, _ = install_transport(monkeypatch, record)
    with pytest.raises(PgsInputError, match="location"):
        await catalog.fetch_catalog(PGS)
    assert len(requests) == 1


@pytest.mark.parametrize("change", ["missing_build", "wrong_id", "wrong_count", "absent"])
@pytest.mark.asyncio
async def test_missing_or_inconsistent_metadata_fails_without_import(monkeypatch, change):
    record = metadata()
    if change == "missing_build":
        record["ftp_harmonized_scoring_files"] = {}
    elif change == "wrong_id":
        record["id"] = "PGS900002"
    elif change == "wrong_count":
        record["variants_number"] = 3
    install_transport(monkeypatch, record, status=404 if change == "absent" else 200)
    with pytest.raises(PgsInputError):
        await catalog.fetch_catalog(PGS)


@pytest.mark.parametrize("content", [b"not gzip", gzip.compress(WEIGHTS.encode())[:-4],
                                    gzip.compress(WEIGHTS.replace("HmPOS_build=GRCh38", "HmPOS_build=GRCh37").encode()),
                                    gzip.compress(WEIGHTS.replace("1\t20\tC\tT\tC\t-1\n", "").encode())])
@pytest.mark.asyncio
async def test_corrupt_truncated_or_wrong_build_files_never_import(monkeypatch, content):
    install_transport(monkeypatch, content=content)
    with pytest.raises((ValueError, PgsInputError)):
        await catalog.fetch_catalog(PGS)


@pytest.mark.asyncio
async def test_download_and_decompression_size_limits_apply(monkeypatch):
    install_transport(monkeypatch)
    monkeypatch.setattr(catalog.settings, "max_upload_bytes", 10)
    with pytest.raises(ValueError, match="exceeds"):
        await catalog.fetch_catalog(PGS)
    monkeypatch.setattr(catalog.settings, "max_upload_bytes", 1024 * 1024)
    monkeypatch.setattr(catalog.settings, "max_decompressed_upload_bytes", 10)
    with pytest.raises(ValueError, match="expands"):
        await catalog.fetch_catalog(PGS)


def test_complete_file_validation_and_invalid_ids():
    with pytest.raises(PgsInputError, match="incomplete"):
        catalog.scoring_header(WEIGHTS.replace("1\t20\tC\tT\tC\t-1", "1\t20\tC"), PGS, 2)
    with pytest.raises(PgsInputError, match="count"):
        catalog.scoring_header(WEIGHTS, PGS, None)
    with pytest.raises(HTTPException) as invalid:
        catalog._valid_id("../PGS900001")
    assert invalid.value.status_code == 400
