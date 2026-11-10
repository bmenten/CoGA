from pathlib import Path

from app.services.family_metadata_context import SampleMetadataContext
from app.services.family_package_common import APCAD_PCF_SOURCE, APCAD_PCF_TRACK_TYPE
from app.services.family_package_discovery import (
    NAMING_SCHEMES,
    _pcf_dataset_availability,
)
from app.services.family_package_tracks import _parse_pcf_segment_row


def test_pcf_availability_detects_verified_dataset_naming(tmp_path: Path) -> None:
    pcf_dir = tmp_path / "PCF"
    pcf_dir.mkdir()
    (pcf_dir / "S1_pcf_mat_data.csv").write_text(
        '"sampleID","CHROM","arm","start.pos","end.pos","n.probes","mean"\n'
        '"S1","chr1","p",10,20,4,0.51\n',
        encoding="utf-8",
    )
    (pcf_dir / "S1_pcf_pat_data.csv").write_text(
        '"sampleID","CHROM","arm","start.pos","end.pos","n.probes","mean"\n'
        '"S1","chr1","p",10,20,4,0.49\n',
        encoding="utf-8",
    )

    availability, manifest_block = _pcf_dataset_availability(
        root=tmp_path,
        family_id="F1",
        sample_ids=["S1", "S2"],
        patterns=NAMING_SCHEMES["standard_v1"]["datasets"]["pcf"],
    )

    assert availability.enabled is True
    assert availability.samples == ["S1"]
    assert manifest_block == {
        "enabled": True,
        "per_sample": {
            "S1": {
                "maternal": "PCF/S1_pcf_mat_data.csv",
                "paternal": "PCF/S1_pcf_pat_data.csv",
            }
        },
    }


def test_pcf_availability_is_optional_when_missing(tmp_path: Path) -> None:
    availability, manifest_block = _pcf_dataset_availability(
        root=tmp_path,
        family_id="F1",
        sample_ids=["S1"],
        patterns=NAMING_SCHEMES["standard_v1"]["datasets"]["pcf"],
    )

    assert availability.enabled is False
    assert availability.complete is False
    assert manifest_block == {"enabled": False, "per_sample": {}}


def test_parse_pcf_segment_row_stores_apcad_pcf_interval_track() -> None:
    sample_context = SampleMetadataContext(
        sample_uuid="sample-uuid",
        sample_id="S1",
        family_uuid="family-uuid",
        family_id="F1",
        sex="female",
        project_ids=[],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )

    row = _parse_pcf_segment_row(
        {
            "sampleID": "S1",
            "CHROM": "chr1",
            "arm": "p",
            "start.pos": "1703565",
            "end.pos": "119614882",
            "n.probes": "232",
            "mean": "0.4948",
        },
        sample_context=sample_context,
        path=Path("S1_pcf_mat_data.csv"),
        line_no=2,
        origin="maternal",
    )

    assert row is not None
    assert row["track_type"] == APCAD_PCF_TRACK_TYPE
    assert row["source"] == APCAD_PCF_SOURCE
    assert row["chr"] == "1"
    assert row["start"] == 1703565
    assert row["end"] == 119614882
    assert row["value"] == 0.4948
    assert row["origin"] == "maternal"
