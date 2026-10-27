"""The clinical CNV knowledgebase names every file it was built from (TRACE-18).

Only ClinGen's recurrent-CNV file was pinned and recorded (TRACE-14): the knowledgebase's other
inputs, the ClinGen dosage curation, the UCSC cytobands, ClinVar's variant summary, Orphanet's
nomenclature and a DECIPHER map, were identified by the rebuild's date alone. The build now
records each file it reads, by its SHA-256 and size (a local file by its name, not where it lies),
writes them to ``--sources-out`` beside the pinned file, and the admin rebuild records them in the
knowledgebase's release, which the signed analysis record freezes. A file the build could not
read into the knowledgebase is not recorded. ClinGen's GRCh37 recurrent-CNV file is pinned too,
so a GRCh37 rebuild no longer stops before downloading it. Nothing here goes to the network.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parents[2] / "scripts"))

import clinical_cnv_knowledgebase as kb_script
from app.services import clinical_cnv_kb_jobs as kb_jobs

HG37_URL = "https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg37.bed"
HG38_URL = "https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg38.bed"
BUNDLED_HG38 = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "ref-data"
    / "clinical_cnv_syndromes_hg38_bundle"
    / "ClinGen_recurrent_CNV_V2.1-hg38.bed"
)
# ClinGen's V2.1 GRCh37 file, downloaded from ftp.clinicalgenome.org on 2026-10-27 with the
# owner's permission: 16,772 bytes, last modified 2026-03-18.
CLINGEN_V2_1_HG37_SHA256 = "92f331fe6dd2ba2932db747fdf29e6284efe53e033a8775c2c1ad5e22ce2e0b1"
CURATION_URL = "https://ftp.clinicalgenome.org/ClinGen_region_curation_list_GRCh38.tsv"
CURATION = b"#ISCA ID\tISCA Region Name\tGenomic Location\nISCA-1\tregion\tchr7:1-2\n"
CYTOBANDS = gzip.compress(b"chr7\t0\t5000000\tp22.3\tgneg\n")


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---- The GRCh37 pin ------------------------------------------------------------------------------


def test_the_grch37_recurrent_file_is_pinned_to_clingens_v2_1_file() -> None:
    assert kb_script.CLINGEN_RECURRENT_CNV_SHA256["GRCh37"] == CLINGEN_V2_1_HG37_SHA256
    assert kb_script.CLINGEN_RECURRENT_CNV["GRCh37"] == HG37_URL


def test_a_grch37_build_downloads_the_file_and_checks_it_against_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    # Before, a GRCh37 rebuild stopped before downloading: nothing was pinned for it.
    served = b'track db="hg19" name="ClinGen Recurrent CNV"\nchr1\t1\t2\tBP1\t0\t.\t1\t2\t0,0,0\n'
    fetched: list[str] = []

    def safe_get(url: str, timeout: int = 60) -> bytes:
        fetched.append(url)
        return served

    monkeypatch.setattr(kb_script, "safe_get", safe_get)

    with pytest.raises(kb_script.SourceUnavailable) as excinfo:
        kb_script.load_clingen_recurrent_regions("GRCh37")

    assert fetched == [HG37_URL]
    assert CLINGEN_V2_1_HG37_SHA256 in str(excinfo.value) and _digest(served) in str(excinfo.value)


# ---- The files a build reads ---------------------------------------------------------------------


def _offline(monkeypatch: pytest.MonkeyPatch, served: dict[str, bytes]) -> list[str]:
    """Serve ``served`` by URL; any other URL is unreachable (Orphanet: skipped with a log line)."""
    fetched: list[str] = []

    def safe_get(url: str, timeout: int = 60) -> bytes:
        fetched.append(url)
        if url in served:
            return served[url]
        raise requests.ConnectionError(f"offline: {url}")

    monkeypatch.setattr(kb_script, "safe_get", safe_get)
    monkeypatch.setattr(kb_script, "discover_clingen_tsv_urls", lambda assembly: [CURATION_URL])
    curated = pd.DataFrame([{"syndrome_name": "region", "chromosome": "7", "start": 1, "end": 2}])
    monkeypatch.setattr(kb_script, "normalize_clingen_table", lambda df, url, assembly: curated)
    monkeypatch.delenv("OMIM_API_KEY", raising=False)
    return fetched


def test_the_build_writes_each_file_it_read_by_sha256_and_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hg38 = BUNDLED_HG38.read_bytes()
    _offline(monkeypatch, {CURATION_URL: CURATION, HG38_URL: hg38, kb_script.UCSC_CYTO["GRCh38"]: CYTOBANDS})
    decipher = tmp_path / "local" / "decipher_map.tsv"
    decipher.parent.mkdir()
    decipher_bytes = b"syndrome_name\tdecipher_id\tdecipher_url\nregion\t7\thttps://example.org/7\n"
    decipher.write_bytes(decipher_bytes)
    out, sources = tmp_path / "kb.tsv", tmp_path / "kb.sources.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "clinical_cnv_knowledgebase.py",
            "--assembly",
            "GRCh38",
            "--out",
            str(out),
            "--skip-clinvar",
            "--decipher-map",
            str(decipher),
            "--sources-out",
            str(sources),
        ],
    )

    kb_script.main()

    written = json.loads(sources.read_text(encoding="utf-8"))
    assert written["checked_files"] == [
        {"source": "ClinGen recurrent CNV", "release": "V2.1", "url": HG38_URL, "sha256": _digest(hg38)}
    ]
    # Before: no record of these at all. The DECIPHER map by its name, not by where it lies.
    assert written["recorded_files"] == [
        {"source": "ClinGen dosage curation", "url": CURATION_URL, "sha256": _digest(CURATION), "size": len(CURATION)},
        {
            "source": "UCSC cytobands",
            "url": kb_script.UCSC_CYTO["GRCh38"],
            "sha256": _digest(CYTOBANDS),
            "size": len(CYTOBANDS),
        },
        {"source": "DECIPHER map", "url": "decipher_map.tsv", "sha256": _digest(decipher_bytes), "size": len(decipher_bytes)},
    ]


def test_each_build_records_only_the_files_it_read(monkeypatch: pytest.MonkeyPatch) -> None:
    hg38 = BUNDLED_HG38.read_bytes()
    _offline(monkeypatch, {CURATION_URL: CURATION, HG38_URL: hg38, kb_script.UCSC_CYTO["GRCh38"]: CYTOBANDS})

    kb_script.build_kb(assembly="GRCh38", skip_clinvar=True)
    kb_script.build_kb(assembly="GRCh38", skip_clinvar=True)

    assert [recorded.source for recorded in kb_script.recorded_files()] == [
        "ClinGen dosage curation",
        "UCSC cytobands",
    ]


class _Stream:
    """A streamed download: ``requests.get(…, stream=True)`` as the ClinVar step reads it."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "_Stream":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int) -> Any:
        for start in range(0, len(self.payload), 7):
            yield self.payload[start : start + 7]


def test_clinvars_summary_is_recorded_by_the_bytes_that_streamed(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = gzip.compress(
        b"Assembly\tChromosome\tStart\tStop\tClinicalSignificance\tType\tName\tVariationID\tRCVaccession\n"
        b"GRCh38\t7\t1\t2\tPathogenic\tcopy number loss\tdel\t1\tRCV1\n"
    )
    monkeypatch.setattr(kb_script.requests, "get", lambda url, **kwargs: _Stream(summary))
    kb_script._RECORDED_FILES.clear()

    kb_script.load_clinvar_cnv_support("GRCh38")

    assert kb_script.recorded_files() == [
        kb_script.RecordedFile(
            source="ClinVar variant summary",
            url=kb_script.CLINVAR_VARIANT_SUMMARY,
            sha256=_digest(summary),
            size=len(summary),
        )
    ]


def test_a_file_the_build_could_not_read_is_not_recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kb_script._RECORDED_FILES.clear()
    kb = pd.DataFrame([{"syndrome_name": "region"}])
    monkeypatch.setattr(kb_script, "safe_get", lambda url, timeout=60: b"<not orphanet>")

    def unreadable(path: str) -> pd.DataFrame:
        raise ValueError("not an Orphanet nomenclature")

    monkeypatch.setattr(kb_script, "parse_orphanet_xml", unreadable)
    kb_script.add_orphanet_matches(kb)
    columns_missing = tmp_path / "decipher_map.tsv"
    columns_missing.write_text("name\tid\nregion\t7\n", encoding="utf-8")
    kb_script.add_decipher_mapping(kb, str(columns_missing))
    unreadable_summary = gzip.compress(b"Chromosome\tStart\n7\t1\n")  # no Assembly column
    monkeypatch.setattr(kb_script.requests, "get", lambda url, **kwargs: _Stream(unreadable_summary))
    kb_script.load_clinvar_cnv_support("GRCh38")

    assert kb_script.recorded_files() == []


def test_a_local_orphanet_file_is_recorded_by_its_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kb_script._RECORDED_FILES.clear()
    xml = tmp_path / "en_product6.xml"
    xml.write_bytes(b"<JDBOR/>")
    monkeypatch.setattr(kb_script, "parse_orphanet_xml", lambda path: pd.DataFrame())

    kb_script.add_orphanet_matches(pd.DataFrame([{"syndrome_name": "region"}]), str(xml))

    assert kb_script.recorded_files() == [
        kb_script.RecordedFile(source="Orphanet nomenclature", url="en_product6.xml", sha256=_digest(b"<JDBOR/>"), size=8)
    ]


# ---- The release the load records ----------------------------------------------------------------

_CHECKED = {"source": "ClinGen recurrent CNV", "release": "V2.1", "url": HG38_URL, "sha256": "a" * 64}
_CYTOBANDS = {"source": "UCSC cytobands", "url": "https://hgdownload.soe.ucsc.edu/x/cytoBand.txt.gz", "sha256": "b" * 64, "size": 9}
_DECIPHER = {"source": "DECIPHER map", "url": "decipher_map.tsv", "sha256": "c" * 64, "size": 3}


def test_the_release_names_the_pinned_file_then_each_file_read_as_published() -> None:
    release = kb_jobs.checked_files_release(json.dumps({"checked_files": [_CHECKED], "recorded_files": [_CYTOBANDS, _DECIPHER]}))

    assert release == (
        f"ClinGen recurrent CNV V2.1: ClinGen_recurrent_CNV_V2.1-hg38.bed, SHA-256 {'a' * 64}; "
        f"read as published: UCSC cytobands: cytoBand.txt.gz, SHA-256 {'b' * 64}; "
        f"DECIPHER map: decipher_map.tsv, SHA-256 {'c' * 64}"
    )


def test_a_record_without_files_read_names_the_pinned_file_alone() -> None:
    assert kb_jobs.checked_files_release(json.dumps({"checked_files": [_CHECKED], "recorded_files": []})) == (
        f"ClinGen recurrent CNV V2.1: ClinGen_recurrent_CNV_V2.1-hg38.bed, SHA-256 {'a' * 64}"
    )


@pytest.mark.parametrize(
    "recorded",
    ["not a list", [{**_CYTOBANDS, "sha256": "b" * 12}], [{**_CYTOBANDS, "source": " "}], [{**_CYTOBANDS, "url": None}], ["x"]],
    ids=["not a list", "a shortened digest", "no source", "no url", "not described"],
)
def test_a_file_read_without_its_full_record_refuses_the_load(recorded: Any) -> None:
    with pytest.raises(ValueError):
        kb_jobs.checked_files_release(json.dumps({"checked_files": [_CHECKED], "recorded_files": recorded}))
