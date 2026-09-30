"""The knowledgebase build's backbone sources: ClinGen's dosage curation and recurrent CNVs.

Until #721 the GRCh37 build fetched ClinGen's recurrent-CNV regions from ``…-hg19.bed``, a
404 (ClinGen publishes ``…-hg37.bed``). The build logged the failure and went on without
them, so every GRCh37 knowledgebase lacked the named recurrent syndromes. The admin rebuild
then replaced whatever knowledgebase there was with that one. Now each backbone source that
cannot be loaded stops the build, and the rebuild keeps the previous knowledgebase.

The GRCh38 recurrent-CNV file writes ``chrx``, which the chromosome cleaning kept as ``x``.
Every clinical-CNV query for X asks for ``X``/``chrX``, so four X-linked recurrent regions
never showed. Chromosome names are now read case-insensitively.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
import requests

sys.path.append(str(Path(__file__).resolve().parents[2] / "scripts"))
import clinical_cnv_knowledgebase as kb_script

HG37_URL = "https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg37.bed"
HG38_URL = "https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg38.bed"
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


def test_the_recurrent_regions_are_downloaded_for_the_assembly(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []

    def safe_get(url: str, timeout: int = 60) -> bytes:
        fetched.append(url)
        return HG37_BED.encode()

    monkeypatch.setattr(kb_script, "safe_get", safe_get)

    regions = kb_script.load_clingen_recurrent_regions("GRCh37")

    assert fetched == [HG37_URL]
    assert len(regions) == 2


def test_recurrent_regions_that_cannot_be_downloaded_stop_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    def safe_get(url: str, timeout: int = 60) -> bytes:
        raise requests.HTTPError(f"404 Client Error: Not Found for url: {url}")

    monkeypatch.setattr(kb_script, "safe_get", safe_get)

    with pytest.raises(kb_script.SourceUnavailable, match="could not be downloaded from .*-hg37.bed"):
        kb_script.load_clingen_recurrent_regions("GRCh37")


def test_a_recurrent_file_without_regions_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    only_markers = "\r\n".join(HG38_BED.splitlines()[:3]) + "\r\n"  # header and BP1
    monkeypatch.setattr(kb_script, "safe_get", lambda url, timeout=60: only_markers.encode())

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
