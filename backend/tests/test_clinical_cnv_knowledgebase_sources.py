"""The knowledgebase build's backbone sources: ClinGen's dosage curation and recurrent CNVs.

Until #721 the GRCh37 build fetched ClinGen's recurrent-CNV regions from ``…-hg19.bed``, a
404 (ClinGen publishes ``…-hg37.bed``). The build logged the failure and went on without
them, so every GRCh37 knowledgebase lacked the named recurrent syndromes. The admin rebuild
then replaced whatever knowledgebase there was with that one. Now each backbone source that
cannot be loaded stops the build, and the rebuild keeps the previous knowledgebase.

The GRCh38 recurrent-CNV file writes ``chrx``, which the chromosome cleaning kept as ``x``.
Every clinical-CNV query for X asks for ``X``/``chrX``, so four X-linked recurrent regions
never showed. Chromosome names are now read case-insensitively.

The recurrent-CNV file was read unchecked, and only its URL was recorded (TRACE-14). It is
pinned per assembly by its SHA-256 now: the build reads it only if its SHA-256 is the pinned
one (GRCh38: the bundled copy of ClinGen's V2.1 file), stops on any other file, does not
download it for an assembly with no pin (GRCh37), and writes the file it checked to
``--sources-out``, which the admin rebuild records with the load. Nothing here goes to the
network: ``safe_get`` is replaced, and serves the bundled file where ClinGen's is asked for.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
import requests

sys.path.append(str(Path(__file__).resolve().parents[2] / "scripts"))
import clinical_cnv_knowledgebase as kb_script

HG37_URL = "https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg37.bed"
HG38_URL = "https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg38.bed"
# ClinGen's V2.1 GRCh38 file as the repository ships it (test_clingen_recurrent_cnv_bundle.py).
BUNDLED_HG38 = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "ref-data"
    / "clinical_cnv_syndromes_hg38_bundle"
    / "ClinGen_recurrent_CNV_V2.1-hg38.bed"
)
COLUMNS = "#chrom\tstart\tend\tname\tscore\tstrand\tthickStart\tthickEnd\titemRgb"

# The two header shapes ClinGen uses, with its CRLF line ends. GRCh37: `#chrom …` on the
# track line, after a run of tabs.
HG37_BED = "\r\n".join(
    [
        'track db="hg19" name="ClinGen Recurrent CNV" description="ClinGen Recurrent CNV" '
        'itemRgb="On"' + "\t" * 11 + COLUMNS,
        "chr1\t144145783\t144919024\tBP1\t0\t.\t144145783\t144919024\t0,0,0",
        "chr1\t144919024\t145748064\t1q21.1 recurrent (TAR syndrome) region (proximal, BP1-BP3) "
        "(includes RBM8A)\t0\t.\t144919024\t145748064\t255,111,0",
        "chrX\t6455151\t8135568\tXp22.31 recurrent region\t0\t.\t6455151\t8135568\t255,111,0",
    ]
) + "\r\n"
# GRCh38: its own track line and `#chrom` line; X written as `chrx`.
HG38_BED = "\r\n".join(
    [
        'track name="ClinGen_recurrent_CNV_V2.0-hg38" itemRGB="On"' + "\t" * 8,
        COLUMNS,
        "chr1\t143184587\t145686998\tBP1\t0\t.\t143184587\t145686998\t0,0,0",
        "chr1\t145686999\t146048495\t1q21.1 recurrent (TAR syndrome) region (proximal, BP2-BP3) "
        "(includes RBM8A)\t0\t.\t145686999\t146048495\t255,111,0",
        "chrx\t6537771\t8156913\tXp22.31 recurrent region\t0\t.\t6537771\t8156913\t255,111,0",
    ]
) + "\r\n"


def test_each_assembly_reads_the_file_clingen_publishes() -> None:
    assert kb_script.CLINGEN_RECURRENT_CNV == {"GRCh38": HG38_URL, "GRCh37": HG37_URL}


@pytest.mark.parametrize(
    ("text", "assembly", "url", "tar_start", "x_start"),
    [
        (HG37_BED, "GRCh37", HG37_URL, 144919024, 6455151),
        (HG38_BED, "GRCh38", HG38_URL, 145686999, 6537771),
    ],
)
def test_both_header_shapes_give_the_named_regions(
    text: str, assembly: str, url: str, tar_start: int, x_start: int
) -> None:
    regions = kb_script.parse_recurrent_cnv_bed(text, assembly=assembly, source_url=url)

    # The header lines and the breakpoint marker (BP1) name no syndrome region.
    assert list(regions["syndrome_name"]) == [
        next(line.split("\t")[3] for line in text.splitlines() if "TAR syndrome" in line),
        "Xp22.31 recurrent region",
    ]
    assert list(regions["chromosome"]) == ["1", "X"]
    assert list(regions["start"]) == [tar_start, x_start]
    assert set(regions["assembly"]) == {assembly}
    assert set(regions["source_url"]) == {url}
    assert set(regions["source"]) == {"ClinGen recurrent CNV"}


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("chr1", "1"),
        ("1", "1"),
        (" chr22 ", "22"),
        ("chrX", "X"),
        ("chrx", "X"),
        ("x", "X"),
        ("CHRY", "Y"),
        ("23", "X"),
        ("24", "Y"),
        ("chrM", "MT"),
        ("chrm", "MT"),
        ("MT", "MT"),
    ],
)
def test_chromosome_names_are_read_case_insensitively(raw: str, clean: str) -> None:
    assert kb_script.clean_chr(raw) == clean


def _serve(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> list[str]:
    """Answer every download with ``payload``; the list collects the URLs requested."""
    fetched: list[str] = []

    def safe_get(url: str, timeout: int = 60) -> bytes:
        fetched.append(url)
        return payload

    monkeypatch.setattr(kb_script, "safe_get", safe_get)
    return fetched


def _bundled_digest() -> str:
    return hashlib.sha256(BUNDLED_HG38.read_bytes()).hexdigest()


def test_the_grch38_pin_is_clingens_v2_1_file_as_bundled_and_grch37_has_none() -> None:
    assert kb_script.CLINGEN_RECURRENT_CNV_RELEASE == "V2.1"
    assert kb_script.CLINGEN_RECURRENT_CNV_SHA256 == {"GRCh38": _bundled_digest()}
    assert kb_script.CLINGEN_RECURRENT_CNV["GRCh38"].endswith("/" + BUNDLED_HG38.name)


def test_the_recurrent_regions_are_downloaded_for_the_assembly_and_read_when_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched = _serve(monkeypatch, BUNDLED_HG38.read_bytes())

    regions, checked = kb_script.load_clingen_recurrent_regions("GRCh38")

    assert fetched == [HG38_URL]
    assert len(regions) > 0 and set(regions["source_url"]) == {HG38_URL}
    # The file read, as the load records it: its digest is computed from the bytes read.
    assert checked == kb_script.CheckedFile(
        source="ClinGen recurrent CNV", release="V2.1", url=HG38_URL, sha256=_bundled_digest()
    )


def test_a_recurrent_file_that_is_not_the_pinned_one_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    # What ClinGen serves after it changed the file, or what reached the build changed on the
    # way: three regions, not the checked release.
    served = HG38_BED.encode()
    fetched = _serve(monkeypatch, served)

    with pytest.raises(kb_script.SourceUnavailable) as excinfo:
        kb_script.load_clingen_recurrent_regions("GRCh38")

    message = str(excinfo.value)
    # The file, both digests and the fix.
    for name in (HG38_URL, hashlib.sha256(served).hexdigest(), _bundled_digest(), "CLINGEN_RECURRENT_CNV_SHA256"):
        assert name in message, name
    assert fetched == [HG38_URL]
    # The admin sees the build's last line, cut at 500 characters (clinical_cnv_kb_jobs).
    assert len(f"Build stopped: {message}") <= 500


def test_an_assembly_without_a_pinned_file_is_not_downloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched = _serve(monkeypatch, HG37_BED.encode())

    with pytest.raises(kb_script.SourceUnavailable) as excinfo:
        kb_script.load_clingen_recurrent_regions("GRCh37")

    assert "No SHA-256 is pinned for ClinGen's recurrent CNV file for GRCh37" in str(excinfo.value)
    assert HG37_URL in str(excinfo.value)
    assert fetched == []


def test_recurrent_regions_that_cannot_be_downloaded_stop_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    def safe_get(url: str, timeout: int = 60) -> bytes:
        raise requests.HTTPError(f"404 Client Error: Not Found for url: {url}")

    monkeypatch.setattr(kb_script, "safe_get", safe_get)

    with pytest.raises(kb_script.SourceUnavailable, match="could not be downloaded from .*-hg38.bed"):
        kb_script.load_clingen_recurrent_regions("GRCh38")


def test_a_recurrent_file_without_regions_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    only_markers = "\r\n".join(HG38_BED.splitlines()[:3]) + "\r\n"  # header and BP1
    # Were this the pinned file, its regions would still be missing.
    monkeypatch.setitem(
        kb_script.CLINGEN_RECURRENT_CNV_SHA256, "GRCh38", hashlib.sha256(only_markers.encode()).hexdigest()
    )
    _serve(monkeypatch, only_markers.encode())

    with pytest.raises(kb_script.SourceUnavailable, match="holds no recurrent regions"):
        kb_script.load_clingen_recurrent_regions("GRCh38")


def test_an_unknown_assembly_has_no_recurrent_regions_to_fall_back_on() -> None:
    with pytest.raises(kb_script.SourceUnavailable, match="No ClinGen recurrent CNV file is known for hg18"):
        kb_script.load_clingen_recurrent_regions("hg18")


CURATION_URL = "https://ftp.clinicalgenome.org/ClinGen_region_curation_list_GRCh37.tsv"


def _curation_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb_script, "discover_clingen_tsv_urls", lambda assembly: [CURATION_URL])


def _no_recurrent_expected(assembly: str) -> pd.DataFrame:
    raise AssertionError("the build went on past a curation it could not load")


def test_a_curation_file_that_cannot_be_loaded_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    _curation_listed(monkeypatch)

    def load_table(url: str) -> pd.DataFrame:
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(kb_script, "load_table_from_url", load_table)
    monkeypatch.setattr(kb_script, "load_clingen_recurrent_regions", _no_recurrent_expected)

    with pytest.raises(kb_script.SourceUnavailable, match=f"curation file {CURATION_URL} could not be loaded"):
        kb_script.build_kb(assembly="GRCh37", skip_clinvar=True)


def test_a_curation_without_records_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    _curation_listed(monkeypatch)
    monkeypatch.setattr(kb_script, "load_table_from_url", lambda url: pd.DataFrame())
    monkeypatch.setattr(kb_script, "normalize_clingen_table", lambda df, url, assembly: pd.DataFrame())
    monkeypatch.setattr(kb_script, "load_clingen_recurrent_regions", _no_recurrent_expected)

    with pytest.raises(kb_script.SourceUnavailable, match="No usable ClinGen dosage-curation records"):
        kb_script.build_kb(assembly="GRCh37", skip_clinvar=True)


def test_missing_recurrent_regions_stop_the_whole_build(monkeypatch: pytest.MonkeyPatch) -> None:
    _curation_listed(monkeypatch)
    curated = pd.DataFrame([{"syndrome_name": "region", "chromosome": "7", "start": 1, "end": 2}])
    monkeypatch.setattr(kb_script, "load_table_from_url", lambda url: pd.DataFrame())
    monkeypatch.setattr(kb_script, "normalize_clingen_table", lambda df, url, assembly: curated)

    def unavailable(assembly: str) -> pd.DataFrame:
        raise kb_script.SourceUnavailable("The ClinGen recurrent CNV regions could not be downloaded")

    monkeypatch.setattr(kb_script, "load_clingen_recurrent_regions", unavailable)

    with pytest.raises(kb_script.SourceUnavailable, match="recurrent CNV regions"):
        kb_script.build_kb(assembly="GRCh37", skip_clinvar=True)


def test_a_stopped_build_exits_non_zero_and_says_why_last(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def build_kb(**kwargs: object) -> pd.DataFrame:
        raise kb_script.SourceUnavailable(f"The ClinGen recurrent CNV regions could not be downloaded from {HG37_URL}")

    out = tmp_path / "kb.tsv"
    monkeypatch.setattr(kb_script, "build_kb", build_kb)
    monkeypatch.setattr(sys, "argv", ["clinical_cnv_knowledgebase.py", "--assembly", "GRCh37", "--out", str(out)])

    with pytest.raises(SystemExit) as exit_info:
        kb_script.main()

    assert exit_info.value.code == 1
    # The admin rebuild shows the last stderr line as the job's error.
    last_line = capsys.readouterr().err.strip().splitlines()[-1]
    assert last_line == f"[build-cnv-kb] Build stopped: The ClinGen recurrent CNV regions could not be downloaded from {HG37_URL}"
    assert not out.exists()


def _offline_curation(monkeypatch: pytest.MonkeyPatch) -> None:
    _curation_listed(monkeypatch)
    curated = pd.DataFrame([{"syndrome_name": "region", "chromosome": "7", "start": 1, "end": 2}])
    monkeypatch.setattr(kb_script, "load_table_from_url", lambda url: pd.DataFrame())
    monkeypatch.setattr(kb_script, "normalize_clingen_table", lambda df, url, assembly: curated)


def _argv(monkeypatch: pytest.MonkeyPatch, out: Path, sources: Path) -> None:
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
            "--sources-out",
            str(sources),
        ],
    )


def test_the_build_writes_the_file_it_checked_for_the_load_to_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A GRCh38 build, offline: the recurrent regions come from the bundled copy of ClinGen's
    file through the pinned check, and ``--sources-out`` names that file by its SHA-256."""
    _offline_curation(monkeypatch)
    monkeypatch.setattr(kb_script, "load_ucsc_cytobands", lambda assembly: pd.DataFrame())
    monkeypatch.setattr(kb_script, "annotate_cytobands", lambda kb, cyto: kb)
    monkeypatch.delenv("OMIM_API_KEY", raising=False)
    fetched: list[str] = []

    def safe_get(url: str, timeout: int = 60) -> bytes:
        fetched.append(url)
        if url == HG38_URL:
            return BUNDLED_HG38.read_bytes()
        raise requests.ConnectionError(f"offline: {url}")  # Orphanet: skipped with a log line

    monkeypatch.setattr(kb_script, "safe_get", safe_get)
    out, sources = tmp_path / "kb.tsv", tmp_path / "kb.sources.json"
    _argv(monkeypatch, out, sources)

    kb_script.main()

    assert HG38_URL in fetched
    assert json.loads(sources.read_text(encoding="utf-8")) == {
        "checked_files": [
            {"source": "ClinGen recurrent CNV", "release": "V2.1", "url": HG38_URL, "sha256": _bundled_digest()}
        ]
    }
    written = pd.read_csv(out, sep="\t", dtype=str)
    recurrent = written[written["source"] == "ClinGen recurrent CNV"]
    assert len(recurrent) > 0 and set(recurrent["source_url"]) == {HG38_URL}


def test_a_build_stopped_by_the_pin_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _offline_curation(monkeypatch)
    _serve(monkeypatch, HG38_BED.encode())
    out, sources = tmp_path / "kb.tsv", tmp_path / "kb.sources.json"
    _argv(monkeypatch, out, sources)

    with pytest.raises(SystemExit) as exit_info:
        kb_script.main()

    assert exit_info.value.code == 1
    last_line = capsys.readouterr().err.strip().splitlines()[-1]
    assert last_line.startswith(
        f"[build-cnv-kb] Build stopped: The ClinGen recurrent CNV file {HG38_URL} is not the pinned V2.1 file"
    )
    assert not out.exists() and not sources.exists()
