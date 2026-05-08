from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.app.services.structural_variant_ingest import iter_structural_variant_records


def test_manual_bnd_records_capture_remote_partner():
    text = "sv1\t7\t72100000\t72100001\tN\tN[18:14300000[\tBND\t0/1\n"

    records = list(iter_structural_variant_records(text, "manual"))

    assert len(records) == 1
    assert records[0].svtype == "BND"
    assert records[0].remote_chr == "18"
    assert records[0].remote_start == 14_300_000
    assert records[0].remote_end == 14_300_000
